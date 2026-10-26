"""命令行入口：用一份标准、一份答卷、一段模型输出跑完整评分。

示例：
    python -m app.cli --rubric rubric.json --answer answer.txt --response model.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core.aggregate import sample_score
from .pipeline import Evaluation, GradingError, evaluate
from .config import load_project_env
from .core.rubric import RubricError, load_file
from .providers.base import ProviderError
from .providers.deepseek import DeepSeekConfig, DeepSeekProvider
from .providers.fake import CannedProvider


def build_report(evaluation: Evaluation, rubric) -> dict:
    combined = evaluation.aggregate
    representative_index = min(
        range(len(evaluation.samples)),
        key=lambda index: abs(
            sample_score(rubric, evaluation.samples[index]) - combined.score
        ),
    )
    representative = evaluation.samples[representative_index]
    by_id = {judgement.item_id: judgement for judgement in representative.items}
    deduction_by_id = {deduction.id: deduction for deduction in representative.deductions}

    items = []
    for item in rubric.items:
        per_sample = [sample.levels()[item.id] for sample in evaluation.samples]
        judgements = [
            next(entry for entry in sample.items if entry.item_id == item.id)
            for sample in evaluation.samples
        ]
        median = combined.levels[item.id]
        # 证据取自"等级等于中位数且已接地"的采样，没有就退回第一份。
        matching = [
            judgement
            for judgement, level in zip(judgements, per_sample)
            if abs(level - median) < 1e-9 and judgement.grounded
        ]
        chosen = matching[0] if matching else judgements[0]

        items.append(
            {
                "id": item.id,
                "level": median,
                "levels": per_sample,
                "hit": any(judgement.hit for judgement in judgements),
                "grounded": all(judgement.grounded for judgement in judgements),
                "evidence": chosen.evidence,
                "span": list(chosen.span) if chosen.span else None,
                "reason": chosen.reason,
            }
        )

    return {
        "question_id": representative.question_id,
        "samples": len(evaluation.samples),
        "score": combined.score,
        "max_score": combined.max_score,
        "confidence": combined.confidence,
        "dispersion": combined.dispersion,
        "total_range": combined.total_range,
        "grounding_rate": combined.grounding_rate,
        "route": evaluation.route.route,
        "reasons": evaluation.route.reasons,
        "ungrounded": sorted(
            {judgement for sample in evaluation.samples for judgement in sample.ungrounded_ids()}
        ),
        "risk": evaluation.route.risk,
        "items": items,
        "deductions": [
            {
                "id": deduction.id,
                "hit": deduction_by_id[deduction.id].hit,
                "grounded": deduction_by_id[deduction.id].grounded,
                "applied": deduction.id in combined.applied_deductions,
                "evidence": deduction_by_id[deduction.id].evidence,
                "span": list(deduction_by_id[deduction.id].span)
                if deduction_by_id[deduction.id].span
                else None,
            }
            for deduction in rubric.deductions
        ],
        "comment": representative.comment,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deepjudge", description="单份答卷评分")
    parser.add_argument("--rubric", required=True, help="评分标准 JSON")
    parser.add_argument("--answer", required=True, help="学生答卷（纯文本）")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--response", help="离线：预置的模型输出（JSON 文本）")
    source.add_argument("--live", action="store_true", help="在线：调用 DeepSeek API")
    parser.add_argument("--samples", type=int, default=1, help="采样次数（默认 1）")
    args = parser.parse_args(argv)
    if args.samples < 1:
        parser.error("--samples 必须大于等于 1")

    try:
        rubric = load_file(args.rubric)
    except RubricError as exc:
        for problem in exc.problems:
            print(f"- {problem}", file=sys.stderr)
        return 2

    answer = Path(args.answer).read_text(encoding="utf-8")

    if args.live:
        load_project_env()
        try:
            config = DeepSeekConfig.from_env()
        except ProviderError as exc:
            print(f"无法调用模型：{exc}", file=sys.stderr)
            return 4
        provider = DeepSeekProvider(config)
    else:
        provider = CannedProvider([Path(args.response).read_text(encoding="utf-8")])

    if not args.live and args.samples > 1:
        print("提示：离线模式用同一份预置输出重复采样，置信度不具参考意义", file=sys.stderr)

    try:
        evaluation = evaluate(rubric, answer, provider, samples=args.samples)
    except ProviderError as exc:
        hint = "（可重试）" if exc.retryable else "（不可重试）"
        print(f"模型调用失败{hint}：{exc}", file=sys.stderr)
        return 4
    except GradingError as exc:
        print(f"评分失败：{exc}", file=sys.stderr)
        return 3

    print(json.dumps(build_report(evaluation, rubric), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

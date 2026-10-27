"""命令行入口：用一份标准、一份答卷、一段模型输出跑完整评分。

示例：
    python -m app.cli --rubric rubric.json --answer answer.txt --response model.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core.aggregate import sample_score
from .pipeline import Evaluation, GradingError, evaluate
from .config import load_project_env
from .core.rubric import RubricError, load_file, validate
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


class SaveError(Exception):
    """写入数据库失败。与评分失败区分开：评分是对的，只是没存下来。"""


def _save(evaluation, rubric, answer: str, args, model: str) -> dict:
    """落库。表结构不在这里创建——建表只能走迁移，否则线上会悄悄少列。

    `model` 由调用方给：离线模式没有模型可记，就记空串，而不是编一个默认型号。
    """
    url = args.database_url or os.environ.get("DATABASE_URL", "")
    if not url:
        raise SaveError("未设置 DATABASE_URL，也没有给 --database-url")

    try:
        from .store import RunMeta, Store
    except ImportError as exc:
        raise SaveError(f"缺少数据库依赖（sqlalchemy / pymysql）：{exc}") from exc

    store = Store.from_url(url)
    try:
        stored = store.save_evaluation(
            rubric=rubric,
            answer_text=answer,
            evaluation=evaluation,
            meta=RunMeta(model=model),
            student_pseudo_id=args.pseudo_id,
        )
    except Exception as exc:  # noqa: BLE001 - 数据库错误一律转成人能读的一句话
        raise SaveError(str(exc)) from exc
    finally:
        store.dispose()

    return {
        "answer_id": stored.answer_id,
        "run_ids": stored.run_ids,
        "aggregate_id": stored.aggregate_id,
        "rubric_id": stored.rubric_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deepjudge", description="单份答卷评分")
    parser.add_argument("--rubric", required=True, help="评分标准 JSON")
    parser.add_argument("--answer", required=True, help="学生答卷（纯文本）")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--response", help="离线：预置的模型输出（JSON 文本）")
    source.add_argument("--live", action="store_true", help="在线：调用 DeepSeek API")
    parser.add_argument("--samples", type=int, default=1, help="采样次数（默认 1）")
    parser.add_argument(
        "--save", action="store_true", help="把本次评分写入数据库（需 DATABASE_URL 或 --database-url）"
    )
    parser.add_argument("--database-url", help="覆盖环境变量 DATABASE_URL")
    # 默认值与 store.DEFAULT_PSEUDO_ID 一致，但不能在模块顶层 import 它：
    # CLI 必须在没装 sqlalchemy 的环境里也能跑（离线评分不依赖数据库）。
    parser.add_argument("--pseudo-id", default="anon", help="学生脱敏编号（默认 anon）")
    args = parser.parse_args(argv)
    if args.samples < 1:
        parser.error("--samples 必须大于等于 1")

    load_project_env()

    try:
        rubric = load_file(args.rubric)
    except RubricError as exc:
        blocking = exc.blocking or exc.issues
        print("评分标准不合法：", file=sys.stderr)
        for issue in blocking:
            print(f"- [{issue.code}] {issue.message}", file=sys.stderr)
        return 2

    # 不拦保存、但会影响评分可靠性：照常评，只把话说到前面。
    for issue in validate(rubric):
        print(f"提示 [{issue.code}]：{issue.message}", file=sys.stderr)

    answer = Path(args.answer).read_text(encoding="utf-8")

    model = ""
    if args.live:
        try:
            config = DeepSeekConfig.from_env()
        except ProviderError as exc:
            print(f"无法调用模型：{exc}", file=sys.stderr)
            return 4
        provider = DeepSeekProvider(config)
        model = config.model
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

    report = build_report(evaluation, rubric)

    if args.save:
        try:
            report["stored"] = _save(evaluation, rubric, answer, args, model)
        except SaveError as exc:
            print(f"写入数据库失败：{exc}", file=sys.stderr)
            return 5

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

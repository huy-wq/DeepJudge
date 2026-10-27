"""把一段教师口述的评分标准整理成结构化标准（三视图输出）。

    # 离线：用预置的模型输出，不花钱不联网
    python3 scripts/gen_rubric.py --stem "简述 TCP 三次握手" --total 10 \
        --standard "三个要点……" --response response.json

    # 在线：真调模型（需要 .env 里的 DEEPSEEK_API_KEY）
    python3 scripts/gen_rubric.py --stem "……" --total 10 --standard-file words.txt --live

    # 顺手把结果存成标准文件（可直接喂给 app.cli 评分）
    python3 scripts/gen_rubric.py ... --save-rubric out/rubric.json

三个视图都会打印出来：摘要（对不对）、预览表（全不全）、校验清单（合不合法）。
阻断保存的问题会让它退出码非 0——**不落半成品**。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_project_env  # noqa: E402
from app.core.rubric import to_dict  # noqa: E402
from app.providers.base import ProviderError  # noqa: E402
from app.providers.deepseek import DeepSeekConfig, DeepSeekProvider  # noqa: E402
from app.providers.fake import CannedProvider  # noqa: E402
from app.rubric_gen import (  # noqa: E402
    RubricGenerationError,
    generate_rubric,
    preview_rows,
)

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_GENERATION = 3
EXIT_PROVIDER = 4


def print_views(result) -> None:
    print("=" * 68)
    print("【摘要】这份标准在考什么")
    print("=" * 68)
    print(result.summary)
    print()

    print("=" * 68)
    print("【预览表】有没有漏、有没有重复")
    print("=" * 68)
    for row in preview_rows(result.rubric):
        print(f"{row['id']}  权重 {row['weight']:g}  {row['desc']}")
        for level in row["levels"]:
            print(f"    等级 {level['level']:g} → {level['score']:g} 分：{level['criterion']}")
    for deduction in result.rubric.deductions:
        print(f"{deduction.id}  扣分 {deduction.effective():g}  {deduction.desc}")
    print()

    print("=" * 68)
    print("【校验清单】合不合法")
    print("=" * 68)
    if not result.issues:
        print("没有发现问题。")
    for issue in result.issues:
        kind = "阻断" if issue.blocking else "提示"
        print(f"[{kind}] {issue.code}: {issue.message}")
    print()
    print(f"（共尝试 {result.attempts} 次）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自然语言评分标准 → 结构化标准")
    parser.add_argument("--stem", required=True, help="题干")
    parser.add_argument("--total", type=float, required=True, help="满分")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--standard", help="教师原话（直接写在命令行里）")
    source.add_argument("--standard-file", help="教师原话（从文件读，适合长文本）")
    provider_group = parser.add_mutually_exclusive_group(required=True)
    provider_group.add_argument("--response", help="离线：预置的模型输出 JSON")
    provider_group.add_argument("--live", action="store_true", help="在线：调用 DeepSeek API")
    parser.add_argument("--reference", default="", help="参考答案（可选，能显著改善等级划分）")
    parser.add_argument("--question-id", default="Q-01", help="题目编号（默认 Q-01）")
    parser.add_argument("--save-rubric", help="把生成结果存成标准文件")
    args = parser.parse_args(argv)

    if args.total <= 0:
        parser.error("--total 必须大于 0")

    load_project_env()

    if args.standard_file:
        try:
            standard_text = Path(args.standard_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"读不到标准原文：{exc}", file=sys.stderr)
            return EXIT_INPUT
    else:
        standard_text = args.standard

    if args.live:
        try:
            # 生成用强档模型：与评分分开口子，否则教师改一次标准就要用评分那档的脑子
            config = DeepSeekConfig.from_env(model_env="DEEPSEEK_GEN_MODEL")
        except ProviderError as exc:
            print(f"无法调用模型：{exc}", file=sys.stderr)
            return EXIT_PROVIDER
        print(f"使用模型：{config.model}", file=sys.stderr)
        provider = DeepSeekProvider(config)
    else:
        try:
            provider = CannedProvider([Path(args.response).read_text(encoding="utf-8")])
        except OSError as exc:
            print(f"读不到预置响应：{exc}", file=sys.stderr)
            return EXIT_INPUT
        # 同一份预置输出重复喂给重试，必然得到同样的结论。提前说清楚，免得把
        # "重试也没用"误读成"重试机制坏了"。
        print(
            "提示：离线模式用同一份预置输出，重试必然得到相同结果；要验证重试请用 --live",
            file=sys.stderr,
        )

    try:
        result = generate_rubric(
            provider,
            stem=args.stem,
            total_score=args.total,
            standard_text=standard_text,
            reference_answer=args.reference,
            question_id=args.question_id,
        )
    except ValueError as exc:
        print(f"输入不完整：{exc}", file=sys.stderr)
        return EXIT_INPUT
    except RubricGenerationError as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        if exc.responses:
            print("\n最后一次模型输出（供排查）：\n" + exc.responses[-1], file=sys.stderr)
        return EXIT_GENERATION
    except ProviderError as exc:
        hint = "（可重试）" if exc.retryable else "（不可重试）"
        print(f"模型调用失败{hint}：{exc}", file=sys.stderr)
        return EXIT_PROVIDER

    print_views(result)

    if args.save_rubric:
        payload = to_dict(result.rubric)
        target = Path(args.save_rubric)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已保存：{target}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

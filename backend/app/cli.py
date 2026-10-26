"""命令行入口：用一份标准、一份答卷、一段模型输出跑完整评分。

示例：
    python -m app.cli --rubric rubric.json --answer answer.txt --response model.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import GradingError, grade
from .core.rubric import RubricError, load_file
from .providers.fake import CannedProvider


def build_report(result) -> dict:
    return {
        "question_id": result.question_id,
        "score": result.score,
        "max_score": result.max_score,
        "items": [
            {
                "id": ij.item_id,
                "level": ij.level,
                "hit": ij.hit,
                "evidence": ij.evidence,
                "reason": ij.reason,
            }
            for ij in result.items
        ],
        "deductions": [
            {"id": dj.id, "hit": dj.hit, "evidence": dj.evidence}
            for dj in result.deductions
        ],
        "comment": result.comment,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deepjudge", description="单份答卷评分")
    parser.add_argument("--rubric", required=True, help="评分标准 JSON")
    parser.add_argument("--answer", required=True, help="学生答卷（纯文本）")
    parser.add_argument("--response", required=True, help="模型输出（JSON 文本，离线运行）")
    args = parser.parse_args(argv)

    try:
        rubric = load_file(args.rubric)
    except RubricError as exc:
        for problem in exc.problems:
            print(f"- {problem}", file=sys.stderr)
        return 2

    answer = Path(args.answer).read_text(encoding="utf-8")
    response = Path(args.response).read_text(encoding="utf-8")

    try:
        result = grade(rubric, answer, CannedProvider([response]))
    except GradingError as exc:
        print(f"评分失败：{exc}", file=sys.stderr)
        return 3

    print(json.dumps(build_report(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

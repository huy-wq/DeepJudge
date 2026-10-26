"""方差探针：同一输入多次调用，看判定是否漂移。

用途见 docs/experiments.md（E1）。需要 .env 里配置 DEEPSEEK_API_KEY。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_project_env  # noqa: E402
from app.core.prompt import build_messages  # noqa: E402
from app.core.rubric import load_file  # noqa: E402
from app.pipeline import grade  # noqa: E402
from app.providers.deepseek import DeepSeekConfig, DeepSeekProvider  # noqa: E402
from app.providers.fake import CannedProvider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="测量同一输入的判定漂移")
    parser.add_argument("--rubric", required=True)
    parser.add_argument("--answer", required=True)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()

    load_project_env()
    rubric = load_file(args.rubric)
    answer = Path(args.answer).read_text(encoding="utf-8")
    provider = DeepSeekProvider(DeepSeekConfig.from_env())
    messages = build_messages(rubric, answer)

    results = []
    for index in range(args.runs):
        raw = provider.complete(messages, sample_index=index)
        result = grade(rubric, answer, CannedProvider([raw]))
        results.append(result)
        levels = " ".join(f"{k}={v}" for k, v in result.levels().items())
        print(f"#{index + 1} score={result.score:5.1f} {levels} ground={result.grounding_rate():.2f}")

    print()
    for item in rubric.items:
        seen = sorted({r.levels()[item.id] for r in results})
        flag = "  ← 有漂移" if len(seen) > 1 else ""
        print(f"{item.id} 出现过的等级：{seen}{flag}")
    scores = sorted({r.score for r in results})
    print("总分集合：", scores, "← 有漂移" if len(scores) > 1 else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""写入演示用的两份复核数据（离线、不调模型）。

用途见 `docs/demo.md`：正式演示走实时模型时，这两份数据保证"复核队列"永远有东西可看；
模型故障时，整个演示直接从这里开始。

    ./scripts/demo.sh seed
    # 或
    python3 scripts/seed_demo.py --database-url "sqlite:////tmp/demo.db"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import load_project_env  # noqa: E402
from app.core.rubric import RubricError, load_file  # noqa: E402
from app.pipeline import evaluate  # noqa: E402
from app.providers.fake import CannedProvider  # noqa: E402
from app.store import RunMeta, Store  # noqa: E402

SAMPLES = ROOT / "data" / "samples"
RUBRIC = SAMPLES / "tcp-handshake" / "rubric.json"
DEMO = SAMPLES / "demo"

# 两份预置判定的路由是刻意选出来的：
# - human：5 分，离及格线 6 分只有 1 分，触发"接近及格线"转人工；
# - auto：3.5 分，证据都在原文里，置信度 1，自动通过。
CASES = (
    {
        "pseudo_id": "S-DEMO-01",
        "answer": DEMO / "answer-human.txt",
        "response": DEMO / "response-human.json",
        "expected_route": "human",
    },
    {
        "pseudo_id": "S-DEMO-02",
        "answer": DEMO / "answer-auto.txt",
        "response": DEMO / "response-auto.json",
        "expected_route": "auto",
    },
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="写入离线演示数据")
    parser.add_argument("--database-url", help="覆盖 DATABASE_URL")
    args = parser.parse_args(argv)

    load_project_env()
    url = args.database_url or (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        print("未设置 DATABASE_URL；先跑 ./scripts/dev-db.sh up 或传 --database-url", file=sys.stderr)
        return 2

    try:
        rubric = load_file(RUBRIC)
    except RubricError as exc:
        print(f"评分标准不合法：{exc}", file=sys.stderr)
        return 2

    store = Store.from_url(url)
    try:
        if store.count_answers(rubric.question_id):
            print(
                f"题号 {rubric.question_id} 已经有答卷；演示库请先 ./scripts/demo.sh reset，"
                "不要重复写入。",
                file=sys.stderr,
            )
            return 2

        for item in CASES:
            answer = item["answer"].read_text(encoding="utf-8")
            response = item["response"].read_text(encoding="utf-8")
            evaluation = evaluate(
                rubric,
                answer,
                CannedProvider([response]),
                samples=1,
            )
            stored = store.save_evaluation(
                rubric=rubric,
                answer_text=answer,
                evaluation=evaluation,
                meta=RunMeta(model="offline-demo"),
                student_pseudo_id=item["pseudo_id"],
            )
            route = evaluation.route.route
            if route != item["expected_route"]:
                print(
                    f"警告：{item['pseudo_id']} 的路由是 {route}，"
                    f"与预期 {item['expected_route']} 不一致；演示前请检查数据。",
                    file=sys.stderr,
                )
            print(
                f"{item['pseudo_id']}  aggregate_id={stored.aggregate_id}  "
                f"score={evaluation.aggregate.score:g}  route={route}"
            )
    except Exception as exc:  # noqa: BLE001 - 数据库错误一律转成一句话
        print(f"写入演示数据失败：{exc}", file=sys.stderr)
        print("表结构可能还没建：先跑 ./scripts/dev-db.sh up", file=sys.stderr)
        return 5
    finally:
        store.dispose()

    print("\n演示数据已准备好：复核队列里有一份待改分（human）和一份待批量通过（auto）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

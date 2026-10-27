"""合成用例校验与对照实验。

    # 只校验用例，不调模型、不写库
    python3 scripts/experiment.py check --rubric r.json --cases cases.json
    python3 scripts/experiment.py check --rubric r.json --cases cases.json \
        --database-url "sqlite:////tmp/deepjudge.db"

    # 真调模型跑四组对照（需要 .env 里的 DEEPSEEK_API_KEY）
    python3 scripts/experiment.py run --rubric r.json --cases cases.json --live
    python3 scripts/experiment.py run ... --configs b0,b2,b3,b5 --samples 3

`check` 是纯校验：题号、采分点 id、期望等级与期望总分都在这一层拦住。
`run` 才调用模型；不加 `--live` 不会发请求。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_project_env  # noqa: E402
from app.core.rubric import RubricError, load_file  # noqa: E402
from app.experiment import (  # noqa: E402
    CONFIG_KEYS,
    CaseFileError,
    check_cases,
    load_case_set,
    run_experiment,
)
from app.providers.base import ProviderError  # noqa: E402
from app.providers.deepseek import DeepSeekConfig, DeepSeekProvider  # noqa: E402
from app.store import Store  # noqa: E402

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_PROVIDER = 4


def _print_issues(issues, *, stream=sys.stdout) -> None:
    for issue in issues:
        print(f"[阻断] {issue.code}: {issue.message}", file=stream)


def _question_exists(database_url: str | None, code: str) -> bool | None:
    if not database_url:
        return None
    store = Store.from_url(database_url)
    try:
        return store.get_question(code) is not None
    finally:
        store.dispose()


def _load(parser: argparse.ArgumentParser, args):
    try:
        rubric = load_file(args.rubric)
    except RubricError as exc:
        for issue in exc.issues:
            print(f"[阻断] {issue.code}: {issue.message}", file=sys.stderr)
        parser.exit(EXIT_INPUT)
    try:
        cases = load_case_set(args.cases)
    except CaseFileError as exc:
        print(f"[阻断] CASE_FILE: {exc.message}", file=sys.stderr)
        parser.exit(EXIT_INPUT)
    return rubric, cases


def command_check(args, parser: argparse.ArgumentParser) -> int:
    rubric, cases = _load(parser, args)
    try:
        exists = _question_exists(args.database_url, cases.question_code)
    except Exception as exc:  # noqa: BLE001 - 连不上库也是输入/环境问题
        print(f"[阻断] DATABASE: 无法检查题号：{exc}", file=sys.stderr)
        return EXIT_INPUT

    issues = check_cases(cases, rubric, question_exists=exists)
    print(f"用例集：{cases.question_code}（{len(cases.cases)} 条）")
    if issues:
        _print_issues(issues, stream=sys.stderr)
        return EXIT_INPUT
    print("检查通过：题号、采分点 id、期望等级与期望总分都合法；没有调用模型。")
    return EXIT_OK


def _format(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def command_run(args, parser: argparse.ArgumentParser) -> int:
    if not args.live:
        print(
            "run 会真实调用模型并产生费用；确认后加 --live。"
            "只校验用例请用 check。",
            file=sys.stderr,
        )
        return EXIT_INPUT

    rubric, cases = _load(parser, args)
    issues = check_cases(cases, rubric)
    if issues:
        _print_issues(issues, stream=sys.stderr)
        return EXIT_INPUT

    keys = [key.strip() for key in args.configs.split(",") if key.strip()]
    unknown = [key for key in keys if key not in CONFIG_KEYS]
    if unknown or not keys:
        print(f"--configs 里有未知配置：{', '.join(unknown) or '（空）'}", file=sys.stderr)
        return EXIT_INPUT
    if args.samples < 1:
        print("--samples 必须大于等于 1", file=sys.stderr)
        return EXIT_INPUT

    load_project_env()
    try:
        config = DeepSeekConfig.from_env()
    except ProviderError as exc:
        print(f"无法调用模型：{exc}", file=sys.stderr)
        return EXIT_PROVIDER
    print(f"使用模型：{config.model}；B5 每份采样 {args.samples} 次", file=sys.stderr)
    results = run_experiment(
        rubric,
        cases,
        keys,
        lambda: DeepSeekProvider(config),
        samples=args.samples,
    )

    if args.json:
        print(
            json.dumps(
                [result.as_dict() for result in results],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("配置  用例  通过  通过率   MAE    QWK    接地率  自动通过率")
        for result in results:
            metrics = result.metrics
            print(
                f"{result.config.key:<5} {metrics.cases:<5} {metrics.passed:<5} "
                f"{_format(metrics.pass_rate * 100, 1):>6}%  "
                f"{_format(metrics.total_mae):>6}  "
                f"{_format(metrics.qwk):>6}  "
                f"{_format(metrics.grounding_rate):>6}  "
                f"{_format(metrics.auto_pass_rate):>8}"
            )
        failures = [line for result in results for line in result.failures]
        if failures:
            print("\n失败明细：")
            for line in failures:
                print(f"- {line}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="合成用例校验与对照实验")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="只校验用例，不调用模型")
    check.add_argument("--rubric", required=True)
    check.add_argument("--cases", required=True)
    check.add_argument("--database-url", help="可选：连库确认题号存在")

    run = sub.add_parser("run", help="真调模型跑对照实验")
    run.add_argument("--rubric", required=True)
    run.add_argument("--cases", required=True)
    run.add_argument("--configs", default="b0,b2,b3,b5")
    run.add_argument("--samples", type=int, default=3)
    run.add_argument("--live", action="store_true", help="确认调用模型")
    run.add_argument("--json", action="store_true", help="输出原始 JSON")

    args = parser.parse_args(argv)
    if args.command == "check":
        return command_check(args, parser)
    return command_run(args, parser)


if __name__ == "__main__":
    raise SystemExit(main())

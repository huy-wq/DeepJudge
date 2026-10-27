"""合成用例与对照实验：校验用例、跑四组配置、算指标。

这一层不碰 HTTP、不碰数据库：命令行与测试都从同一组函数进。
只有 `run_*` 会调用模型，`check_cases` 与指标计算是纯函数。
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .core.json_extract import PayloadError, extract_payload
from .core.rubric import to_dict
from .core.types import EPS, Rubric
from .pipeline import GradingError, evaluate
from .providers.base import GradingProvider, ProviderError

CONFIG_KEYS = ("b0", "b2", "b3", "b5")
TOTAL_TOLERANCE = 0.01


class CaseFileError(Exception):
    """用例文件读不了或结构不对。"""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ExperimentProblem(Exception):
    """实验过程中的可预期问题（例如 B0 的输出没有合法总分）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True)
class Case:
    case_id: str
    answer: str
    expected_total: float
    expected_items: dict[str, float]
    correct: bool = False


@dataclass(frozen=True)
class CaseSet:
    question_code: str
    cases: list[Case]


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    case_id: str | None = None


@dataclass(frozen=True)
class Config:
    key: str
    label: str
    samples: int
    grounding: bool
    direct: bool
    routing: bool


def config_for(key: str, *, samples: int = 3) -> Config:
    """四组对照配置。B0 直接要总分；B2 逐点不校验；B3 加接地；B5 再加采样与路由。"""
    if key not in CONFIG_KEYS:
        raise ValueError(f"未知配置：{key}（可选 {', '.join(CONFIG_KEYS)}）")
    if key == "b0":
        return Config("b0", "直接要总分", 1, grounding=False, direct=True, routing=False)
    if key == "b2":
        return Config("b2", "逐点判定", 1, grounding=False, direct=False, routing=False)
    if key == "b3":
        return Config("b3", "逐点 + 接地", 1, grounding=True, direct=False, routing=False)
    return Config(
        "b5",
        "完整链路",
        max(1, samples),
        grounding=True,
        direct=False,
        routing=True,
    )


@dataclass
class Outcome:
    case_id: str
    expected_total: float
    predicted_total: float | None
    expected_items: dict[str, float]
    predicted_items: dict[str, float] | None
    passed: bool
    error: str | None
    route: str | None
    grounding_rate: float | None
    item_mismatches: list[tuple[str, float, float]]


@dataclass(frozen=True)
class Metrics:
    cases: int
    passed: int
    pass_rate: float
    total_mae: float | None
    qwk: float | None
    item_accuracy: float | None
    grounding_rate: float | None
    auto_pass_rate: float | None


@dataclass
class ConfigResult:
    config: Config
    metrics: Metrics
    outcomes: list[Outcome]
    failures: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "config": asdict(self.config),
            "metrics": asdict(self.metrics),
            "failures": self.failures,
            "outcomes": [asdict(outcome) for outcome in self.outcomes],
        }


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_case_set(data: Any) -> CaseSet:
    if not isinstance(data, dict):
        raise CaseFileError("用例文件必须是 JSON 对象")
    question = data.get("question_code")
    if not isinstance(question, str) or not question.strip():
        raise CaseFileError("缺少 question_code")
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise CaseFileError("cases 必须是非空数组")

    cases: list[Case] = []
    for index, raw in enumerate(raw_cases, start=1):
        if not isinstance(raw, dict):
            raise CaseFileError(f"第 {index} 条用例不是对象")
        case_id = raw.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise CaseFileError(f"第 {index} 条用例缺少 id")
        answer = raw.get("answer")
        if not isinstance(answer, str):
            raise CaseFileError(f"用例 {case_id} 缺少 answer")
        expected = raw.get("expected")
        if not isinstance(expected, dict):
            raise CaseFileError(f"用例 {case_id} 缺少 expected")
        total = expected.get("total")
        if not _number(total):
            raise CaseFileError(f"用例 {case_id} 的 expected.total 必须是数字")
        items = expected.get("items")
        if not isinstance(items, dict):
            raise CaseFileError(f"用例 {case_id} 的 expected.items 必须是对象")
        levels: dict[str, float] = {}
        for item_id, level in items.items():
            if not isinstance(item_id, str) or not item_id.strip():
                raise CaseFileError(f"用例 {case_id} 的采分点 id 必须是非空字符串")
            if not _number(level):
                raise CaseFileError(f"用例 {case_id} 的 {item_id} 期望等级必须是数字")
            levels[item_id] = float(level)
        correct = raw.get("correct", False)
        if not isinstance(correct, bool):
            raise CaseFileError(f"用例 {case_id} 的 correct 必须是布尔值")
        cases.append(
            Case(
                case_id=case_id.strip(),
                answer=answer,
                expected_total=float(total),
                expected_items=levels,
                correct=correct,
            )
        )
    return CaseSet(question_code=question.strip(), cases=cases)


def load_case_set(path: str | Path) -> CaseSet:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CaseFileError(f"读不到用例文件：{exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CaseFileError(f"用例文件不是合法 JSON：{exc}") from exc
    return parse_case_set(data)


def check_cases(
    cases: CaseSet,
    rubric: Rubric,
    *,
    question_exists: bool | None = None,
) -> list[Issue]:
    """纯校验：只看用例与标准是否自洽，不调用模型。"""
    issues: list[Issue] = []
    if question_exists is False:
        issues.append(
            Issue("QUESTION_NOT_FOUND", f"题号 {cases.question_code} 在数据库里不存在")
        )
    if cases.question_code != rubric.question_id:
        issues.append(
            Issue(
                "QUESTION_MISMATCH",
                f"用例题号 {cases.question_code} 与标准题号 {rubric.question_id} 不一致",
            )
        )

    items = {item.id: item for item in rubric.items}
    seen: set[str] = set()
    has_correct = False

    for case in cases.cases:
        if not case.case_id:
            issues.append(Issue("EMPTY_CASE_ID", "用例 id 不能为空"))
        if case.case_id in seen:
            issues.append(Issue("DUPLICATE_CASE_ID", f"用例 id 重复：{case.case_id}", case.case_id))
        seen.add(case.case_id)

        if not case.answer.strip():
            issues.append(Issue("EMPTY_ANSWER", f"用例 {case.case_id} 的答案为空", case.case_id))

        if not (0 <= case.expected_total <= rubric.total_score):
            issues.append(
                Issue(
                    "TOTAL_OUT_OF_RANGE",
                    f"用例 {case.case_id} 的期望总分 {case.expected_total:g} 不在 0–{rubric.total_score:g}",
                    case.case_id,
                )
            )

        for item_id, level in case.expected_items.items():
            item = items.get(item_id)
            if item is None:
                issues.append(
                    Issue(
                        "UNKNOWN_ITEM",
                        f"用例 {case.case_id} 出现标准里没有的采分点 {item_id}",
                        case.case_id,
                    )
                )
                continue
            if not any(abs(level - allowed) < EPS for allowed in item.allowed_levels):
                issues.append(
                    Issue(
                        "LEVEL_NOT_ALLOWED",
                        f"用例 {case.case_id} 的 {item_id} 期望等级 {level:g} 不在允许值 "
                        f"{list(item.allowed_levels)} 中",
                        case.case_id,
                    )
                )

        missing = [item_id for item_id in items if item_id not in case.expected_items]
        if missing:
            issues.append(
                Issue(
                    "MISSING_ITEM",
                    f"用例 {case.case_id} 缺少采分点期望：" + "、".join(missing),
                    case.case_id,
                )
            )

        if case.correct:
            has_correct = True
            full = abs(case.expected_total - rubric.total_score) < EPS
            if full:
                for item_id, item in items.items():
                    expected = case.expected_items.get(item_id)
                    if expected is None or abs(expected - max(item.allowed_levels)) >= EPS:
                        full = False
                        break
            if not full:
                issues.append(
                    Issue(
                        "CORRECT_CASE_NOT_FULL",
                        f"用例 {case.case_id} 标了 correct，但总分或各点等级不是满分",
                        case.case_id,
                    )
                )

    if not has_correct:
        issues.append(
            Issue("NO_CORRECT_CASE", "用例集至少要有一条 correct=true 的正确答案用例")
        )
    return issues


DIRECT_SYSTEM = """你是评分老师。根据题干、参考答案和结构化评分标准，直接给出这份答卷的总分。
只输出 JSON，形如 {"total": 7.5}；total 必须在 0 到满分之间。
不要逐条解释，不要输出其他字段。"""


def build_total_messages(rubric: Rubric, answer: str) -> list[dict[str, str]]:
    rubric_text = json.dumps(to_dict(rubric), ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": DIRECT_SYSTEM},
        {
            "role": "user",
            "content": f"<rubric>\n{rubric_text}\n</rubric>\n<answer>\n{answer}\n</answer>",
        },
    ]


def grade_total(rubric: Rubric, answer: str, provider: GradingProvider) -> float:
    """B0：只要总分。分数仍做范围校验与截断，不采信越界值。"""
    try:
        payload = extract_payload(
            provider.complete(build_total_messages(rubric, answer), sample_index=0)
        )
    except PayloadError as exc:
        raise ExperimentProblem(exc.code, exc.message) from exc

    total = payload.get("total")
    if not _number(total) or not math.isfinite(float(total)):
        raise ExperimentProblem("INVALID_TOTAL", f"模型没有给出合法总分：{total!r}")
    value = float(total)
    return round(max(0.0, min(rubric.total_score, value)), 2)


def _mismatches(
    rubric: Rubric,
    case: Case,
    predicted_items: dict[str, float] | None,
) -> list[tuple[str, float, float]]:
    if predicted_items is None:
        return []
    mismatches = []
    for item in rubric.items:
        expected = case.expected_items.get(item.id)
        if expected is None:
            continue
        actual = predicted_items.get(item.id)
        if actual is None or abs(actual - expected) >= EPS:
            mismatches.append((item.id, expected, actual if actual is not None else float("nan")))
    return mismatches


def run_config(
    rubric: Rubric,
    cases: CaseSet,
    config: Config,
    provider_factory: Callable[[], GradingProvider],
) -> ConfigResult:
    outcomes: list[Outcome] = []

    for case in cases.cases:
        try:
            if config.direct:
                predicted_total = grade_total(rubric, case.answer, provider_factory())
                predicted_items = None
                route = None
                grounding_rate = None
            else:
                evaluation = evaluate(
                    rubric,
                    case.answer,
                    provider_factory(),
                    samples=config.samples,
                    check_grounding=config.grounding,
                )
                predicted_total = evaluation.aggregate.score
                predicted_items = dict(evaluation.aggregate.levels)
                route = evaluation.route.route if config.routing else None
                grounding_rate = (
                    evaluation.aggregate.grounding_rate if config.grounding else None
                )
        except (GradingError, ProviderError, ExperimentProblem, ValueError) as exc:
            outcomes.append(
                Outcome(
                    case_id=case.case_id,
                    expected_total=case.expected_total,
                    predicted_total=None,
                    expected_items=dict(case.expected_items),
                    predicted_items=None,
                    passed=False,
                    error=f"{type(exc).__name__}: {exc}",
                    route=None,
                    grounding_rate=None,
                    item_mismatches=[],
                )
            )
            continue

        mismatches = _mismatches(rubric, case, predicted_items)
        passed = (
            abs(predicted_total - case.expected_total) <= TOTAL_TOLERANCE and not mismatches
        )
        outcomes.append(
            Outcome(
                case_id=case.case_id,
                expected_total=case.expected_total,
                predicted_total=predicted_total,
                expected_items=dict(case.expected_items),
                predicted_items=predicted_items,
                passed=passed,
                error=None,
                route=route,
                grounding_rate=grounding_rate,
                item_mismatches=mismatches,
            )
        )

    metrics = _metrics(config, outcomes, rubric)
    failures = _failures(config, outcomes)
    return ConfigResult(config=config, metrics=metrics, outcomes=outcomes, failures=failures)


def _metrics(config: Config, outcomes: list[Outcome], rubric: Rubric) -> Metrics:
    total_cases = len(outcomes)
    passed = sum(1 for outcome in outcomes if outcome.passed)
    pairs = [
        (outcome.expected_total, outcome.predicted_total)
        for outcome in outcomes
        if outcome.error is None and outcome.predicted_total is not None
    ]
    total_mae = (
        round(sum(abs(expected - predicted) for expected, predicted in pairs) / len(pairs), 4)
        if pairs
        else None
    )

    item_accuracy = None
    if not config.direct:
        checks = 0
        matched = 0
        for outcome in outcomes:
            if outcome.error is not None or outcome.predicted_items is None:
                continue
            for item in rubric.items:
                expected = outcome.expected_items.get(item.id)
                if expected is None:
                    continue
                checks += 1
                actual = outcome.predicted_items.get(item.id)
                if actual is not None and abs(actual - expected) < EPS:
                    matched += 1
        item_accuracy = round(matched / checks, 4) if checks else None

    grounding_values = [
        outcome.grounding_rate
        for outcome in outcomes
        if outcome.grounding_rate is not None
    ]
    grounding_rate = (
        round(sum(grounding_values) / len(grounding_values), 4) if grounding_values else None
    )

    routed = [outcome for outcome in outcomes if outcome.route is not None]
    auto_pass_rate = (
        round(sum(1 for outcome in routed if outcome.route == "auto") / len(routed), 4)
        if routed
        else None
    )

    return Metrics(
        cases=total_cases,
        passed=passed,
        pass_rate=round(passed / total_cases, 4) if total_cases else 0.0,
        total_mae=total_mae,
        qwk=quadratic_weighted_kappa(
            [expected for expected, _ in pairs],
            [predicted for _, predicted in pairs],
        ),
        item_accuracy=item_accuracy,
        grounding_rate=grounding_rate,
        auto_pass_rate=auto_pass_rate,
    )


def _failures(config: Config, outcomes: list[Outcome]) -> list[str]:
    failures = []
    for outcome in outcomes:
        if outcome.passed:
            continue
        if outcome.error is not None:
            failures.append(f"{config.key}/{outcome.case_id}: {outcome.error}")
            continue
        parts = [
            f"总分 {outcome.predicted_total:g} ≠ 期望 {outcome.expected_total:g}"
            if outcome.predicted_total is not None
            else "没有预测总分"
        ]
        for item_id, expected, actual in outcome.item_mismatches:
            parts.append(f"{item_id} {actual:g} ≠ 期望 {expected:g}")
        failures.append(f"{config.key}/{outcome.case_id}: " + "；".join(parts))
    return failures


def quadratic_weighted_kappa(
    expected: Iterable[float],
    predicted: Iterable[float],
    *,
    step: float = 0.5,
) -> float | None:
    """按 0.5 分档的二次加权 kappa；样本不足两个时无定义。"""
    expected_list = [round(float(value) / step) * step for value in expected]
    predicted_list = [round(float(value) / step) * step for value in predicted]
    if len(expected_list) != len(predicted_list) or len(expected_list) < 2:
        return None

    labels = sorted(set(expected_list) | set(predicted_list))
    if len(labels) < 2:
        return None
    index = {label: position for position, label in enumerate(labels)}
    size = len(labels)

    observed = [[0.0] * size for _ in range(size)]
    for exp, pred in zip(expected_list, predicted_list):
        observed[index[exp]][index[pred]] += 1

    count = len(expected_list)
    hist_expected = [0.0] * size
    hist_predicted = [0.0] * size
    for value in expected_list:
        hist_expected[index[value]] += 1
    for value in predicted_list:
        hist_predicted[index[value]] += 1

    weights = [
        [(i - j) ** 2 / ((size - 1) ** 2) for j in range(size)]
        for i in range(size)
    ]
    numerator = sum(
        weights[i][j] * observed[i][j] for i in range(size) for j in range(size)
    )
    denominator = sum(
        weights[i][j] * hist_expected[i] * hist_predicted[j] / count
        for i in range(size)
        for j in range(size)
    )
    if denominator == 0:
        return None
    return round(1 - numerator / denominator, 4)


def run_experiment(
    rubric: Rubric,
    cases: CaseSet,
    keys: Iterable[str],
    provider_factory: Callable[[], GradingProvider],
    *,
    samples: int = 3,
) -> list[ConfigResult]:
    return [
        run_config(rubric, cases, config_for(key, samples=samples), provider_factory)
        for key in keys
    ]

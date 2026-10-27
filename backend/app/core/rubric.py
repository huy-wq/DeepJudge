"""评分标准的解析与校验。

服务端的硬约束集中在这里：权重和、id 唯一、等级单调、分值域。
解析是"收集式"的：缺字段、类型错、语义问题会一起报出来，而不是遇到第一个就抛。

校验结果按架构 §5.3 分成两类：

- **阻断保存**（`blocking=True`）：标准本身不成立，存进去也算不出分；
- **仅提示**（`blocking=False`）：标准能算分，但会让评分不可靠，交给人判断。

`from_dict` 只在存在阻断项时抛错——告警不该拦住"先把标准存下来"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import EPS, Deduction, Level, Rubric, RubricItem


@dataclass(frozen=True)
class RubricIssue:
    """一条校验结果。

    `code` 是稳定的机器可读标识（测试与前端按它分支），`message` 是给教师看的一句话，
    `blocking` 决定它拦不拦保存。两者必须一起给：只给句子的话，调用方只能靠匹配中文来分类。
    """

    code: str
    message: str
    blocking: bool = True

    def __str__(self) -> str:
        return self.message


def _error(code: str, message: str) -> RubricIssue:
    return RubricIssue(code=code, message=message, blocking=True)


def _warning(code: str, message: str) -> RubricIssue:
    return RubricIssue(code=code, message=message, blocking=False)


# 「听起来像判定依据，其实没法验证」的措辞。只做提示，不拦保存——教师有权说"就按这个判"。
VAGUE_PHRASES = (
    "论述充分",
    "理解到位",
    "言之有理",
    "表述清楚",
    "回答完整",
    "基本正确",
)


class RubricError(ValueError):
    """评分标准不合法；`issues` 里是全部问题，不是第一条。"""

    def __init__(self, issues: list[RubricIssue]) -> None:
        self.issues = list(issues)
        super().__init__(
            "评分标准不合法：\n- " + "\n- ".join(issue.message for issue in self.issues)
        )

    @property
    def problems(self) -> list[str]:
        """兼容旧接口：只要句子。新代码请用 `issues`，按 `code` 分类。"""
        return [issue.message for issue in self.issues]

    @property
    def blocking(self) -> list[RubricIssue]:
        return [issue for issue in self.issues if issue.blocking]

    @property
    def warnings(self) -> list[RubricIssue]:
        return [issue for issue in self.issues if not issue.blocking]


def _parse_item(raw: Any, position: int) -> RubricItem:
    if not isinstance(raw, dict):
        raise TypeError("采分点必须是 JSON 对象")
    item_id = str(raw["id"])
    levels = tuple(
        Level(
            level=float(lv["level"]),
            score=float(lv["score"]),
            criterion=str(lv.get("criterion", "")),
        )
        for lv in raw["levels"]
    )
    return RubricItem(
        id=item_id,
        desc=str(raw.get("desc", "")),
        weight=float(raw["weight"]),
        levels=levels,
        must_hit=bool(raw.get("must_hit", False)),
        acceptable=tuple(str(a) for a in raw.get("acceptable", [])),
    )


def _parse_deduction(raw: Any) -> Deduction:
    if not isinstance(raw, dict):
        raise TypeError("扣分项必须是 JSON 对象")
    return Deduction(
        id=str(raw["id"]),
        desc=str(raw.get("desc", "")),
        penalty=float(raw["penalty"]),
        cap=float(raw.get("cap", raw["penalty"])),
    )


@dataclass(frozen=True)
class ParsedRubric:
    """解析结果：标准本身 + 它的全部问题（阻断项与仅提示项都在里面）。

    存在的理由：`from_dict` 只关心"能不能用"，而确认界面要"用得上 + 值不值得"。
    如果让调用方先 `from_dict` 再 `validate`，同一份清单就有了两条产出路径、
    每次要算两遍。这里一次算清，两边都从这里取。
    """

    rubric: Rubric
    issues: list[RubricIssue]

    @property
    def blocking(self) -> list[RubricIssue]:
        return [issue for issue in self.issues if issue.blocking]

    @property
    def warnings(self) -> list[RubricIssue]:
        return [issue for issue in self.issues if not issue.blocking]

    @property
    def usable(self) -> bool:
        return not self.blocking


def from_dict(data: dict[str, Any]) -> Rubric:
    """解析并返回标准；存在阻断项就抛。需要连仅提示项一起拿时改用 `parse`。"""
    parsed = parse(data)
    if not parsed.usable:
        raise RubricError(parsed.issues)
    return parsed.rubric


def parse(data: dict[str, Any]) -> ParsedRubric:
    """解析一次，返回标准与**全部**问题；任何情况下都不抛。"""
    if not isinstance(data, dict):
        return ParsedRubric(
            rubric=Rubric(question_id="", stem="", reference_answer="", total_score=0.0, items=[]),
            issues=[_error("NOT_OBJECT", "评分标准必须是 JSON 对象")],
        )

    issues: list[RubricIssue] = []

    def required(key: str, caster, default):
        if key not in data:
            issues.append(_error("MISSING_FIELD", f"缺少必填字段：{key}"))
            return default
        try:
            return caster(data[key])
        except (TypeError, ValueError):
            issues.append(_error("BAD_TYPE", f"字段 {key} 的类型不正确"))
            return default

    question_id = required("question_id", str, "")
    total_score = required("total_score", float, 0.0)

    items: list[RubricItem] = []
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        issues.append(_error("BAD_ITEMS", "items 必须是非空数组"))
    else:
        for index, raw in enumerate(raw_items, start=1):
            try:
                items.append(_parse_item(raw, index))
            except KeyError as exc:
                label = raw.get("id") if isinstance(raw, dict) else f"第 {index} 个"
                issues.append(
                    _error("MISSING_FIELD", f"{label}: 缺少必填字段 {exc.args[0]}")
                )
            except (TypeError, ValueError) as exc:
                label = raw.get("id") if isinstance(raw, dict) else f"第 {index} 个"
                issues.append(_error("BAD_ITEM", f"{label}: 字段类型或取值错误（{exc}）"))

    deductions: list[Deduction] = []
    raw_deductions = data.get("deductions", [])
    if not isinstance(raw_deductions, list):
        issues.append(_error("BAD_DEDUCTIONS", "deductions 必须是数组"))
    else:
        for index, raw in enumerate(raw_deductions, start=1):
            try:
                deductions.append(_parse_deduction(raw))
            except KeyError as exc:
                issues.append(
                    _error(
                        "MISSING_FIELD",
                        f"扣分项第 {index} 个：缺少必填字段 {exc.args[0]}",
                    )
                )
            except (TypeError, ValueError) as exc:
                issues.append(
                    _error("BAD_DEDUCTION", f"扣分项第 {index} 个：字段类型或取值错误（{exc}）")
                )

    rubric = Rubric(
        question_id=question_id,
        stem=str(data.get("stem", "")),
        reference_answer=str(data.get("reference_answer", "")),
        total_score=total_score,
        items=items,
        deductions=deductions,
        rules=dict(data.get("rules", {})),
    )

    # 结构问题也照样带上"权重合计"那条：差额正是教师最需要看到的数字，
    # 结构错乱时更需要（少了几个采分点，差额会更大）。早期版本在这里过滤它，
    # 结果是模型漏字段时"还差 3 分"完全消失，只剩一句"缺少必填字段"。
    return ParsedRubric(rubric=rubric, issues=issues + validate(rubric))


def validate(rubric: Rubric) -> list[RubricIssue]:
    """返回全部问题（阻断项 + 仅提示项）；空列表表示完全没问题。"""
    issues: list[RubricIssue] = []

    if rubric.total_score <= 0:
        issues.append(_error("TOTAL_SCORE_NON_POSITIVE", "total_score 必须大于 0"))
    if not rubric.items:
        issues.append(_error("EMPTY_ITEMS", "items 不能为空"))

    ids = [it.id for it in rubric.items] + [d.id for d in rubric.deductions]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    if duplicated:
        issues.append(_error("DUPLICATE_ID", f"id 重复：{', '.join(duplicated)}"))

    total_weight = sum(it.weight for it in rubric.items)
    gap = rubric.total_score - total_weight
    if abs(total_weight - rubric.total_score) > EPS:
        # 先判方向再格式化。若先把差值 round 到 2 位，0.004 会变成 0，方向就丢了。
        if gap > 0:
            detail = f"还有 {gap:g} 分未分配（不会自动补点，请补充采分点或调整满分）"
        else:
            detail = f"超出 {-gap:g} 分（请下调权重或上调满分）"
        issues.append(
            _error(
                "WEIGHT_SUM",
                f"采分点权重合计 {total_weight:g} 与满分 {rubric.total_score:g} 不一致：{detail}",
            )
        )

    seen_descriptions: dict[str, str] = {}
    for it in rubric.items:
        if it.weight <= 0:
            issues.append(_error("WEIGHT_NON_POSITIVE", f"{it.id}: weight 必须大于 0"))
        if len(it.levels) < 2:
            issues.append(_error("TOO_FEW_LEVELS", f"{it.id}: 至少需要两个等级"))

        ordered = sorted(it.levels, key=lambda lv: lv.level)
        for lv in ordered:
            if lv.score < -EPS or lv.score > it.weight + EPS:
                issues.append(
                    _error(
                        "SCORE_OUT_OF_RANGE",
                        f"{it.id}: 等级 {lv.level:g} 的分值 {lv.score:g} 超出 [0, {it.weight:g}]",
                    )
                )
            if not lv.criterion.strip():
                issues.append(
                    _warning(
                        "EMPTY_CRITERION",
                        f"{it.id}: 等级 {lv.level:g} 没有判定依据（criterion 为空）",
                    )
                )
            vague = [phrase for phrase in VAGUE_PHRASES if phrase in lv.criterion]
            if vague:
                issues.append(
                    _warning(
                        "VAGUE_CRITERION",
                        f"{it.id}: 等级 {lv.level:g} 的判定依据包含不可验证的措辞「{vague[0]}」",
                    )
                )
        for prev, cur in zip(ordered, ordered[1:]):
            if cur.score < prev.score - EPS:
                issues.append(
                    _error(
                        "LEVEL_NOT_MONOTONIC",
                        f"{it.id}: 分值随等级下降（{prev.level:g} → {cur.level:g}）",
                    )
                )

        top_score = max(lv.score for lv in it.levels) if it.levels else 0.0
        if it.levels and top_score < it.weight - EPS:
            issues.append(
                _warning(
                    "TOP_LEVEL_BELOW_WEIGHT",
                    f"{it.id}: 最高档分值 {top_score:g} 低于权重 {it.weight:g}，该采分点满分不可达",
                )
            )

        desc = it.desc.strip()
        if desc:
            if desc in seen_descriptions:
                issues.append(
                    _warning(
                        "DUPLICATE_DESC",
                        f"{it.id} 与 {seen_descriptions[desc]} 的描述完全相同，可能是重复的采分点",
                    )
                )
            else:
                seen_descriptions[desc] = it.id

    for d in rubric.deductions:
        if d.penalty > 0:
            issues.append(_error("PENALTY_POSITIVE", f"{d.id}: penalty 必须 ≤ 0"))
        if d.cap > 0:
            issues.append(_error("CAP_POSITIVE", f"{d.id}: cap 必须 ≤ 0"))
        if d.penalty < d.cap - EPS:
            issues.append(
                _error(
                    "CAP_INEFFECTIVE",
                    f"{d.id}: penalty({d.penalty:g}) 低于 cap({d.cap:g})，封顶不起作用",
                )
            )

    return issues


def load_file(path: str | Path) -> Rubric:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RubricError([_error("FILE_UNREADABLE", f"无法读取评分标准文件：{exc}")]) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RubricError(
            [_error("INVALID_JSON", f"评分标准文件不是合法 JSON：{exc}")]
        ) from exc
    return from_dict(data)


def to_dict(rubric: Rubric) -> dict[str, Any]:
    """Rubric → 可 JSON 序列化的 dict，供入库快照与前端展示。

    与 from_dict 严格互逆：`from_dict(to_dict(r)) == r`，
    否则"标准快照"四字就是空话——存进去的读出来不是同一份标准。
    """
    return {
        "question_id": rubric.question_id,
        "stem": rubric.stem,
        "reference_answer": rubric.reference_answer,
        "total_score": float(rubric.total_score),
        "items": [
            {
                "id": item.id,
                "desc": item.desc,
                "weight": float(item.weight),
                "levels": [
                    {
                        "level": float(level.level),
                        "score": float(level.score),
                        "criterion": level.criterion,
                    }
                    for level in item.levels
                ],
                "must_hit": bool(item.must_hit),
                "acceptable": list(item.acceptable),
            }
            for item in rubric.items
        ],
        "deductions": [
            {
                "id": deduction.id,
                "desc": deduction.desc,
                "penalty": float(deduction.penalty),
                "cap": float(deduction.cap),
            }
            for deduction in rubric.deductions
        ],
        "rules": dict(rubric.rules),
    }

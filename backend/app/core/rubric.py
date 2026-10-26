"""评分标准的解析与校验。

服务端的硬约束集中在这里：权重和、id 唯一、等级单调、分值域。
解析是"收集式"的：缺字段、类型错、语义问题会一起报出来，而不是遇到第一个就抛。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import EPS, Deduction, Level, Rubric, RubricItem


class RubricError(ValueError):
    """评分标准不合法；problems 里是全部问题，不是第一条。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("评分标准不合法：\n- " + "\n- ".join(problems))


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


def from_dict(data: dict[str, Any]) -> Rubric:
    if not isinstance(data, dict):
        raise RubricError(["评分标准必须是 JSON 对象"])

    problems: list[str] = []

    def required(key: str, caster, default):
        if key not in data:
            problems.append(f"缺少必填字段：{key}")
            return default
        try:
            return caster(data[key])
        except (TypeError, ValueError):
            problems.append(f"字段 {key} 的类型不正确")
            return default

    question_id = required("question_id", str, "")
    total_score = required("total_score", float, 0.0)

    items: list[RubricItem] = []
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        problems.append("items 必须是非空数组")
    else:
        for index, raw in enumerate(raw_items, start=1):
            try:
                items.append(_parse_item(raw, index))
            except KeyError as exc:
                label = raw.get("id") if isinstance(raw, dict) else f"第 {index} 个"
                problems.append(f"{label}: 缺少必填字段 {exc.args[0]}")
            except (TypeError, ValueError) as exc:
                label = raw.get("id") if isinstance(raw, dict) else f"第 {index} 个"
                problems.append(f"{label}: 字段类型或取值错误（{exc}）")

    deductions: list[Deduction] = []
    raw_deductions = data.get("deductions", [])
    if not isinstance(raw_deductions, list):
        problems.append("deductions 必须是数组")
    else:
        for index, raw in enumerate(raw_deductions, start=1):
            try:
                deductions.append(_parse_deduction(raw))
            except KeyError as exc:
                problems.append(f"扣分项第 {index} 个：缺少必填字段 {exc.args[0]}")
            except (TypeError, ValueError) as exc:
                problems.append(f"扣分项第 {index} 个：字段类型或取值错误（{exc}）")

    rubric = Rubric(
        question_id=question_id,
        stem=str(data.get("stem", "")),
        reference_answer=str(data.get("reference_answer", "")),
        total_score=total_score,
        items=items,
        deductions=deductions,
        rules=dict(data.get("rules", {})),
    )

    semantic = validate(rubric)
    if problems:
        # 结构问题存在时，权重合计必然对不上，那条提示只会制造噪音。
        semantic = [p for p in semantic if not p.startswith("采分点权重合计")]

    all_problems = problems + semantic
    if all_problems:
        raise RubricError(all_problems)
    return rubric


def validate(rubric: Rubric) -> list[str]:
    """返回全部语义问题；空列表表示合法。"""
    problems: list[str] = []

    if rubric.total_score <= 0:
        problems.append("total_score 必须大于 0")
    if not rubric.items:
        problems.append("items 不能为空")

    ids = [it.id for it in rubric.items] + [d.id for d in rubric.deductions]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    if duplicated:
        problems.append(f"id 重复：{', '.join(duplicated)}")

    total_weight = sum(it.weight for it in rubric.items)
    if abs(total_weight - rubric.total_score) > EPS:
        problems.append(
            f"采分点权重合计 {total_weight:g} 与满分 {rubric.total_score:g} 不一致"
        )

    for it in rubric.items:
        if it.weight <= 0:
            problems.append(f"{it.id}: weight 必须大于 0")
        if len(it.levels) < 2:
            problems.append(f"{it.id}: 至少需要两个等级")

        ordered = sorted(it.levels, key=lambda lv: lv.level)
        for lv in ordered:
            if lv.score < -EPS or lv.score > it.weight + EPS:
                problems.append(
                    f"{it.id}: 等级 {lv.level:g} 的分值 {lv.score:g} 超出 [0, {it.weight:g}]"
                )
        for prev, cur in zip(ordered, ordered[1:]):
            if cur.score < prev.score - EPS:
                problems.append(
                    f"{it.id}: 分值随等级下降（{prev.level:g} → {cur.level:g}）"
                )

    for d in rubric.deductions:
        if d.penalty > 0:
            problems.append(f"{d.id}: penalty 必须 ≤ 0")
        if d.cap > 0:
            problems.append(f"{d.id}: cap 必须 ≤ 0")
        if d.penalty < d.cap - EPS:
            problems.append(
                f"{d.id}: penalty({d.penalty:g}) 低于 cap({d.cap:g})，封顶不起作用"
            )

    return problems


def load_file(path: str | Path) -> Rubric:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RubricError([f"无法读取评分标准文件：{exc}"]) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RubricError([f"评分标准文件不是合法 JSON：{exc}"]) from exc
    return from_dict(data)

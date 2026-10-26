"""领域对象：评分标准与判定结果。

纯标准库（dataclasses），不依赖 pydantic、数据库或任何模型 SDK，
保证核心逻辑可在无第三方包的环境下直接单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EPS = 1e-9


@dataclass(frozen=True)
class Level:
    """采分点的一个等级：level 是等级标识，score 是该等级对应的分值。"""

    level: float
    score: float
    criterion: str = ""


@dataclass
class RubricItem:
    id: str
    desc: str
    weight: float
    levels: tuple[Level, ...]
    must_hit: bool = False
    acceptable: tuple[str, ...] = ()

    @property
    def allowed_levels(self) -> tuple[float, ...]:
        return tuple(sorted({lv.level for lv in self.levels}))

    def score_for(self, level: float) -> float:
        for lv in self.levels:
            if abs(lv.level - level) < EPS:
                return lv.score
        raise ValueError(
            f"采分点 {self.id} 不存在等级 {level:g}，允许值：{list(self.allowed_levels)}"
        )


@dataclass
class Deduction:
    """扣分项。penalty 与 cap 均为负数，cap 表示封顶。"""

    id: str
    desc: str
    penalty: float
    cap: float

    def effective(self) -> float:
        return max(self.penalty, self.cap)


@dataclass
class Rubric:
    question_id: str
    stem: str
    reference_answer: str
    total_score: float
    items: list[RubricItem]
    deductions: list[Deduction] = field(default_factory=list)
    rules: dict[str, Any] = field(default_factory=dict)

    def item(self, item_id: str) -> RubricItem:
        for it in self.items:
            if it.id == item_id:
                return it
        raise KeyError(item_id)


@dataclass
class ItemJudgement:
    item_id: str
    level: float = 0.0
    hit: bool = False
    evidence: str = ""
    reason: str = ""


@dataclass
class DeductionJudgement:
    id: str
    hit: bool = False
    evidence: str = ""
    reason: str = ""


@dataclass
class SampleJudgement:
    """一次采样的完整判定结果。"""

    question_id: str
    items: list[ItemJudgement]
    deductions: list[DeductionJudgement] = field(default_factory=list)
    comment: str = ""
    score: float = 0.0
    max_score: float = 0.0

    def levels(self) -> dict[str, float]:
        return {ij.item_id: ij.level for ij in self.items}

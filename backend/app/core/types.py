"""领域对象：评分标准与判定结果。

纯标准库（dataclasses），不依赖 pydantic、数据库或任何模型 SDK，
保证核心逻辑可在无第三方包的环境下直接单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EPS = 1e-9

UNGROUNDED_NOTE = "证据未在答卷中定位，按未命中处理"


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
    span: tuple[int, int] | None = None
    grounded: bool = False
    reason: str = ""

    def mark_grounded(self, span: tuple[int, int]) -> None:
        self.grounded = True
        self.span = span

    def mark_ungrounded(self) -> None:
        """证据定位失败：保留模型的命中声称，但该点不再计分。"""
        self.grounded = False
        self.span = None
        self.level = 0.0
        self.reason = f"{self.reason}（{UNGROUNDED_NOTE}）" if self.reason else UNGROUNDED_NOTE


@dataclass
class DeductionJudgement:
    id: str
    hit: bool = False
    evidence: str = ""
    span: tuple[int, int] | None = None
    grounded: bool = False
    reason: str = ""

    def mark_grounded(self, span: tuple[int, int]) -> None:
        self.grounded = True
        self.span = span

    def mark_ungrounded(self) -> None:
        """证据定位失败：扣分项不生效。"""
        self.grounded = False
        self.span = None
        self.reason = f"{self.reason}（{UNGROUNDED_NOTE}）" if self.reason else UNGROUNDED_NOTE


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

    def grounding_rate(self) -> float:
        """模型声称命中的判定里，有多少条证据真的定位到了。

        分母只算声称命中的项：模型什么都没命中时，不存在"编造证据"，
        不该把未命中项算成通过率的一部分。
        """
        checks = [ij.grounded for ij in self.items if ij.hit] + [
            dj.grounded for dj in self.deductions if dj.hit
        ]
        if not checks:
            return 1.0
        return sum(1 for ok in checks if ok) / len(checks)

    def ungrounded_ids(self) -> list[str]:
        """未接地的判定 id，落库时作为 UNGROUNDED 标记呈现。"""
        items = [ij.item_id for ij in self.items if ij.hit and not ij.grounded]
        deductions = [dj.id for dj in self.deductions if dj.hit and not dj.grounded]
        return items + deductions

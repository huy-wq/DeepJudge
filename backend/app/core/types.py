"""领域模型：评分标准（rubric）与判定结果。

本模块刻意只依赖标准库（dataclasses），保证核心评分逻辑在没装任何第三方包的
环境下也能被测试和复用。Web 框架只作为外层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EPS = 1e-9


@dataclass(frozen=True)
class Level:
    """采分点的一个等级：level 是等级标识（0 / 0.5 / 1），score 是该等级对应的分值。"""

    level: float
    score: float
    criterion: str = ""


@dataclass
class RubricItem:
    id: str
    desc: str
    weight: float
    levels: list[Level]
    must_hit: bool = False
    acceptable: list[str] = field(default_factory=list)
    # 仅供离线模拟器（providers/heuristic.py）使用，不会进入真实提示词
    sim_keywords: list[str] = field(default_factory=list)

    @property
    def allowed_levels(self) -> list[float]:
        return sorted({lv.level for lv in self.levels})

    def score_for(self, level: float) -> float:
        for lv in self.levels:
            if abs(lv.level - level) < EPS:
                return lv.score
        raise ValueError(
            f"采分点 {self.id} 不存在等级 {level}，允许值：{self.allowed_levels}"
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "desc": self.desc,
            "weight": self.weight,
            "levels": [
                {"level": lv.level, "criterion": lv.criterion} for lv in self.levels
            ],
            "must_hit": self.must_hit,
            "acceptable": self.acceptable,
        }


@dataclass
class Deduction:
    """扣分项。penalty 为负数（如 -2），cap 为该扣分项的下限（如 -3，即最多扣 3 分）。"""

    id: str
    desc: str
    penalty: float
    cap: float
    sim_keywords: list[str] = field(default_factory=list)

    def effective(self) -> float:
        """实际扣分：不超过 cap。"""
        return max(self.penalty, self.cap)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {"id": self.id, "desc": self.desc, "penalty": self.effective()}


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

    def to_prompt_dict(self) -> dict[str, Any]:
        """用于提示词的 rubric 视图：不含 sim_keywords，也不含分值（防止模型自己算分）。"""
        return {
            "question_id": self.question_id,
            "stem": self.stem,
            "reference_answer": self.reference_answer,
            "total_score": self.total_score,
            "items": [it.to_prompt_dict() for it in self.items],
            "deductions": [d.to_prompt_dict() for d in self.deductions],
            "rules": self.rules,
        }


@dataclass
class ItemJudgement:
    """单次采样中，一个采分点的判定结果。"""

    item_id: str
    level: float
    hit: bool
    grounded: bool
    evidence: str = ""
    evidence_span: tuple[int, int] | None = None
    reason: str = ""


@dataclass
class DeductionJudgement:
    id: str
    hit: bool
    grounded: bool = True
    evidence: str = ""
    reason: str = ""


@dataclass
class SampleJudgement:
    """一次模型采样的完整判定（已通过证据接地校验）。"""

    items: list[ItemJudgement]
    deductions: list[DeductionJudgement] = field(default_factory=list)
    comment: str = ""

    def levels(self) -> dict[str, float]:
        return {ij.item_id: ij.level for ij in self.items}

    def score(self, rubric: Rubric) -> float:
        total = sum(rubric.item(ij.item_id).score_for(ij.level) for ij in self.items)
        total += sum(
            rubric_deduction.effective()
            for rubric_deduction, dj in zip(rubric.deductions, self.deductions)
            if dj.hit
        )
        return round(max(0.0, min(rubric.total_score, total)), 2)

    def grounded_ratio(self) -> float:
        checks = [ij.grounded for ij in self.items] + [dj.grounded for dj in self.deductions]
        if not checks:
            return 0.0
        return sum(1 for c in checks if c) / len(checks)

"""提示词组装。

两条约束：稳定前缀在前（题目 + 采分点 + 参考答案），学生答卷永远在最后；
同一输入两次组装必须字节一致，否则上下文缓存与可复现性都会失效。
"""

from __future__ import annotations

from .types import Rubric

SYSTEM = """你是一名严谨的阅卷教师，负责按采分点逐条判定学生答卷。

必须遵守以下纪律：
1. 只依据采分点与参考答案判分；采分点未覆盖的维度不加分也不扣分。
2. 不得依据答案长度、书写格式、卷面整洁度给分。
3. 每条判定必须给出 evidence：逐字摘自学生答卷的连续片段。
4. 找不到证据就判 hit=false、evidence 为空——宁可漏判，不可编造。
5. 不评价考生身份，不推测考生水平。
6. 只输出 JSON，不要输出总分、满分或任何派生字段。
"""

OUTPUT_SHAPE = """返回一个 JSON 对象，且只包含这些字段：
{
  "items": [
    {"id": "采分点 id", "hit": true, "level": 等级值, "evidence": "原文片段", "reason": "判定理由"}
  ],
  "deductions": [
    {"id": "扣分项 id", "hit": false, "evidence": ""}
  ],
  "comment": "面向学生的简短评语，不泄露参考答案"
}"""


def render_rubric(rubric: Rubric) -> str:
    lines: list[str] = []
    for it in rubric.items:
        levels = "；".join(
            f"{lv.level:g} 分档：{lv.criterion}" for lv in it.levels if lv.criterion
        )
        allowed = "、".join(f"{lv:g}" for lv in it.allowed_levels)
        lines.append(f"- id: {it.id}")
        lines.append(f"  描述: {it.desc}")
        lines.append(f"  可选取值: {allowed}")
        if levels:
            lines.append(f"  判定标准: {levels}")
        if it.must_hit:
            lines.append("  注意: 核心采分点，未命中视为不合格")
        if it.acceptable:
            lines.append(f"  可接受表述: {'；'.join(it.acceptable)}")

    for d in rubric.deductions:
        lines.append(f"- 扣分项 id: {d.id}")
        lines.append(f"  描述: {d.desc}")
        lines.append(f"  扣分: {d.effective():g}")

    return "\n".join(lines)


def build_messages(rubric: Rubric, answer: str) -> list[dict]:
    """返回 OpenAI 兼容的 messages；答案固定在最后。"""
    user = f"""## 题目
{rubric.stem}

## 参考答案
{rubric.reference_answer}

## 采分点（逐条判定）
{render_rubric(rubric)}

## 输出格式
{OUTPUT_SHAPE}

## 学生答卷
<answer>
{answer}
</answer>
"""
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]

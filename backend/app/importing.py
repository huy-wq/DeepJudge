"""答卷导入：把教师给的文本变成"可入库的行 + 坏行清单"。

**纯函数**：不碰数据库、不碰 HTTP。查重、脱敏编号、落库都在 Store 与 API 层，
这样解析规则的每一条都能用一段字符串直接测。

两种来源共用同一个解析器：

- `csv`：一行一份，可选表头；列数是 2（题号,答案）或 3（学号,题号,答案）。
  指定了题号时改成 1（答案）或 2（学号,答案）——题号已经被钉住，不必再占一列。
- `paste`：整段文本就是**一份**答案，题号必须由调用方给出（单份录入/补录）。

**学号在任何一步都不保留**：读到即丢，连预览都不回显——预览里只报"有多少行带学号"。
脱敏编号在落库时按题号顺序生成，与学号无关（架构 §5.7）。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

SOURCE_CSV = "csv"
SOURCE_PASTE = "paste"
SOURCES = (SOURCE_CSV, SOURCE_PASTE)

# 表头里出现这些词就认为这一行是表头。中英混排都认，大小写不敏感。
HEADER_WORDS = {
    "学号",
    "姓名",
    "学生",
    "题号",
    "题目",
    "题目编号",
    "答案",
    "回答",
    "作答",
    "answer",
    "responses",
    "id",
    "student",
    "student_id",
    "question",
    "question_id",
}
QUESTION_HEADERS = {"题号", "题目", "题目编号", "question", "question_id"}
ANSWER_HEADERS = {"答案", "回答", "作答", "answer", "responses"}
# 只用来统计"有多少行带学号"——这些列的内容读进来就丢，不保留、不回显
STUDENT_HEADERS = {"学号", "姓名", "学生", "id", "student", "student_id"}


@dataclass(frozen=True)
class _Layout:
    """一份文件里各列的角色。用类型表达，免得几个裸 int 顺着参数往下传。"""

    answer_at: int
    question_at: int | None
    has_student: bool


@dataclass(frozen=True)
class ParsedRow:
    line: int
    question_code: str
    answer_text: str


@dataclass(frozen=True)
class ProblemRow:
    """坏行：只说哪一行、为什么。

    **刻意不回显原文**：一行坏掉时，我们并不知道哪一列是学号，回显就等于把学号
    原样送到前端与日志里。行号足够教师定位（Excel 里第几行）。
    """

    line: int
    reason: str


@dataclass(frozen=True)
class ParsedImport:
    source: str
    rows: list[ParsedRow] = field(default_factory=list)
    problems: list[ProblemRow] = field(default_factory=list)
    question_codes: list[str] = field(default_factory=list)
    blank_lines: int = 0
    rows_with_student_id: int = 0
    error: str | None = None

    @property
    def total_rows(self) -> int:
        """非空数据行数（含坏行）。空行不算，否则"共 45 行"会莫名其妙变大。"""
        return len(self.rows) + len(self.problems)

    @property
    def ok(self) -> bool:
        return self.error is None


def parse_answers(
    text: str,
    *,
    source: str = SOURCE_CSV,
    question_code: str | None = None,
) -> ParsedImport:
    """解析导入文本。永远返回 `ParsedImport`，坏行进 `problems`，不抛异常。"""
    if source not in SOURCES:
        return ParsedImport(source=source, error=f"不支持的来源：{source}")
    if not (text or "").strip():
        return ParsedImport(source=source, error="内容为空")

    if source == SOURCE_PASTE:
        return _parse_paste(text, question_code)
    return _parse_csv(text, question_code)


def _parse_paste(text: str, question_code: str | None) -> ParsedImport:
    if not question_code:
        return ParsedImport(source=SOURCE_PASTE, error="单份粘贴必须指定题号")
    answer = text.strip()
    if not answer:
        return ParsedImport(source=SOURCE_PASTE, error="答案为空")
    return ParsedImport(
        source=SOURCE_PASTE,
        rows=[ParsedRow(line=1, question_code=question_code, answer_text=answer)],
        question_codes=[question_code],
    )


def _parse_csv(text: str, pinned_question: str | None) -> ParsedImport:
    # utf-8-sig 已在读文件时处理；这里再兜一次 Excel 常见的 BOM
    text = text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(text))

    records: list[tuple[int, list[str]]] = []
    blank_lines = 0
    previous_line = 0
    for fields in reader:
        start_line = previous_line + 1
        previous_line = reader.line_num
        if not any(cell.strip() for cell in fields):
            blank_lines += 1
            continue
        records.append((start_line, fields))

    if not records:
        return ParsedImport(source=SOURCE_CSV, error="内容为空")

    first_line, first_fields = records[0]
    del first_line
    header = _header_index(first_fields)
    if header is not None:
        if "answer" not in header:
            return ParsedImport(source=SOURCE_CSV, error="表头缺少『答案』列")
        if "question" not in header and pinned_question is None:
            return ParsedImport(
                source=SOURCE_CSV, error="表头缺少『题号』列；或者先指定题号再导入"
            )
        layout = _Layout(
            answer_at=header["answer"],
            question_at=header.get("question"),
            has_student="student" in header,
        )
        body = records[1:]
    else:
        layout = _positional_layout(len(first_fields), pinned_question)
        if isinstance(layout, str):
            # 第一行就定不下列角色，整份文件无法解读，直接给一句人话
            return ParsedImport(source=SOURCE_CSV, error=layout)
        body = records
    expected_width = len(first_fields)

    rows: list[ParsedRow] = []
    problems: list[ProblemRow] = []
    with_student_id = 0

    for line, fields in body:
        if len(fields) != expected_width:
            problems.append(
                ProblemRow(
                    line=line,
                    reason=f"列数不对：本文件应为 {expected_width} 列，实际 {len(fields)} 列",
                )
            )
            continue
        parsed = _row_from(fields, layout, pinned_question, line)
        if isinstance(parsed, ProblemRow):
            problems.append(parsed)
            continue
        if layout.has_student:
            with_student_id += 1
        rows.append(parsed)

    codes = sorted({row.question_code for row in rows})
    return ParsedImport(
        source=SOURCE_CSV,
        rows=rows,
        problems=problems,
        question_codes=codes,
        blank_lines=blank_lines,
        rows_with_student_id=with_student_id,
    )


def _header_index(fields: list[str]) -> dict[str, int] | None:
    """认表头。返回 {question:n, answer:n, student:n}，不是表头就返回 None。"""
    lowered = [cell.strip().lower() for cell in fields]
    non_empty = [cell for cell in lowered if cell]
    # 全部单元格都得是表头词才算表头。只看"有一个像表头"会误伤数据行
    # （例如学生把答案写成"答案"两个字）。
    if not non_empty or any(cell not in HEADER_WORDS for cell in non_empty):
        return None
    index: dict[str, int] = {}
    # 用**原始**下标：过滤空单元格会改变位置，表头里有一个空列就会整体错位，
    # 错位后可能把学号列当成答案列。
    for position, cell in enumerate(lowered):
        if cell in QUESTION_HEADERS and "question" not in index:
            index["question"] = position
        elif cell in ANSWER_HEADERS and "answer" not in index:
            index["answer"] = position
        elif cell in STUDENT_HEADERS and "student" not in index:
            index["student"] = position
    return index


def _positional_layout(count: int, pinned_question: str | None) -> "_Layout" | str:
    """没有表头时按列数定角色。定不下来就返回一句人话（整份文件无法解读）。"""
    if pinned_question:
        if count == 1:
            return _Layout(answer_at=0, question_at=None, has_student=False)
        if count == 2:
            return _Layout(answer_at=1, question_at=None, has_student=True)
        return f"已指定题号时只接受 1~2 列（答案 / 学号,答案），实际 {count} 列"
    if count == 2:
        return _Layout(answer_at=1, question_at=0, has_student=False)
    if count == 3:
        return _Layout(answer_at=2, question_at=1, has_student=True)
    return f"需要 2 列（题号,答案）或 3 列（学号,题号,答案），实际 {count} 列"


def _row_from(fields: list[str], layout: "_Layout", pinned_question: str | None, line: int):
    """按既定布局取出一行。返回 ParsedRow 或 ProblemRow。"""
    answer = fields[layout.answer_at].strip()
    if not answer:
        return ProblemRow(line=line, reason="答案为空")

    if pinned_question:
        return ParsedRow(line=line, question_code=pinned_question, answer_text=answer)

    if layout.question_at is None:
        return ProblemRow(line=line, reason="缺少题号列")
    code = fields[layout.question_at].strip()
    if not code:
        return ProblemRow(line=line, reason="题号为空")
    return ParsedRow(line=line, question_code=code, answer_text=answer)

"""答卷导入的解析规则：纯函数，一段字符串就能测。"""

import unittest

from app.core.normalize import content_hash
from app.importing import SOURCE_PASTE, parse_answers


class CsvWithoutHeader(unittest.TestCase):
    def test_two_columns_are_question_and_answer(self):
        parsed = parse_answers("Q-01,客户端发送 SYN\nQ-02,先算再除\n")

        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.total_rows, 2)
        self.assertEqual(
            [(row.question_code, row.answer_text) for row in parsed.rows],
            [("Q-01", "客户端发送 SYN"), ("Q-02", "先算再除")],
        )
        self.assertEqual(parsed.question_codes, ["Q-01", "Q-02"])

    def test_three_columns_drop_the_student_id(self):
        parsed = parse_answers("2024001,Q-01,客户端发送 SYN\n")

        self.assertEqual(parsed.rows[0].answer_text, "客户端发送 SYN")
        self.assertEqual(parsed.rows_with_student_id, 1)
        # 学号不许出现在任何返回结构里
        self.assertNotIn("2024001", repr(parsed))

    def test_broken_rows_never_echo_the_original_line(self):
        parsed = parse_answers("Q-01,答案一\n2024001,Q-02,答案二,多余\n")

        self.assertEqual(len(parsed.problems), 1)
        self.assertEqual(parsed.problems[0].line, 2)
        self.assertNotIn("2024001", repr(parsed))

    def test_a_row_with_a_different_width_is_a_problem_row(self):
        parsed = parse_answers("Q-01,答案一\nQ-02,答案二,多了一列\nQ-03,答案三\n")

        self.assertEqual([row.question_code for row in parsed.rows], ["Q-01", "Q-03"])
        self.assertEqual(len(parsed.problems), 1)
        self.assertEqual(parsed.problems[0].line, 2)
        self.assertIn("应为 2 列", parsed.problems[0].reason)
        self.assertIn("实际 3 列", parsed.problems[0].reason)

    def test_a_file_whose_first_row_has_a_bad_width_is_rejected_whole(self):
        parsed = parse_answers("只有一个字段\nQ-02,答案\n")

        self.assertFalse(parsed.ok)
        self.assertIn("2 列", parsed.error)

    def test_blank_lines_are_not_counted_as_rows(self):
        parsed = parse_answers("Q-01,答案一\n\n\nQ-02,答案二\n")

        self.assertEqual(parsed.total_rows, 2)
        self.assertEqual(parsed.blank_lines, 2)

    def test_empty_answer_and_empty_question_are_separate_reasons(self):
        parsed = parse_answers("Q-01,\n,Q-02 的答案\n")

        self.assertEqual([p.reason for p in parsed.problems], ["答案为空", "题号为空"])

    def test_quoted_field_keeps_commas_and_newlines(self):
        text = 'Q-01,"第一行, 带逗号\n第二行"\n'
        parsed = parse_answers(text)

        self.assertEqual(parsed.rows[0].answer_text, "第一行, 带逗号\n第二行")

    def test_bom_and_crlf_are_tolerated(self):
        parsed = parse_answers("\ufeffQ-01,答案\r\nQ-02,另一份\r\n")

        self.assertEqual([row.question_code for row in parsed.rows], ["Q-01", "Q-02"])

    def test_problem_rows_carry_their_line_number(self):
        parsed = parse_answers("Q-01,好\nQ-02,好\n坏行\n")

        self.assertEqual(parsed.problems[0].line, 3)


class CsvWithHeader(unittest.TestCase):
    def test_header_maps_columns_by_name(self):
        text = "学号,题号,答案\n2024001,Q-01,客户端发送 SYN\n"

        parsed = parse_answers(text)

        self.assertEqual(parsed.rows[0].question_code, "Q-01")
        self.assertEqual(parsed.rows[0].answer_text, "客户端发送 SYN")
        self.assertEqual(parsed.problems, [])

    def test_english_header_is_recognised(self):
        parsed = parse_answers("question_id,answer\nQ-01,hello\n")

        self.assertEqual(parsed.rows[0].question_code, "Q-01")

    def test_header_order_does_not_matter(self):
        parsed = parse_answers("答案,题号\n答案在这里,Q-07\n")

        self.assertEqual(parsed.rows[0].question_code, "Q-07")

    def test_empty_column_in_header_does_not_shift_roles(self):
        # 过滤空单元格后再用下标，会把答案列错位到空列上（甚至把学号当答案）
        parsed = parse_answers("学号,,题号,答案\n2024001,x,Q-01,这是答案\n")

        self.assertEqual(parsed.rows[0].question_code, "Q-01")
        self.assertEqual(parsed.rows[0].answer_text, "这是答案")
        self.assertNotIn("2024001", repr(parsed))

    def test_header_without_answer_column_is_a_fatal_error(self):
        parsed = parse_answers("题号,学生\nQ-01,张三\n")

        self.assertFalse(parsed.ok)
        self.assertIn("答案", parsed.error)

    def test_header_without_question_column_needs_pinned_question(self):
        parsed = parse_answers("学号,答案\n2024001,内容\n")

        self.assertFalse(parsed.ok)
        self.assertIn("题号", parsed.error)


class PinnedQuestion(unittest.TestCase):
    def test_single_column_is_the_answer(self):
        parsed = parse_answers("客户端发送 SYN，服务器回复 SYN+ACK", question_code="Q-01")

        self.assertEqual(parsed.rows[0].question_code, "Q-01")
        self.assertEqual(parsed.rows[0].answer_text, "客户端发送 SYN，服务器回复 SYN+ACK")

    def test_two_columns_are_student_and_answer(self):
        parsed = parse_answers("2024001,客户端发送 SYN\n", question_code="Q-01")

        self.assertEqual(parsed.rows[0].answer_text, "客户端发送 SYN")

    def test_three_columns_are_rejected_with_a_clear_reason(self):
        parsed = parse_answers("2024001,Q-01,答案\n", question_code="Q-01")

        self.assertFalse(parsed.ok)
        self.assertIn("1~2 列", parsed.error)


class PasteSingleAnswer(unittest.TestCase):
    def test_whole_text_is_one_answer(self):
        parsed = parse_answers(
            "第一段，带逗号\n第二段",
            source=SOURCE_PASTE,
            question_code="Q-01",
        )

        self.assertEqual(parsed.total_rows, 1)
        self.assertEqual(parsed.rows[0].answer_text, "第一段，带逗号\n第二段")

    def test_paste_requires_a_question_code(self):
        parsed = parse_answers("任意内容", source=SOURCE_PASTE)

        self.assertFalse(parsed.ok)
        self.assertIn("题号", parsed.error)


class RejectGarbage(unittest.TestCase):
    def test_empty_input(self):
        self.assertFalse(parse_answers("   ").ok)

    def test_unknown_source(self):
        parsed = parse_answers("Q-01,x", source="excel")
        self.assertFalse(parsed.ok)
        self.assertIn("不支持的来源", parsed.error)


class ContentHash(unittest.TestCase):
    def test_whitespace_and_fullwidth_do_not_change_the_hash(self):
        self.assertEqual(
            content_hash("客户端发送 SYN，seq=x"),
            content_hash("  客户端发送  SYN, seq=x  "),
        )

    def test_different_answers_hash_differently(self):
        self.assertNotEqual(content_hash("答案一"), content_hash("答案二"))


if __name__ == "__main__":
    unittest.main()

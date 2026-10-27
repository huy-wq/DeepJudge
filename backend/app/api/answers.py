"""答卷导入接口：先预览、再入库。

两条路由共用**同一份文本与同一个解析器**：import 不接收客户端回传的行，
而是拿着同一段原文重新解析。这样"预览里看到 45 行"和"入库 45 行"不可能对不上——
信任客户端回显的行，等于把预览的准确性交给前端。
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from ..core.normalize import content_hash
from ..importing import parse_answers
from ..store import Store
from .deps import require_admin, require_store
from .schemas import ImportConfirm, ImportRequest


def register(app: FastAPI) -> None:
    @app.post("/api/answers/preview", dependencies=[Depends(require_admin)])
    def preview(payload: ImportRequest, store: Store = Depends(require_store)) -> dict:
        parsed = parse_answers(
            payload.text, source=payload.source, question_code=payload.question_code
        )
        if not parsed.ok:
            return {
                "ok": False,
                "error": parsed.error,
                "total_rows": 0,
                "rows": [],
                "problems": [],
                "questions": [],
            }

        # 题号不存在是批次级错误：预览与入库都必须拒绝同一个文件，
        # 否则会出现"预览说能导、入库整批 404"这种自相矛盾。
        missing = [code for code in parsed.question_codes if store.get_question(code) is None]
        if missing:
            return {
                "ok": False,
                "error": "题号不存在：" + "、".join(missing) + "（先保存这份评分标准，再导入答卷）",
                "total_rows": parsed.total_rows,
                "rows": [],
                "problems": [],
                "questions": [
                    {"code": code, "exists": code not in missing} for code in parsed.question_codes
                ],
            }

        digests = [content_hash(row.answer_text) for row in parsed.rows]
        pairs = [(row.question_code, digest) for row, digest in zip(parsed.rows, digests)]
        in_database = store.find_duplicates(pairs)

        # 批内重复也要算：同一个文件里出现两遍，预览不标的话，
        # 教师会看到"0 处重复"却在入库时被悄悄跳掉一行。
        # 只把**第二次及以后**的出现算重复：第一次出现的那行本身不能标成重复
        first_occurrence: set[tuple[str, str]] = set()
        repeated_in_batch: list[bool] = []
        for pair in pairs:
            repeated_in_batch.append(pair in first_occurrence)
            first_occurrence.add(pair)

        questions = [store.get_question(code) for code in parsed.question_codes]
        return {
            "ok": True,
            "error": None,
            "total_rows": parsed.total_rows,
            "blank_lines": parsed.blank_lines,
            "rows_with_student_id": parsed.rows_with_student_id,
            "question_codes": parsed.question_codes,
            "questions": [
                {"code": question["code"], "exists": True, "total_score": question["total_score"]}
                for question in questions
            ],
            "rows": [
                {
                    "line": row.line,
                    "question_code": row.question_code,
                    "answer_text": row.answer_text,
                    "content_hash": digest,
                    "duplicate": (row.question_code, digest) in in_database
                    or repeated,
                }
                for row, digest, repeated in zip(parsed.rows, digests, repeated_in_batch)
            ],
            "problems": [
                {"line": problem.line, "reason": problem.reason}
                for problem in parsed.problems
            ],
        }

    @app.post("/api/answers/import", dependencies=[Depends(require_admin)])
    def do_import(payload: ImportConfirm, store: Store = Depends(require_store)) -> dict:
        parsed = parse_answers(
            payload.text, source=payload.source, question_code=payload.question_code
        )
        if not parsed.ok:
            return {"imported": 0, "skipped_duplicates": 0, "pseudo_ids": [], "error": parsed.error}
        if not parsed.rows:
            return {
                "imported": 0,
                "skipped_duplicates": 0,
                "pseudo_ids": [],
                "error": "没有可导入的行（全部是坏行）",
            }

        outcome = store.import_answers(
            [(row.question_code, row.answer_text) for row in parsed.rows],
            source=payload.source,
            skip_duplicates=payload.skip_duplicates,
        )
        return {
            "imported": len(outcome.imported),
            "skipped_duplicates": outcome.skipped_duplicates,
            "pseudo_ids": [item.pseudo_id for item in outcome.imported],
            "problems": [
                {"line": problem.line, "reason": problem.reason}
                for problem in parsed.problems
            ],
            "error": None,
        }

    @app.get("/api/answers", dependencies=[Depends(require_admin)])
    def list_answers(
        question_code: str, store: Store = Depends(require_store)
    ) -> dict:
        return {
            "question_code": question_code,
            "total": store.count_answers(question_code),
            "items": store.list_answers(question_code),
        }

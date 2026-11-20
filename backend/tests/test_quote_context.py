from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app import db, models as m, pdftext
from app.chat.context import Ctx
from app.chat.reply import to_blocks
from app.chat.text import quote_range
from app.chat.validate import Draft
from app.statement_sections import samples
from app.web.student import _message_view
from app.xdoj.assemble import source_text


CASES = json.loads((Path(__file__).resolve().parent / "fixtures/statement_sections.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_pdf_sample_boundaries_match_frontend(case):
    assert [body for _, body in samples(case["text"])] == case["samples"]


def test_tidy_cannot_swap_or_change_spaced_pdf_samples():
    raw = CASES[1]["text"]
    assert pdftext.check_tidy(raw, raw.replace("输\u200b出 示\u2060例\u00a0１︰", "输出示例 1："), False) is None
    assert "样例" in pdftext.check_tidy(raw, raw.replace("Thursday", "Wednesday"), False)


@pytest.mark.parametrize("a,b,expected", [(6, 6, (4, 8)), (1, 1, (1, 5)), (12, 12, (8, 12)), (4, 11, (4, 11))])
def test_quotes_keep_context_and_original_line_numbers(a, b, expected):
    lines = [f"line {n};" for n in range(1, 13)]
    assert quote_range(lines, a, b) == expected
    block = to_blocks(Draft(quote=(a, b, [a])), Ctx(user_id="synthetic", source="\n".join(lines), attempt_no=3))[0]
    lo, hi = expected
    assert block["start"] == lo and block["lines"] == lines[lo - 1:hi] and block["mark"] == [a]
    assert block["caption"].endswith(f"第 {lo} 到 {hi} 行")


def test_quotes_never_invent_missing_context_or_accept_bad_ranges():
    assert quote_range(["int x;", "x = 0;"], 2, 2) == (1, 2)
    for a, b in [(0, 1), (3, 2), (1, 9), (1, 13)]:
        with pytest.raises(ValueError):
            quote_range(["x;"] * 12, a, b)


def test_legacy_reply_context_uses_its_own_source_without_mutating_history(demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        step = s.scalars(select(m.ChatStep).where(m.ChatStep.problem_id == 406).order_by(m.ChatStep.id)).first()
        source = "\n".join(["int total = 0;", "int month = 3;", "for (int i = 0; i < 12; ++i) {", "    total += i;", "}"])
        original = s.get(m.ChatMessage, step.message_id)
        original.content = {"blocks": [{"t":"code", "caption":"我贴的代码", "start":1, "lines":source.splitlines(), "mark":[]}]}
        blocks = [{"t":"code", "caption":"你贴的代码，第 3 行", "start":3, "lines":[source.splitlines()[2]], "mark":[3]}]
        step.evidence_card = {"sourceKind":"pasted"}
        step.reply = {"blocks":blocks}
        msg = m.ChatMessage(user_id=step.user_id, round_id=step.round_id, server_id=sid, problem_id=406,
                            course="C", role="agent", kind="text", content={"blocks":blocks},
                            created_at=step.created_at + dt.timedelta(microseconds=1))
        s.add(msg)
        s.flush()
        view = _message_view(s, msg, False)
        assert view["blocks"][0]["lines"] == source.splitlines()
        assert view["blocks"][0]["mark"] == [3]
        assert len(msg.content["blocks"][0]["lines"]) == 1
        # 同一行与旧源码对不上时保留旧记录，不用别的提交拼接。
        msg.content = {"blocks":[{**blocks[0], "lines":["other code;"]}]}
        assert _message_view(s, msg, False)["blocks"] == msg.content["blocks"]


def test_legacy_submission_quote_cannot_use_another_student_or_course(demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        step = s.scalars(select(m.ChatStep).where(m.ChatStep.problem_id == 406).order_by(m.ChatStep.id)).first()
        sub = s.get(m.Submission, (sid, step.submission_id))
        lines = source_text(s, sid, sub.submission_id).splitlines()
        blocks = [{"t":"code", "caption":"你的第 3 次提交，第 10 行", "start":10,
                   "lines":[lines[9].strip()], "mark":[10]}]
        step.reply = {"blocks":blocks}
        step.evidence_card = {"sourceKind":"submission"}
        msg = m.ChatMessage(user_id=step.user_id, round_id=step.round_id, server_id=sid, problem_id=406,
                            course="C", role="agent", kind="text", content={"blocks":blocks},
                            created_at=step.created_at + dt.timedelta(microseconds=1))
        s.add(msg)
        s.flush()
        assert len(_message_view(s, msg, False)["blocks"][0]["lines"]) == 5
        owner = sub.user_id
        sub.user_id = "another-synthetic-student"
        assert _message_view(s, msg, False)["blocks"] == blocks
        sub.user_id = owner
        server = s.get(m.Server, sid)
        server.course = "D"
        assert _message_view(s, msg, False)["blocks"] == blocks
        server.course = "C"
        # 来源虽然关联成功，但引用文字被改过时也不得扩展。
        wrong = [{**blocks[0], "lines":["other code;"]}]
        msg.content, step.reply = {"blocks":wrong}, {"blocks":wrong}
        assert _message_view(s, msg, False)["blocks"] == wrong

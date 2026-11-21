"""每次帮助之后怎么样了（app/helpeffect.py）：帮助和这道题后面的提交连起来，全用程序算。"""
from __future__ import annotations

import datetime as dt

from app import db, helpeffect
from app import models as m

UID, SID, PID = "2500000009", 1, 701
T0 = dt.datetime(2026, 11, 2, 10, 0, tzinfo=dt.timezone.utc)


def _step(s, minutes: float, level: str, error: str = "LP01", pid: int = PID) -> m.ChatStep:
    st = m.ChatStep(message_id=1, round_id=1, user_id=UID, server_id=SID, problem_id=pid, course_id="C",
                    help_level=level, root_error_code=error, root_skill_code="C05.02.01",
                    created_at=T0 + dt.timedelta(minutes=minutes))
    s.add(st)
    return st


def _sub(s, sid: int, minutes: float, judgement: str, errors: list[str] | None = None, pid: int = PID) -> None:
    s.add(m.Submission(server_id=SID, submission_id=sid, user_id=UID, problem_id=pid, judgement=judgement,
                       submit_time=T0 + dt.timedelta(minutes=minutes), detail_status="done"))
    for code in errors or []:
        s.add(m.SubmissionLabel(server_id=SID, submission_id=sid, error_code=code, skill_code="C05.02.01",
                                role="primary", confidence=0.9, is_current=True))


def test_last_help_before_each_resubmission(database):
    with db.session_scope() as s:
        _sub(s, 100, 0, "WA", ["LP01"])
        _step(s, 2, "H0")            # 同一段里先反问
        _step(s, 4, "H1")            # 再指出位置：这一次才算
        _sub(s, 101, 10, "WA", ["LP01"])   # 讨论的错误还在
        _step(s, 12, "H2")
        _sub(s, 102, 20, "WA", ["IO02"])   # 讨论的错误没了（换了个错）
        _step(s, 22, "H3")
        _sub(s, 103, 25, "AC")
        _step(s, 30, "H0", pid=702)  # 另一道题：给了帮助，还没再交
    with db.session_scope() as s:
        eps = {(e.step.problem_id, e.level): e for e in helpeffect.episodes(s, [UID], "C")}
        assert set(eps) == {(PID, "H1"), (PID, "H2"), (PID, "H3"), (702, "H0")}
        h1 = eps[(PID, "H1")]
        assert (h1.helps_before, h1.minutes, h1.next_pass, h1.error_gone, h1.passed, h1.tries_to_pass) == (1, 6.0, False, False, True, 3)
        h2 = eps[(PID, "H2")]
        assert (h2.error_gone, h2.tries_to_pass) == (True, 2)
        h3 = eps[(PID, "H3")]
        assert (h3.next_pass, h3.error_gone, h3.tries_to_pass) == (True, None, 1)
        silent = eps[(702, "H0")]
        assert (silent.next_sub, silent.next_pass, silent.passed) == (None, None, False)
        rows = {r["level"]: r for r in helpeffect.summarize(list(eps.values()))}
        assert rows["all"]["n"] == 4 and rows["all"]["resubmitted"] == {"n": 3, "d": 4}
        assert rows["all"]["nextPass"] == {"n": 1, "d": 3} and rows["all"]["errorGone"] == {"n": 1, "d": 2}
        assert rows["H0"]["resubmitted"] == {"n": 0, "d": 1} and rows["H0"]["nextPass"] is None and rows["all"]["medianTries"] == 2.0
        row = helpeffect.export_row(h1)
        assert row["version"] == helpeffect.VERSION and row["helpsBefore"] == 1 and row["nextSubmission"] == 101


def test_unknown_when_not_judged_or_not_labeled(database):
    with db.session_scope() as s:
        _sub(s, 200, 0, "WA", ["LP01"])
        _step(s, 1, "H1")
        _sub(s, 201, 5, "WA")        # 还没标注：判断不了错误在不在
        _step(s, 6, "H1", error="")  # 帮助时没诊断出错误
        _sub(s, 202, 9, "PENDING")   # 还在评测
    with db.session_scope() as s:
        a, b = helpeffect.episodes(s, [UID], "C")
        assert (a.next_pass, a.error_gone) == (False, None)
        assert (b.next_pass, b.error_gone, b.minutes) == (None, None, 3.0)
        assert helpeffect.episodes(s, [UID], "D") == []                       # 按课程分开
        assert helpeffect.episodes(s, [UID], "C", problems={(SID, 999)}) == []  # 只看班上布置的题


def test_low_confidence_other_and_missing_error_are_unknown(database):
    with db.session_scope() as s:
        for i in range(4):
            _step(s, 2 * i, "H1", error="" if i == 2 else "LP01")
            _sub(s, 300+i, 2*i+1, "WA", None)
            s.add(m.SubmissionLabel(server_id=SID, submission_id=300+i, role="primary", is_current=True,
                                    skill_code="C05.02.01", confidence=0.3 if i == 0 else 0.9,
                                    error_code="OT99" if i == 1 else "IO02"))
    with db.session_scope() as s:
        eps = helpeffect.episodes(s, [UID], "C")
        assert [e.next_status for e in eps] == ["unknown", "unknown", "unknown", "absent"]
        row = helpeffect.summarize(eps)[-1]
        assert row["nextStatus"]["unknown"] == {"n": 3, "d": 4}
        assert sum(x["n"] for x in row["nextStatus"].values()) == 4

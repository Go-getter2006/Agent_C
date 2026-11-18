"""小题：题库验证、作答流程、判分。需要 gcc。"""
from __future__ import annotations

import shutil

import pytest
from sqlalchemy import select

from app import db, mastery, quiz
from app import models as m

pytestmark = pytest.mark.skipif(shutil.which("gcc") is None, reason="没有 gcc")
UID = "2500000001"


@pytest.fixture()
def bank(database):
    with db.session_scope() as s:
        quiz.load_bank(s)
        results = quiz.verify_bank(s)
    return results


def item_of(s, code):
    return s.scalar(select(m.QuizItem).where(m.QuizItem.code == code))


def new_attempt(s, code, reason="after_pass"):
    """以前的作答协议（把握 → 判断 → 改）：旧记录照旧能做完。新出的题见 test_independent.py。"""
    msg = quiz.start(s, UID, item_of(s, code), reason, "顺手问一道：")
    a = s.get(m.QuizAttempt, msg.quiz_attempt_id)
    a.protocol, a.state, a.events = 1, "confidence", []
    return a


def test_whole_bank_verifies(bank):
    bad = [(code, why) for code, ok, why in bank if not ok]
    assert not bad, bad
    assert len(bank) >= 12


def test_view_hides_answer_until_done(bank):
    with db.session_scope() as s:
        a = new_attempt(s, "max-init-fix")
        v = quiz.view(s, a)
        assert v["state"] == "confidence" and "result" not in v and "choices" not in v
        blob = repr(v)
        for secret in ("a[0]", "bug", "TY09", "C06", "FIX", "全是负数"):
            assert secret not in blob.replace("'judged'", "")
        quiz.judge(s, a, True)  # 没选把握程度之前不能判断
        assert a.state == "confidence"


def test_fix_right_first_try(bank):
    with db.session_scope() as s:
        a = new_attempt(s, "max-init-fix", reason="revisit")
        quiz.set_confidence(s, a, "很有把握", 2400)
        quiz.judge(s, a, True)
        assert a.state == "edit" and quiz.view(s, a)["triesLeft"] == 2
        quiz.run_fix(s, a, "int max = a[0];\nfor (i = 0; i < n; i++)\n    if (a[i] > max) max = a[i];")
        assert a.state == "done" and a.outcome == "right" and a.changed_lines == [1]
        st = mastery.skill_states(s, UID, decay=False)["C06.03.02"]
        assert st.s == 1.5  # 回访题乘 1.5
        v = quiz.view(s, a)
        assert v["result"]["outcome"] == "right" and "fix" not in v["result"]


def test_fix_wrong_then_right_and_messages(bank):
    with db.session_scope() as s:
        a = new_attempt(s, "max-init-fix")
        quiz.set_confidence(s, a, "一半", 3000)
        quiz.judge(s, a, True)
        code = "int max = 0;\nfor (i = 0; i < n; i++)\n    if (a[i] > max) max = a[i];"
        quiz.run_fix(s, a, code)
        assert a.tries == 0 and "还没有改动" in quiz.view(s, a)["message"]["text"]
        quiz.run_fix(s, a, "int max = a[0]\nfor (i = 0; i < n; i++)\n    if (a[i] > max) max = a[i];")
        v = quiz.view(s, a)
        assert a.tries == 0 and v["message"]["kind"] == "compile" and "分号" in v["message"]["text"]
        quiz.run_fix(s, a, "int x = system(\"ls\");\nfor (i = 0; i < n; i++)\n    if (a[i] > max) max = a[i];")
        assert a.tries == 0 and "system" in quiz.view(s, a)["message"]["text"]
        quiz.run_fix(s, a, "int max = -1;\nfor (i = 0; i < n; i++)\n    if (a[i] > max) max = a[i];")
        v = quiz.view(s, a)
        assert a.tries == 1 and v["message"]["text"] == "输入 -5 -2 -8 时，输出 -1，应该是 -2。"
        assert v["triesLeft"] == 1 and "result" not in v
        quiz.run_fix(s, a, "int max = a[0];\nfor (i = 0; i < n; i++)\n    if (a[i] > max) max = a[i];")
        assert a.outcome == "right_second"
        st = mastery.skill_states(s, UID, decay=False)["C06.03.02"]
        assert (st.s, st.f) == (0.5, 0.5)


def test_fix_two_failures_reveals(bank):
    with db.session_scope() as s:
        a = new_attempt(s, "min-init-fix")
        quiz.set_confidence(s, a, "很有把握", 2000)
        quiz.judge(s, a, True)
        quiz.run_fix(s, a, "int min = 0;\nfor (i = 1; i < n; i++)\n    if (a[i] < min) min = a[i];")
        quiz.run_fix(s, a, "int min = 1;\nfor (i = 0; i < n; i++)\n    if (a[i] < min) min = a[i];")
        v = quiz.view(s, a)
        assert a.outcome == "wrong_fix" and v["result"]["bugLine"] == 1
        assert v["result"]["fix"]["lines"][0] == "int min = a[0];" and v["result"]["fix"]["mark"] == [1]
        assert mastery.skill_states(s, UID, decay=False)["C06.03.02"].f == 1.5  # 很有把握却答错


def test_too_many_changes_and_bypass(bank):
    with db.session_scope() as s:
        a = new_attempt(s, "average-intdiv-fix")
        quiz.set_confidence(s, a, "一半", 2000)
        quiz.judge(s, a, True)
        quiz.run_fix(s, a, "double sum = 0;\nfor (i = 0; i <= n - 1; i++)\n    sum = sum + a[i];\ndouble avg = sum / n;\nprintf(\"%.2lf\\n\", avg);")
        assert a.tries == 0 and "只需要改一两处" in quiz.view(s, a)["message"]["text"]
        # 改 printf 那一行绕开：结果对，但没碰出错的行
        quiz.run_fix(s, a, "int sum = 0;\nfor (i = 0; i < n; i++)\n    sum += a[i];\ndouble avg = sum / n;\nprintf(\"%.2f\\n\", 1.0 * sum / n);")
        assert a.outcome == "right_bypass" and a.changed_lines == [5]


def test_judge_items_and_misjudge_on_correct_code(bank):
    with db.session_scope() as s:
        a = new_attempt(s, "max-from-zero-judge")
        quiz.set_confidence(s, a, "没把握", 1500)
        quiz.judge(s, a, False)  # 说“对”，确实对
        assert a.outcome == "right_judge" and a.correct
        assert mastery.skill_states(s, UID, decay=False)["C06.03.02"].s == 0.5  # 没把握却答对：五折
        b = new_attempt(s, "range-chain-judge")
        quiz.set_confidence(s, b, "一半", 1500)
        quiz.judge(s, b, False)  # 有错的代码说成对
        v = quiz.view(s, b)
        assert b.outcome == "wrong_judge" and v["result"]["headline"] == "这段代码其实有错。"
        assert v["result"]["fix"]["lines"][0] == "if (x >= 1 && x <= 100)"


def test_choice_mode_runs_in_sandbox(bank):
    with db.session_scope() as s:
        a = new_attempt(s, "prime-one-fix")
        quiz.set_confidence(s, a, "一半", 1500)
        quiz.judge(s, a, True)
        labels = [c["label"] for c in quiz.view(s, a)["choices"]]
        assert sorted(labels) == sorted(c["label"] for c in item_of(s, "prime-one-fix").choices)
        quiz.run_fix(s, a, choice=1)
        assert a.tries == 1 and a.state == "edit"
        quiz.run_fix(s, a, choice=0)
        assert a.outcome == "right_second"


def test_streak_bonus_and_limits(bank):
    with db.session_scope() as s:
        for code in ("max-from-zero-judge", "max-plain-judge"):
            a = new_attempt(s, code)
            quiz.set_confidence(s, a, "很有把握", 2500)
            quiz.judge(s, a, False)
            assert a.correct
        ev = s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.event_type == "quiz_streak_bonus")).all()
        assert len(ev) == 1
        assert not quiz.can_start(s, UID, "C06.03.02")   # 同一技能同一天最多 2 道
        assert quiz.can_start(s, UID, "C02.01.02")
        new_attempt(s, "factorial-small-judge")
        assert not quiz.can_start(s, UID, "C02.01.02")   # 每天上限 3 道


def test_careless_confidence_not_used(bank):
    with db.session_scope() as s:
        a = new_attempt(s, "range-and-judge")
        quiz.set_confidence(s, a, "没把握", 300)  # 点得太快
        quiz.judge(s, a, False)
        assert mastery.skill_states(s, UID, decay=False)["C03.03.01"].s == 1.0  # 不打折


def test_skip_records_no_evidence(bank):
    with db.session_scope() as s:
        a = new_attempt(s, "leap-logic-fix")
        quiz.skip(s, a)
        assert a.skipped and quiz.view(s, a)["result"]["outcome"] == "skipped"
        assert "C03.03.01" not in mastery.skill_states(s, UID)

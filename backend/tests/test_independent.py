"""复习提升的独立作答（界面改造方案第 4 节）：先判断 → 再选位置/依据 → 判断有问题的修改 → 反馈。需要 gcc。

- 第一步界面上不给能猜出答案的东西，反馈之前不下发错在哪、标准改法、解释；
- 四种正误组合各走一遍，首次判断、首次选行、首稿各记一次；运行公开样例、看提示、重试按顺序记下；
- 重复确认不重复记；选行要一行或连续几行；
- 进复习提升就有一道备好的题，侧边栏数字只数备好的题。
"""
from __future__ import annotations

import shutil

import pytest
from sqlalchemy import select

from app import db, quiz, review
from app import models as m

pytestmark = pytest.mark.skipif(shutil.which("gcc") is None, reason="没有 gcc")
UID = "2500000001"
FIX = "int max = a[0];\nfor (i = 0; i < n; i++)\n    if (a[i] > max) max = a[i];"


@pytest.fixture()
def bank(database):
    with db.session_scope() as s:
        quiz.load_bank(s)
        quiz.verify_bank(s)


def attempt_for(s, code: str) -> m.QuizAttempt:
    item = s.scalar(select(m.QuizItem).where(m.QuizItem.code == code))
    return quiz.new_attempt(s, UID, item, "review")


def kinds(a: m.QuizAttempt) -> list[str]:
    return [e["kind"] for e in a.events]


def test_first_step_gives_nothing_away(bank):
    with db.session_scope() as s:
        a = attempt_for(s, "max-init-fix")
        v = quiz.view(s, a)
        assert a.protocol == 2 and v["state"] == "judge" and v["judged"] is None and a.item_version
        assert "mark" not in v                         # 不标任何一行
        blob = repr(v)
        for secret in ("a[0]", "全是负数", "bugLines", "explanation", "fix", "result", "hasBug", "has_bug", "TY09", "C06"):
            assert secret not in blob
        quiz.locate(s, a, [1])                       # 没判断不能选位置
        assert a.state == "judge" and not a.first_lines


def test_bug_found_located_and_fixed_first_try(bank):
    with db.session_scope() as s:
        a = attempt_for(s, "max-init-fix")
        quiz.judge2(s, a, True, "很有把握", 2100)
        quiz.judge2(s, a, False)                     # 确认以后锁定：再点不算
        assert a.state == "locate" and a.judged_has_bug is True and a.confidence == "很有把握"
        assert quiz.locate(s, a, []) and quiz.locate(s, a, [1, 3]) and a.state == "locate"   # 要一行或连续几行
        assert quiz.locate(s, a, [1]) is None and a.state == "edit" and a.locate_hit is True
        v = quiz.view(s, a)
        assert v["hints"] == [] and v["moreHints"] and "result" not in v and "explanation" not in repr(v)
        quiz.submit_fix(s, a, FIX)
        assert a.state == "done" and a.outcome == "right" and a.first_fix_pass and a.first_fix_clean
        r = quiz.view(s, a)["result"]
        assert r["headline"] == "你找到并改对了错误"
        assert r["lines"] == [1] and "summary" not in r and "judgeText" not in r
        assert r["bugLines"] == [1] and "fix" not in r and r["explanation"]
        assert kinds(a) == ["prepared", "judge", "locate", "submit", "feedback"]


def test_sample_run_and_hint_are_recorded_and_change_the_label(bank):
    with db.session_scope() as s:
        a = attempt_for(s, "max-init-fix")
        quiz.judge2(s, a, True)
        quiz.locate(s, a, [3])                       # 选错了地方
        assert a.locate_hit is False
        quiz.run_sample(s, a, "int max = -1;\nfor (i = 0; i < n; i++)\n    if (a[i] > max) max = a[i];")
        v = quiz.view(s, a)
        assert a.sample_runs == 1 and v["message"]["kind"] == "sample" and v["message"]["text"].startswith("公开样例")
        assert a.tries == 0                          # 运行公开样例不算一次提交
        quiz.hint(s, a)
        assert quiz.view(s, a)["hints"] == ["看看第 1 行。"]
        quiz.submit_fix(s, a, "int max = -1;\nfor (i = 0; i < n; i++)\n    if (a[i] > max) max = a[i];")
        assert a.tries == 1 and a.first_fix_pass is False and a.first_fix_clean is False and a.state == "edit"
        quiz.submit_fix(s, a, FIX)
        assert a.outcome == "right_second"
        r = quiz.view(s, a)["result"]
        assert r["lines"] == [3] and r["bugLines"] == [1] and a.hints == 1
        assert kinds(a) == ["prepared", "judge", "locate", "sample", "hint", "submit", "submit", "feedback"]


def test_missed_bug_ends_with_feedback_and_the_fix(bank):
    with db.session_scope() as s:
        a = attempt_for(s, "max-init-fix")
        quiz.judge2(s, a, False)
        assert quiz.view(s, a)["state"] == "locate"   # 判断满足要求的也要给依据，界面不会因为提前结束而泄题
        quiz.locate(s, a, [2])
        assert a.state == "done" and a.outcome == "wrong_judge" and a.locate_hit is None
        r = quiz.view(s, a)["result"]
        assert r["headline"] == "这段代码其实有错" and "summary" not in r
        assert r["fix"]["lines"][0] == "int max = a[0];" and r["bugLines"] == [1]


def test_correct_code_recognised_has_a_full_ending(bank):
    with db.session_scope() as s:
        a = attempt_for(s, "max-plain-judge")
        quiz.judge2(s, a, False)
        quiz.locate(s, a, [2])
        assert a.outcome == "right_judge" and a.correct
        r = quiz.view(s, a)["result"]
        assert r["headline"] == "你识别出了一段正确的代码"
        assert r["lines"] == [2] and "summary" not in r
        assert "fix" not in r and "bugLines" not in r


def test_correct_code_judged_wrong_can_be_kept_as_is(bank):
    with db.session_scope() as s:
        a = attempt_for(s, "max-plain-judge")
        quiz.judge2(s, a, True)
        quiz.locate(s, a, [1])
        assert a.state == "edit" and a.locate_hit is None
        assert quiz.view(s, a)["hints"] == []
        quiz.hint(s, a)
        assert "原样提交" in quiz.view(s, a)["hints"][0]          # 提示不说破
        item = s.get(m.QuizItem, a.item_id)
        quiz.submit_fix(s, a, item.shown_code)                    # 原样提交：自己纠正了判断
        assert a.outcome == "wrong_judge" and a.first_fix_pass is True
        assert a.events[-2]["misjudge"] == "kept"
        r = quiz.view(s, a)["result"]
        assert r["headline"] == "这段代码本来是对的" and r["lines"] == [1]


def test_old_protocol_attempts_still_work(bank):
    with db.session_scope() as s:
        a = attempt_for(s, "max-init-fix")
        a.protocol, a.state = 1, "confidence"
        quiz.judge2(s, a, True)                      # 新协议的动作不碰旧记录
        assert a.state == "confidence"
        quiz.set_confidence(s, a, "一半", 1500)
        quiz.judge(s, a, True)
        assert a.state == "edit"


def test_review_page_prepares_one_ready_question(demo):
    with db.session_scope() as s:
        assert review.ready_count(s, UID, "C") == 0
        status = review.prepare(s, UID, "C")
        assert status == "ready" and review.ready_count(s, UID, "C") == 1
        assert review.prepare(s, UID, "C") == "ready" and review.ready_count(s, UID, "C") == 1   # 不重复备题
        pending = s.scalars(select(m.QuizAttempt).where(m.QuizAttempt.reason == "review",
                                                        m.QuizAttempt.state != "done")).all()
        assert len(pending) == 1 and pending[0].protocol == 2


def test_one_item_that_cannot_be_made_does_not_block_the_rest(demo, monkeypatch):
    """排在最前面的一项这会儿出不了题：换下一项，不让一项卡住全部。"""
    from app import quizgen

    with db.session_scope() as s:
        due = review.due_items(s, UID, course="C")
        assert len(due) >= 2
        first, second = due[0].skill_code, due[1].id
        # 第一项出不了题；别的项能出（演示数据没有大模型，有的技能题库里没有题：直接给一道题库里验证过的题）
        bank = s.scalars(select(m.QuizItem).where(m.QuizItem.verified.is_(True))).first()
        assert bank is not None
        monkeypatch.setattr(quizgen, "generate", lambda s_, u, skill, *a, **k: None if skill == first else bank)
        assert review.prepare(s, UID, "C") == "ready"
        pending = s.scalars(select(m.QuizAttempt).where(m.QuizAttempt.reason == "review",
                                                        m.QuizAttempt.state != "done")).all()
        assert len(pending) == 1 and pending[0].review_item_id == second

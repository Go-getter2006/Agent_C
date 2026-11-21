"""复习提升的独立作答（界面改造方案第 4 节）：先判断 → 判断有问题的选位置、修改；判断满足要求的智能体追问一句
（坚持就出反馈，改判再去选位置、修改）→ 反馈。需要 gcc。

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
        assert a.protocol == quiz.PROTOCOL == 3 and v["state"] == "judge" and v["judged"] is None and a.item_version
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
        v = quiz.view(s, a)
        # 判断满足要求：智能体追问一句，不透露对错（界面不会因为提前结束而泄题）
        assert v["state"] == "challenge" and v["challenge"] == {"text": quiz.CHALLENGE, "reply": None}
        assert "result" not in v and "bugLines" not in repr(v) and v["current"] == "ok"
        quiz.locate(s, a, [2])                       # 追问还没回答：不能选行
        assert a.state == "challenge" and not a.first_lines
        quiz.challenge_reply(s, a, keep=True)
        assert a.state == "done" and a.outcome == "wrong_judge" and a.locate_hit is None
        r = quiz.view(s, a)["result"]
        assert r["headline"] == "这段代码其实有错" and "summary" not in r and r["challenge"] == "kept"
        assert r["fix"]["lines"][0] == "int max = a[0];" and r["bugLines"] == [1]
        assert kinds(a) == ["prepared", "judge", "challenge", "challenge_reply", "feedback"]


def test_correct_code_recognised_has_a_full_ending(bank):
    with db.session_scope() as s:
        a = attempt_for(s, "max-plain-judge")
        quiz.judge2(s, a, False)
        assert quiz.view(s, a)["challenge"]["text"] == quiz.CHALLENGE   # 正确的代码，追问的话一模一样
        quiz.challenge_reply(s, a, keep=True)
        quiz.challenge_reply(s, a, keep=False)       # 回答过了：再点不算
        assert a.outcome == "right_judge" and a.correct
        r = quiz.view(s, a)["result"]
        assert r["headline"] == "你识别出了一段正确的代码" and r["challenge"] == "kept"
        assert r["lines"] == [] and "summary" not in r
        assert "fix" not in r and "bugLines" not in r


def test_flip_after_the_challenge_then_fix(bank):
    """有错的代码判断满足要求，被追问后改判：去选位置、修改。首次判断不改；改判后的选行和修改不算独立作答。"""
    from app import diagnosis

    with db.session_scope() as s:
        a = attempt_for(s, "max-init-fix")
        quiz.judge2(s, a, False)
        quiz.challenge_reply(s, a, keep=False)
        v = quiz.view(s, a)
        assert a.state == "locate" and v["judged"] == "ok" and v["current"] == "bug"
        assert v["challenge"]["reply"] == "flipped"
        assert quiz.locate(s, a, [1]) is None and a.state == "edit" and a.locate_hit is None
        quiz.submit_fix(s, a, FIX)
        assert a.outcome == "right_second" and a.judged_has_bug is False and a.judge_correct is False
        r = quiz.view(s, a)["result"]
        assert r["headline"] == "追问以后你找到并改对了错误" and r["challenge"] == "flipped"
        d = diagnosis.count([(a, s.get(m.QuizItem, a.item_id))])["C06.03"]
        assert (d.judge.n, d.judge.d) == (0, 1) and (d.turned.n, d.turned.d) == (1, 1)
        assert d.locate.d == 0 and d.edit.d == 0 and d.missed == 1      # 不进定位、修改
        steps = diagnosis.evidence(s, a, s.get(m.QuizItem, a.item_id))["steps"]
        assert [x["label"] for x in steps] == ["判断", "追问", "定位", "修改"]
        assert "不算独立" in steps[2]["note"] and "不算独立" in steps[3]["note"]


def test_correct_code_given_up_under_the_challenge(bank):
    """本来正确的代码判断满足要求，被追问后改判：最后按判断错记；坚持率的分母里有它。"""
    from app import diagnosis

    with db.session_scope() as s:
        a = attempt_for(s, "max-plain-judge")
        quiz.judge2(s, a, False)
        quiz.challenge_reply(s, a, keep=False)
        quiz.locate(s, a, [1])
        item = s.get(m.QuizItem, a.item_id)
        quiz.submit_fix(s, a, item.shown_code)
        assert a.outcome == "wrong_judge" and a.judge_correct is True
        r = quiz.view(s, a)["result"]
        assert r["headline"] == "这段代码本来是对的，你一开始的判断没错" and r["challenge"] == "flipped"
        d = diagnosis.count([(a, item)])["C06.03"]
        assert (d.judge_ok.n, d.judge_ok.d) == (1, 1) and (d.held.n, d.held.d) == (0, 1)


def test_protocol_2_attempts_keep_choosing_the_basis(bank):
    """以前出的题（protocol 2）判断满足要求的照旧选依据，不追问。"""
    with db.session_scope() as s:
        a = attempt_for(s, "max-plain-judge")
        a.protocol = 2
        quiz.judge2(s, a, False)
        assert a.state == "locate" and "challenge" not in quiz.view(s, a)
        quiz.locate(s, a, [2])
        assert a.outcome == "right_judge" and quiz.view(s, a)["result"]["lines"] == [2]


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
        assert len(pending) == 1 and pending[0].protocol == quiz.PROTOCOL


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

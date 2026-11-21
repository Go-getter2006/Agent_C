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


def _only_bank_skills_due(s):
    """演示环境没有大模型：只让题库里有题的复习项到期（先出哪项是随机抽的，抽到哪项都要出得了题）。"""
    import datetime as dt

    bank = set(s.scalars(select(m.QuizItem.skill_code).where(m.QuizItem.verified.is_(True))).all())
    for item in s.scalars(select(m.ReviewItem).where(m.ReviewItem.user_id == UID)).all():
        if item.skill_code not in bank:
            item.due_at = db.now() + dt.timedelta(days=30)


def test_review_page_prepares_one_ready_question(demo):
    with db.session_scope() as s:
        _only_bank_skills_due(s)
        assert review.ready_count(s, UID, "C") == 0
        status = review.prepare(s, UID, "C")
        assert status == "ready" and review.ready_count(s, UID, "C") == 1
        assert review.prepare(s, UID, "C") == "ready" and review.ready_count(s, UID, "C") == 1   # 不重复备题
        pending = s.scalars(select(m.QuizAttempt).where(m.QuizAttempt.reason == "review",
                                                        m.QuizAttempt.state != "done")).all()
        assert len(pending) == 1 and pending[0].protocol == quiz.PROTOCOL


def test_one_item_that_cannot_be_made_does_not_block_the_rest(demo, monkeypatch):
    """抽到的一项这会儿出不了题：从剩下的里再抽，不让一项卡住全部；每一次抽样都记在决策记录里。"""
    from app import quizgen

    with db.session_scope() as s:
        due = review.due_items(s, UID, course="C", candidates=True)
        assert len(due) >= 2
        bad = due[0].skill_code
        bank = s.scalars(select(m.QuizItem).where(m.QuizItem.verified.is_(True))).first()
        assert bank is not None
        monkeypatch.setattr(quizgen, "generate", lambda s_, u, skill, *a, **k: None if skill == bad else bank)
        # 让第一次抽样一定抽到排在最前、出不了题的那一项（第一个数最小），之后随便
        calls = iter([0.0] + [0.5] * 100)
        monkeypatch.setattr(review._rng, "betavariate", lambda a, b: next(calls))
        assert review.prepare(s, UID, "C") == "ready"
        pending = s.scalars(select(m.QuizAttempt).where(m.QuizAttempt.reason == "review",
                                                        m.QuizAttempt.state != "done")).all()
        assert len(pending) == 1 and s.get(m.ReviewItem, pending[0].review_item_id).skill_code != bad
        pick = s.scalars(select(m.DecisionLog).where(m.DecisionLog.kind == "review_pick")
                         .order_by(m.DecisionLog.id.desc())).first()
        assert len(pick.context["draws"]) >= 2 and pick.context["missed"]


def test_challenge_is_randomized_and_recorded(bank, monkeypatch):
    """判断“满足要求”以后追不追问是随机的（config.QUIZ['challenge_p']），每一次都记进决策记录，记真实概率。
    不追问的直接出反馈；坚持率、改判率只算追问了的。"""
    from app import config, diagnosis

    monkeypatch.setitem(config.QUIZ, "challenge_p", 0.5)
    with db.session_scope() as s:
        monkeypatch.setattr(quiz._challenge_rng, "random", lambda: 0.9)     # 0.9 ≥ 0.5：这次不追问
        a = attempt_for(s, "max-plain-judge")
        quiz.judge2(s, a, False)
        assert a.state == "done" and a.outcome == "right_judge" and "challenge" not in quiz.view(s, a)
        assert kinds(a) == ["prepared", "judge", "no_challenge", "feedback"]
        log = s.scalars(select(m.DecisionLog).where(m.DecisionLog.kind == "challenge")).one()
        assert (log.chosen, log.probability, log.rule, log.ref["attempt"]) == ("none", 0.5, "challenge-random-1", a.id)

        monkeypatch.setattr(quiz._challenge_rng, "random", lambda: 0.1)     # 这次追问
        b = attempt_for(s, "max-plain-judge")
        quiz.judge2(s, b, False)
        assert b.state == "challenge"
        logs = s.scalars(select(m.DecisionLog).where(m.DecisionLog.kind == "challenge").order_by(m.DecisionLog.id)).all()
        assert (logs[-1].chosen, logs[-1].probability) == ("challenge", 0.5)
        quiz.challenge_reply(s, b, keep=True)
        d = diagnosis.count([(a, s.get(m.QuizItem, a.item_id)), (b, s.get(m.QuizItem, b.item_id))])["C06.03"]
        assert (d.held.n, d.held.d) == (1, 1)                              # 没追问的那次不进坚持率

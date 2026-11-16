"""掌握程度算法：用附件一 8.6 的例子逐行核对。"""
from __future__ import annotations

import datetime as dt

from app import db, mastery
from app import models as m
from app.taxonomy import LEVEL_BASIC, LEVEL_MASTERED, LEVEL_NONE, LEVEL_WEAK

SKILL = "C06.03.02"
UID = "2500000001"


def label():
    return m.ProblemLabel(server_id=1, problem_id=1, primary_skill=SKILL, secondary_skills=[])


def state(s, at):
    return mastery.skill_states(s, UID, at=at, decay=False).get(SKILL, mastery.SkillState(SKILL))


def test_example_8_6(database):
    t0 = dt.datetime(2026, 11, 1, tzinfo=db.UTC)
    day = dt.timedelta(days=1)
    with db.session_scope() as s:
        st = state(s, t0)
        assert round(st.p, 2) == 0.50 and st.level == LEVEL_NONE

        # 「成绩最高分」卡住，给了示例代码后通过
        mastery.record_stuck(s, UID, SKILL, 1, "stuck:1", t0)
        mastery.record_pass(s, UID, label(), "pass:1", t0, earlier_attempts=2, help_level="H3")
        st = state(s, t0)
        assert round(st.p, 2) == 0.33 and st.level == LEVEL_WEAK

        # 5 天后「最低气温」再次卡住；靠挖空补上后通过
        t1 = t0 + 5 * day
        mastery.record_stuck(s, UID, SKILL, 2, "stuck:2", t1)
        mastery.record_pass(s, UID, label(), "pass:2", t1, earlier_attempts=1, help_level="H2")
        st = state(s, t1)
        assert round(st.p, 2) == 0.26 and st.level == LEVEL_WEAK

        # 3 天后回访题（FIX）第一次就改对，很有把握
        t2 = t1 + 3 * day
        mastery.record_quiz(s, UID, SKILL, "right", "很有把握", "revisit", "quiz:1", ts=t2)
        assert round(state(s, t2).p, 2) == 0.44

        # 同一天变式题（JUDGE）答对，很有把握
        mastery.record_quiz(s, UID, SKILL, "right_judge", "很有把握", "revisit", "quiz:2", ts=t2)
        assert round(state(s, t2).p, 2) == 0.55

        # 连续两道都很有把握地答对 → 奖励
        assert mastery.record_streak_bonus(s, UID, SKILL, "bonus:1", t2)
        st = state(s, t2)
        assert round(st.p, 3) == 0.598 and st.level == LEVEL_WEAK
        assert not mastery.record_streak_bonus(s, UID, SKILL, "bonus:2", t2 + day), "7 天内最多奖励一次"

        # 之后一道新题一次独立通过
        t3 = t2 + day
        mastery.record_pass(s, UID, label(), "pass:3", t3, earlier_attempts=0, help_level="NA")
        st = state(s, t3)
        assert round(st.p, 2) == 0.64 and st.level == LEVEL_BASIC


def test_levels_and_adjustments(database):
    now = db.now() - dt.timedelta(hours=1)
    with db.session_scope() as s:
        # 选“没把握”却答对：打五折；选“很有把握”却答错：乘 1.5
        mastery.record_quiz(s, UID, "C03.01.01", "right_judge", "没把握", "after_pass", "q:a", ts=now)
        mastery.record_quiz(s, UID, "C03.03.01", "wrong_judge", "很有把握", "after_pass", "q:b", ts=now)
        # 把握数据不可靠的学生不做调整
        mastery.record_quiz(s, UID, "C05.05.01", "right_judge", "没把握", "after_pass", "q:c",
                            confidence_reliable=False, ts=now)
        # 第二次才改对：会 0.5、不会 0.5
        mastery.record_quiz(s, UID, "C05.04.03", "right_second", "一半", "after_pass", "q:d", ts=now)
        st = mastery.skill_states(s, UID, decay=False)
        assert st["C03.01.01"].s == 0.5 and st["C03.03.01"].f == 1.5 and st["C05.05.01"].s == 1.0
        assert (st["C05.04.03"].s, st["C05.04.03"].f) == (0.5, 0.5)
        assert st["C03.01.01"].level == LEVEL_NONE  # 证据不到 1
        # 重复记同一个来源：不会重复计
        assert not mastery.add_evidence(s, UID, "C03.01.01", "quiz_judge_right", True, 1.0, "q:a", now)

        # 已掌握：p ≥ 0.8、证据量 ≥ 3、最近一次是独立成功
        lab = m.ProblemLabel(server_id=1, problem_id=9, primary_skill="C04.01.01", secondary_skills=["C04.02.01"])
        for i in range(8):
            mastery.record_pass(s, UID, lab, f"p:{i}", now, 0, "NA")
        st = mastery.skill_states(s, UID, decay=False)
        assert st["C04.01.01"].level == LEVEL_MASTERED
        assert st["C04.02.01"].s == 8 * 0.5  # 次技能按比例折算
        mastery.record_pass(s, UID, lab, "p:h3", now + dt.timedelta(seconds=1), 3, "H3")
        assert mastery.skill_states(s, UID, decay=False)["C04.01.01"].level == LEVEL_BASIC, "最近一次不是独立成功"


def test_decay_halves_in_about_35_days(database):
    now = db.now()
    with db.session_scope() as s:
        mastery.add_evidence(s, UID, SKILL, "pass_first_try", True, 1.0, "old", now - dt.timedelta(days=35), True)
        st = mastery.skill_states(s, UID, at=now)[SKILL]
        assert 0.47 < st.s < 0.52
        assert st.level != LEVEL_NONE, "练过的技能不会因为时间久了变回“还没练到”"


def test_kp_level_is_evidence_weighted(database):
    now = db.now()
    with db.session_scope() as s:
        mastery.add_evidence(s, UID, "C06.03.02", "stuck_first", False, 3.0, "a", now)
        mastery.add_evidence(s, UID, "C06.03.03", "pass_first_try", True, 1.0, "b", now, True)
        kp = mastery.kp_states(s, UID)["C06.03"]
        p1, p2 = 1 / 5, 2 / 3
        assert abs(kp.p - (3 * p1 + 1 * p2) / 4) < 1e-9 and kp.level == LEVEL_WEAK

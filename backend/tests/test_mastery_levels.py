"""掌握程度：会话占一部分，逐渐改对、一次做对也是一部分；不能几乎人人“待加强”。

- 交两次没过、后来自己改对：基本掌握（以前算待加强）；
- 一直没过：待加强；改对过又犯：待加强；
- 错在别的技能上、后来改对：改对本身算会；
- 题目还没有大模型标注：按 XDOJ 的标签兜底标注，做对的题照样记证据；以前没记上的补记；
- 教师端：学生列表有掌握分，学生明细、知识图谱节点有分数和一句话，节点详情有全班一句、每个学生一句；
- 学生端：自己每个知识点一句话，没有分数。
"""
from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, kpdesc, mastery, pipeline
from app import models as m
from app.labeling import problems
from app.taxonomy import LEVEL_BASIC, LEVEL_MASTERED, LEVEL_WEAK
from app.web.app import create_app

UID = "2599000077"
SKILL = "C06.03.03"


def _sub(s, sid: int, pid: int, judgement: str, t: dt.datetime, uid: str = UID) -> m.Submission:
    sub = m.Submission(server_id=1, submission_id=sid, user_id=uid, problem_id=pid, judgement=judgement,
                       judgement_text=judgement, submit_time=t, detail_status="done")
    s.add(sub)
    s.flush()
    return sub


def _setup(s):
    s.add(m.Server(id=1, code="s1", name="测试", course="C", base_url=""))
    s.add(m.Student(user_id=UID, name="测试生"))
    s.flush()


def _fail(s, sid, pid, t, skill=SKILL):
    sub = _sub(s, sid, pid, "WA", t)
    mastery.record_failed_submission(s, sub, skill)


def _pass(s, sid, pid, t, earlier, skills=(SKILL,)):
    _sub(s, sid, pid, "AC", t)
    label = m.ProblemLabel(server_id=1, problem_id=pid, version=1, is_current=True, brief="",
                           primary_skill=skills[0], secondary_skills=list(skills[1:]), source="seed")
    mastery.record_pass(s, UID, label, f"pass:1:{sid}", t, earlier, "NA")


def _level(s, skill=SKILL):
    return mastery.skill_states(s, UID)[skill].level


def test_fixing_it_yourself_is_not_weak(database):
    t = db.now() - dt.timedelta(hours=3)
    with db.session_scope() as s:
        _setup(s)
        _fail(s, 1, 100, t)
        _fail(s, 2, 100, t + dt.timedelta(minutes=5))
        assert _level(s) == LEVEL_WEAK                                 # 交了两次还没过：待加强
        _pass(s, 3, 100, t + dt.timedelta(minutes=10), earlier=2)
        assert _level(s) == LEVEL_BASIC                                # 自己改对了：基本掌握（以前是待加强）


def test_first_try_passes_reach_mastered(database):
    t = db.now() - dt.timedelta(hours=3)
    with db.session_scope() as s:
        _setup(s)
        for i in range(3):
            _pass(s, 10 + i, 200 + i, t + dt.timedelta(minutes=i), earlier=0)
        assert _level(s) == LEVEL_MASTERED


def test_mistake_on_another_skill_fixed_counts_as_knowing_it(database):
    """题考的是最值，错在 printf 格式上，后来改对了：printf 这个技能算会（不是待加强）。"""
    t = db.now() - dt.timedelta(hours=3)
    with db.session_scope() as s:
        _setup(s)
        _fail(s, 1, 100, t, skill="C01.02.01")
        _pass(s, 2, 100, t + dt.timedelta(minutes=5), earlier=1)
        st = mastery.skill_states(s, UID)["C01.02.01"]
        assert st.level == LEVEL_BASIC and st.s > 0


def test_mistake_fixed_in_the_next_submission_counts_before_passing(database):
    """缺分号改掉了（下一次编译过了，错在算法上）：语法那个技能算改对了；算法那个还没改对，待加强。"""
    t = db.now() - dt.timedelta(hours=3)
    with db.session_scope() as s:
        _setup(s)
        _fail(s, 1, 100, t, skill="C01.01.01")
        s.add(m.SubmissionLabel(server_id=1, submission_id=1, error_code="SY01", skill_code="C01.01.01",
                                role="primary", confidence=0.9))
        _fail(s, 2, 100, t + dt.timedelta(minutes=5))
        s.add(m.SubmissionLabel(server_id=1, submission_id=2, error_code="TY09", skill_code=SKILL,
                                role="primary", confidence=0.9))
        s.flush()
        assert _level(s, "C01.01.01") == LEVEL_BASIC
        assert _level(s) == LEVEL_WEAK
        recs = kpdesc.records(s, [UID], "C")[UID]
        assert "1 道改对了" in kpdesc.describe(recs["C01.01"])


def test_same_mistake_again_after_fixing_is_weak(database):
    t = db.now() - dt.timedelta(hours=3)
    with db.session_scope() as s:
        _setup(s)
        _fail(s, 1, 100, t)
        _pass(s, 2, 100, t + dt.timedelta(minutes=5), earlier=1)
        _pass(s, 3, 101, t + dt.timedelta(minutes=10), earlier=0)
        assert _level(s) == LEVEL_BASIC
        _fail(s, 4, 102, t + dt.timedelta(minutes=20))                  # 新的题上又错，还没改对
        assert _level(s) == LEVEL_WEAK
        _pass(s, 5, 102, t + dt.timedelta(minutes=25), earlier=1)      # 又改对了
        assert _level(s) == LEVEL_BASIC


# ------------------------------------------------------------ 没有大模型标注：按标签兜底
def test_tag_fallback_label_and_backfill(ctx, fake):
    from app.xdoj import sync_meta
    from app.xdoj.daemon import Daemon

    fake.problems[1]["tags"] = [{"name": "一维数组"}, {"name": "循环"}]
    sync_meta.sync_metadata(ctx)
    Daemon([ctx]).drain()
    with db.session_scope() as s:
        for row in s.scalars(select(m.ProblemLabel)).all():   # 当作大模型从来没标过
            s.delete(row)
        for row in s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.event_type.like("pass%"))).all():
            s.delete(row)
    with db.session_scope() as s:
        assert problems.label_assigned(s) == 2
        label = problems.tag_skills(["一维数组", "循环"])
        assert label == ["C06.02.01", "C05.03.01"]
        cur = s.scalar(select(m.ProblemLabel).where(m.ProblemLabel.problem_id == 407, m.ProblemLabel.is_current.is_(True)))
        assert cur.source == "tags" and cur.primary_skill == "C06.02.01"
        assert pipeline.backfill_passes(s) >= 1                       # 以前没记上的通过补记
        assert pipeline.backfill_passes(s) == 0
        assert s.scalar(select(m.SkillEvidence).where(m.SkillEvidence.event_type.like("pass%"))) is not None


# ------------------------------------------------------------ 一句话描述
def test_descriptions():
    rec = kpdesc.Record(problems=4, first_try=2, fixed_tries=[3], unsolved=1, talks=[2, 3])
    rec.errors["变量没给合适的初值"] += 2
    text = kpdesc.describe(rec)
    assert text == "做过 4 道：2 道一次就做对，1 道交了 3 次改对，1 道还没过；对话里能讲清楚为什么；常错：变量没给合适的初值。"
    assert "智能体评过 2 次" in kpdesc.describe(rec, teacher=True)
    assert kpdesc.describe(None) == "还没有做过这方面的题"
    whole = kpdesc.describe_class([rec, kpdesc.Record(problems=1, first_try=1), kpdesc.Record()], 3)
    assert whole.startswith("全班 3 人里 2 人做过，一共 5 题次：一次做对 60%，改对 20%，还没过 20%")
    assert "常错：变量没给合适的初值" in whole


def test_teacher_sees_scores_and_descriptions(demo):
    sid = demo["server_id"]
    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"loginName": "demo-teacher", "password": "test-pass"})
        cls = c.get("/api/teacher/classes").json()
        key = (cls.get("classes") or cls)[0]["key"]
        rows = c.get(f"/api/teacher/classes/{key}/students").json()["students"]
        lin = next(r for r in rows if r["userId"] == "2500000001")
        assert isinstance(lin["score"], int) and 0 <= lin["score"] <= 100
        detail = c.get("/api/teacher/students/2500000001").json()
        kp = next(k for k in detail["knowledge"] if k["code"] == "C06.03")
        assert isinstance(kp["score"], int) and kp["text"].startswith("做过")
        graph = c.get(f"/api/teacher/classes/{key}/knowledge-map?student=2500000001").json()
        node = next(n for n in graph["nodes"] if n["code"] == "C06.03")
        assert isinstance(node["score"], int) and node["student"]["text"].startswith("做过")
        one = c.get(f"/api/teacher/classes/{key}/knowledge-map/C06.03").json()
        assert one["summary"].startswith("全班") and len(one["students"]) == len(graph["students"])
        assert all("text" in r and "score" in r for r in one["students"])
        assert one["students"][0]["level"] == "待加强"                 # 待加强的排在前面


def test_student_sees_own_description_without_numbers(demo):
    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"loginName": "2500000001", "password": "test-pass"})
        prog = c.get("/api/progress").json()
        items = [i for g in prog["knowledge"] for i in g["items"] if i["basis"].startswith("做过")]
        assert items and all("score" not in i for i in items)
        assert any("一次就做对" in i["basis"] or "改对" in i["basis"] for i in items)



def test_repeated_errors_keep_more_weight_after_correction(database):
    from types import SimpleNamespace

    now = db.now()
    skill = "C06.03.02"
    label = SimpleNamespace(primary_skill=skill, secondary_skills=[])
    with db.session_scope() as s:
        for uid, misses, reliability in (("single", 1, 1.0), ("repeat", 2, 1.0), ("uncertain", 0, 0.4)):
            for sid in range(1, misses + 1):
                failed = m.Submission(user_id=uid, server_id=1, problem_id=201, submission_id=sid + misses * 10,
                                      judgement="WA", submit_time=now)
                s.add(failed)
                mastery.record_failed_submission(s, failed, skill)
            sid = 100 + misses
            s.add(m.Submission(user_id=uid, server_id=1, problem_id=201, submission_id=sid,
                               judgement="AC", submit_time=now))
            mastery.record_pass(s, uid, label, f"pass:1:{sid}", now, misses, "NA", reliability)
        single = mastery.skill_states(s, "single", at=now, decay=False, model="v2")[skill]
        repeat = mastery.skill_states(s, "repeat", at=now, decay=False, model="v2")[skill]
        uncertain = mastery.skill_states(s, "uncertain", at=now, decay=False, model="v2")[skill]
        assert repeat.score < single.score and repeat.f > single.f > 0
        assert uncertain.f == 0 and uncertain.s > 0 and not uncertain.last_independent_success
        assert uncertain.discounted == 1

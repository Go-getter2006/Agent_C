"""判断、定位、修改三维（界面改造方案第 5 节）：用已知答案的合成作答回算图 9，六种情形各有正确的标签和分母。

图 9：条件边界 判断 3/4、定位 1/2、修改 1/2；判断拆分：错题识别 2/2、正确代码识别 1/2。
六种情形：未进入、未作答、不适用、提示后才改对、系统失败、历史缺定位。
"""
from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from app import db, diagnosis
from app import models as m
from app.web.app import create_app

UID = "2599000088"
KP_SKILL = "C06.03.02"     # 求最值
OTHER_SKILL = "C05.03.01"  # for
_n = [0]


def item(s, has_bug: bool, skill: str = KP_SKILL) -> m.QuizItem:
    _n[0] += 1
    row = m.QuizItem(code=f"t{_n[0]}", quiz_type="FIX", skill_code=skill, stem="统计及格人数",
                     shown_code="int count = 0;\nfor (int i = 0; i < n; ++i) {\n    count += score[i] > 60;\n}\nreturn count;",
                     template="@@", has_bug=has_bug, bug_line=3 if has_bug else None, bug_lines=[3] if has_bug else [],
                     checks=[{"input": "1", "expected": "1"}], verified=True)
    s.add(row)
    s.flush()
    return row


def attempt(s, it: m.QuizItem, **kw) -> m.QuizAttempt:
    base = dict(user_id=UID, item_id=it.id, quiz_type="FIX", skill_code=it.skill_code, reason="review", protocol=2,
                state="done", events=[], first_lines=[], tries=0, hints=0, sample_runs=0)
    base.update(kw)
    a = m.QuizAttempt(**base)
    s.add(a)
    s.flush()
    return a


def seed(s):
    s.add(m.Student(user_id=UID, name="测试生"))
    bug1, bug2, ok1, ok2 = item(s, True), item(s, True), item(s, False), item(s, False)
    # 1 有错 → 判断有问题 → 选中第 3 行 → 第一稿（没看提示、没运行）就改对
    attempt(s, bug1, judged_has_bug=True, judge_correct=True, first_lines=[3], locate_hit=True, tries=1,
            first_fix="x", first_fix_pass=True, first_fix_clean=True, outcome="right", correct=True)
    # 2 有错 → 判断有问题 → 选错了行 → 第一稿没过 → 看了提示才改对（提示后才改对）
    attempt(s, bug2, judged_has_bug=True, judge_correct=True, first_lines=[1], locate_hit=False, tries=2, hints=1,
            first_fix="y", first_fix_pass=False, first_fix_clean=True, outcome="right_second", correct=True)
    # 3 正确 → 判断满足要求 → 选依据
    attempt(s, ok1, judged_has_bug=False, judge_correct=True, first_lines=[3], outcome="right_judge", correct=True)
    # 4 正确 → 判断有问题（误报）→ 原样提交（不适用：不计入定位、修改）
    attempt(s, ok2, judged_has_bug=True, judge_correct=False, first_lines=[2], tries=1, first_fix="z",
            first_fix_pass=True, first_fix_clean=True, outcome="wrong_judge", correct=False,
            events=[{"kind": "submit", "misjudge": "kept"}])
    # 另一个知识点：其余几种情形
    b3, b4, b5, b6 = item(s, True, OTHER_SKILL), item(s, True, OTHER_SKILL), item(s, True, OTHER_SKILL), item(s, True, OTHER_SKILL)
    # 未进入：有错却判断满足要求，定位和修改都没进
    attempt(s, b3, judged_has_bug=False, judge_correct=False, first_lines=[1], outcome="wrong_judge", correct=False)
    # 未作答：出了题还没判断
    attempt(s, b4, state="judge", events=[{"kind": "viewed"}])
    # 系统失败：进了修改，提交时运行环境不可用，不算一次提交
    attempt(s, b5, state="edit", judged_has_bug=True, judge_correct=True, first_lines=[3], locate_hit=True, tries=0)
    # 历史缺定位：以前的作答协议，有判断、没有独立选行
    attempt(s, b6, protocol=1, judged_has_bug=True, judge_correct=True, changed_lines=[3], tries=1,
            outcome="right", correct=True)


def test_figure_nine_and_the_six_cases(database):
    with db.session_scope() as s:
        seed(s)
        d = diagnosis.by_student(s, [UID], "C")[UID]
        k = d["C06.03"]
        assert (diagnosis.frac(k.judge), diagnosis.frac(k.locate), diagnosis.frac(k.edit)) == ("3/4", "1/2", "1/2")
        assert (diagnosis.frac(k.judge_bug), diagnosis.frac(k.judge_ok)) == ("2/2", "1/2")
        assert k.shown == 4 and k.after_help == 1 and k.locate_entered == 2 and k.legacy == 0

        o = d["C05.03"]
        assert o.shown == 4 and o.open == 2                                   # 未作答、系统失败都还没做完
        assert diagnosis.frac(o.judge) == "2/3"                               # 未作答不进判断的分母
        assert diagnosis.frac(o.locate) == "1/1"                              # 未进入、历史缺定位都不进定位分母
        assert diagnosis.frac(o.edit) == "尚无记录"                            # 系统失败没有算一次提交
        assert o.legacy == 1 and o.locate_entered == 1

        t = {x["code"]: x for x in diagnosis.teacher_rows(s, d)}
        r = t["C06.03"]
        assert r["judge"] == {"n": 3, "d": 4} and r["locate"] == {"n": 1, "d": 2} and r["edit"] == {"n": 1, "d": 2}
        ev = r["latest"]                                            # 最近一次：误报那一次
        assert [st["value"] for st in ev["steps"]] == ["发现问题", "选中第 2 行", "原样提交"]
        assert t["C05.03"]["counts"]["legacy"] == 1


def test_evidence_chain_after_hint(database):
    with db.session_scope() as s:
        s.add(m.Student(user_id=UID, name="测试生"))
        bug = item(s, True)
        a = attempt(s, bug, judged_has_bug=True, judge_correct=True, first_lines=[3], locate_hit=True, tries=2, hints=1,
                    first_fix="y", first_fix_pass=False, first_fix_clean=True, outcome="right_second", correct=True,
                    events=[{"kind": "judge"}, {"kind": "locate"}, {"kind": "submit"}, {"kind": "hint"}, {"kind": "submit"}])
        ev = diagnosis.evidence(s, a, bug)
        assert [(x["label"], x["value"]) for x in ev["steps"]] == [("判断", "发现问题"), ("定位", "选中第 3 行"), ("修改", "提示后通过")]
        assert ev["steps"][1]["note"] == "首次选择命中评分范围"
        assert ev["steps"][2]["note"] == "首次独立修改未通过，查看提示后再次提交"
        assert ev["code"]["picked"] == [3] and ev["code"]["start"] == 2 and len(ev["code"]["lines"]) == 3
        assert ev["order"] == ["judge", "locate", "submit", "hint", "submit"]


def test_teacher_pages_show_dimensions_and_student_sees_mastery(demo):
    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"loginName": "2500000001", "password": "test-pass"})
        progress = c.get("/api/progress").json()
        assert "dims" not in progress and progress["summary"]["levels"]
        c.post("/api/auth/logout")
        c.post("/api/auth/login", json={"loginName": "demo-teacher", "password": "test-pass"})
        detail = c.get("/api/teacher/students/2500000001").json()
        assert isinstance(detail["diagnosis"], list)
        cls = c.get("/api/teacher/classes").json()
        key = (cls.get("classes") or cls)[0]["key"]
        node = c.get(f"/api/teacher/classes/{key}/knowledge-map/C06.03").json()
        assert set(node["diagnosis"]) >= {"judge", "locate", "edit", "shown"} and isinstance(node["studentDims"], dict)


def test_sample_run_before_the_first_draft_still_counts_as_a_fix(database):
    """dims-2：先运行公开样例再交的第一稿也算修改（运行样例是自己测，不是帮助）；没运行就交的另列；
    看了提示才交的第一稿不算。没有数的格子说清楚为什么。"""
    with db.session_scope() as s:
        bug = item(s, True)
        ev = lambda *kinds: [{"kind": k} for k in kinds]   # noqa: E731
        attempt(s, bug, judged_has_bug=True, judge_correct=True, first_lines=[3], locate_hit=True, tries=1,
                first_fix_pass=True, first_fix_clean=False, sample_runs=1, outcome="right",
                events=ev("viewed", "judge", "locate", "sample", "submit", "feedback"))
        attempt(s, bug, judged_has_bug=True, judge_correct=True, first_lines=[3], locate_hit=True, tries=1,
                first_fix_pass=True, first_fix_clean=True, outcome="right",
                events=ev("viewed", "judge", "locate", "submit", "feedback"))
        attempt(s, bug, judged_has_bug=True, judge_correct=True, first_lines=[3], locate_hit=True, tries=2,
                first_fix_pass=False, first_fix_clean=False, hints=1, outcome="right_second",
                events=ev("viewed", "judge", "locate", "hint", "submit", "submit", "feedback"))
        d = diagnosis.by_student(s, [UID], "C")[UID]["C06.03"]
        assert (d.edit.n, d.edit.d) == (2, 2) and (d.edit_clean.n, d.edit_clean.d) == (1, 1) and d.after_help == 1
        why = diagnosis.why_empty(d)
        assert why == {"judgeOk": "还没做过本来正确的代码"}


def test_empty_cells_say_why(database):
    with db.session_scope() as s:
        ok, bug = item(s, False), item(s, True)
        attempt(s, ok, judged_has_bug=False, judge_correct=True, events=[{"kind": "viewed"}])
        d = diagnosis.by_student(s, [UID], "C")[UID]["C06.03"]
        assert diagnosis.why_empty(d)["locate"] == "还没做过有错的代码"
        attempt(s, bug, judged_has_bug=False, judge_correct=False, events=[{"kind": "viewed"}])
        d = diagnosis.by_student(s, [UID], "C")[UID]["C06.03"]
        why = diagnosis.why_empty(d)
        assert why["locate"] == why["edit"] == "没看出有错，没进入定位" and d.missed == 1
        row = diagnosis.teacher_rows(s, {"C06.03": d})[0]
        assert row["why"]["locate"] and row["editClean"] is None

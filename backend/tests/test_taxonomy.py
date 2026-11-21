"""知识点表（0.2）：一个知识点只讲一件事；每个知识点都在图上（和别的知识点至少有一条先修边）；
技能表改过以后，用到意思变窄的技能的旧标注重标一次，以前的“通过”证据跟着新标注走。"""
from __future__ import annotations

import json

from sqlalchemy import select

from app import config, db, llm, mastery
from app import models as m
from app.labeling import problems
from app.taxonomy import ERRORS, KNOWLEDGE_POINTS, SKILLS, kp_of_skill, kp_rank, skills_of_kp
from app.taxonomy.ds import DS_ERRORS
from app.taxonomy.prereq import has_cycle, has_kp_cycle, kp_edges


def test_every_knowledge_point_is_one_concept_with_skills():
    assert all(kp_of_skill(c) in KNOWLEDGE_POINTS for c in SKILLS)
    assert all(skills_of_kp(k) for k in KNOWLEDGE_POINTS)
    # 名称里不再把几件事用顿号并在一起（以前的“最值、求和、统计”“累加、累乘、计数”）
    assert not [n for n in KNOWLEDGE_POINTS.values() if "、" in n]
    assert KNOWLEDGE_POINTS["C06.03"] == "求最值" and kp_of_skill("C06.03.01") == "C05.05"   # 数组求和归到累加
    assert kp_of_skill("C05.05.02") == "C05.09" and KNOWLEDGE_POINTS["C05.09"] == "计数"
    # 显示按教学顺序（表的顺序），不按编码：数组下标（C06.07）排在求最值（C06.03）前面
    assert kp_rank("C06.07") < kp_rank("C06.03") < kp_rank("C06.08")
    # 错误类型指向的主知识点都在表里
    assert all(kp in KNOWLEDGE_POINTS for _n, kp, _s in ERRORS.values() if kp)
    assert all(kp in KNOWLEDGE_POINTS for _n, kp, _s in DS_ERRORS.values() if kp)


def test_no_isolated_knowledge_point_and_no_cycle():
    inside = [(a, b) for a, b, _n, cross in kp_edges() if not cross]
    touched = {x for e in inside for x in e}
    assert [k for k in KNOWLEDGE_POINTS if k not in touched] == []
    assert not has_cycle() and not has_kp_cycle()   # 汇总到知识点也不能成环（教师端的分层画法要求）


def _problem_with_old_label(s, sid: int, pid: int) -> m.ProblemLabel:
    s.add(m.Server(id=sid, code="t", name="测试", course="C"))
    s.add(m.Student(user_id="u1", name="甲"))
    s.flush()
    s.add(m.Problem(server_id=sid, problem_id=pid, title="统计及格人数"))
    s.flush()
    s.add(m.ProblemText(server_id=sid, problem_id=pid, extracted_text="输入 n 个成绩，统计及格的人数。"))
    s.flush()
    label = problems.save_problem_label(s, sid, pid, "统计及格人数", "C06.03.01", ["C06.02.01"], [], "llm")
    label.taxonomy_version = "0.1"
    s.add(m.Submission(server_id=sid, submission_id=9001, user_id="u1", problem_id=pid, judgement="AC"))
    s.flush()
    mastery.record_pass(s, "u1", label, f"pass:{sid}:9001", db.now(), 0, "NA")
    return label


def test_relabel_after_the_taxonomy_change_moves_the_evidence(database, monkeypatch):
    """C06.03.01 以前是“遍历数组求和或计数”，0.2 只管求和：用到它的旧标注重标一次，以前的通过记到新技能上。"""
    def backend(messages, *_a):
        return json.dumps({"brief": "统计及格的人数", "primary": "C05.05.02", "secondary": ["C06.02.01"],
                           "testcases": []}, ensure_ascii=False), 10, 10

    monkeypatch.setattr(problems.pdftext, "prepare", lambda *a: None)   # 不交给大模型整理题面
    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            old = _problem_with_old_label(s, 1, 77)
            before = {r.skill_code: r.weight for r in s.scalars(select(m.SkillEvidence)).all()}
            assert set(before) == {"C06.03.01", "C06.02.01"}
            assert problems.relabel_changed(s, limit=5) == 1
            cur = s.scalars(select(m.ProblemLabel).where(m.ProblemLabel.problem_id == 77,
                                                         m.ProblemLabel.is_current.is_(True))).one()
            assert cur.id != old.id and cur.primary_skill == "C05.05.02" and cur.taxonomy_version == config.TAXONOMY_VERSION
            after = {r.skill_code: r for r in s.scalars(select(m.SkillEvidence)).all()}
            assert set(after) == {"C05.05.02", "C06.02.01"}                    # 跟着新标注走
            assert after["C05.05.02"].weight == before["C06.03.01"]           # 分量、事件、独立不变
            assert after["C05.05.02"].event_type == "pass_first_try" and after["C05.05.02"].independent
            assert problems.relabel_changed(s, limit=5) == 0                  # 只重标一次
    finally:
        llm.set_backend(None)


def test_labels_not_using_changed_skills_are_only_marked(database):
    with db.session_scope() as s:
        s.add(m.Server(id=2, code="t2", name="测试", course="C"))
        s.flush()
        label = problems.save_problem_label(s, 2, 5, "求最大值", "C06.03.02", [], [], "llm")
        label.taxonomy_version = "0.1"
        llm.set_backend(lambda *a: (_ for _ in ()).throw(AssertionError("不该调大模型")))
        try:
            assert problems.relabel_changed(s) == 0 and label.taxonomy_version == config.TAXONOMY_VERSION
        finally:
            llm.set_backend(None)

"""两门课共用小题、标注和计分流程，个人历史按课程分别使用。"""
import json
import random

import pytest
from sqlalchemy import select

from app import config, db, llm, mastery, models as m, quiz, quizgen
from app.labeling import labeler, problems

C_SKILL, D_SKILL = "C06.04.01", "D08.01.01"


def item(s, code, skill, bug=False):
    row = m.QuizItem(code=code, quiz_type="JUDGE", skill_code=skill, stem="判断代码",
                     shown_code="int x = 1;", template="int main(){}", has_bug=bug, verified=True)
    s.add(row)
    s.flush()
    return row


@pytest.mark.parametrize("course,skill,other", [("C", C_SKILL, D_SKILL), ("D", D_SKILL, C_SKILL)])
def test_quiz_planning_and_daily_limit_are_the_same_per_course(database, course, skill, other):
    with db.session_scope() as s:
        foreign = item(s, "other", other)
        right = item(s, "right", skill)
        item(s, "wrong", skill, True)
        for _ in range(config.QUIZ["daily_limit"]):
            s.add(m.QuizAttempt(user_id="u", item_id=foreign.id, skill_code=other, quiz_type="JUDGE",
                               confidence="很有把握", correct=False, created_at=db.now()))
        s.flush()
        plan = quizgen.make_plan(s, "u", skill, "after_pass", rng=random.Random(1))
        assert plan.window == 0 and plan.correct_ratio is None and not plan.overconfident
        assert quiz.can_start(s, "u", skill)
        assert not quiz.can_start(s, "u", other)
        # 外课历史都是正确代码，不应逼本课固定出错题。
        assert quiz.choose_item(s, "u", skill, random.Random(1)).has_bug is False
        for _ in range(config.QUIZ["daily_limit"]):
            s.add(m.QuizAttempt(user_id="u", item_id=right.id, skill_code=skill, quiz_type="JUDGE", created_at=db.now()))
        s.flush()
        assert not quiz.can_start(s, "u", skill)
        mastery.add_evidence(s, "u", skill, "pass_first_try", True, 1, f"pass:{course}", db.now(), independent=True)
        assert round(mastery.skill_state(s, "u", skill, model="v2").score * 100) == 67


def test_ds_old_c_skill_label_is_hidden_and_relabelled_without_changing_weight(database, monkeypatch):
    monkeypatch.setattr(problems.pdftext, "prepare", lambda *args: None)
    monkeypatch.setattr(problems, "reference_solution", lambda *args: "int main(void){return 0;}")
    calls = []

    def backend(messages, *_args):
        calls.append(messages)
        return json.dumps({"brief": "顺序查找一个元素", "primary": D_SKILL, "secondary": [], "testcases": []}), 10, 10

    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            s.add(m.Server(id=1, code="ds-test", course="D", name="数据结构", base_url="http://fake.test"))
            s.flush()
            s.add(m.Problem(server_id=1, problem_id=99573, title="顺序查找", category="查找"))
            s.add(m.ProblemText(server_id=1, problem_id=99573, extracted_text="查找给定的整数。"))
            s.flush()
            old = problems.save_problem_label(s, 1, 99573, "查找", C_SKILL, [], [], "tags")
            s.add(m.Submission(server_id=1, submission_id=99573, user_id="u", problem_id=99573,
                               judgement="AC", detail_status="done", submit_time=db.now()))
            s.flush()
            mastery.record_pass(s, "u", old, "pass:1:99573", db.now(), 0, "NA")
            assert labeler.current_problem_label(s, 1, 99573) is None
            assert problems.tag_label(s, 1, 99573) is None
            assert problems.relabel_changed(s) == 1
            new = labeler.current_problem_label(s, 1, 99573)
            assert new.primary_skill == D_SKILL
            ev = s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.source_key == "pass:1:99573")).one()
            assert ev.skill_code == D_SKILL and ev.weight == 1 and ev.independent
            assert "数据结构课程" in calls[-1][0]["content"]
    finally:
        llm.set_backend(None)

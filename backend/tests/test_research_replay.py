"""用不同的作答/抓取/标注时间验证逐条回算。"""
import datetime as dt
from types import SimpleNamespace

from app import db, models as m, research

T = dt.datetime(2025, 1, 1, tzinfo=db.UTC)
A, B = "C01.02.01", "C01.01.01"


def at(n):
    return T + dt.timedelta(minutes=n)


def test_paired_cutoff_revisions_and_coverage(database):
    with db.session_scope() as s:
        for pid, v, t, skill in [(1, 1, 0, A), (1, 2, 6, B), (2, 1, 0, A), (3, 1, 7, A)]:
            s.add(m.ProblemLabel(server_id=1, problem_id=pid, version=v, primary_skill=skill, created_at=at(t)))
        for pid, t, fetched in [(1, 1, 1), (2, 5, 5), (3, 7, 7), (4, 8, 10)]:
            s.add(m.Submission(server_id=1, user_id="one", submission_id=pid, problem_id=pid, submit_time=at(t),
                               fetched_at=at(fetched), judgement="AC"))
        s.add(m.AgentAssessment(user_id="one", course="C", skill_code=A, basis="chat", level=0, created_at=at(3)))
        s.add(m.ChatMessage(user_id="one", server_id=1, problem_id=2, role="agent", course="C", created_at=at(1)))
        s.add(m.ChatStep(user_id="one", message_id=1, server_id=1, problem_id=2, course_id="C", help_level="H3",
                         created_at=at(5)))  # 和首交同一时刻也不算之前
        s.add(m.ChatMessage(user_id="one", server_id=1, problem_id=1, role="student", course="C", created_at=at(0)))
        item = m.QuizItem(code="research-1", quiz_type="FIX", skill_code=A, has_bug=True, stem="test",
                          shown_code="test", template="test")
        s.add(item)
        s.flush()
        s.add(m.QuizAttempt(user_id="one", item_id=item.id, quiz_type="FIX", skill_code=A,
              protocol=3, created_at=at(2), judged_has_bug=True, judge_correct=True, locate_hit=False,
              events=[{"kind":"judge", "t":at(4).isoformat()}, {"kind":"locate", "t":at(6).isoformat()},
                      {"kind":"submit","t":at(10).isoformat(), "first":True, "ok":True}]))
    people = [SimpleNamespace(user_id=uid, name=uid) for uid in ["one", "two"]]
    with db.session_scope() as s:
        r = research.exam_report(s, people, 1, "C", at(9), at(12))
        x, y = r["paired"]
        assert x["helpObserved"] and x["maxHelp"] is None and x["suspicious"] is None
        assert x["estimates"] == [0.5]*3
        assert y["estimates"] == [2/3, 2/4, 3/5] and not y["helpObserved"]
        assert r["noEvidenceStudents"] == ["two"] and r["missingDialogue"] == r["missingQuiz"] == 1
        assert r["exclusions"]["noLabelAtSubmission"][0]["problemId"] == 3
        assert r["exclusions"]["resultUnavailableAtCutoff"][0]["problemId"] == 4
        rows = {v["skill"]: v for v in r["rows"]}
        assert rows[B]["evidenceCounts"] == [1,0,0] and rows[A]["evidenceCounts"] == [2,1,1]
        assert rows[A]["estimates"][2] == (1+2+0+.5)/(2+2+1+1)
        assert research.exam_report(s, people, 1, "C", at(20), at(9))["scores"] == r["scores"]
        assert all(x["interval"] is None for x in r["differences"])


def test_cluster_interval_repeats_with_same_data():
    loss = {"a": [50, 20, 10, 15], "b": [1, 0, 1, 0]}
    result = research.paired_brier(loss)
    assert result == research.paired_brier(loss)
    low, high = result["differences"][0]["interval"]
    assert low <= -0.2 and high >= 1


def test_randomized_followup_counts_missing_and_uses_next_unseen_item(database):
    from app import diagnosis

    with db.session_scope() as s:
        items = []
        for i in range(3):
            item = m.QuizItem(code=f"follow-{i}", quiz_type="FIX", skill_code=A, has_bug=True,
                              stem="test", shown_code="test", template="test")
            s.add(item)
            s.flush()
            items.append(item)
        for uid, arm in [("one", "challenge"), ("two", "none")]:
            def attempt(i, t, choice):
                events = [{"kind": "viewed", "state": "judge", "t": at(t).isoformat()}]
                if choice is not None:
                    events.append({"kind": "judge", "t": at(t).isoformat()})
                a = m.QuizAttempt(user_id=uid, item_id=items[i].id, quiz_type="FIX", skill_code=A, protocol=3,
                                 events=events, judged_has_bug=choice, judge_correct=choice, created_at=at(t))
                s.add(a)
                s.flush()
                return a
            original = attempt(0, 1, False)
            s.add(m.DecisionLog(user_id=uid, course="C", kind="challenge", rule="challenge-random-1",
                                ts=at(2), chosen=arm, probability=.5, ref={"attempt": original.id}))
            attempt(0, 3, True)  # 原题重复作答
            attempt(1, 4, True if arm == "challenge" else None)
            attempt(2, 5, True)  # 上一题没判断时也不拿更晚一题代替
        s.flush()
        report = diagnosis.research_flow(s, ["one","two"], "C")
        assert report["arms"]["challenge"] == dict(assigned=1, students=1, shownNext=1, answered=1, correct=1)
        assert report["arms"]["none"] == dict(assigned=1, students=1, shownNext=1, answered=0, correct=0)
        assert report["flow"]["shownBug"] == 8 and report["flow"]["located"] == 0


def test_nightly_stores_versions_available_at_first_submission(database):
    from app import graphmodel

    with db.session_scope() as s:
        s.add(m.Submission(server_id=1, user_id="one", submission_id=7, problem_id=10,
                           submit_time=at(5), fetched_at=at(5), judgement="AC"))
        for v, time, skill in [(1, 1, A), (2, 6, B)]:
            s.add(m.ProblemLabel(server_id=1, problem_id=10, version=v, primary_skill=skill,
                                 created_at=at(time), is_current=v == 2))
            s.add(m.MasteryParams(course="C", version=f"test-{v}", fitted_at=at(time)))
            s.add(m.FusionWeightsHistory(course="C", weights={"bias": 0.1*v}, fitted_at=at(time)))
        s.add(m.ChatStep(user_id="one", message_id=1, server_id=1, problem_id=10, course_id="C",
                         help_level="H1", created_at=at(3)))
        s.add(m.ChatStep(user_id="one", message_id=2, server_id=1, problem_id=10, course_id="C",
                         help_level="H3", created_at=at(6)))
        s.flush()
        assert graphmodel.log_predictions(s, "one") == 1
        s.flush()
        from sqlalchemy import select
        r = s.scalar(select(m.PredictionLog))
        assert r.skill_code == A and r.params_version == "test-1"
        assert r.provenance["labelVersion"] == 1 and not r.provenance["labelFallback"]
        assert r.provenance["fusionWeightsAt"] == at(1).isoformat()
        assert r.provenance["helpObserved"] and r.provenance["maxHelp"] == "H1"
        assert r.provenance["suspicious"] is None
        assert graphmodel.log_predictions(s, "one") == 0

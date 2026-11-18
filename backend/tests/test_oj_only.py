"""只在 OJ 上交题、从没问过智能体的学生，也要有完整的掌握分析：没过的提交是证据、改错过程是一个维度、
智能体看他怎么改代码来评“理解”。没问过问题不代表有问题——可能就是会，不需要问。"""
from __future__ import annotations

import json

from sqlalchemy import func, select

from app import assess, config, db, mastery, process, profile
from app import llm
from app import models as m


def _silent_students(s) -> list[str]:
    talked = set(s.scalars(select(m.ChatMessage.user_id).where(m.ChatMessage.role == "student")).all())
    return [u for u in s.scalars(select(m.Submission.user_id).distinct()).all() if u not in talked]


def test_failed_submissions_are_evidence_without_any_chat(demo):
    with db.session_scope() as s:
        silent = _silent_students(s)
        assert silent
        uid = max(silent, key=lambda u: s.scalar(select(func.count()).select_from(m.SkillEvidence).where(
            m.SkillEvidence.user_id == u, m.SkillEvidence.event_type == "submit_failed")) or 0)
        fails = s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.user_id == uid,
                                                        m.SkillEvidence.event_type == "submit_failed")).all()
        assert fails and all(not r.success for r in fails)
        prof = profile.kp_profile(s, uid, "C")
        assert prof and any(d.debugging is not None for d in prof.values())   # 改错维度只靠 OJ 提交
        assert all(d.understanding is None or d.n_assess for d in prof.values())
        rows = profile.teacher_view(s, uid, "C")
        assert rows and all(r["perf"] is not None or r["debugging"] is not None for r in rows)
        # v1（附件一的基线）不用这类证据
        v1 = mastery.skill_states(s, uid, model="v1")
        v2 = mastery.skill_states(s, uid, model="v2")
        assert any(st.f_raw > 0 for st in v2.values()) and set(v2) >= set(v1)


def test_failed_evidence_diminishes_per_problem(demo):
    with db.session_scope() as s:
        sub = s.scalars(select(m.Submission).where(m.Submission.judgement != "AC")).first()
        made = []
        for i in range(4):
            fake = m.Submission(server_id=sub.server_id, submission_id=9_000_000 + i, user_id="2599999999",
                                problem_id=sub.problem_id, submit_time=db.now(), judgement="WA")
            made.append(mastery.record_failed_submission(s, fake, "C06.02.03"))
        assert made == [True, True, False, False]           # 同一道题：0.5、0.25，再往后不记
        ws = s.scalars(select(m.SkillEvidence.weight).where(m.SkillEvidence.user_id == "2599999999")
                       .order_by(m.SkillEvidence.id)).all()
        assert ws == [0.5, 0.25]


def test_episode_scoring():
    def sub(i, j, t):
        x = m.Submission(server_id=1, submission_id=i, user_id="u", problem_id=1, judgement=j)
        x.submit_time = db.now().replace(microsecond=0) + __import__("datetime").timedelta(seconds=t)
        return x
    clean = process.Episode(1, 1, [sub(1, "WA", 0), sub(2, "AC", 600)], None, errors=["AL02"])
    clean.passed = clean.subs[-1]
    messy = process.Episode(1, 1, [sub(1, "WA", 0), sub(2, "WA", 20), sub(3, "WA", 40), sub(4, "AC", 50)], None,
                            errors=["AL02", "AL02", "AL02"])
    messy.passed = messy.subs[-1]
    assert clean.state == "fixed" and clean.score == 1.0
    assert messy.same_repeats == 2 and messy.rapid == 3 and messy.score < 0.4
    first = process.Episode(1, 1, [sub(1, "AC", 0)], None)
    first.passed = first.subs[0]
    assert first.state == "first_try" and first.score is None    # 一次就过的不算改错过程


def _code_case(s):
    """演示数据里一道“没过、后来自己改对了、没问过智能体”的题。"""
    for sub in assess.pending_code(s, 50):
        ep = process.episode_for(s, sub.user_id, sub.server_id, sub.problem_id)
        if ep and ep.error_skills and any(ep.error_skills):
            return sub, ep
    return None, None


def test_code_assessment(demo, monkeypatch):
    monkeypatch.setitem(config.AGENT_ASSESS, "code_days", 3650)
    with db.session_scope() as s:
        sub, ep = _code_case(s)
        assert sub is not None
        from app.xdoj.assemble import source_text

        before = assess._lines(source_text(s, sub.server_id, ep.failed[-1].submission_id))
        after = assess._lines(source_text(s, sub.server_id, sub.submission_id))
        _diff, changed = assess._diff(before, after)
        real = next(x for x in changed if x.strip())
    sent = []
    answers = [{"level": 2, "evidence": ["int max = 0; // 编的"], "misconception": "", "confidence": 0.8},
               {"level": 2, "evidence": [real.strip()], "misconception": "", "confidence": 0.8}]

    def backend(messages, json_mode, max_tokens, temperature):
        sent.append(messages[-1]["content"])
        return json.dumps(answers[min(len(sent) - 1, 1)], ensure_ascii=False), 100, 40

    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            row = assess.assess_code(s, s.get(m.Submission, (sub.server_id, sub.submission_id)))
            assert row.basis == "code" and row.round_id is None and row.source == "llm"
            assert row.evidence == [real.strip()] and "不是改动里的行" in sent[-1]
            ev = s.scalars(select(m.SkillEvidence).where(
                m.SkillEvidence.source_key == f"assess-code:{sub.server_id}:{sub.submission_id}")).all()
            assert len(ev) == 1 and ev[0].event_type == "agent_understanding"
            assert assess.assess_code(s, s.get(m.Submission, (sub.server_id, sub.submission_id))).id == row.id
            # 对话回合的待评列表不受看代码评（round_id 为空）的影响
            assert isinstance(assess.pending_rounds(s, 10), list)
            view = assess.view(s, row)
            assert view["basisName"] == "看代码怎么改的" and view["problem"]
    finally:
        llm.set_backend(None)


def test_code_guard_caps_trial_and_error():
    ep = process.Episode(1, 1, [], None, errors=["AL02", "AL02", "AL02"])
    assert ep.same_repeats == 2
    assert assess.code_guard(3, ep, 4) == (1, "反复试出来的（急着重交或同一个错误反复出现）")
    clean = process.Episode(1, 1, [], None, errors=["AL02"])
    assert assess.code_guard(3, clean, 30)[0] == 2
    assert assess.code_guard(2, clean, 3) == (2, "")

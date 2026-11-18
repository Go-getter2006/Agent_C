"""多维掌握画像：智能体评估“理解”（要有学生原话、和事实冲突时下调）、对话行为、维度权重的学习、学生端不出数字。"""
from __future__ import annotations

import datetime as dt
import json
import random

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import assess, config, db, graphmodel, llm, mastery, profile
from app import models as m
from app.chat import workflow
from app.chat.analyze import dialogue_act
from app.web.app import create_app

UID = "2500000001"
GOOD = "因为 max 一开始是 0，全是负数的时候比不过，所以要从 a[0] 开始"


def _round(demo, second: str = GOOD, correct: bool | None = True, help_level: str = "H1") -> int:
    """一个已经结束的回合：学生说没过 → 智能体反问 → 学生回答（答没答对由 correct 定）。"""
    sid = demo["server_id"]
    now = db.now()
    with db.session_scope() as s:
        rnd = workflow.open_round(s, UID)
        if rnd is not None:
            workflow.close_round(s, rnd, "换题", now)
        rnd = m.ChatRound(user_id=UID, server_id=sid, problem_id=406, course="C", skill_code="C06.03.02",
                          error_code="TY09", started_at=now, last_message_at=now, status="closed",
                          end_reason="学生表示懂了", ended_at=now, max_help_level=help_level)
        s.add(rnd)
        s.flush()
        talk = [("student", "还是有一组不过，我检查了循环，没看出问题。", "DEBUG", "debug_vague", None),
                ("agent", "如果成绩全是负数，max 最后会是多少？", None, None, None),
                ("student", second, "REPLY", "self_explain", correct)]
        for role, text, intent, act, ok in talk:
            msg = m.ChatMessage(user_id=UID, round_id=rnd.id, server_id=sid, problem_id=406, course="C", role=role,
                                kind="text", content={"blocks": [{"t": "p", "text": text}]}, created_at=now)
            s.add(msg)
            s.flush()
            if role == "student":
                s.add(m.ChatStep(message_id=msg.id, round_id=rnd.id, user_id=UID, intent=intent, act=act,
                                 has_attempt=act != "debug_vague", answer_correct=ok, course_id="C",
                                 analysis_source="rule", created_at=now))
        return rnd.id


def _fake(answer: dict | list[dict], seen: list | None = None):
    answers = answer if isinstance(answer, list) else [answer]
    calls = {"n": 0}

    def backend(messages, json_mode, max_tokens, temperature):
        if seen is not None:
            seen.append("\n".join(x["content"] for x in messages))
        out = answers[min(calls["n"], len(answers) - 1)]
        calls["n"] += 1
        return json.dumps(out, ensure_ascii=False), 100, 40
    return backend


def test_dialogue_acts():
    assert dialogue_act("CONCEPT", False) == "concept"
    assert dialogue_act("DEBUG", True) == "debug_specific" and dialogue_act("DEBUG", False) == "debug_vague"
    assert dialogue_act("ANSWER", False) == "answer_seeking" and dialogue_act("REPLY", False, "tell") == "answer_seeking"
    assert dialogue_act("REPLY", True) == "self_explain" and dialogue_act("REPLY", False, "think") == "self_regulate"


def test_workflow_records_dialogue_acts(demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        workflow.close_round(s, workflow.open_round(s, UID), "换题", db.now())
        workflow.handle(s, UID, (sid, 406), "还是有一组不过，我检查了循环，没看出问题。")
        workflow.handle(s, UID, (sid, 406), GOOD)
        workflow.handle(s, UID, (sid, 406), "", action="tell")
        steps = s.scalars(select(m.ChatStep).order_by(m.ChatStep.id.desc()).limit(3)).all()[::-1]
        acts = [st.act for st in steps]
        assert acts[0] == "debug_vague" and acts[2] == "answer_seeking"
        assert acts[1] in ("self_explain", "debug_specific")    # 回答智能体的问题，或者带着自己的想法再问
        assert all(st.course_id == "C" for st in steps)


def test_rule_assessment_without_llm(demo):
    rid = _round(demo)
    with db.session_scope() as s:
        row = assess.assess_round(s, s.get(m.ChatRound, rid))
        assert (row.source, row.level, row.rule_level) == ("rule", 2, 2)
        # 规则得出的等级不另记证据：它用的事实已经在做题证据里了
        assert s.scalar(select(m.SkillEvidence.id).where(m.SkillEvidence.source_key == f"assess:{rid}")) is None
        assert assess.assess_round(s, s.get(m.ChatRound, rid)).id == row.id     # 不重评


def test_llm_assessment_needs_real_quotes_and_is_capped(demo):
    rid = _round(demo, second="是不是 max 的初值不对", correct=None, help_level="H3")
    seen: list[str] = []
    # 第一次编了一句学生没说过的话，第二次给了原话
    llm.set_backend(_fake([{"level": 3, "evidence": ["我知道所有求最值的题都要这样"], "misconception": "", "confidence": 0.9},
                           {"level": 3, "evidence": ["max 的初值不对"], "misconception": "", "confidence": 0.8}], seen))
    try:
        with db.session_scope() as s:
            row = assess.assess_round(s, s.get(m.ChatRound, rid))   # 这个回合最后给了示例代码
            assert row.source == "llm" and row.raw_level == 3 and row.level == 2 and row.adjusted == "看了示例代码才改对"
            assert row.evidence == ["max 的初值不对"] and row.rule_level is None   # 规则看不出来
            ev = s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.source_key == f"assess:{rid}")).all()
            assert len(ev) == 1 and ev[0].event_type == "agent_understanding" and ev[0].success
            assert not ev[0].independent and ev[0].weight == round(0.5 * 0.8, 3)   # 2 档 × 置信度
        sent = "\n".join(seen)
        assert "不是学生的原话" in sent                  # 编造的原话被退回重写
        assert UID not in sent and "林晓" not in sent     # 不带学号姓名
    finally:
        llm.set_backend(None)


def test_llm_assessment_falls_back_to_rules(demo):
    rid = _round(demo)
    llm.set_backend(_fake({"level": 2, "evidence": [], "misconception": "", "confidence": 0.9}))  # 2 档没有原话
    try:
        with db.session_scope() as s:
            row = assess.assess_round(s, s.get(m.ChatRound, rid))
            assert row.source == "rule" and row.level == 2
    finally:
        llm.set_backend(None)


def test_agent_evidence_moves_estimate_but_not_level(demo):
    """智能体的证据只改估计值：没做过题的技能不会因此有档位，也不改变“最近一次是不是独立做对”。"""
    with db.session_scope() as s:
        mastery.add_evidence(s, UID, "C09.06.01", "agent_understanding", False, 0.6, "assess:x1", db.now())
        st = mastery.skill_states(s, UID, model="v2")["C09.06.01"]
        assert st.level == "还没练到" and st.p < 0.5 and st.events == 0
        assert "C09.06.01" not in mastery.skill_states(s, UID, model="v1")          # 基线不用
        before = mastery.skill_states(s, UID, model="v2")["C06.03.02"]
        base_before = mastery.skill_states(s, UID, model="v2", include_agent=False)["C06.03.02"]
        mastery.add_evidence(s, UID, "C06.03.02", "agent_understanding", False, 0.6, "assess:x2", db.now())
        after = mastery.skill_states(s, UID, model="v2")["C06.03.02"]
        assert after.p < before.p and after.events == before.events
        assert after.last_independent_success == before.last_independent_success
        base = mastery.skill_states(s, UID, model="v2", include_agent=False)["C06.03.02"]
        assert abs(base.p - base_before.p) < 1e-6          # 消融对照：去掉智能体的证据


def test_fit_weights_follows_data():
    rng = random.Random(7)
    samples = []
    for _ in range(400):
        u = rng.uniform(-2, 2)
        y = rng.random() < profile.sigmoid(2.0 * u)       # 结果主要由“理解”决定
        samples.append(({"perf": 0.0, "understanding": u, "independence": 0.0, "retention": 0.0, "prereq": 0.0}, y))
    prior = config.PROFILE["default_weights"]
    w = profile.fit_weights(samples, prior, ridge=2.0)
    assert w["understanding"] > 1.0 > prior["understanding"]
    few = profile.fit_weights(samples[:5], prior, ridge=50.0)
    assert abs(few["understanding"] - prior["understanding"]) < 0.3     # 数据少时往默认值收缩


def test_profile_dims_and_nightly(demo):
    rid = _round(demo)
    with db.session_scope() as s:
        assess.assess_round(s, s.get(m.ChatRound, rid))
        prof = profile.kp_profile(s, UID, "C")
        d = prof["C06.03"]
        assert d.perf is not None and d.understanding is not None and d.independence is not None
        assert d.fused is not None and 0 < d.fused < 1
        assert all(k.startswith("C") for k in prof)
        rows = profile.teacher_view(s, UID, "C")
        assert any(r["code"] == "C06.03" and r["understanding"] is not None for r in rows)
        assert profile.dialogue_profile(s, UID, "C")["total"] >= 2
    result = graphmodel.nightly()      # 演示数据入库时已经跑过一次，预测都记过了
    assert set(result["weights"]) == {"C", "D"}
    with db.session_scope() as s:
        logged = s.scalars(select(m.PredictionLog)).all()
        assert logged and all(r.p_fused is not None and r.p_v2_base is not None and r.features for r in logged)
        assert s.get(m.ModelWeights, "C") is not None


def test_student_sees_dims_without_numbers(demo):
    rid = _round(demo)
    with db.session_scope() as s:
        assess.assess_round(s, s.get(m.ChatRound, rid))
    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"loginName": UID, "password": "test-pass"})
        prog = c.get("/api/progress").json()
    items = [i for g in prog["knowledge"] for i in g["items"]]
    with_dims = [i for i in items if i.get("dims")]
    assert with_dims and all(isinstance(x["segments"], int) and 1 <= x["segments"] <= 3 for i in with_dims for x in i["dims"])
    assert all(set(x) == {"name", "segments", "hint"} for i in with_dims for x in i["dims"])
    assert all(isinstance(i["insight"], str) for i in items)


def _pending(now=None) -> list[tuple[str, str]]:
    with db.session_scope() as s:
        return profile.pending_reviews(s, now=now)


def test_teacher_profile_and_agent_review(demo):
    rid = _round(demo)
    with db.session_scope() as s:
        assess.assess_round(s, s.get(m.ChatRound, rid))
    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"loginName": "demo-teacher", "password": "test-pass"})
        d = c.get(f"/api/teacher/students/{UID}").json()
        assert d["profile"] and d["assessments"][0]["levelName"] == "能解释" and d["dialogue"]["total"] >= 2
        # 演示数据入库时跑过夜里的任务：评语已经自动写好了（没有大模型时按规则）；没有手动入口
        assert d["agentReview"]["source"] == "rule" and d["agentReview"]["summary"]
        assert c.post(f"/api/teacher/students/{UID}/agent-review", json={}).status_code in (404, 405)
        # 刚才有了新的理解评估：一小时内不重写，过了一小时自动重写
        later = db.now() + dt.timedelta(hours=2)
        assert (UID, "C") not in _pending() and (UID, "C") in _pending(later)
        assert profile.refresh_reviews(now=later) >= 1
        assert (UID, "C") not in _pending(later)
        # 有大模型：评语里的知识点必须是画像里的；不合格两次就退回规则
        codes = [x["code"] for x in d["profile"]]
        good = {"summary": "最值这一块会做，也讲得清原因。", "strengths": [codes[0]],
                "concerns": [{"kp": codes[-1], "dimension": "independence", "why": "多靠提示"}], "suggestions": ["多让他先说想法"]}
        llm.set_backend(_fake(good))
        try:
            with db.session_scope() as s:
                row = profile.agent_review(s, UID, "C", force=True)
                assert row.source == "llm"
            r = c.get(f"/api/teacher/students/{UID}").json()["agentReview"]
            assert r["summary"].startswith("最值") and r["concerns"][0]["dimension"] == "独立"
            llm.set_backend(_fake({**good, "strengths": ["D02.02"]}))
            with db.session_scope() as s:
                again = profile.agent_review(s, UID, "C", force=True)
                assert again.id == row.id                 # 画像没变：用上一次的，不重写
        finally:
            llm.set_backend(None)
        g = c.get(f"/api/teacher/classes/c{demo['server_id']}-31/graph").json()
        assert g["fusion"]["weights"][0]["dim"] == "bias" and "agreement" in g and "dialogue" in g

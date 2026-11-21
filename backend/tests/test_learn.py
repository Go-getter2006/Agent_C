"""每周学一次掌握程度的参数（app/learn.py）：经验值当先验，用“新题第一次提交能不能一次做对”校正；
学完冻结成一版，学情和预测都用这一版；同时记经验值版本的预测好比较。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app import config, db, learn, mastery
from app import models as m


def _opportunity(s, uid: str, skill: str, events: list[tuple[str, float]], outcome: bool, at: dt.datetime, n: int):
    """一个学生：先有这些证据，之后一道新题的第一次提交（结果 outcome）。"""
    for i, (event, weight) in enumerate(events):
        mastery.add_evidence(s, uid, skill, event, True, weight, f"e:{uid}:{n}:{i}", at - dt.timedelta(days=3), True)
    s.add(m.PredictionLog(user_id=uid, skill_code=skill, source_key=f"first:{uid}:{n}", ts=at, outcome=outcome,
                          p_simple=0.5, p_v2=0.5, p_graph=0.5))


def test_scales_move_toward_what_predicts(database):
    """一次做对的证据之后多半又做对、提示后才做对的之后多半没做对：前者的系数往上、后者往下。"""
    at = db.now() - dt.timedelta(days=1)
    with db.session_scope() as s:
        for i in range(40):
            _opportunity(s, f"a{i}", "C03.01.01", [("pass_first_try", 1.0)] * 2, i % 10 != 0, at, i)
            _opportunity(s, f"b{i}", "C03.01.01", [("pass_after_h2", 0.2)] * 5, i % 10 == 0, at, i)
        opps = learn.opportunities(s, "C")
        assert len(opps) == 80 and all(o.contrib for o in opps)
        prm, detail = learn.fit(opps)
        assert prm.scale("pass_first_try+") > 1.0 > prm.scale("pass_after_h2+")
        assert detail["nllFit"] < detail["nllPrior"]
        assert detail["keys"]["pass_first_try+"]["opportunities"] == 40


def test_weekly_version_is_frozen_and_used(database, monkeypatch):
    monkeypatch.setitem(config.LEARN, "min_opportunities", 10)
    at = db.now() - dt.timedelta(days=1)
    with db.session_scope() as s:
        for i in range(20):
            _opportunity(s, f"a{i}", "C03.01.01", [("pass_first_try", 1.0)] * 2, True, at, i)
        out = learn.weekly(s)
        version = learn.week_version("C")
        assert out["C"] == version and "沿用经验值" in out["D"]   # 数据结构没有数据：也记一版，用经验值
        row = s.scalars(select(m.MasteryParams).where(m.MasteryParams.version == version)).one()
        assert row.learned and row.n == 20 and row.params["scales"]["pass_first_try+"] > 1
        assert learn.weekly(s) == {}                                # 这一周已经有了：不再学
        st = mastery.skill_states(s, "a0", model="v2")["C03.01.01"]
        fixed = mastery.skill_states(s, "a0", model="v2", params=mastery.PRIOR)["C03.01.01"]
        assert st.params_version == version and st.score > fixed.score   # 学到的这一版在用


def test_too_few_opportunities_keep_the_prior(database):
    with db.session_scope() as s:
        out = learn.weekly(s)
        assert "沿用经验值" in out["C"]
        row = s.scalars(select(m.MasteryParams).where(m.MasteryParams.course == "C")).one()
        assert not row.learned and row.params == {} and "不到" in row.detail["reason"]
        assert mastery.active_params(s, "C").scale("pass_first_try+") == 1.0


def test_predictions_record_learned_and_prior_scores(demo):
    """预测记录里同时有这一版参数和经验值参数算的掌握分，带版本，之后比谁准。"""
    with db.session_scope() as s:
        rows = s.scalars(select(m.PredictionLog)).all()
        assert rows and all(r.p_score is not None and r.p_score_fixed is not None for r in rows)
        assert all(r.params_version for r in rows)

"""老师盲评“理解”（app/rating.py）：材料和大模型看到的一样、不露学生和等级；一致率、下调的效果、两位老师之间。"""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import assess, db, rating
from app import models as m
from app.web.app import create_app

UID = "2500000001"


def test_pair_stats_known_values():
    assert assess.pair_stats([]) == {"n": 0, "exact": None, "kappa": None, "within1": None, "weighted": None}
    perfect = assess.pair_stats([(0, 0), (1, 1), (2, 2), (3, 3)])
    assert perfect["exact"] == 1 and perfect["kappa"] == 1 and perfect["weighted"] == 1
    # 两组各 2 对：一致 1 对，另一对差一档。po=0.5，pa=pb=(0.5 在 1 档, 0.5 在 2 档 / 0.5 在 1, 0.5 在 1)……用手算的值核对
    st = assess.pair_stats([(1, 1), (2, 1)])
    assert st["exact"] == 0.5 and st["within1"] == 1.0 and st["kappa"] == 0.0


def test_blind_rating_flow(demo):
    sid = demo["server_id"]
    key = f"c{sid}-31"
    with db.session_scope() as s:
        # 一条大模型评的、下调过的：原始 3，看了示例代码才改对，降到 2
        a = s.scalars(select(m.AgentAssessment).where(m.AgentAssessment.basis == "chat",
                                                      m.AgentAssessment.round_id.is_not(None))).first()
        a.source, a.raw_level, a.level, a.adjusted, a.evidence = "llm", 3, 2, "看了示例代码才改对", ["独一无二的原话摘抄"]
        aid = a.id
        name = s.get(m.Student, a.user_id).name
    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"loginName": "demo-teacher", "password": "test-pass"})
        first = c.get(f"/api/teacher/classes/{key}/rating").json()
        assert first["total"] >= 1 and first["rated"] == 0 and [x["level"] for x in first["levels"]] == [0, 1, 2, 3]
        seen = []
        cur = first
        while cur["item"] is not None:
            item = cur["item"]
            blob = json.dumps(item, ensure_ascii=False)
            assert set(item) == {"id", "kp", "skill", "problem", "facts", "lines", "shownAt"}   # 没有等级、原话摘抄
            assert name not in blob and UID not in blob and "独一无二的原话摘抄" not in blob
            assert any(x["who"] == "学生" for x in item["lines"])
            seen.append(item["id"])
            level = 2 if item["id"] == aid else None
            cur = c.post(f"/api/teacher/classes/{key}/rating",
                         json={"assessmentId": item["id"], "level": level, "shownAt": item["shownAt"]}).json()
        assert aid in seen and len(seen) == len(set(seen)) == cur["rated"] == cur["total"]
        assert c.post(f"/api/teacher/classes/{key}/rating", json={"assessmentId": aid, "level": 7}).status_code == 400
        assert c.post(f"/api/teacher/classes/{key}/rating", json={"assessmentId": 10 ** 8, "level": 1}).status_code == 400
        summary = c.get(f"/api/teacher/classes/{key}/rating").json()["summary"]
        human = summary["human"]
        assert "human" not in c.get(f"/api/teacher/classes/{key}/graph").json()   # 一致率在“理解评估”页，不在模型检验
        assert set(summary["agreement"]) >= {"n", "exact", "kappa", "within1"}
        assert human["llmRaw"]["n"] == 1 and human["llmRaw"]["exact"] == 0     # 老师 2，原始 3
        assert human["llmFinal"]["exact"] == 1                                 # 下调到 2 以后一致
        assert human["adjusted"] == {"n": 1, "closer": 1, "same": 0, "farther": 0}
        assert human["unsure"] == len(seen) - 1

    with db.session_scope() as s:   # 第二位老师也评了这一条：两位老师之间的一致率
        ids = [st.user_id for st in s.scalars(select(m.Student)).all()]
        rating.rate(s, SimpleNamespace(id=99999), ids, "C", aid, 1)
        out = rating.agreement(s, ids, "C")
        assert out["raters"] == 2 and out["betweenRaters"]["n"] == 1 and out["betweenRaters"]["within1"] == 1.0

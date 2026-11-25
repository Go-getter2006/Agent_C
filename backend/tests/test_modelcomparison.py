import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import config, db, llm, modelcomparison, models as m
from app.web.app import create_app


def prediction(uid, key, day, *, outcome=True, version=None, course="C", simple=.6, v2=.7, graph=.8):
    return m.PredictionLog(user_id=uid, source_key=key, skill_code=course + "01.01.01", ts=day,
                           outcome=outcome, p_simple=simple, p_v2=v2, p_graph=graph,
                           model_version=version or config.GRAPH_VERSION)


def test_date_boundaries_and_baseline_only_use_earlier_same_scope_records(database, monkeypatch):
    now = dt.datetime(2026, 11, 25, 12, tzinfo=db.BEIJING)
    monkeypatch.setattr(db, "now", lambda: now)
    start = dt.datetime(2026, 11, 12, tzinfo=db.BEIJING)
    with db.session_scope() as s:
        s.add_all([
            prediction("one", "past", start - dt.timedelta(days=1)),
            prediction("one", "first", start),
            prediction("two", "same-time", start, outcome=False),
            prediction("one", "last", now, outcome=False),
            prediction("one", "future", now + dt.timedelta(days=1)),
            prediction("other", "outside-class", start - dt.timedelta(hours=1)),
            prediction("one", "other-course", start - dt.timedelta(hours=1), course="D"),
            prediction("one", "other-version", start - dt.timedelta(hours=1), version="older"),
        ])
    with db.session_scope() as s:
        report = modelcomparison.compare(s, ["one", "two"], "C")
        assert (report["from"], report["to"], report["summary"]["n"]) == ("2026-11-12", "2026-11-25", 3)
        assert len(report["periods"]) == 14
        assert report["periods"][0]["n"] == 2 and report["periods"][-1]["n"] == 1
        assert report["periods"][1]["v2Brier"] is None
        first = modelcomparison.compare(s, ["one", "two"], "C", start=start.date(), end=start.date())
        assert first["summary"]["historyBrier"] == pytest.approx(((2/3 - 1)**2 + (2/3)**2) / 2)
        assert first["summary"]["v2Brier"] == pytest.approx((.3**2 + .7**2) / 2)
        assert first["summary"]["halfBrier"] == .25
        all_rows = modelcomparison.compare(s, ["one", "two"], "C", days=0)
        assert all_rows["summary"]["n"] == 4 and all_rows["from"] == "2026-11-11"
        assert modelcomparison.compare(s, ["one"], "C", "older", days=0)["summary"]["n"] == 1


def test_pairing_and_calibration_keep_students_and_exact_probabilities(database):
    now = db.now()
    with db.session_scope() as s:
        for uid in ("one", "two"):
            for i in range(2):
                s.add(prediction(uid, f"{uid}-{i}", now, simple=.2, v2=.8, graph=.9))
    with db.session_scope() as s:
        report = modelcomparison.compare(s, ["one", "two"], "C")
        assert report["summary"]["students"] == 2 and report["summary"]["n"] == 4
        assert report["differences"][0]["delta"] == pytest.approx(-.60)
        assert all(x == pytest.approx(-.60) for x in report["differences"][0]["interval"])
        graph_bin = next(bin for bin in report["calibration"] if bin["model"] == "graph" and bin["n"])
        assert graph_bin["predicted"] == .9 and graph_bin["observed"] == 1
        assert modelcomparison.compare(s, ["one"], "C")["differences"][0]["interval"] is None
        assert modelcomparison.compare(s, [], "C")["summary"]["v2Brier"] is None


def test_comparison_is_developer_only_and_export_uses_selected_range(demo, monkeypatch):
    def reject_model(*args, **kwargs):
        raise AssertionError("统计已有预测时不应调用大模型")

    monkeypatch.setattr(llm, "call_json", reject_model)
    with TestClient(create_app()) as client:
        url = f"/api/teacher/classes/c{demo['server_id']}-31/model-comparison"
        client.post("/api/auth/login", json={"loginName": "demo-teacher", "password": "test-pass"})
        assert client.get(url).status_code == 403
        client.post("/api/auth/login", json={"loginName": "dev-test", "password": "dev-pass"})
        response = client.get(url)
        assert response.status_code == 200 and len(response.json()["periods"]) == 14
        key = f"c{demo['server_id']}-31"
        graph = client.get(f"/api/teacher/classes/{key}/graph").json()
        assert graph["scoringRules"]["mastered"] == config.MASTERY_V2["mastered_score"]
        assert graph["scoreBreakdown"] and all("quality" in row and "totalProblems" in row for row in graph["scoreBreakdown"])
        assert client.get(url, params={"start": "2026-11-25"}).status_code == 400
        assert client.get(url, params={"start": "2026-11-25", "end": "2026-11-01"}).status_code == 400
        assert client.get(url, params={"days": -1}).status_code == 400
        selected = {"start": "2000-01-01", "end": "2000-01-02"}
        empty = client.get(url, params=selected).json()
        assert empty["summary"]["n"] == 0
        export = client.get("/api/teacher/export/models", params={"key": f"c{demo['server_id']}-31", **selected}).json()
        assert export["data"]["comparison"]["from"] == empty["from"]
        assert export["data"]["weekly"] == [] and export["data"]["comparison"]["summary"]["n"] == 0

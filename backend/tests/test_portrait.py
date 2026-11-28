"""真实数据接入：时间归属、同题去重、来源校验、无模型的请求路径、云端复用与课程隔离。"""
import datetime as dt
import json

import pytest
from sqlalchemy import func, select

from app import catalog, codequality, db, llm, models as m, portrait, progress

UID = "2500000001"


def sub(s, sid, ident, at, code, result="AC", pid=406):
    row = m.Submission(server_id=sid, submission_id=ident, user_id=UID, problem_id=pid,
                       submit_time=at, language="C", judgement=result, detail_status="done")
    s.add(row)
    s.add(m.SourceSnapshot(server_id=sid, submission_id=ident, source_bytes=code.encode(), encoding="utf-8"))
    s.flush()
    return row


def output(inputs, level=2):
    return {"items": [{"id": ident, "metrics": {key: {"level": level, "confidence": .9,
                "reason": "根据这一行的控制结构", "lines": [1]} for key in codequality.DIMENSIONS},
              "time": {"value": "O(1)", "variable": "无随输入增长的存储", "reason": "固定次数的操作", "lines": [1]},
              "space": {"value": "O(1)", "variable": "固定数量变量", "reason": "没有变长存储", "lines": [1]}}
             for ident in inputs]}


def test_weekly_representatives_use_submission_time_and_one_code_per_problem(demo):
    now = db.now()
    start = codequality.monday(now)
    with db.session_scope() as s:
        sid = demo["server_id"]
        a = sub(s, sid, 900001, start-dt.timedelta(days=2), "int main(){return 0;}")
        b = sub(s, sid, 900002, start+dt.timedelta(hours=1), "int main(){return 1;}", "WA")
        c = sub(s, sid, 900003, start+dt.timedelta(hours=2), "int main(){return 0;}")
        d = sub(s, sid, 900004, start+dt.timedelta(hours=3), "int main(){return 2;}", "WA")
        snap = progress.Snapshot(s, UID, now=start+dt.timedelta(days=1), visible={(sid,406)})
        snap.subs = {(sid,406): [a,b,c,d]}
        weeks = portrait.representative_submissions(snap)
        assert weeks[start][(sid,406)].submission_id == c.submission_id
        assert len(weeks[start]) == 1
        assert weeks[start-dt.timedelta(weeks=1)][(sid,406)].submission_id == a.submission_id


def test_page_never_calls_model_and_persists_deduplicated_queue(demo, monkeypatch):
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: pytest.fail("page must not call a model"))
    with db.session_scope() as s:
        data = progress.build(s, UID)
        queued = s.scalar(select(func.count()).select_from(m.CodeAnalysis))
        assert queued > 0
        again = progress.build(s, UID)
        assert s.scalar(select(func.count()).select_from(m.CodeAnalysis)) == queued
        assert data["portrait"]["style"]["pending"] == again["portrait"]["style"]["pending"]
        assert all(d["value"] is None for d in data["portrait"]["style"]["dimensions"])
        assert all("p" not in k and k["degree"] is None or 0 <= k["degree"] <= 100
                   for g in data["knowledge"] for k in g["items"])


def test_equal_source_reuses_cloud_result_and_worker_batches_once(demo, monkeypatch):
    calls=[]
    def generate(purpose, system, user, validate, **kw):
        inputs={item["id"]:item for item in json.loads(user)["items"]}
        calls.append((purpose, len(inputs), kw))
        return validate(output(inputs))
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "call_json", generate)
    sid=demo["server_id"]
    with db.session_scope() as s:
        at=codequality.monday(db.now())+dt.timedelta(hours=1)
        a=sub(s,sid,910001,at,"int main(){return 0;}")
        b=sub(s,sid,910002,at+dt.timedelta(minutes=1),"int main(){return 0;}")
        r=codequality.enqueue(s,a,"C")
        assert codequality.enqueue(s,b,"C").id == r.id
        other=next(pid for server,pid in catalog.visible_problems(s,UID,"C") if server==sid and pid!=406)
        sub(s,sid,910003,at+dt.timedelta(minutes=2),"int main(){return 3;}",pid=other)
        codequality.enqueue(s,s.get(m.Submission,(sid,910003)),"C")
    assert codequality.run_pending() == 2
    assert len(calls)==1 and calls[0][0]=="portrait_code" and calls[0][1]==2
    assert calls[0][2]["max_tokens"] is None
    assert codequality.run_pending() == 0
    with db.session_scope() as s:
        data=progress.build(s,UID,visible={(sid,406)})
        assert all(d["value"] is not None for d in data["portrait"]["style"]["dimensions"])
        assert all(d["low"]<=d["value"]<=d["high"] for d in data["portrait"]["style"]["dimensions"])


def test_invalid_evidence_has_no_fabricated_score_and_retries_are_delayed(demo, monkeypatch):
    monkeypatch.setattr(llm,"available",lambda:True)
    def fail(*a,**k):
        raise llm.LLMUnavailable("invalid source evidence")
    monkeypatch.setattr(llm,"call_json",fail)
    with db.session_scope() as s:
        row=sub(s,demo["server_id"],920001,db.now(),"int main(){return 0;}")
        codequality.enqueue(s,row,"C")
    assert codequality.run_pending()==1
    assert codequality.run_pending()==0
    with db.session_scope() as s:
        result=s.scalar(select(m.CodeAnalysis))
        assert result.state=="retry" and result.attempts==1 and result.result=={}
        assert result.retry_at > db.now()


def test_validator_rejects_unknown_source_and_accepts_not_applicable():
    inputs={1:{"code":"int main(){return 0;}"}}
    data=output(inputs)
    data["items"][0]["metrics"]["functions"].update(level=None,lines=[])
    assert codequality.validate(data,inputs)[1]["metrics"]["functions"]["level"] is None
    data["items"][0]["metrics"]["layout"]["lines"]=[99]
    with pytest.raises(ValueError,match="行号"):
        codequality.validate(data,inputs)


def test_scope_and_course_do_not_leak_other_submissions(demo):
    with db.session_scope() as s:
        sid=demo["server_id"]
        data=progress.build(s,UID,"C",visible={(sid,406)})
        assert all(not record.get("problem") or record["problem"]["key"]==f"p{sid}-406"
                   for dim in data["portrait"]["abilities"] for record in dim["records"])
        assert progress.build(s,UID,"D")["portrait"]["style"]["dimensions"][0]["value"] is None
        assert all(not point["name"].startswith("C") for g in data["knowledge"] for point in g["items"])


def test_migration_adds_queue_without_removing_existing_records(database):
    from sqlalchemy import inspect
    db.migrate()
    assert "code_analyses" in inspect(db.engine()).get_table_names()
    db.migrate()

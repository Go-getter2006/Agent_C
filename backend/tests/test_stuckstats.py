"""卡点按知识点汇总（app/stuckstats.py）：用已知结果的合成记录回算每个分子分母。"""
from __future__ import annotations

import datetime as dt

from app import db, stuckstats
from app import models as m

SV = 1
T0 = dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.timezone.utc)
A, B, C = "2599100001", "2599100002", "2599100003"
_sid = [100]


def at(hours: float) -> dt.datetime:
    return T0 + dt.timedelta(hours=hours)


def problem(s, pid: int, skill: str) -> None:
    s.add(m.Problem(server_id=SV, problem_id=pid, title=f"题{pid}"))
    s.add(m.ProblemLabel(server_id=SV, problem_id=pid, primary_skill=skill, is_current=True))


def sub(s, uid: str, pid: int, hours: float, result: str) -> None:
    _sid[0] += 1
    s.add(m.Submission(server_id=SV, submission_id=_sid[0], user_id=uid, problem_id=pid, submit_time=at(hours),
                       judgement=result))


def stuck(s, uid: str, pid: int | None, skill: str, nth: int, resolution: str, hours: float, rnd: int,
          stubborn: bool = False) -> None:
    s.add(m.StuckPoint(user_id=uid, course="C", server_id=SV if pid else None, problem_id=pid, skill_code=skill,
                       nth=nth, resolution=resolution, round_id=rnd, stubborn=stubborn, created_at=at(hours)))


def seed(s) -> None:
    s.add(m.Server(id=SV, code="s-t", name="测试服务器", course="C"))
    s.flush()
    for uid in (A, B, C):
        s.add(m.Student(user_id=uid, name="生" + uid[-1]))
    problem(s, 1, "C06.03.02")    # 求最值
    problem(s, 2, "C06.03.03")    # 同一个知识点（求最值）里的另一个技能
    problem(s, 3, "C06.03.02")
    problem(s, 4, "C05.03.01")    # for
    # A：在题 1 卡住两次（第二次自己想起来），之后又交两次通过；之后新做的题 2 一次通过、题 3 第一次没过
    sub(s, A, 1, 0, "WA")
    stuck(s, A, 1, "C06.03.02", 1, "example_given", 1, rnd=11)
    stuck(s, A, 1, "C06.03.02", 2, "recalled", 2, rnd=12)
    sub(s, A, 1, 3, "WA")
    sub(s, A, 1, 4, "AC")
    sub(s, A, 2, 5, "AC")
    sub(s, A, 3, 6, "WA")
    sub(s, A, 3, 7, "AC")
    # B：在题 1 卡住、第二次没想起来（挖空），到现在没通过；没绑定题的一次（概念问题）
    stuck(s, B, 1, "C06.03.02", 1, "example_given", 1, rnd=21)
    stuck(s, B, 1, "C06.03.02", 2, "blank_filled", 2, rnd=22)
    stuck(s, B, None, "C06.03.03", 1, "example_given", 3, rnd=23)
    sub(s, B, 1, 4, "WA")
    # 题 2 在卡住之前就做过：不算“之后新题”
    sub(s, B, 2, 0, "AC")
    # C：题 4 交了 3 次没过、没来问 → 没来问；题 1 交了 2 次没过 → 不够 3 次
    sub(s, C, 4, 1, "WA")
    sub(s, C, 4, 2, "RE")
    sub(s, C, 4, 3, "WA")
    sub(s, C, 4, 3.5, "PENDING")   # 还在评测的不算
    sub(s, C, 1, 1, "WA")
    sub(s, C, 1, 2, "WA")
    # B 在题 4 也没过 3 次，但开过求助回合：不算没来问
    for h in (1, 2, 3):
        sub(s, B, 4, h, "WA")
    s.add(m.ChatRound(user_id=B, server_id=SV, problem_id=4, course="C"))


def test_kp_numbers(database):
    with db.session_scope() as s:
        seed(s)
        s.flush()
        stats = stuckstats.compute(s, [A, B, C], "C", [(SV, p) for p in (1, 2, 3, 4)])
        k = stats["C06.03"]
        assert len(k.episodes) == 5 and k.students == {A, B}
        assert (k.recall.n, k.recall.d) == (1, 2)          # 第二次卡住：A 想起来、B 挖空
        assert (k.passed.n, k.passed.d) == (2, 4)          # 绑定题的 4 次：A 两次都通过，B 两次没有
        assert (k.transfer.n, k.transfer.d) == (1, 2)      # A 之后新做的题 2 通过、题 3 第一次没过；B 的题 2 在之前
        a_eps = [e for e in k.episodes if e.sp.user_id == A]
        assert [e.tries_after for e in a_eps] == [2, 2] and all(e.passed for e in a_eps)
        f = stats["C05.03"]
        assert f.episodes == [] and set(f.silent) == {C} and f.silent[C] == [(SV, 4, 3)]

        rows = {r["code"]: r for r in stuckstats.kp_rows(s, stats, {A: "生1", B: "生2", C: "生3"})}
        r = rows["C06.03"]
        assert (r["students"], r["episodes"], r["repeat"], r["stubborn"]) == (2, 5, 2, 0)
        assert r["recall"] == {"n": 1, "d": 2} and r["passed"] == {"n": 2, "d": 4} and r["transfer"] == {"n": 1, "d": 2}
        assert [x["code"] for x in r["skills"]] == ["C06.03.02", "C06.03.03"]
        b = next(x for x in r["studentRows"] if x["userId"] == B)
        assert b["rounds"] == 3 and b["passed"] is None     # 最近一次没绑定题
        assert rows["C05.03"]["silent"] == 1 and rows["C05.03"]["silentRows"][0]["problems"] == [
            {"title": "题4", "problemId": 4, "fails": 3}]

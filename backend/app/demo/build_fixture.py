"""生成 fixture.json：用沙箱真实编译运行，算出每个测试点的标准输出和每种写法的评测结果。

运行：python -m app.demo.build_fixture   （需要 gcc；结果提交进仓库，演示数据入库时不再需要 gcc）
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from ..sandbox import run_program
from . import content as c

FIXTURE = Path(__file__).with_name("fixture.json")


def _same(a: str, b: str) -> bool:
    norm = lambda t: [x.rstrip() for x in t.replace("\r\n", "\n").rstrip().split("\n")]  # noqa: E731
    return norm(a) == norm(b)


def expected_outputs() -> dict[int, list[str]]:
    out = {}
    for pid, p in c.PROBLEMS.items():
        res = run_program(p["solution"], [t[0] for t in p["tests"]], cpu_seconds=2, memory_mb=128)
        assert res.status == "ok", (pid, res.compile_output)
        assert all(r.status == "ok" for r in res.runs), (pid, [r.status for r in res.runs])
        out[pid] = [r.stdout for r in res.runs]
    return out


def judge(source: str, pid: int, expected: list[str]) -> dict:
    """像 XDOJ 那样评一次：整体结果、分数、编译输出、每个测试点的结果和没通过时的程序输出。"""
    tests = c.PROBLEMS[pid]["tests"]
    res = run_program(source, [t[0] for t in tests], cpu_seconds=2, memory_mb=128)
    if res.status == "compile_error":
        diag = "\n".join(line for line in res.compile_output.splitlines() if "error" in line or "^" in line)[:600]
        return {"result": "编译错误", "points": 0, "compile_output": diag or res.compile_output[:600], "runs": []}
    assert res.status == "ok", res.status
    runs, overall = [], "正确"
    kinds = {"runtime_error": ("运行错误", "运行错误"), "timeout": ("超时", "时间超限"), "output_limit": ("错误", "答案错误")}
    for run, want in zip(res.runs, expected):
        if run.status == "ok" and _same(run.stdout, want):
            runs.append({"result": "正确", "output": None, "time": max(1, run.time_ms)})
            continue
        text, whole = ("错误", "答案错误") if run.status == "ok" else kinds[run.status]
        runs.append({"result": text, "output": run.stdout[:4000], "time": max(1, run.time_ms)})
        if overall == "正确":
            overall = whole
    passed = sum(1 for r in runs if r["result"] == "正确")
    return {"result": overall, "points": round(100 * passed / len(runs)), "compile_output": None, "runs": runs}


def build() -> dict:
    expected = expected_outputs()
    sources: dict[str, str] = {}
    judges: dict[str, dict] = {}

    def add(pid: int, source: str) -> str:
        key = f"{pid}:{hashlib.sha1(source.encode('utf-8')).hexdigest()[:10]}"
        if key not in sources:
            sources[key] = source
            judges[key] = judge(source, pid, expected[pid])
        return key

    def source_of(pid: int, kind: str) -> str:
        if kind == "ok":
            return c.OWN_SOLUTIONS.get(pid, c.PROBLEMS[pid]["solution"])
        assert c.BUGGY[kind]["problem"] == pid, (pid, kind)
        return c.BUGGY[kind]["source"]

    contest_of = {pid: (cid, start, end) for cid, _t, start, end, _cls, pids in c.CONTESTS for pid in pids}
    subs: list[dict] = []
    uid, name = c.DEMO_STUDENT
    for pid, kind, ago in c.DEMO_SUBMISSIONS:
        subs.append({"user": uid, "name": name, "problem": pid, "contest": contest_of[pid][0], "class": 31,
                     "ago": ago, "source": add(pid, source_of(pid, kind)), "kind": kind})

    rng = random.Random(20261116)
    for idx, (uid, name) in enumerate(c.CLASSMATES):
        for cid, _title, start, end, classes, pids in c.CONTESTS:
            if 31 not in classes:
                continue
            for pid in pids:
                roll = rng.random()
                if idx == 0:
                    roll = 0.0  # 第一位同学每道题都一次通过：保证每道题都有同学的通过提交可以当参考
                bugs = c.CLASSMATE_BUGS.get(pid, [])
                latest = max(2, -end) if end < 0 else 2
                t = rng.randint(latest + 40, max(latest + 60, start - 30))
                if roll < 0.55 or (roll < 0.85 and not bugs):
                    plan = ["ok"]
                elif roll < 0.85:
                    plan = [rng.choice(bugs)] * rng.choice([1, 1, 2]) + ["ok"]
                elif roll < 0.93 and bugs:
                    plan = [rng.choice(bugs)] * rng.choice([1, 2])
                else:
                    continue
                for step, kind in enumerate(plan):
                    ago = max(latest, t - step * rng.randint(3, 9))
                    src = c.PROBLEMS[pid]["solution"] if kind == "ok" else c.BUGGY[kind]["source"]
                    subs.append({"user": uid, "name": name, "problem": pid, "contest": cid, "class": 31,
                                 "ago": ago, "source": add(pid, src), "kind": kind})
    uid, name = c.OTHER_CLASS_STUDENT
    subs.append({"user": uid, "name": name, "problem": 601, "contest": 504, "class": 32, "ago": 3 * c.DAY,
                 "source": add(601, c.PROBLEMS[601]["solution"]), "kind": "ok"})
    subs.sort(key=lambda x: (-x["ago"], x["user"], x["problem"]))

    # 核对演示学生每次提交的结果和设想的一致
    want = {"406-ce": "编译错误", "406-skip-first": "PPFFP", "406-zero-init": "PPFPP", "407-sentinel": "PPPPP",
            "410-zero-init": "PPFFF", "411-intdiv": "FPFPF", "301-int": "PPPFF", "301-printf": "PPPFF",
            "303-one": "PPFPP", "304-single": "PFFFF", "202-and": "FPFPP", "203-lines": "FFFFF"}
    for kind, pattern in want.items():
        j = judges[add(c.BUGGY[kind]["problem"], c.BUGGY[kind]["source"])]
        got = j["result"] if not j["runs"] else "".join("P" if r["result"] == "正确" else "F" for r in j["runs"])
        assert got == pattern, (kind, got, pattern)
    for pid in c.PROBLEMS:
        assert judges[add(pid, c.PROBLEMS[pid]["solution"])]["result"] == "正确", pid
    for pid, src in c.OWN_SOLUTIONS.items():
        assert judges[add(pid, src)]["result"] == "正确", pid
    return {"expected": {str(k): v for k, v in expected.items()}, "sources": sources, "judges": judges,
            "submissions": subs}


if __name__ == "__main__":
    data = build()
    FIXTURE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = {}
    for j in data["judges"].values():
        kinds[j["result"]] = kinds.get(j["result"], 0) + 1
    print(f"提交 {len(data['submissions'])} 条，不同的源码 {len(data['sources'])} 份，结果分布 {kinds}")
    for key in ("408-index", "408-small-array"):
        src = c.BUGGY[key]["source"]
        k = f"408:{hashlib.sha1(src.encode('utf-8')).hexdigest()[:10]}"
        print(key, data["judges"][k]["result"], "".join("P" if r["result"] == "正确" else "F" for r in data["judges"][k]["runs"]))

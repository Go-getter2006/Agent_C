"""开发者端的时间筛选、简单基线与配对比较；只读取已经保存的预测。"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from itertools import groupby

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, db, models as m, research

MODELS = (("v2", "p_v2"), ("simple", "p_simple"), ("graph", "p_graph"))


def _stats(rows, baselines) -> dict:
    n = len(rows)
    result = {"n": n, "students": len({r.user_id for r in rows}), "passed": sum(r.outcome for r in rows)}
    for key, attr in MODELS:
        result[key + "Brier"] = sum((getattr(r, attr) - int(r.outcome)) ** 2 for r in rows) / n if n else None
        result[key + "Accuracy"] = sum((getattr(r, attr) >= .5) == bool(r.outcome) for r in rows) / n if n else None
    result["halfBrier"] = .25 if n else None
    result["historyBrier"] = sum((baselines[r.id] - int(r.outcome)) ** 2 for r in rows) / n if n else None
    for count, fields in (("nFused", (("fusedBrier", "p_fused"), ("v2BaseBrier", "p_v2_base"), ("v2SameBrier", "p_v2"))),
                          ("nScore", (("scoreBrier", "p_score"), ("scoreFixedBrier", "p_score_fixed")))):
        paired = [r for r in rows if all(getattr(r, attr) is not None for _, attr in fields)]
        result[count] = len(paired)
        for key, attr in fields:
            result[key] = sum((getattr(r, attr) - int(r.outcome)) ** 2 for r in paired) / len(paired) if paired else None
    return result


def compare(s: Session, ids: list[str], course: str, version: str | None = None, *, days: int = 14,
            start: dt.date | None = None, end: dt.date | None = None) -> dict:
    now = db.now()
    today = now.astimezone(db.BEIJING).date()
    rows = list(s.scalars(select(m.PredictionLog).where(
        m.PredictionLog.user_id.in_(ids), m.PredictionLog.skill_code.startswith(course),
        m.PredictionLog.ts <= now).order_by(m.PredictionLog.ts, m.PredictionLog.id))) if ids else []
    versions = {}
    for row in rows:
        value = versions.setdefault(row.model_version or "", {"n": 0, "first": row.ts, "last": row.ts})
        value["n"] += 1
        value["last"] = row.ts
    selected_version = config.GRAPH_VERSION if version is None else version
    selected = [r for r in rows if (r.model_version or "") == selected_version]
    end = end or today
    if start is None:
        start = min((r.ts.astimezone(db.BEIJING).date() for r in selected), default=end) if days == 0 else end - dt.timedelta(days=days - 1)
    begin = dt.datetime.combine(start, dt.time.min, db.BEIJING)
    until = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, db.BEIJING)
    evaluated = [r for r in selected if begin <= r.ts < until]

    # 同一时间戳的一组全部预测完才更新基线，避免把本次或同时发生的结果用进去。
    baselines = {}
    successes = count = 0
    for _, batch in groupby(selected, key=lambda r: r.ts):
        batch = list(batch)
        probability = (1 + successes) / (2 + count)
        baselines.update((r.id, probability) for r in batch)
        successes += sum(r.outcome for r in batch)
        count += len(batch)

    daily = (end - start).days < 31
    buckets = defaultdict(list)
    for row in evaluated:
        day = row.ts.astimezone(db.BEIJING).date()
        bucket = day if daily else day - dt.timedelta(days=day.weekday())
        buckets[bucket].append(row)
    cursor = start if daily else start - dt.timedelta(days=start.weekday())
    periods = []
    while cursor <= end:
        periods.append({"date": cursor.isoformat(), **_stats(buckets[cursor], baselines)})
        cursor += dt.timedelta(days=1 if daily else 7)

    losses = defaultdict(lambda: [0, 0., 0., 0.])
    calibration = []
    for row in evaluated:
        loss = losses[row.user_id]
        loss[0] += 1
        for i, attr in enumerate(("p_simple", "p_v2", "p_graph"), 1):
            loss[i] += (getattr(row, attr) - int(row.outcome)) ** 2
    for key, attr in MODELS:
        bins = defaultdict(list)
        for row in evaluated:
            bins[min(4, int(getattr(row, attr) * 5))].append(row)
        for index in range(5):
            batch = bins[index]
            calibration.append({"model": key, "lower": index / 5, "upper": (index + 1) / 5, "n": len(batch),
                                "predicted": sum(getattr(r, attr) for r in batch) / len(batch) if batch else None,
                                "observed": sum(r.outcome for r in batch) / len(batch) if batch else None})
    paired = research.paired_brier(losses)
    return {"version": selected_version, "legacyGraph": selected_version in ("", "2026-11-17a"),
            "from": start.isoformat(), "to": end.isoformat(), "today": today.isoformat(),
            "grouping": "day" if daily else "week", "summary": _stats(evaluated, baselines), "periods": periods,
            "versions": [{"version": key, "n": value["n"], "first": value["first"].astimezone(db.BEIJING).date().isoformat(),
                          "last": value["last"].astimezone(db.BEIJING).date().isoformat()} for key, value in versions.items()],
            "differences": paired["differences"], "calibration": calibration,
            "provenance": {"retrospective": sum((r.provenance or {}).get("mode") == "retrospective" for r in evaluated),
                           "legacy": sum(r.provenance is None for r in evaluated)}}

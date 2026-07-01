"""Live data-flow map — nodes + edges with this instance's real numbers.

Powers the animated pipeline map (How-it-works hero + dashboard mini). Layout
lives in the frontend; here we only supply values, edge throughput levels and
statuses. Reuses the buddy phase so the map and the mascot always agree. Pure
read; degrades to a neutral 'idle' map on any error.

edge.rate is a coarse 0..3 throughput level (drives dot density/speed):
  0 none/stalled · 1 trickle · 2 steady · 3 busy
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _level(n, a, b) -> int:
    if not n:
        return 0
    return 1 if n < a else 2 if n < b else 3


def _per_day(n) -> str:
    if not n:
        return "idle"
    return f"{n / 1000:.0f}k/day" if n >= 1000 else f"{int(n)}/day"


def flow_state(repo, tsdb) -> dict:
    from .buddy import buddy_state
    b = buddy_state(repo, tsdb)
    setup = b["phase"].startswith("setup")
    now = datetime.now(timezone.utc)

    bindings = [x for x in (_safe(repo.bindings, []) or []) if x.enabled]
    persons = [p for p in (_safe(repo.persons, []) or []) if getattr(p, "enabled", True)]
    promoted = [m for m in (_safe(repo.models, []) or []) if m.promoted]
    root = next((m for m in promoted if m.node == "root"), None)

    events24 = _safe(lambda: tsdb.count_raw_events(24), 0) if tsdb else 0
    recent = _safe(lambda: tsdb.count_raw_events(3), None) if tsdb else None
    first = _safe(lambda: tsdb.first_raw_time(), None) if tsdb else None
    preds_by_person: dict[str, int] = {}
    if tsdb:
        for p in persons:
            preds_by_person[p.id] = len(
                _safe(lambda p=p: tsdb.read_predictions(p.id, now - timedelta(days=1), now), []) or [])
    preds24 = sum(preds_by_person.values())
    open_q = len(_safe(lambda: repo.open_questions(), []) or [])
    patterns = len(_safe(lambda: repo.clusters(status="new"), []) or [])

    # One model PER PERSON (each person's promoted root classifier, plus any
    # per-activity child models). The map used to show only the first person's
    # root; now we surface every person's model so a multi-member home reads true.
    def _acc(mr) -> float | None:
        mm = mr.metrics or {}
        return mm.get("accuracy_confirmed") or mm.get("accuracy_bootstrap")

    promoted_by_person: dict[str, list] = {}
    for mr in promoted:
        promoted_by_person.setdefault(mr.person_id, []).append(mr)
    models = []
    for p in persons:
        mine = promoted_by_person.get(p.id, [])
        root_m = next((mr for mr in mine if mr.node == "root"), None)
        models.append({
            "person_id": p.id, "name": getattr(p, "name", p.id),
            "avatar": getattr(p, "avatar", None),
            "version": root_m.version if root_m else None,
            "accuracy": _acc(root_m) if root_m else None,
            "nodes": len(mine),                       # root + child classifiers
            "preds": preds_by_person.get(p.id, 0),
            "status": "ok" if root_m else "work",
        })

    trained = [x for x in models if x["version"]]
    accs = [x["accuracy"] for x in trained if x["accuracy"]]
    avg_acc = sum(accs) / len(accs) if accs else None
    if len(trained) <= 1:                             # single member → familiar form
        model_value = (f"{root.version} · {round(_acc(root) * 100)}%"
                       if root and _acc(root) else root.version if root else "training…")
    else:
        model_value = (f"{len(trained)} models · {round(avg_acc * 100)}%"
                       if avg_acc else f"{len(trained)} models")

    m = root.metrics if root else {}
    n_train = m.get("n_train")
    confirmed = (root.label_counts or {}).get("confirmed", 0) if root else 0

    stalled = bool(tsdb and bindings and recent == 0 and first and (now - first) > timedelta(hours=6))

    # Generic "Data source" node — the platform feeding sensors. Today that's
    # only Home Assistant; resolved from the connection so adding another source
    # (e.g. Homey) later just changes the name the hover reveals.
    source_name = "Home Assistant" if _safe(lambda: repo.get_connection("ha")) else None

    # raw-store hover text reflects the configured retention (Settings → Model)
    ret_days = _safe(lambda: repo.get_setting("retention.days", 730), 730) or 730
    if not isinstance(ret_days, int) or ret_days <= 0:
        ret_txt = "kept forever"
    elif ret_days >= 365 and ret_days % 365 == 0:
        yrs = ret_days // 365
        ret_txt = f"{yrs}-year retention"
    else:
        ret_txt = f"{ret_days}-day retention"

    nodes = {
        "ha": {"label": "Data source", "source": source_name,
               "value": f"{len(bindings)} sensors",
               "status": "ok" if bindings else "idle", "href": "/sensors", "step": "sources"},
        "raw": {"label": "Raw store", "value": _per_day(events24),
                "desc": f"Raw events kept in InfluxDB ({ret_txt}).",
                "status": "alert" if stalled else "ok" if events24 else "idle",
                "href": "/settings", "step": "normalise"},
        "features": {"label": "Features", "value": f"{n_train:,} windows" if n_train else "building…",
                     "status": "work" if setup else "ok", "href": "/sensors", "step": "features"},
        "model": {"label": "Models" if len(models) > 1 else "Model",
                  "value": model_value,
                  "status": "ok" if root else "work", "href": "/models", "step": "model"},
        "predictions": {"label": "Predictions", "value": f"{preds24}/day" if preds24 else "warming up",
                        "status": "ok" if preds24 else "idle", "href": "/", "step": "serving"},
        "you": {"label": "You", "value": f"{open_q} to answer" if open_q else "all caught up",
                "status": "ask" if open_q else "ok", "href": "/inbox", "step": "labels"},
        "discovery": {"label": "Discovery",
                      "value": f"{patterns} pattern{'s' if patterns != 1 else ''}" if patterns else "none new",
                      "status": "ask" if patterns else "ok", "href": "/patterns", "step": "discovery"},
    }
    edges = {
        "ha_raw": {"rate": 0 if stalled else _level(events24, 2000, 30000),
                   "status": "alert" if stalled else "ok", "label": _per_day(events24)},
        "raw_features": {"rate": 2 if (n_train or setup) else (1 if events24 else 0), "status": "ok"},
        "features_model": {"rate": 1, "status": "ok"},
        "model_predictions": {"rate": _level(preds24, 50, 250), "status": "ok" if preds24 else "idle"},
        "predictions_you": {"rate": 1 if open_q else 0, "status": "ask" if open_q else "ok"},
        "you_model": {"rate": _level(confirmed, 10, 100), "status": "ok"},
        "features_discovery": {"rate": 1 if patterns else 0, "status": "ok"},
    }
    return {"phase": b["phase"], "tone": b["tone"], "nodes": nodes, "edges": edges,
            "models": models}

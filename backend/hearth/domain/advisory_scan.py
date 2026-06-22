"""Advisory scan — turns signals Hearth already computes into standing advisories
the buddy can surface. Runs daily. Pure-ish (repo reads + advisory/event writes).

Two producers today:
  - coverage blind-spots  (coverage.advisor.gaps_from_home)  -> "add a sensor in X"
  - model health          (insight.model_insight)            -> low accuracy / off
                                                                calibration / drift
Foundational demotions are produced inline by foundational.facts.run_verdicts.
"""
from __future__ import annotations

import logging

from . import advisories, events
from .coverage.advisor import gaps_from_home
from .insight import model_insight

log = logging.getLogger(__name__)


def refresh_system_advisories(repo) -> None:
    _coverage(repo)
    _model_health(repo)


def _first_time(repo, kind: str) -> bool:
    return kind not in (repo.get_setting(advisories.KEY) or {})


def _coverage(repo) -> None:
    kind = "coverage:blindspot"
    try:
        gaps = gaps_from_home(repo)
    except Exception:
        gaps = []
    if not gaps:
        advisories.clear_advisory(repo, kind)
        return
    top = gaps[0]
    fresh = _first_time(repo, kind)
    advisories.record_advisory(
        repo, kind, "A blind spot in your home", top.recommendation,
        severity="info", cta={"label": "Sensors", "href": "/sensors"})
    if fresh:
        events.record_event(repo, "blindspot", "Found a blind spot", top.recommendation)


def _model_health(repo) -> None:
    try:
        persons = list(repo.persons())
    except Exception:
        persons = []
    for p in persons:
        kind = f"model:{p.id}"
        try:
            ins = model_insight(p.id, repo)
        except Exception:
            continue
        f = ins.get("facts") or {}
        sev = title = detail = None
        cta = {"label": "Methodology", "href": "/methodology"}
        if f.get("accuracy_gold") is not None and f["accuracy_gold"] < 0.6:
            sev, title = "warn", f"{p.name}'s model accuracy is low"
            detail = f"About {f['accuracy_gold'] * 100:.0f}% on real-world spot-checks — more confirmations would help."
        elif f.get("drifted"):
            sev, title = "info", f"{p.name}'s signals have drifted"
            detail = f"{', '.join(f['drifted'][:3])} changed since training — a retrain would recalibrate."
            cta = {"label": "Models", "href": "/models"}
        elif f.get("ece") is not None and f["ece"] > 0.15:
            sev, title = "info", f"{p.name}'s confidence is a bit off"
            detail = "Read its confidence with a pinch of salt until the next retrain."
        elif f.get("beats_flat") is False:
            sev, title = "info", f"{p.name}'s hierarchy isn't adding much"
            detail = "A simpler flat model does about as well here."
        if sev is None:
            advisories.clear_advisory(repo, kind)
            continue
        fresh = _first_time(repo, kind)
        advisories.record_advisory(repo, kind, title, detail, severity=sev, cta=cta)
        if fresh:
            events.record_event(repo, "model_health", title, detail)

"""Label-error detection — confident learning over pooled out-of-sample probs.

The model is only as good as its labels, and Hearth's labels come from three
fallible sources: starter rules (wrong when a binding is wrong), human answers
(mis-taps, misremembered recaps), and imports. Confident learning (Northcutt,
Jiang & Chuang 2021, JAIR — "cleanlab") finds the likely errors WITHOUT any
clean reference set: estimate each class j's self-confidence threshold t_j
(mean predicted probability of j across examples labeled j), then flag any
example whose OUT-OF-SAMPLE probability for a *different* class beats that
class's threshold — the model, judged only on data it never trained on, is
more confident the example belongs elsewhere than typical members of that
class belong there at all.

Crucially the probabilities come from the blocked-CV pooled predictions the
trainer already computes — every row scored by a fold that never saw it, so a
memorised wrong label can't vouch for itself.

What happens to a flag, by provenance:
  - any flag  -> the window's training weight drops to SUSPECT_WEIGHT on the
    NEXT fit (never this one — no circularity), floored above zero so a wrong
    flag can't erase a right label.
  - confirmed/corrected (a human said it) -> also queued for a gentle re-ask
    via the morning recap ("You said cooking around 3 pm — it looked more like
    away. Which was it?"). The answer lands as a fresh CONFIRMED label, and
    the label merge (labeling/merge.py) lets the newer same-trust event win —
    resolution is automatic, no special repair path.

Flags cap at CAP_FRAC of scored rows (small-sample noise estimates above that
are unstable — Northcutt's own guidance) and classes with fewer than
MIN_CLASS_COUNT examples get no threshold (nor flags): you can't say what
"typical confidence for cleaning" is from three examples.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from math import ceil

import pandas as pd

log = logging.getLogger(__name__)

SUSPECT_WEIGHT = 0.3      # training-weight multiplier for flagged windows
CAP_FRAC = 0.10           # flag at most this share of scored rows
MIN_CLASS_COUNT = 5       # no thresholds (or flags) for tinier classes
LEDGER_MAX = 200          # keep the ledger bounded
_KEY = "labels.suspects.{pid}"
HUMAN = ("confirmed", "corrected")


def confident_flags(probs: pd.DataFrame, y: pd.Series) -> list[dict]:
    """Cleanlab-style off-diagonal flags from pooled OOS probabilities.

    probs: rows = scored windows (index = window ts), columns = classes.
    y: the given label per row, same index. Returns flags sorted worst-first
    (lowest self-confidence), each:
      {ts, given, suggested, self_conf, suggested_p}
    """
    if probs is None or len(probs) == 0:
        return []
    counts = y.value_counts()
    scorable = [c for c in probs.columns
                if int(counts.get(c, 0)) >= MIN_CLASS_COUNT]
    if len(scorable) < 2:
        return []                      # nothing to confuse with
    # t_j: how confident the model typically is on examples that ARE class j
    thresholds = {c: float(probs.loc[y == c, c].mean()) for c in scorable}

    flags: list[dict] = []
    # probs and y come from the same pooled concat — positionally aligned, so
    # zip beats per-row .loc (which also trips on duplicate timestamps).
    for (ts, given), (_, row) in zip(y.items(), probs.iterrows()):
        if given not in thresholds:
            continue
        best_alt, best_p = None, 0.0
        for c in scorable:
            if c == given:
                continue
            p = float(row.get(c, 0.0))
            if p >= thresholds[c] and p > best_p:
                best_alt, best_p = c, p
        if best_alt is None:
            continue
        self_conf = float(row.get(given, 0.0))
        if best_p <= self_conf:
            continue                   # still likelier its own label — leave it
        flags.append({"ts": pd.Timestamp(ts).isoformat(), "given": str(given),
                      "suggested": best_alt, "self_conf": round(self_conf, 4),
                      "suggested_p": round(best_p, 4)})
    flags.sort(key=lambda f: f["self_conf"])
    return flags[:max(1, ceil(CAP_FRAC * len(y)))] if flags else []


def update_suspects(repo, person_id: str, node: str,
                    flags: list[dict], provenance: pd.Series) -> None:
    """Refresh this node's slice of the suspect ledger. Entries this node no
    longer flags are dropped (a re-label or retrain cleared them); `asked`
    survives a refresh so nobody is asked about the same window twice."""
    key = _KEY.format(pid=person_id)
    old = repo.get_setting(key) or {}
    if not isinstance(old, dict):
        old = {}
    fresh: dict = {k: v for k, v in old.items()
                   if isinstance(v, dict) and v.get("node") != node}
    prov_by_ts = {pd.Timestamp(ts).isoformat(): str(p)
                  for ts, p in provenance.items()}
    now = datetime.now(timezone.utc).isoformat()
    for f in flags:
        prior = old.get(f["ts"]) or {}
        fresh[f["ts"]] = {**f, "node": node,
                          "provenance": prov_by_ts.get(f["ts"], "bootstrap"),
                          "asked": bool(prior.get("asked")), "at": now}
    if len(fresh) > LEDGER_MAX:        # newest first, keep the worst offenders
        keep = sorted(fresh.items(), key=lambda kv: kv[1].get("self_conf", 1.0))
        fresh = dict(keep[:LEDGER_MAX])
    repo.set_setting(key, fresh)


def suspect_multipliers(repo, person_id: str, index) -> pd.Series:
    """Per-window training-weight multiplier (1.0 or SUSPECT_WEIGHT) aligned to
    `index`. Reads LAST train's ledger — flags never touch the fit that raised
    them, which keeps the estimate honest and the loop non-circular."""
    out = pd.Series(1.0, index=index)
    try:
        ledger = repo.get_setting(_KEY.format(pid=person_id)) or {}
        if not isinstance(ledger, dict) or not ledger:
            return out
        stamps = [pd.Timestamp(ts) for ts in ledger]
        hits = out.index.intersection(pd.DatetimeIndex(stamps))
        if len(hits):
            out.loc[hits] = SUSPECT_WEIGHT
    except Exception:
        log.debug("suspect multipliers failed — neutral weights", exc_info=True)
    return out


def suspects_to_ask(repo, person_id: str, max_n: int = 2) -> list[dict]:
    """Human-provenance suspects not yet re-asked, worst first. Machine labels
    (bootstrap/rules/import) don't need human arbitration — down-weighting is
    their whole treatment."""
    ledger = repo.get_setting(_KEY.format(pid=person_id)) or {}
    if not isinstance(ledger, dict):
        return []
    todo = [{**v, "ts": k} for k, v in ledger.items()
            if isinstance(v, dict) and not v.get("asked")
            and v.get("provenance") in HUMAN]
    todo.sort(key=lambda f: f.get("self_conf", 1.0))
    return todo[:max_n]


def mark_asked(repo, person_id: str, ts_list: list[str]) -> None:
    key = _KEY.format(pid=person_id)
    ledger = repo.get_setting(key) or {}
    changed = False
    for ts in ts_list:
        if isinstance(ledger.get(ts), dict):
            ledger[ts]["asked"] = True
            changed = True
    if changed:
        repo.set_setting(key, ledger)

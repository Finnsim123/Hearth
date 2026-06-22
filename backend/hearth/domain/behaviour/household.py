"""Household co-occurrence — how two people's activities relate in time.

The ONLY cross-person view. It's privacy-sensitive (it exposes one person's
patterns to another), so it is strictly opt-in and consensual: nothing is computed
or returned unless BOTH people have set `behaviour.share.<person_id>`. The maths is
a window-aligned cross-tab; the care is all in the gating (the caller enforces it).

cooccurrence() is pure. Conditional framing: "when A is X, B is usually Y (p%)".
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .summary import UNKNOWN, _parse, _state


class CoocItem(BaseModel):
    a: str                         # person A's activity
    b: str                         # person B's most common activity meanwhile
    minutes: float                 # overlap in that pair
    frac: float                    # minutes(a,b) / minutes A spent in `a` (0..1)


def _buckets(rows: list[dict], window_min: int) -> dict[datetime, str]:
    secs = window_min * 60
    out: dict[datetime, str] = {}
    for r in rows:
        if not r.get("time"):
            continue
        st = _state(r)
        if st == UNKNOWN:
            continue
        ts = _parse(r["time"])
        out[datetime.fromtimestamp((int(ts.timestamp()) // secs) * secs,
                                   ts.tzinfo)] = st
    return out


def cooccurrence(a_rows: list[dict], b_rows: list[dict], *, window_min: int = 30,
                 min_state_min: float = 60.0, top: int = 12) -> list[CoocItem]:
    """For each of A's activities (with enough overlapping time), the single most
    common thing B was doing meanwhile, and how often. Self-pairs (a == b) kept —
    "you both cook together" is a real, interesting co-occurrence."""
    A, B = _buckets(a_rows, window_min), _buckets(b_rows, window_min)
    pair: dict[tuple[str, str], int] = {}
    amarg: dict[str, int] = {}
    for bkt, av in A.items():
        bv = B.get(bkt)
        if bv is None:
            continue
        pair[(av, bv)] = pair.get((av, bv), 0) + 1
        amarg[av] = amarg.get(av, 0) + 1
    items: list[CoocItem] = []
    for av, total in amarg.items():
        if total * window_min < min_state_min:
            continue
        bs = [(bv, c) for (aa, bv), c in pair.items() if aa == av]
        bv, c = max(bs, key=lambda x: x[1])
        items.append(CoocItem(a=av, b=bv, minutes=c * window_min,
                              frac=round(c / total, 4)))
    items.sort(key=lambda it: -it.minutes)
    return items[:top]


# ── consent helpers (settings-backed, no schema change) ──────────────────────
def shares(repo, person_id: str) -> bool:
    return bool(repo.get_setting(f"behaviour.share.{person_id}"))


def set_share(repo, person_id: str, enabled: bool) -> None:
    repo.set_setting(f"behaviour.share.{person_id}", bool(enabled))


def opted_in_ids(repo) -> list[str]:
    out = []
    try:
        for p in repo.persons():
            if getattr(p, "enabled", True) and shares(repo, p.id):
                out.append(p.id)
    except Exception:
        pass
    return out

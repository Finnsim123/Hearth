"""Presence gate — home/away is a SENSOR READING, not a prediction.

A person tracker that says `not_home` is ground truth: the person IS away, no
inference needed. Letting the model "predict" away from time/alarm features is
strictly worse — it never learns a rare class well and falls back on the clock,
so it gets stuck on "home" and never flips when you actually leave.

So at inference we GATE the model's coarse probability row with the person
tracker (PERSON-role bindings owned by that person):
  * tracker says away  → force "away" (instant, certain)
  * tracker says home  → the model may NOT say away (zero it, renormalize)
  * no tracker / unknown → leave the model alone

home_last (person recipe): 1.0 home · 0.0 not_home · -1.0 imputed-unknown.
"""
from __future__ import annotations

from ..schemas import Binding, Role

AWAY = "away"


def presence_state(row, bindings: list[Binding], person_id: str) -> str | None:
    """'home' | 'away' | None from this person's tracker(s). Any tracker
    reading home wins (you're home if you're home on either phone)."""
    cols = [f"{b.name}_home_last" for b in bindings
            if b.role == Role.PERSON and b.person_id == person_id]
    seen = [row[c] for c in cols if c in row.index and row[c] in (0.0, 1.0)]
    if not seen:
        return None
    return "home" if any(v == 1.0 for v in seen) else AWAY


def gate_row(row, presence: str | None):
    """Apply the presence verdict to a coarse probability Series. Returns a
    (possibly new) row; argmax it afterwards."""
    if presence == AWAY and AWAY in row.index:
        out = row * 0.0
        out[AWAY] = 1.0
        return out
    if presence == "home" and AWAY in row.index and row[AWAY] > 0:
        out = row.copy()
        out[AWAY] = 0.0
        total = float(out.sum())
        return out / total if total > 0 else out
    return row

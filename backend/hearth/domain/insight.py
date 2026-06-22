"""Model-insight summary (UX10) — a plain-language "how's the model doing?".

The buddy is a phase narrator, not a chat agent, so rather than bolt on an LLM
Q&A loop this gives the buddy (and the UI) a deterministic, honest summary of the
live model's health built from metrics the pipeline already produces: the unbiased
gold accuracy, whether its confidence is trustworthy (calibration), whether the
home has drifted, and where it's weakest (slice). Cheap, no LLM round-trip.
"""
from __future__ import annotations

GOLD_MIN = 30


def model_insight(person_id: str, repo) -> dict:
    """Structured + one-line summary of the live model's honest health for one
    person. Returns {"summary": str, "facts": {...}} or a no-model note."""
    record = next((m for m in repo.models(person_id)
                   if m.promoted and m.node == "root"), None)
    if record is None:
        return {"summary": "No live model yet — still learning the routine.", "facts": {}}
    mt = record.metrics or {}
    facts: dict = {"version": record.version}
    bits: list[str] = []

    n_gold = mt.get("n_gold", 0)
    if n_gold >= GOLD_MIN and mt.get("accuracy_gold") is not None:
        acc = mt["accuracy_gold"]
        facts["accuracy_gold"] = acc
        bits.append(f"Right about {acc * 100:.0f}% of the time on real-world spot-checks.")
    else:
        facts["gathering"] = f"{n_gold}/{GOLD_MIN}"
        bits.append(f"Still gathering spot-checks ({n_gold}/{GOLD_MIN}) before I can claim a fair accuracy.")

    cal = mt.get("calibration") or {}
    if "ece" in cal:
        facts["ece"] = cal["ece"]
        bits.append("My confidence is trustworthy." if cal["ece"] <= 0.1
                    else "My confidence runs a little off — read it with a pinch of salt.")

    fb = mt.get("flat_baseline") or {}
    mine = mt.get("accuracy_gold") or mt.get("accuracy_confirmed")
    flat = fb.get("accuracy_gold") or fb.get("accuracy_confirmed")
    if mine is not None and flat is not None:
        facts["beats_flat"] = mine >= flat
        if mine < flat:
            bits.append("A simpler flat model does just as well here — the hierarchy isn't adding much.")

    drift = repo.get_setting(f"drift.{person_id}") or {}
    drifted = drift.get("drifted") or []
    if drifted:
        facts["drifted"] = drifted[:3]
        bits.append(f"{len(drifted)} signal(s) have drifted since I trained (e.g. {', '.join(drifted[:3])}) "
                    "— a retrain would recalibrate.")

    weak = _weakest_slice(mt.get("slices"))
    if weak:
        facts["weakest_slice"] = weak
        bits.append(f"Weakest on {weak['activity']} in the {weak['daypart']} ({weak['acc'] * 100:.0f}%).")

    return {"summary": " ".join(bits), "facts": facts}


def _weakest_slice(slices: dict | None) -> dict | None:
    """Lowest-accuracy daypart×activity cell with enough windows to judge."""
    if not slices:
        return None
    dayparts = slices.get("dayparts", [])
    worst = None
    for row in slices.get("by_activity_daypart", []):
        for i, cell in enumerate(row.get("cells", [])):
            if cell.get("n", 0) >= 5 and cell.get("acc") is not None:
                if worst is None or cell["acc"] < worst["acc"]:
                    worst = {"activity": row["activity"], "daypart": dayparts[i] if i < len(dayparts) else str(i),
                             "acc": cell["acc"], "n": cell["n"]}
    return worst

"""Per-person controls exposed two-way over the output channel (MQTT today).

Two controls, both stored as settings and honoured by the rest of the domain:

  * questions opt-out — a switch. OFF = stop asking this person training questions
    (the asking policy skips them). ON = ask normally.
  * manual override — a select. Pin the person's published activity to a chosen
    one until set back to "auto"; the inference path then serves that instead of
    the model's guess.

Pure-ish helpers (settings reads/writes only), so the MQTT adapter, the asking
policy and the inference path all share one source of truth.
"""
from __future__ import annotations

AUTO = "auto"   # the select's "no override" option


# ── questions opt-out ────────────────────────────────────────────────────────
def set_questions_optout(repo, person_id: str, optout: bool) -> None:
    repo.set_setting(f"questions.optout.{person_id}", bool(optout))


def questions_disabled(repo, person_id: str) -> bool:
    try:
        return bool(repo.get_setting(f"questions.optout.{person_id}"))
    except Exception:
        return False


# ── manual override ──────────────────────────────────────────────────────────
def set_override(repo, person_id: str, value: str, valid_slugs) -> str | None:
    """Set the override to a valid activity slug, or clear it (returns the stored
    slug, or None when cleared). "auto", empty, or an unknown slug all clear it."""
    v = (value or "").strip()
    if not v or v.lower() == AUTO or v not in set(valid_slugs):
        repo.set_setting(f"override.{person_id}", None)
        return None
    repo.set_setting(f"override.{person_id}", v)
    return v


def active_override(repo, person_id: str) -> str | None:
    try:
        v = repo.get_setting(f"override.{person_id}")
        return v if isinstance(v, str) and v else None
    except Exception:
        return None


def override_prediction(pred, slug: str):
    """A Prediction pinned to `slug`: full confidence, marked as a manual override
    so the UI and metrics never mistake it for a model output."""
    return pred.model_copy(update={
        "predicted": slug, "smoothed": slug, "confidence": 1.0,
        "probabilities": {slug: 1.0}, "model_version": "override",
        "explanation": [("manual override", 1.0)],
        "parent": None, "coarse_confidence": None, "evidence": None})


# ── command parsing (from the MQTT command topics) ───────────────────────────
def parse_command(topic: str):
    """`hearth/<person>/<control>/set` -> (person_id, control) for the two-way
    controls, else None."""
    parts = topic.split("/")
    if (len(parts) == 4 and parts[0] == "hearth" and parts[3] == "set"
            and parts[2] in ("questions", "override")):
        return parts[1], parts[2]
    return None


def apply_command(repo, topic: str, payload: str, valid_slugs) -> tuple | None:
    """Apply a control command; returns (control, person_id, new_state) so the
    caller can republish the entity's state, or None if the topic isn't a command.
    """
    parsed = parse_command(topic)
    if parsed is None:
        return None
    pid, control = parsed
    if control == "questions":
        on = (payload or "").strip().upper() in ("ON", "TRUE", "1")
        set_questions_optout(repo, pid, not on)        # switch ON = questions enabled
        return ("questions", pid, "ON" if on else "OFF")
    slug = set_override(repo, pid, payload or "", valid_slugs)
    return ("override", pid, slug or AUTO)

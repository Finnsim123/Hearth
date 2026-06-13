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

from datetime import datetime, timedelta, timezone

AUTO = "auto"   # the select's "no override" option

# While an override is this fresh, each predicted window is also written as a
# CONFIRMED label so the model learns from your correction. After that the
# override keeps pinning the display but stops writing labels — so a pin you
# forget to clear can't quietly poison training with hours of wrong "truth".
OVERRIDE_LABEL_WINDOW_MIN = 60


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
    slug, or None when cleared). "auto", empty, or an unknown slug all clear it.
    Stamps the set-time so labeling can be bounded to a freshness window."""
    v = (value or "").strip()
    if not v or v.lower() == AUTO or v not in set(valid_slugs):
        repo.set_setting(f"override.{person_id}", None)
        return None
    repo.set_setting(f"override.{person_id}",
                     {"activity": v, "set_at": datetime.now(timezone.utc).isoformat()})
    return v


def _override_raw(repo, person_id: str):
    try:
        return repo.get_setting(f"override.{person_id}")
    except Exception:
        return None


def active_override(repo, person_id: str) -> str | None:
    raw = _override_raw(repo, person_id)
    if isinstance(raw, dict):                 # {"activity", "set_at"}
        a = raw.get("activity")
        return a if isinstance(a, str) and a else None
    return raw if isinstance(raw, str) and raw else None   # tolerate a bare slug


def override_set_at(repo, person_id: str) -> datetime | None:
    raw = _override_raw(repo, person_id)
    if isinstance(raw, dict) and raw.get("set_at"):
        try:
            dt = datetime.fromisoformat(raw["set_at"])
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
    return None


def override_is_labeling(repo, person_id: str, now: datetime,
                         window_min: int = OVERRIDE_LABEL_WINDOW_MIN) -> bool:
    """True while an active override is fresh enough to write confirmed labels."""
    at = override_set_at(repo, person_id)
    return at is not None and (now - at) <= timedelta(minutes=window_min)


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

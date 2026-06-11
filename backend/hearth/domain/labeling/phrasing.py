"""Dynamic question phrasing — questions read like a housemate, not a cron job.

The phrasing MODE follows the shape of the model's uncertainty:

  confident   p1 high, clear gap      "Are you watching a movie right now?"
  toss_up     two classes neck&neck   "I can't tell if you're cooking or in
                                       bed — which is it?"
  unsure      flat distribution       "What are you up to right now?"

Each mode has several templates (picked by a hash of the window timestamp, so
re-sends of the SAME question are stable but consecutive questions vary).
Activities supply a verb phrase ("watching a movie"); custom activities
default to their name and can be edited in the taxonomy UI.
"""
from __future__ import annotations

from datetime import datetime

from ..schemas import Activity

# Fallback verb phrases for the built-in taxonomy; Activity.phrase overrides.
_DEFAULT_PHRASES = {
    "sleeping": "sleeping", "away": "out of the house", "home": "just at home",
    "cooking": "cooking", "eating": "eating", "movie": "watching something",
    "working": "working",
}

_CONFIDENT = [
    "Are you {p1} right now?",
    "Looks like you're {p1} — right?",
    "Quick check: {p1} at the moment?",
]
_TOSS_UP = [
    "I can't tell if you're {p1} or {p2} — which is it?",
    "Hmm — {p1}, or {p2}?",
    "Not sure right now: are you {p1} or {p2}?",
]
_UNSURE = [
    "What are you up to right now?",
    "I'm a bit lost — what's happening at the moment?",
    "Help me out: what are you doing right now?",
]
# Shown after the user rejects the first guess ("No" / "Other"): the next batch
# of candidates. The chain repeats until the right one is tapped.
_FOLLOWUP = [
    "OK — was it one of these instead?",
    "Got it. Which of these is right?",
    "Then what was it — any of these?",
]

CONFIDENT_GAP = 0.25   # p1 - p2 above this -> confident mode
TOSS_UP_GAP = 0.15     # p1 - p2 below this (and p1 sane) -> toss-up mode
MAX_BUTTONS = 3        # HA actionable notifications reliably show ~3 actions


def verb_phrase(slug: str, activities: list[Activity]) -> str:
    for a in activities:
        if a.slug == slug and a.phrase:
            return a.phrase
    return _DEFAULT_PHRASES.get(slug, slug.replace("_", " "))


def _pick(templates: list[str], window_ts: datetime) -> str:
    return templates[int(window_ts.timestamp() // 1800) % len(templates)]


def phrase_question(
    probabilities: dict[str, float],
    activities: list[Activity],
    window_ts: datetime,
) -> tuple[str, list[str]]:
    """-> (message, option_slugs ordered for buttons, max 3).

    Button order = the answer the user most likely needs first:
      confident: [predicted, runner_up, third]
      toss_up:   [p1, p2, third]
      unsure:    top three classes
    """
    ranked = sorted(probabilities.items(), key=lambda kv: -kv[1])
    slugs = [s for s, _ in ranked[:3]]
    p1 = ranked[0][1]
    p2 = ranked[1][1] if len(ranked) > 1 else 0.0
    v1 = verb_phrase(slugs[0], activities)

    if p1 - p2 >= CONFIDENT_GAP and p1 >= 0.4:
        msg = _pick(_CONFIDENT, window_ts).format(p1=v1)
    elif p1 - p2 <= TOSS_UP_GAP and len(slugs) >= 2:
        msg = _pick(_TOSS_UP, window_ts).format(
            p1=v1, p2=verb_phrase(slugs[1], activities))
    else:
        msg = _pick(_UNSURE, window_ts)
    return msg, slugs


def option_universe(probabilities: dict[str, float], activities: list[Activity]) -> list[str]:
    """Every answerable activity, best-guess first: known classes by descending
    probability, then any remaining enabled activities (so the chain can always
    reach the right answer, even a class the model gave ~0). De-duplicated."""
    ranked = [s for s, _ in sorted(probabilities.items(), key=lambda kv: -kv[1])]
    enabled = [a.slug for a in activities if getattr(a, "enabled", True)]
    if enabled:
        ranked = [s for s in ranked if s in enabled]
        ranked += sorted(s for s in enabled if s not in ranked)
    out: list[str] = []
    for s in ranked:
        if s not in out:
            out.append(s)
    return out


def root_options(
    probabilities: dict[str, float], activities: list[Activity], window_ts: datetime,
) -> tuple[str, list[str], bool]:
    """First question: (message, option_slugs, has_more).
      confident  -> 1 option (Yes/No);   toss-up / unsure -> 2 options + Other.
    has_more = there are further candidates the escape button can reveal."""
    universe = option_universe(probabilities, activities)
    ranked = sorted(probabilities.items(), key=lambda kv: -kv[1])
    p1 = ranked[0][1] if ranked else 0.0
    p2 = ranked[1][1] if len(ranked) > 1 else 0.0
    v1 = verb_phrase(universe[0], activities) if universe else "around"
    if p1 - p2 >= CONFIDENT_GAP and p1 >= 0.4:
        opts = universe[:1]
        msg = _pick(_CONFIDENT, window_ts).format(p1=v1)
    elif p1 - p2 <= TOSS_UP_GAP and len(universe) >= 2:
        opts = universe[:2]
        msg = _pick(_TOSS_UP, window_ts).format(p1=v1, p2=verb_phrase(universe[1], activities))
    else:
        opts = universe[:2]
        msg = _pick(_UNSURE, window_ts)
    return msg, opts, len(universe) > len(opts)


def next_batch(
    probabilities: dict[str, float], activities: list[Activity], asked: list[str],
) -> tuple[list[str], bool]:
    """Follow-up: the next options to offer, excluding everything already shown.
    Returns (batch, has_more). Leaves room for an 'Other' escape unless this
    batch finishes the list."""
    remaining = [s for s in option_universe(probabilities, activities) if s not in set(asked)]
    if len(remaining) <= MAX_BUTTONS:
        return remaining, False                 # final batch — force a pick, no escape
    return remaining[:MAX_BUTTONS - 1], True     # leave a slot for "Other"


def followup_message(window_ts: datetime) -> str:
    return _pick(_FOLLOWUP, window_ts)


def button_titles(option_slugs: list[str], activities: list[Activity]) -> list[str]:
    """Short button labels (Yes-style first button when confident is handled
    by the notifier: it prepends ✓ to the first option)."""
    by_slug = {a.slug: a for a in activities}
    out = []
    for slug in option_slugs:
        a = by_slug.get(slug)
        out.append((a.name if a else slug.replace("_", " ")).capitalize())
    return out

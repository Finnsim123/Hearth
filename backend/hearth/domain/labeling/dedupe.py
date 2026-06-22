"""Activity de-duplication — never mint a near-synonym of an activity that already
exists. The classic trap: the AI names an 'away' cluster "Alex out of the house",
creating a duplicate of the reserved coarse state `away`.

`canonical_activity(name, activities, person_names)` returns an EXISTING slug when
the proposed name is really the same thing (exact match, or a synonym of a reserved
coarse state that already exists), else None → it's genuinely new. Pure + deterministic
so it works without the LLM and is the safety net behind it.
"""
from __future__ import annotations

import re

from ..schemas import Activity

# Synonyms for the reserved coarse states. away/asleep are matched aggressively
# (the common duplicate); home conservatively (lots of fine activities live
# "at home", so only near-exact phrasings should fold into it).
_RESERVED: dict[str, set[str]] = {
    "away": {"away", "out", "gone", "absent", "outside", "not home", "not in",
             "out of the house", "away from home", "out and about", "left the house",
             "not at home", "out of home"},
    "asleep": {"asleep", "sleeping", "sleep", "napping", "nap", "in bed", "asleep in bed"},
    "home": {"home", "at home", "in the house", "back home", "present", "indoors"},
}
# Single tokens distinctive enough to fold into a reserved state on their own.
_RESERVED_TOKENS: dict[str, set[str]] = {
    "away": {"away", "out", "gone", "absent", "outside"},
    "asleep": {"asleep", "sleeping", "sleep", "napping"},
    "home": set(),                              # too ambiguous to match on one token
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def canonical_activity(name: str, activities: list[Activity],
                       person_names: list[str] | None = None) -> str | None:
    """Existing slug this name duplicates, or None if it's genuinely new."""
    raw = _norm(name)
    if not raw:
        return None
    # strip household member name tokens: "alex out of the house" → "out of the house"
    pn = {t for p in (person_names or []) for t in _norm(p).split()}
    stripped = " ".join(t for t in raw.split() if t not in pn) or raw

    by_slug = {a.slug for a in activities}
    by_name = {_norm(a.name): a.slug for a in activities}
    by_slug_words = {_norm(a.slug.replace("_", " ")): a.slug for a in activities}

    for cand in (stripped, raw):
        # 1. exact match against an existing activity (slug, name, or slug-as-words)
        if _slugify(cand) in by_slug:
            return _slugify(cand)
        if cand in by_name:
            return by_name[cand]
        if cand in by_slug_words:
            return by_slug_words[cand]

    # 2. reserved coarse-state synonyms — only fold into a state that EXISTS
    toks = set(stripped.split())
    for slug, syns in _RESERVED.items():
        if slug not in by_slug:
            continue
        if stripped in syns or raw in syns:
            return slug
        if any(len(s.split()) > 1 and s in stripped for s in syns):   # phrase contained
            return slug
        if toks & _RESERVED_TOKENS[slug]:                              # distinctive token
            return slug
    return None


def dedupe_suggestions(suggestions: list[dict], activities: list[Activity],
                       person_names: list[str] | None = None) -> list[dict]:
    """Rewrite any 'new activity' suggestion that's really an existing one into an
    'existing' suggestion pointing at that slug — so the UI offers 'Away' instead
    of a duplicate '+ Alex out of the house'."""
    by_slug = {a.slug: a for a in activities}
    out = []
    for s in suggestions or []:
        s = dict(s)
        if not s.get("slug"):
            dup = canonical_activity(s.get("name", ""), activities, person_names)
            if dup:
                s["slug"], s["kind"] = dup, "existing"
                if dup in by_slug:
                    s["name"] = by_slug[dup].name
        out.append(s)
    return out

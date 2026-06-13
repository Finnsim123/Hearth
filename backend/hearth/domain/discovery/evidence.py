"""Evidence card for a discovered pattern — the deterministic context that
turns "name this statistical blob" into "recognise this situation". Pure code,
no LLM: it humanises the signature, says WHEN and WHERE the pattern happens,
how often it's a weekday, what named activity tends to sit before/after it, and
which existing activity it most resembles. Works with no AI key; doubles as the
compact, metadata-only prompt the LLM name-suggester is fed (see openrouter_llm).
"""
from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..schemas import ClusterCard
from .lexicon import humanize_feature

log = logging.getLogger(__name__)

WINDOW_MIN = 30


def _daypart(hour: int) -> str:
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def _when(hist: list[int]) -> dict | None:
    total = sum(hist)
    if total == 0:
        return None
    order = sorted(range(24), key=lambda h: hist[h], reverse=True)
    chosen, acc = [], 0
    for h in order:                       # smallest set of hours covering ~70%
        chosen.append(h)
        acc += hist[h]
        if acc / total >= 0.7:
            break
    chosen.sort()
    lo, hi = chosen[0], chosen[-1]
    peak = order[0]
    return {"span": f"{lo:02d}:00–{(hi + 1) % 24:02d}:00",
            "peak_hour": peak, "daypart": _daypart(peak)}


def _cadence(windows: list[datetime], tz: str) -> dict | None:
    if not windows:
        return None
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = timezone.utc
    wk = 0
    for w in windows:
        local = w.astimezone(zone) if w.tzinfo else w.replace(tzinfo=timezone.utc).astimezone(zone)
        if local.weekday() < 5:
            wk += 1
    frac = wk / len(windows)
    phrase = ("mostly on weekdays" if frac >= 0.75 else
              "mostly at weekends" if frac <= 0.25 else "any day of the week")
    return {"weekday_frac": round(frac, 2), "phrase": phrase}


def _adjacency(card: ClusterCard, tsdb, names: dict[str, str]) -> dict | None:
    """What named activity tends to sit just before / just after this pattern."""
    if not card.example_windows or tsdb is None:
        return None
    try:
        lo = min(card.example_windows) - timedelta(hours=1)
        hi = max(card.example_windows) + timedelta(hours=1)
        preds = tsdb.read_predictions(card.person_id, lo, hi)
        seq = sorted((datetime.fromisoformat(p["time"]),
                      p.get("smoothed") or p.get("predicted")) for p in preds)
        if not seq:
            return None
        times = [t for t, _ in seq]
        before: Counter = Counter()
        after: Counter = Counter()
        for w in card.example_windows:
            w0 = w if w.tzinfo else w.replace(tzinfo=timezone.utc)
            i = bisect_left(times, w0)
            if i - 1 >= 0 and seq[i - 1][1]:
                before[seq[i - 1][1]] += 1
            j = bisect_right(times, w0 + timedelta(minutes=WINDOW_MIN))
            if j < len(seq) and seq[j][1]:
                after[seq[j][1]] += 1
    except Exception:
        log.debug("adjacency failed", exc_info=True)
        return None
    out: dict = {}
    if before:
        out["before"] = names.get(before.most_common(1)[0][0], before.most_common(1)[0][0])
    if after:
        out["after"] = names.get(after.most_common(1)[0][0], after.most_common(1)[0][0])
    return out or None


def _contrast(card: ClusterCard, repo) -> dict | None:
    """The named pattern this one most resembles (shared top features)."""
    try:
        mine = {f for f, _ in card.signature[:4]}
        if not mine:
            return None
        names = {a.slug: a.name for a in repo.activities()}
        best, best_overlap = None, 0
        for other in repo.clusters():
            if other.id == card.id or not other.named_activity_slug:
                continue
            shared = mine & {f for f, _ in other.signature[:4]}
            if len(shared) > best_overlap:
                best, best_overlap = other, len(shared)
        if best is None or best_overlap < 2:
            return None
        slug = best.named_activity_slug
        return {"slug": slug, "name": names.get(slug, slug), "shared": best_overlap}
    except Exception:
        return None


def _summary(plain: list[dict], when: dict | None, where: list[str],
             cadence: dict | None) -> str:
    bits: list[str] = []
    if cadence and when:
        bits.append(f"{cadence['phrase'].capitalize()} in the {when['daypart']}, "
                    f"around {when['span']}")
    elif when:
        bits.append(f"Usually in the {when['daypart']}, around {when['span']}")
    if where:
        bits.append("mostly " + (where[0] if len(where) == 1
                                 else f"{where[0]} and {where[1]}"))
    lead = ", ".join(bits) + "." if bits else ""
    # the single most defining signal, in plain words
    if plain:
        lead += f" Defined by: {plain[0]['label'].lower()}."
    return lead.strip()


def build_evidence(card: ClusterCard, repo, tsdb) -> dict:
    """Assemble the full evidence card. Every part is best-effort and degrades
    to None/empty so a fresh install still renders something useful."""
    bindings = repo.bindings()
    persons = {p.id: p.name for p in repo.persons()}
    act_names = {a.slug: a.name for a in repo.activities()}
    tz = repo.get_setting("timezone", "UTC") or "UTC"

    plain = [humanize_feature(feat, z, bindings, persons)
             for feat, z in card.signature]
    # rooms, ranked by how strongly they appear in the signature
    room_weight: Counter = Counter()
    for item, (_, z) in zip(plain, card.signature):
        if item["room"]:
            room_weight[item["room"]] += abs(z)
    where = [r for r, _ in room_weight.most_common(3)]

    when = _when(card.hour_histogram)
    cadence = _cadence(card.example_windows, tz)
    return {
        "plain": plain,
        "when": when,
        "where": where,
        "cadence": cadence,
        "adjacency": _adjacency(card, tsdb, act_names),
        "contrast": _contrast(card, repo),
        "summary": _summary(plain, when, where, cadence),
    }

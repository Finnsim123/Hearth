"""Confirm-yesterday harvester — batched retrospective gold labels.

The live asking policy (active.py) interrupts the moment: it may only ask a
few times a day, at inconvenient moments, and silent activities (sleep) are
never pushed at all. This module runs once each morning and mines YESTERDAY:
it segments the day's predictions into activity runs, finds the ones worth a
human glance, and drops a small batch of questions in the Inbox — plus one
summary push ("30 seconds to confirm yesterday?") instead of N pings.

Two kinds of picks, mirroring the live policy's honesty split:
  - targeted ("uncertain"): the least-confident / flappiest runs — high
    training value, but a BIASED sample, so never gold.
  - one random ("explore"): drawn uniformly from all eligible runs — an
    unbiased probe of yesterday, so its answer lands as a GOLD eval label
    (the answer endpoint already flags gold on ask_reason == "explore").

Asking about the past is deliberate: recall for "what were you doing around
3 pm yesterday?" is good enough at run granularity (we only ask about runs
≥ 15 min), and a batched morning recap costs one interaction instead of
scattering pushes through the day (question fatigue is the real budget).
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..schemas import Question

log = logging.getLogger(__name__)

MAX_TARGETED = 3          # targeted picks per person per morning
MIN_RUN_MIN = 15          # don't ask about blips nobody remembers
MAX_OPEN_QUESTIONS = 6    # skip the harvest while the inbox is already piled up
FLAPPY_RUN_MIN = 35       # runs shorter than this next to different labels
                          # count as "flappy" (model was see-sawing)


def _day_bounds_utc(repo, now: datetime) -> tuple[datetime, datetime]:
    """Yesterday's [local midnight, local midnight) as UTC instants."""
    tz = ZoneInfo(repo.get_setting("timezone", "UTC") or "UTC")
    today_local = now.astimezone(tz).date()
    start_local = datetime.combine(today_local - timedelta(days=1), time.min, tz)
    end_local = datetime.combine(today_local, time.min, tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def segment_runs(preds: list[dict]) -> list[dict]:
    """Contiguous same-label runs from prediction rows (any order).
    Each: {label, start, end, minutes, mean_conf, rows:[...]} — smoothed label,
    because that's what the person saw on the dashboard and will recognise."""
    rows = sorted(preds, key=lambda r: r["time"])
    runs: list[dict] = []
    for r in rows:
        label = r.get("smoothed") or r["predicted"]
        ts = datetime.fromisoformat(r["time"])
        if runs and runs[-1]["label"] == label \
                and (ts - runs[-1]["end"]).total_seconds() <= 600:
            runs[-1]["end"] = ts
            runs[-1]["rows"].append(r)
        else:
            runs.append({"label": label, "start": ts, "end": ts, "rows": [r]})
    for run in runs:
        run["minutes"] = (run["end"] - run["start"]).total_seconds() / 60 + 5
        run["mean_conf"] = sum(x["confidence"] for x in run["rows"]) / len(run["rows"])
    return runs


def _covered(run: dict, labels: list) -> bool:
    """A human already labeled inside this run — nothing to ask."""
    return any(run["start"] <= lab.window_ts <= run["end"] for lab in labels)


def _mid_row(run: dict) -> dict:
    return run["rows"][len(run["rows"]) // 2]


def pick_recaps(preds: list[dict], labels: list,
                asked_ts: set[datetime], rng: random.Random | None = None) -> list[dict]:
    """Choose which of yesterday's runs to ask about.

    Returns picks as {run, reason} with reason "uncertain" (targeted) or
    "explore" (the one random, gold-eligible pick). Pure — no I/O — so the
    selection logic is unit-testable.
    """
    rng = rng or random.Random()
    runs = segment_runs(preds)
    eligible = []
    for i, run in enumerate(runs):
        if run["minutes"] < MIN_RUN_MIN:
            continue
        if _covered(run, labels):
            continue
        mid_ts = datetime.fromisoformat(_mid_row(run)["time"])
        if mid_ts in asked_ts:
            continue                      # already asked about this window
        flappy_neighbours = sum(
            1 for j in (i - 1, i + 1)
            if 0 <= j < len(runs) and runs[j]["minutes"] < FLAPPY_RUN_MIN)
        run["_score"] = (1.0 - run["mean_conf"]) + 0.15 * flappy_neighbours
        eligible.append(run)
    if not eligible:
        return []

    # the gold probe FIRST, drawn uniformly — choosing it after the targeted
    # picks would condition on "not interesting", which is no longer unbiased.
    gold = rng.choice(eligible)
    picks = [{"run": gold, "reason": "explore"}]
    targeted = sorted((r for r in eligible if r is not gold),
                      key=lambda r: -r["_score"])[:MAX_TARGETED]
    picks += [{"run": r, "reason": "uncertain"} for r in targeted]
    return picks


async def run_harvest(repo, tsdb, notifier=None) -> dict:
    """The morning job: for each enabled person, mine yesterday and file the
    recap questions. One summary push per person (mute- and consent-gated by
    the notifier); the questions themselves are inbox-only."""
    from ..controls import questions_disabled
    from .phrasing import root_options

    now = datetime.now(timezone.utc)
    start, end = _day_bounds_utc(repo, now)
    out: dict[str, int] = {}
    for person in repo.persons():
        if not getattr(person, "enabled", True):
            continue
        if questions_disabled(repo, person.id):
            continue
        if len(repo.open_questions(person.id)) >= MAX_OPEN_QUESTIONS:
            log.info("harvest: %s inbox already full — skipping", person.id)
            continue
        try:
            preds = tsdb.read_predictions(person.id, start, end)
            if not preds:
                continue
            labels = [lab for lab in tsdb.read_labels(person.id, start, end)
                      if lab.provenance.value in ("confirmed", "corrected")]
            asked = repo.question_windows_since(person.id, start)
            picks = pick_recaps(preds, labels, asked)
        except Exception:
            log.exception("harvest: mining yesterday failed for %s", person.id)
            continue
        n = 0
        try:
            activities = repo.activities()
        except Exception:
            activities = []
        for pick in picks:
            run, mid = pick["run"], _mid_row(pick["run"])
            probs = mid.get("probs") or {run["label"]: run["mean_conf"]}
            _msg, alternatives, _more = root_options(
                probs, activities, datetime.fromisoformat(mid["time"]))
            q = Question(person_id=person.id,
                         window_ts=datetime.fromisoformat(mid["time"]),
                         predicted=run["label"], confidence=run["mean_conf"],
                         alternatives=alternatives or [run["label"]],
                         asked=list(alternatives or [run["label"]]),
                         probabilities=probs, ask_reason=pick["reason"],
                         channel="inbox")
            repo.save_question(q)
            n += 1

        # confident-learning re-asks: human labels the trainer flagged as
        # likely errors ("you said cooking, it looked like away"). Two options
        # only — the disputed label and the model's suggestion — and the fresh
        # CONFIRMED answer outranks the old one at the next label merge.
        try:
            from ..training.label_quality import mark_asked, suspects_to_ask
            suspects = suspects_to_ask(repo, person.id, max_n=2)
            for sp in suspects:
                repo.save_question(Question(
                    person_id=person.id,
                    window_ts=datetime.fromisoformat(sp["ts"]),
                    predicted=sp["given"], confidence=sp.get("self_conf", 0.0),
                    alternatives=[sp["given"], sp["suggested"]],
                    asked=[sp["given"], sp["suggested"]],
                    probabilities={sp["given"]: sp.get("self_conf", 0.0),
                                   sp["suggested"]: sp.get("suggested_p", 0.0)},
                    ask_reason="uncertain", channel="inbox"))
                n += 1
            if suspects:
                mark_asked(repo, person.id, [sp["ts"] for sp in suspects])
        except Exception:
            log.exception("harvest: suspect re-asks failed for %s", person.id)
        if n:
            out[person.id] = n
            if notifier is not None and person.has_device:
                try:
                    await notifier.notify(
                        person, "Quick recap of yesterday?",
                        f"{n} moment{'s' if n != 1 else ''} I'd like to double-check "
                        "— 30 seconds in the Inbox settles it.",
                        data={"url": "/inbox"})
                except Exception:
                    log.exception("harvest: summary push failed for %s", person.id)
    if out:
        log.info("confirm-yesterday harvest: %s",
                 ", ".join(f"{p}: {c}" for p, c in out.items()))
    return out

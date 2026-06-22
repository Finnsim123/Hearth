"""Newsletter orchestration — gather a person's week, optionally add an LLM
intro, render via newsletter.build_newsletter, and send through the EmailSender.

Kept apart from the pure renderer so that module stays DB/network-free. Data
comes from the same path the Behaviour page uses (read_predictions → summarize +
trends), so the email and the dashboard never disagree."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from html import escape

from .newsletter import DETAIL_LEVELS, build_newsletter
from .summary import summarize, trends

log = logging.getLogger(__name__)

_INTRO_LEN = {
    "overview": "one warm sentence",
    "medium": "two or three sentences",
    "detailed": "a short paragraph of three or four sentences",
}


def _person_accuracy(repo, person_id: str) -> float | None:
    """Best confirmed accuracy of this person's promoted model, if any."""
    try:
        best = None
        for m in repo.models():
            if not getattr(m, "promoted", False):
                continue
            if getattr(m, "person_id", None) not in (None, person_id):
                continue
            acc = (m.metrics or {}).get("accuracy_confirmed") or \
                  (m.metrics or {}).get("accuracy_bootstrap")
            if acc is not None:
                best = max(best or 0.0, float(acc))
        return best
    except Exception:
        return None


def gather(repo, tsdb, person, days: int = 7):
    """-> (name, summary_dict, trends_list, accuracy). Empty summary if no data."""
    tz = repo.get_setting("timezone", "UTC") or "UTC"
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = []
    if tsdb is not None:
        try:
            rows = tsdb.read_predictions(person.id, start, end)
        except Exception as exc:
            log.warning("newsletter: read_predictions failed for %s: %s", person.id, exc)
    summary = summarize(person.id, rows, tz=tz).model_dump(mode="json")
    trend_list = [c.model_dump(mode="json") for c in trends(person.id, rows, tz=tz)]
    return person.name, summary, trend_list, _person_accuracy(repo, person.id)


async def _llm_intro(repo, name, summary, trend_list, detail) -> str | None:
    """A short, warm prose intro via the configured LLM. Best-effort: returns
    None on any error or when no LLM is connected. Direct chat call (not the
    advisor's _chat, which post-processes JSON)."""
    conn = repo.get_connection("llm")
    if not conn:
        return None
    try:
        import aiohttp

        from ...adapters.openrouter_llm import DEFAULT_MODEL, choose_model
        model = choose_model((conn.get("options") or {}).get("model"), DEFAULT_MODEL)
        top = sorted(summary.get("totals", {}).items(), key=lambda kv: -kv[1])[:6]
        facts = "; ".join(f"{k.replace('_',' ')} {round(v/60,1)}h" for k, v in top) or "little activity"
        shifts = "; ".join((c.get("text") or c.get("activity", "")) for c in trend_list[:5]) or "none"
        system = ("You write a warm, concrete weekly home-habits note for one family "
                  "member. Friendly, never creepy or judgmental, no lists, no preamble. "
                  "Address them as 'you'.")
        user = (f"Member: {name}. Time by activity this week: {facts}. "
                f"Notable shifts vs last week: {shifts}. "
                f"Write {_INTRO_LEN.get(detail, _INTRO_LEN['medium'])} summarizing the week.")
        payload = {"model": model, "max_tokens": 320, "temperature": 0.4,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}]}
        url = f"{conn['url'].rstrip('/')}/chat/completions"
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload,
                              headers={"Authorization": f"Bearer {conn['token']}"},
                              timeout=aiohttp.ClientTimeout(total=60)) as r:
                if r.status >= 400:
                    return None
                data = await r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        content = (content or "").strip()
        if not content:
            return None
        # prose → safe HTML: escape, turn blank lines into paragraph breaks
        return "<br><br>".join(escape(p.strip()) for p in content.split("\n\n") if p.strip())
    except Exception as exc:
        log.warning("newsletter LLM intro failed: %s", exc)
        return None


def build_for_person(repo, tsdb, person, detail: str = "medium",
                     intro_html: str | None = None) -> tuple[str, str, str]:
    """Synchronous render (no network beyond the tsdb read). Returns
    (subject, html, text)."""
    name, summary, trend_list, accuracy = gather(repo, tsdb, person)
    return build_newsletter(name=name, summary=summary, trends_list=trend_list,
                            accuracy=accuracy, detail=detail, intro_html=intro_html)


async def build_async(repo, tsdb, person, detail: str = "medium",
                      with_llm: bool = True) -> tuple[str, str, str]:
    name, summary, trend_list, accuracy = gather(repo, tsdb, person)
    intro = await _llm_intro(repo, name, summary, trend_list, detail) if with_llm else None
    return build_newsletter(name=name, summary=summary, trends_list=trend_list,
                            accuracy=accuracy, detail=detail, intro_html=intro)


def current_detail(repo) -> str:
    d = repo.get_setting("newsletter.detail", "medium")
    return d if d in DETAIL_LEVELS else "medium"


async def send_weekly(deps) -> dict:
    """Send the newsletter to every opted-in member with an email. Returns a
    small report. Skips silently when SMTP isn't configured."""
    import asyncio

    repo = deps["repo"]
    tsdb = deps.get("tsdb")
    sender = deps.get("email")
    if sender is None or not sender.configured():
        log.info("newsletter: SMTP not configured — skipping weekly send")
        return {"sent": 0, "skipped": "no_smtp"}
    detail = current_detail(repo)
    sent, failed = 0, 0
    for p in repo.persons():
        if not (getattr(p, "enabled", True) and getattr(p, "newsletter", False) and getattr(p, "email", None)):
            continue
        try:
            subject, html, text = await build_async(repo, tsdb, p, detail, with_llm=True)
            ok = await asyncio.to_thread(sender.send, p.email, subject, html, text)
            sent += 1 if ok else 0
            failed += 0 if ok else 1
        except Exception:
            log.exception("newsletter send failed for %s", p.id)
            failed += 1
    log.info("newsletter: sent %d, failed %d (detail=%s)", sent, failed, detail)
    return {"sent": sent, "failed": failed, "detail": detail}

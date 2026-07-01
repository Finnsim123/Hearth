"""Push through Home Assistant when the AI key needs attention.

The LLM adapter records `llm.status` on each call; the ember buddy already shows it
in-app. This reconciler also sends a ONE-SHOT HA push (deduped per outage, re-armed on
recovery) so you find out without opening the app. Predictions keep running locally —
the key only affects setup/mapping, so the copy says so.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_SENT = "llm.push_sent"          # the code we already pushed for; cleared on recovery
_LOCAL = "Predictions keep running locally — the key only affects setup."
_MSG = {
    402: ("AI credits used up", f"Top up your AI provider to restore smart sensor mapping. {_LOCAL}"),
    429: ("AI key rate-limited", f"Your AI key hit its limit; it should recover, or top up for headroom. {_LOCAL}"),
    401: ("AI key was rejected", f"Check the AI key in Settings → Connections. {_LOCAL}"),
    403: ("AI key was rejected", f"Check the AI key in Settings → Connections. {_LOCAL}"),
}


async def check_llm_credits(repo, notifier) -> dict:
    """Send one HA push if the key is in a credit/rate/rejected state we haven't
    pushed for yet. Returns {"pushed": bool}. Idempotent per outage."""
    st = repo.get_setting("llm.status") or {}
    code = st.get("code")
    sent = repo.get_setting(_SENT)
    if st.get("ok", True) or code not in _MSG:
        if sent is not None:
            repo.set_setting(_SENT, None)          # recovered → re-arm for next time
        return {"pushed": False}
    if sent == code:
        return {"pushed": False}                   # already pushed for this outage

    title, body = _MSG[code]
    pushed = False
    try:
        for p in repo.persons():
            if getattr(p, "notify_system", False) and notifier is not None:
                if await notifier.notify(p, f"Hearth: {title}", body):
                    pushed = True
    except Exception:
        log.exception("LLM credit push failed")
    repo.set_setting(_SENT, code)                  # dedupe even if nobody's opted in
    if pushed:
        from . import events as ev
        ev.record_event(repo, "llm_key", title, body)
    return {"pushed": pushed}

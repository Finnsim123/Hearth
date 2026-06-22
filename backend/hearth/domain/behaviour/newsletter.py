"""Weekly habits newsletter — a designed, email-safe "newspaper" rendered from
the behaviour summary + trends.

Three detail tiers (overview / medium / detailed) share one data bundle and
progressively reveal sections. Everything is INLINE-STYLED HTML with table
layout and CSS/`bgcolor` charts — NO SVG, NO <script>, NO external CSS — because
Gmail and most clients strip all three. The only image is an optional inline
(CID) logo; the charts are coloured <div>/<td> bars and a heatmap grid that
render identically everywhere.

Pure + deterministic: callers pass the data and an optional LLM intro; this
module just lays it out, so it's unit-testable without a DB or network.
"""
from __future__ import annotations

from datetime import datetime
from html import escape

DETAIL_LEVELS = ("overview", "medium", "detailed")

# Activity palette — mirrors theme.css --act-* so the email matches the app.
ACT_COLORS = {
    "asleep": "#818cf8", "sleeping": "#818cf8", "away": "#94a3b8",
    "home": "#34d399", "cooking": "#f59e0b", "eating": "#fb923c",
    "media": "#f472b6", "watching_tv": "#f472b6", "working": "#60a5fa",
    "reading": "#22d3ee", "exercise": "#fb7185", "unknown": "#cbd5e1",
}
_FALLBACK = ["#a78bfa", "#2dd4bf", "#fbbf24", "#f87171", "#38bdf8", "#c084fc"]
BG = "#0f1115"; CARD = "#161a21"; LINE = "#2a313d"; TEXT = "#e7eaf0"
DIM = "#9aa3b2"; ACCENT = "#f59e0b"


def color_for(activity: str) -> str:
    a = (activity or "").lower()
    if a in ACT_COLORS:
        return ACT_COLORS[a]
    return _FALLBACK[sum(ord(c) for c in a) % len(_FALLBACK)]


def _mix(hex_color: str, t: float, toward: str = "#0f1115") -> str:
    """Blend `hex_color` toward `toward` by (1-t); t=1 keeps the colour, t=0 →
    background. Used to fade heatmap cells by intensity."""
    t = max(0.0, min(1.0, t))
    a = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(toward[i:i + 2], 16) for i in (1, 3, 5)]
    out = [round(b[i] + (a[i] - b[i]) * t) for i in range(3)]
    return "#%02x%02x%02x" % tuple(out)


def _fmt_hm(minutes: float) -> str:
    m = int(round(minutes))
    h, mm = divmod(m, 60)
    if h and mm:
        return f"{h}h {mm}m"
    return f"{h}h" if h else f"{mm}m"


def _pretty(slug: str) -> str:
    return (slug or "").replace("_", " ").strip().capitalize() or "Unknown"


# ── chart fragments (all email-safe) ────────────────────────────────────────
def _mix_bar(totals: dict[str, float]) -> str:
    """Single horizontal stacked bar of the time split across activities."""
    items = [(k, v) for k, v in sorted(totals.items(), key=lambda kv: -kv[1]) if v > 0]
    tot = sum(v for _, v in items) or 1.0
    cells = "".join(
        f'<td bgcolor="{color_for(k)}" width="{round(v / tot * 100, 2)}%" '
        f'style="height:14px;font-size:0;line-height:0;">&nbsp;</td>'
        for k, v in items)
    legend = "".join(
        f'<span style="display:inline-block;margin:0 10px 4px 0;font-size:12px;color:{DIM};">'
        f'<span style="display:inline-block;width:9px;height:9px;border-radius:2px;'
        f'background:{color_for(k)};margin-right:4px;"></span>{escape(_pretty(k))} '
        f'<b style="color:{TEXT};">{_fmt_hm(v)}</b></span>'
        for k, v in items[:8])
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-radius:6px;overflow:hidden;border:1px solid {LINE};">'
            f'<tr>{cells}</tr></table>'
            f'<div style="margin-top:8px;">{legend}</div>')


def _day_bars(per_day: list[dict]) -> str:
    """Column chart: classified (active) hours per day."""
    if not per_day:
        return ""
    vals = []
    for d in per_day:
        classified = sum(d.get("totals", {}).values())
        vals.append((d.get("date", ""), classified))
    mx = max((v for _, v in vals), default=1) or 1
    cols = ""
    for date, v in vals:
        h = round(v / mx * 90) + 2
        label = date[5:] if len(date) >= 10 else date           # MM-DD
        cols += (
            f'<td align="center" valign="bottom" style="padding:0 4px;">'
            f'<div style="height:100px;display:flex;align-items:flex-end;justify-content:center;">'
            f'<div style="width:18px;height:{h}px;background:{ACCENT};border-radius:3px 3px 0 0;"></div>'
            f'</div>'
            f'<div style="font-size:10px;color:{DIM};margin-top:4px;">{escape(label)}</div>'
            f'<div style="font-size:10px;color:{TEXT};">{_fmt_hm(v)}</div></td>')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>{cols}</tr></table>')


_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _heatmap(rhythm: list[dict], *, condensed: bool) -> str:
    """7×24 (or 7×6 condensed) grid: each cell coloured by its dominant activity,
    faded by how much was observed there. The classic 'when things happen' view."""
    grid: dict[tuple[int, int], dict[str, float]] = {}
    for c in rhythm:
        grid[(c.get("dow", 0), c.get("hour", 0))] = c.get("totals", {})
    span = 4 if condensed else 1
    n_cols = 24 // span
    cell_w = 16 if condensed else 7
    # max per-cell minutes for intensity scaling
    def cell_tot(dow, h0):
        merged: dict[str, float] = {}
        for h in range(h0, h0 + span):
            for k, v in grid.get((dow, h), {}).items():
                merged[k] = merged.get(k, 0.0) + v
        return merged
    mx = 1.0
    for dow in range(7):
        for ci in range(n_cols):
            mx = max(mx, sum(cell_tot(dow, ci * span).values()) or 0.0)
    rows = ""
    for dow in range(7):
        cells = ""
        for ci in range(n_cols):
            merged = cell_tot(dow, ci * span)
            tot = sum(merged.values())
            if tot <= 0:
                bg = CARD
            else:
                dom = max(merged.items(), key=lambda kv: kv[1])[0]
                bg = _mix(color_for(dom), 0.25 + 0.75 * min(1.0, tot / mx), CARD)
            cells += (f'<td bgcolor="{bg}" width="{cell_w}" '
                      f'style="height:{cell_w}px;font-size:0;line-height:0;'
                      f'border:1px solid {BG};">&nbsp;</td>')
        rows += (f'<tr><td style="font-size:10px;color:{DIM};padding-right:6px;'
                 f'white-space:nowrap;">{_DOW[dow]}</td>{cells}</tr>')
    hours_lbl = ""
    if not condensed:
        marks = {0: "0", 6: "6", 12: "12", 18: "18"}
        tick = "".join(
            f'<td width="7" style="font-size:9px;color:{DIM};">{marks.get(h, "")}</td>'
            for h in range(24))
        hours_lbl = f'<tr><td></td>{tick}</tr>'
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;">{rows}{hours_lbl}</table>')


def _trend_chips(trend_list: list[dict]) -> str:
    if not trend_list:
        return ""
    chips = ""
    for c in trend_list[:6]:
        txt = c.get("text") or c.get("headline") or ""
        if not txt:
            act = c.get("activity", "")
            txt = _pretty(act)
        arrow = "▲" if c.get("direction") == "up" else "▼" if c.get("direction") == "down" else "•"
        col = "#34d399" if c.get("direction") == "up" else "#f87171" if c.get("direction") == "down" else DIM
        chips += (f'<span style="display:inline-block;margin:0 8px 8px 0;padding:5px 10px;'
                  f'background:{CARD};border:1px solid {LINE};border-radius:999px;font-size:12px;'
                  f'color:{TEXT};"><span style="color:{col};">{arrow}</span> {escape(txt)}</span>')
    return chips


def _transitions(seqs: list[dict], limit: int) -> str:
    seqs = sorted(seqs, key=lambda s: -s.get("count", 0))[:limit]
    if not seqs:
        return ""
    rows = ""
    for s in seqs:
        prob = round(s.get("prob", 0) * 100)
        rows += (f'<tr><td style="padding:5px 0;font-size:13px;color:{TEXT};">'
                 f'{escape(_pretty(s.get("src","")))} '
                 f'<span style="color:{DIM};">→</span> {escape(_pretty(s.get("dst","")))}</td>'
                 f'<td align="right" style="padding:5px 0;font-size:12px;color:{DIM};">'
                 f'{s.get("count",0)}× · {prob}%</td></tr>')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>')


# ── section + shell ─────────────────────────────────────────────────────────
def _section(title: str, body: str, sub: str = "") -> str:
    if not body:
        return ""
    subhtml = f'<div style="font-size:12px;color:{DIM};margin:-2px 0 10px;">{escape(sub)}</div>' if sub else ""
    return (f'<tr><td style="padding:18px 22px;border-top:1px solid {LINE};">'
            f'<div style="font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
            f'color:{ACCENT};margin-bottom:8px;">{escape(title)}</div>{subhtml}{body}</td></tr>')


def _header(name: str, date_range: str, logo_cid: str | None) -> str:
    logo = (f'<img src="cid:{logo_cid}" width="28" height="28" alt="Hearth" '
            f'style="vertical-align:middle;margin-right:8px;">' if logo_cid
            else '<span style="font-size:22px;margin-right:6px;">🕯️</span>')
    return (f'<tr><td style="padding:24px 22px 18px;background:{CARD};">'
            f'<div>{logo}<span style="font-size:18px;font-weight:600;color:{TEXT};'
            f'letter-spacing:-.01em;vertical-align:middle;">Hearth</span>'
            f'<span style="float:right;font-size:12px;color:{DIM};">Weekly habits</span></div>'
            f'<div style="font-size:24px;font-weight:700;color:{TEXT};margin-top:14px;">'
            f'{escape(name)}’s week at home</div>'
            f'<div style="font-size:13px;color:{DIM};margin-top:2px;">{escape(date_range)}</div>'
            f'</td></tr>')


def _footer() -> str:
    return (f'<tr><td style="padding:18px 22px;border-top:1px solid {LINE};font-size:11px;'
            f'color:{DIM};">Sent by your self-hosted Hearth. This recap was generated locally '
            f'from your own data. Manage or turn off the newsletter in Hearth → Settings '
            f'→ Household.</td></tr>')


def _headline(summary: dict, accuracy: float | None) -> str:
    tot = summary.get("total_min", 0) or 1
    cov = round(summary.get("coverage", 0) * 100)
    known = round(summary.get("known_fraction", 0) * 100)
    top = sorted(summary.get("totals", {}).items(), key=lambda kv: -kv[1])
    top_act = _pretty(top[0][0]) if top else "—"
    stats = [("Top activity", top_act), ("Tracked", f"{cov}%")]
    if accuracy is not None:
        stats.append(("Model accuracy", f"{round(accuracy * 100)}%"))
    else:
        stats.append(("Known facts", f"{known}%"))
    cells = "".join(
        f'<td width="33%" align="center" style="padding:6px;">'
        f'<div style="font-size:22px;font-weight:700;color:{TEXT};">{escape(str(v))}</div>'
        f'<div style="font-size:11px;color:{DIM};">{escape(k)}</div></td>'
        for k, v in stats)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{cells}</tr></table>')


def build_newsletter(*, name: str, summary: dict, trends_list: list[dict] | None = None,
                     accuracy: float | None = None, detail: str = "medium",
                     intro_html: str | None = None, logo_cid: str | None = None,
                     now: datetime | None = None) -> tuple[str, str, str]:
    """Render the newsletter. Returns (subject, html, plain_text).

    `summary` is BehaviourSummary.model_dump(); `trends_list` is the trends()
    callouts dumped. `detail` ∈ DETAIL_LEVELS. `intro_html` is an optional
    LLM-written paragraph. `logo_cid` enables an inline logo image."""
    detail = detail if detail in DETAIL_LEVELS else "medium"
    trends_list = trends_list or []
    start = (summary.get("start") or "")[:10]
    end = (summary.get("end") or "")[:10]
    date_range = f"{start} – {end}" if start and end else ""

    body = _header(name, date_range, logo_cid)
    if intro_html:
        body += _section("This week", f'<div style="font-size:14px;color:{TEXT};'
                         f'line-height:1.55;">{intro_html}</div>')
    body += _section("At a glance", _headline(summary, accuracy))
    body += _section("How the week split", _mix_bar(summary.get("totals", {})),
                     "Time across activities")

    if detail in ("medium", "detailed"):
        body += _section("When things happen",
                         _heatmap(summary.get("rhythm", []), condensed=(detail == "medium")),
                         "Darker = more time. Rows are days, columns are hours.")
        body += _section("Shifts from last week", _trend_chips(trends_list))
        body += _section("What usually follows what",
                         _transitions(summary.get("sequences", []), 5))

    if detail == "detailed":
        body += _section("Active hours per day", _day_bars(summary.get("per_day", [])))
        body += _section("More transitions",
                         _transitions(summary.get("sequences", []), 15))
        sleep = summary.get("sleep_per_day_min", {})
        away = summary.get("away_per_day_min", {})
        if sleep or away:
            avg_sleep = sum(sleep.values()) / len(sleep) if sleep else 0
            avg_away = sum(away.values()) / len(away) if away else 0
            body += _section("Rest & away", (
                f'<div style="font-size:13px;color:{TEXT};">Average sleep '
                f'<b>{_fmt_hm(avg_sleep)}</b> · average time away <b>{_fmt_hm(avg_away)}</b></div>'))

    body += _footer()

    html = (f'<!doctype html><html><body style="margin:0;padding:0;background:{BG};">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{BG};"><tr><td align="center" style="padding:20px 12px;">'
            f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
            f'style="max-width:600px;width:100%;background:{BG};border:1px solid {LINE};'
            f'border-radius:12px;overflow:hidden;font-family:Inter,Helvetica,Arial,sans-serif;">'
            f'{body}</table></td></tr></table></body></html>')

    subject = f"{name}’s week at home" + (f" · {end}" if end else "")
    text = _plain_text(name, summary, trends_list, accuracy)
    return subject, html, text


def _plain_text(name, summary, trends_list, accuracy) -> str:
    lines = [f"{name}'s week at home", ""]
    top = sorted(summary.get("totals", {}).items(), key=lambda kv: -kv[1])
    for k, v in top[:8]:
        lines.append(f"  {_pretty(k):<14} {_fmt_hm(v)}")
    if accuracy is not None:
        lines.append(f"\nModel accuracy: {round(accuracy*100)}%")
    if trends_list:
        lines.append("\nShifts from last week:")
        for c in trends_list[:6]:
            lines.append("  - " + (c.get("text") or _pretty(c.get("activity", ""))))
    lines.append("\nSent by your self-hosted Hearth.")
    return "\n".join(lines)

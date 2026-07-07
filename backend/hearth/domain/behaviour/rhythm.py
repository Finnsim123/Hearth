"""Daily rhythm — the periodicity idea from Björkegren & Grosman, surfaced as an
insight rather than a model feature. (A per-person periodicity value is constant
across that person's windows, so it can't discriminate *within* their model — but
"how regular is your routine, and on what cycle" is a genuinely useful thing to
show.)

From an hourly activity series (evt_count per window → per hour) we compute the
autocorrelation at daily (24h) and weekly (168h) lags — how self-similar the
routine is at those periods — and the dominant repeating period via FFT. Purely
descriptive; never framed as a health signal.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _acf(x: np.ndarray, lag: int) -> float | None:
    """Autocorrelation of x at `lag` samples; None if the series is too short."""
    if len(x) <= lag:
        return None
    a, b = x[:-lag], x[lag:]
    if a.std() < 1e-9 or b.std() < 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _dominant_period_h(x: np.ndarray) -> float | None:
    """Strongest repeating period (hours) via the FFT power spectrum, ignoring
    the DC term and periods longer than half the series (unmeasurable)."""
    n = len(x)
    if n < 8:
        return None
    xd = x - x.mean()
    power = np.abs(np.fft.rfft(xd)) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0)      # cycles per hour
    power[0] = 0.0                         # drop DC
    power[freqs < 2.0 / n] = 0.0           # drop periods > n/2 (can't be seen)
    if power.max() <= 0:
        return None
    k = int(np.argmax(power))
    return float(1.0 / freqs[k]) if freqs[k] > 0 else None


def _regularity_label(daily: float | None) -> str:
    if daily is None:
        return ""
    if daily >= 0.5:
        return "a very regular daily rhythm"
    if daily >= 0.25:
        return "a loose daily rhythm"
    return "an irregular rhythm"


def _period_label(period_h: float | None) -> str:
    if period_h is None:
        return ""
    if 20 <= period_h <= 28:
        return "about a day"
    if 150 <= period_h <= 186:
        return "about a week"
    if 10 <= period_h <= 14:
        return "about twice a day"
    return f"about every {round(period_h)} h"


def rhythm(tsdb, repo, person_id: str, days: int = 28) -> dict | None:
    """Assemble the daily-rhythm panel from ~4 weeks of activity, or None when
    there isn't enough history."""
    from ..features.registry import active_feature_set_version
    try:
        fset = active_feature_set_version(repo)
    except Exception:
        return None
    end = datetime.now(timezone.utc)
    try:
        feats = tsdb.read_features(person_id, fset, end - timedelta(days=days), end)
    except Exception:
        log.debug("rhythm: read_features failed", exc_info=True)
        return None
    if feats is None or feats.empty or "evt_count" not in feats.columns:
        return None

    hourly = feats["evt_count"].resample("1h").sum()
    if hourly.empty:
        return None
    full = pd.date_range(hourly.index.min().floor("h"),
                         hourly.index.max().ceil("h"), freq="1h")
    hourly = hourly.reindex(full, fill_value=0.0)
    x = hourly.to_numpy(dtype=float)
    if len(x) < 48 or x.std() < 1e-9:        # need >2 days and some variation
        return None

    daily = _acf(x, 24)
    weekly = _acf(x, 168) if len(x) >= 168 + 24 else None
    period_h = _dominant_period_h(x)
    reg = max(0.0, daily) if daily is not None else None
    return {
        "daily_regularity": reg,
        "weekly_regularity": (max(0.0, weekly) if weekly is not None else None),
        "dominant_period_h": round(period_h, 1) if period_h else None,
        "regularity_label": _regularity_label(daily),
        "period_label": _period_label(period_h),
        "hours": int(len(x)),
    }

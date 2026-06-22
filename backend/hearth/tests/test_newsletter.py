"""Newsletter renderer: tier gating + email-safety invariants."""
from __future__ import annotations

from hearth.domain.behaviour.newsletter import (DETAIL_LEVELS, build_newsletter,
                                                color_for)


def _summary():
    return {
        "person_id": "alex", "start": "2026-06-15T00:00:00+00:00",
        "end": "2026-06-22T00:00:00+00:00", "window_min": 30,
        "totals": {"asleep": 2800, "home": 1900, "away": 1500, "cooking": 300},
        "total_min": 10080, "classified_min": 6500, "coverage": 0.64,
        "fact_min": 4000, "inferred_min": 2500, "known_fraction": 0.61,
        "per_day": [{"date": "2026-06-1%d" % d, "totals": {"home": 600 + d * 20},
                     "unknown_min": 100, "fact_min": 300, "inferred_min": 300} for d in range(5, 9)],
        "today": [],
        "sleep_per_day_min": {"2026-06-15": 420, "2026-06-16": 400},
        "away_per_day_min": {"2026-06-15": 200},
        "rhythm": [{"dow": d, "hour": h, "totals": {"asleep" if h < 7 else "home": 30}}
                   for d in range(7) for h in range(0, 24, 3)],
        "sequences": [{"src": "asleep", "dst": "home", "count": 7, "prob": 0.9},
                      {"src": "home", "dst": "away", "count": 5, "prob": 0.5}],
    }


def test_all_tiers_render_and_are_email_safe():
    for detail in DETAIL_LEVELS:
        subject, html, text = build_newsletter(name="Alex", summary=_summary(),
                                               trends_list=[], detail=detail)
        assert "Alex" in subject and "Alex" in html
        # email-safety: no script/svg, has the outer table + footer
        assert "<script" not in html.lower() and "<svg" not in html.lower()
        assert "<table" in html and "Settings" in html  # footer present
        assert "Alex" in text


def test_tier_gating():
    s = _summary()
    ov = build_newsletter(name="A", summary=s, detail="overview")[1]
    md = build_newsletter(name="A", summary=s, detail="medium")[1]
    dt = build_newsletter(name="A", summary=s, detail="detailed")[1]
    # heatmap ("When things happen") only from medium up
    assert "When things happen" not in ov
    assert "When things happen" in md and "When things happen" in dt
    # per-day breakdown only in detailed
    assert "Active hours per day" not in md
    assert "Active hours per day" in dt


def test_intro_is_injected():
    html = build_newsletter(name="A", summary=_summary(), detail="overview",
                            intro_html="You had a calm week.")[1]
    assert "You had a calm week." in html


def test_color_for_is_stable():
    assert color_for("asleep") == color_for("sleeping")  # both map to the same hue
    assert color_for("mystery_activity") == color_for("mystery_activity")

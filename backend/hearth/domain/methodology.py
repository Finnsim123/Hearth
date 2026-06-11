"""Methodology injection — assembles the live numbers that personalise the
in-app Methodology page (docs/METHODOLOGY.md). Pure read: every field is best
-effort and degrades to None so a fresh install still renders complete prose.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from .schemas import Provenance, Role

log = logging.getLogger(__name__)


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        log.debug("methodology field failed", exc_info=True)
        return default


def build_methodology(repo, tsdb) -> dict:
    out: dict = {"generated_at": datetime.now(timezone.utc).isoformat()}
    now = datetime.now(timezone.utc)
    bindings = _safe(repo.bindings, []) or []
    enabled = [b for b in bindings if b.enabled]

    # ── A. connections ────────────────────────────────────────────────────
    llm = _safe(lambda: repo.get_connection("llm"))
    out["ha_connected"] = _safe(lambda: repo.get_connection("ha") is not None, None)
    out["influx_connected"] = tsdb is not None
    out["influx_mode"] = _safe(lambda: repo.get_setting("influx.mode"))
    out["llm_enabled"] = bool(llm)
    out["llm_model"] = (llm.get("options") or {}).get("model") if llm else None
    first = _safe(lambda: tsdb.first_raw_time() if tsdb else None)
    out["recording_since"] = first.isoformat() if first else None
    out["history_days"] = round((now - first).total_seconds() / 86400, 1) if first else None
    out["events_24h"] = _safe(lambda: tsdb.count_raw_events(24) if tsdb else None)

    # ── B. entity funnel (cached at scan time by inventory_sync) ───────────
    scan = _safe(lambda: repo.get_setting("inventory.scan")) or {}
    out["entity_total"] = scan.get("entity_total")
    out["bindable_count"] = scan.get("bindable_count") or len(bindings)
    out["entity_filtered"] = (scan["entity_total"] - len(bindings)
                              if scan.get("entity_total") else None)
    out["filtered_examples"] = scan.get("filtered_examples")
    out["llm_assist"] = bool(llm)

    # ── C. roles ──────────────────────────────────────────────────────────
    roles = Counter(b.role.value for b in enabled)
    out["sensor_count"] = len(enabled)
    out["role_count"] = len(roles)
    out["role_breakdown"] = dict(roles.most_common())

    # ── D. rooms + tiers ──────────────────────────────────────────────────
    tiers = _safe(lambda: _binding_tiers(bindings), {}) or {}
    rooms = Counter((b.room or "Unassigned") for b in enabled)
    named = [r for r in rooms if r != "Unassigned"]
    out["room_count"] = len(named)
    out["room_list"] = dict(rooms.most_common())
    out["unassigned_count"] = rooms.get("Unassigned", 0)
    direct = Counter(b.room for b in enabled
                     if (b.room or "Unassigned") != "Unassigned" and tiers.get(b.name) == 1)
    out["weakest_room"] = (min(named, key=lambda r: direct.get(r, 0)) if named else None)
    tc = Counter(tiers.get(b.name, 2) for b in enabled)
    out["tier_breakdown"] = {1: tc.get(1, 0), 2: tc.get(2, 0), 3: tc.get(3, 0)}
    out["weak_evidence_cap"] = 0.70

    # ── E. history import ─────────────────────────────────────────────────
    ft = _safe(lambda: repo.get_setting("fasttrack.status")) or {}
    pruned = _safe(lambda: repo.get_setting("fasttrack.pruned")) or []
    out["imported_points"] = ft.get("points")
    out["import_span_days"] = ft.get("span_days")
    out["pruned_note"] = (f"{len(pruned)} sensors had no history and were disabled"
                          if pruned else None)

    # ── F–I. windows, features, time ──────────────────────────────────────
    from .features.registry import all_recipes, feature_set_version
    recipes = _safe(all_recipes, {}) or {}
    out["window_minutes"] = 30
    out["role_windows"] = {role.value: r.window_min for role, r in recipes.items()}
    out["window_presence"] = recipes[Role.PRESENCE].window_min if Role.PRESENCE in recipes else 15
    out["window_steps"] = recipes[Role.STEPS].window_min if Role.STEPS in recipes else 180
    out["stride_live"], out["stride_train"] = 5, 30
    composites = _safe(lambda: repo.get_setting("composites", []), []) or []
    tg = _safe(lambda: repo.get_setting("time_granularity", "coarse"), "coarse") or "coarse"
    out["time_granularity"] = tg
    out["feature_set_version"] = _safe(lambda: feature_set_version(composites, tg))
    out["composite_count"] = len(composites)
    out["composite_names"] = [c.get("name") for c in composites][:12]
    out["rule_count"] = len(_safe(repo.rules, []) or [])

    # ── L. activities + hierarchy ─────────────────────────────────────────
    activities = _safe(repo.activities, []) or []
    by_id = {a.id: a.slug for a in activities}
    hierarchy: dict[str, list[str]] = {}
    for a in activities:
        if a.parent_id and a.parent_id in by_id:
            hierarchy.setdefault(by_id[a.parent_id], []).append(a.slug)
    out["activity_count"] = len(activities)
    out["activity_list"] = [a.slug for a in activities]
    out["silent_activities"] = [a.slug for a in activities if getattr(a, "silent", False)]
    out["hierarchy"] = hierarchy

    # ── O–R. the live model ───────────────────────────────────────────────
    from .training.trainer import (MIN_TRAIN_WINDOWS, RECENCY_HALF_LIFE_DAYS)
    out["recency_half_life"] = RECENCY_HALF_LIFE_DAYS
    out["min_train_windows"] = MIN_TRAIN_WINDOWS
    models = _safe(repo.models, []) or []
    promoted = [m for m in models if m.promoted]
    root = next((m for m in promoted if m.node == "root"), None)
    out["n_nodes"] = len(promoted)
    if root is not None:
        m = root.metrics or {}
        per_class = m.get("per_class", {}) or {}
        f1 = {c: v.get("f1") for c, v in per_class.items() if isinstance(v, dict)}
        worst = min(f1.items(), key=lambda kv: (kv[1] is None, kv[1]), default=(None, None))
        lc = root.label_counts or {}
        out.update({
            "model_version": root.version,
            "model_trained_at": root.trained_at.isoformat() if root.trained_at else None,
            "train_window_count": m.get("n_train"),
            "test_window_count": m.get("n_val"),
            "model_accuracy": m.get("accuracy_confirmed") or m.get("accuracy_bootstrap"),
            "per_class_f1": f1 or None,
            "worst_class": worst[0],
            "calibration_status": "active" if m.get("calibrated") else "not yet",
            "bootstrap_label_count": lc.get(Provenance.BOOTSTRAP.value),
            "confirmed_label_count": lc.get(Provenance.CONFIRMED.value),
            "last_train_outcome": f"promoted {root.version}",
        })
    else:
        out.update({"model_version": None, "model_accuracy": None,
                    "last_train_outcome": "no model promoted yet"})

    # ── N. asking policy ──────────────────────────────────────────────────
    model_cfg = _safe(lambda: repo.get_setting("model")) or {}
    persons = [p for p in (_safe(repo.persons, []) or []) if getattr(p, "enabled", True)]
    out["ask_threshold"] = model_cfg.get("askThreshold", 0.75)
    out["ask_budget"] = (persons[0].ask_budget_per_day if persons
                         else model_cfg.get("askBudget", 8))
    out["questions_today"] = _safe(lambda: sum(
        repo.questions_since(p.id, now - timedelta(days=1)) for p in persons), None)
    out["margin_sampling"] = True

    # ── S. serving ────────────────────────────────────────────────────────
    preds_24h, current = 0, {}
    if tsdb:
        for p in persons:
            rows = _safe(lambda p=p: tsdb.read_predictions(p.id, now - timedelta(days=1), now), []) or []
            preds_24h += len(rows)
            if rows:
                last = rows[-1]
                current[p.name] = {"state": last.get("smoothed") or last.get("predicted"),
                                   "confidence": last.get("confidence")}
    out["predictions_24h"] = preds_24h if tsdb else None
    out["current_states"] = current or None

    # ── T. members ────────────────────────────────────────────────────────
    def _role(p):
        if not getattr(p, "has_device", True):
            return "no notifications (no phone)"
        return "system alerts + own questions" if getattr(p, "notify_system", False) \
            else "own training questions only"
    out["member_roles"] = {p.name: _role(p) for p in persons} or None

    # ── U. discovery ──────────────────────────────────────────────────────
    out["patterns_pending"] = len(_safe(lambda: repo.clusters(status="new"), []) or [])
    out["patterns_found"] = len(_safe(lambda: repo.clusters(), []) or [])

    # ── V. cadence (scheduler config) ─────────────────────────────────────
    out["discovery_schedule"] = "every Saturday"
    out["retrain_schedule"] = "every Sunday"
    out["retrain_window_weeks"] = 6
    return out


def _binding_tiers(bindings):
    from .features.evidence import binding_tiers
    return binding_tiers(bindings)

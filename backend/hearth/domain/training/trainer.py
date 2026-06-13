"""Training service — one run per person, scheduled weekly + UI 'Train now'.

features (single feature_set) -> bootstrap rules -> provenance overlay ->
temporal split -> fit -> honest evaluation -> registry -> CI-aware promotion
gate -> artifact. Never trains across a feature_set boundary (ADR-7).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone

from ..features.registry import active_feature_set_version
from ..labeling.merge import merge_labels
from ..labeling.rules import bootstrap_labels
from ..schemas import ModelRecord
from .estimators import RandomForestEstimator, tune_hyperparams
from .evaluate import evaluate_model, wilson_interval

log = logging.getLogger(__name__)

MIN_TRAIN_WINDOWS = 100
VAL_DAYS = 7
RECENCY_HALF_LIFE_DAYS = 21  # last week counts ~2x vs a month ago (thesillyhome's
                             # recency-weighting idea — drift mitigation without forgetting)
TUNE_MIN_WINDOWS = 500     # below this, tuning fits noise — use defaults
TUNE_EVERY_DAYS = 30       # re-tune monthly, not every weekly retrain
MIN_CONFIRMED_FOR_VALIDATED = 30  # below this a model is "provisional", not
                                  # "validated": at cold start / fast track there
                                  # are 0 confirmed labels, so the model is
                                  # promoted on bootstrap AGREEMENT (agreement with
                                  # the rules that generated its own labels —
                                  # circular). It must still serve day-one
                                  # predictions, but it must NOT be presented as
                                  # validated until enough human-confirmed labels
                                  # exist to measure it non-circularly (audit §3).


@dataclass(frozen=True)
class TrainingConfig:
    """Every training behaviour knob in one place, so they are DATA (editable in
    Settings via the 'training.config' setting) rather than scattered module
    constants. Defaults equal the historical constants, so loading with no
    override is a no-op. (gap analysis B2; levers in model_levers.md)"""
    min_train_windows: int = MIN_TRAIN_WINDOWS
    val_days: int = VAL_DAYS
    recency_half_life_days: float = RECENCY_HALF_LIFE_DAYS
    tune_min_windows: int = TUNE_MIN_WINDOWS
    tune_every_days: int = TUNE_EVERY_DAYS
    min_confirmed_for_validated: int = MIN_CONFIRMED_FOR_VALIDATED
    promotion_margin: float = 0.02   # confirmed-accuracy CI slack tolerated before
                                     # a new model is rejected as a regression


def load_training_config(repo) -> TrainingConfig:
    """TrainingConfig with per-instance overrides from the 'training.config'
    setting merged over the defaults. Unknown keys and non-numeric values are
    ignored, so a bad setting degrades to defaults and never crashes training."""
    try:
        raw = repo.get_setting("training.config") or {}
    except Exception:
        raw = {}
    if not isinstance(raw, dict) or not raw:
        return TrainingConfig()
    names = {f.name for f in fields(TrainingConfig)}
    clean = {k: v for k, v in raw.items()
             if k in names and isinstance(v, (int, float)) and not isinstance(v, bool)}
    try:
        return replace(TrainingConfig(), **clean)
    except Exception:
        return TrainingConfig()


def validation_status(n_confirmed: int,
                      threshold: int = MIN_CONFIRMED_FOR_VALIDATED) -> str:
    """Honest cold-start label: 'validated' only once enough human-confirmed
    labels back the confirmed-accuracy metric; else 'provisional'."""
    return "validated" if n_confirmed >= threshold else "provisional"


def train_person(person_id: str, tsdb, repo, store,
                 weeks: int = 8, force: bool = False) -> ModelRecord | None:
    cfg = load_training_config(repo)
    fset = active_feature_set_version(repo)
    end = datetime.now(timezone.utc)
    start = end - timedelta(weeks=weeks)

    feats = tsdb.read_features(person_id, fset, start, end)
    if len(feats) < cfg.min_train_windows:
        log.info("[%s] %d windows < %d — skip", person_id, len(feats), cfg.min_train_windows)
        return None

    # never learn from another member's personal-cadence sensors (their alarm
    # says nothing about YOU — at best it leaks schedule correlation)
    from ..features.person_scope import drop_foreign_personal
    feats, excluded = drop_foreign_personal(
        feats, repo.bindings(), repo.persons(), person_id)

    default_activity = repo.get_setting("default_activity", "home") or "home"
    bootstrap = bootstrap_labels(repo.rules(), feats, person_id, default_activity)
    events = tsdb.read_labels(person_id, start, end)
    labels, provenance = merge_labels(bootstrap, events)

    # ── hierarchy (LCPN, labeling/taxonomy.py) ────────────────────────────
    # root model: every label projected to its coarse state (eating → home);
    # then one child model per parent that has enough fine-labeled windows.
    from ..labeling.taxonomy import (
        MIN_CHILD_WINDOWS, fine_label_series, parent_map, parents_with_children,
        to_coarse)
    activities = repo.activities()
    pmap = parent_map(activities)
    coarse_labels = labels.map(lambda lab: to_coarse(lab, pmap))

    record = _fit_node(person_id, "root", feats, coarse_labels, provenance,
                       repo, store, fset, end, excluded, force, cfg)
    if record is not None:
        from ..inference.smoothing import learn_transitions
        repo.set_setting(f"transitions.{person_id}", learn_transitions(coarse_labels))

    for parent in parents_with_children(activities):
        fine = fine_label_series(labels, parent, pmap)
        mask = fine.notna()
        n_fine_children = int((fine[mask] != parent).sum())
        if mask.sum() < MIN_CHILD_WINDOWS or fine[mask].nunique() < 2 \
                or n_fine_children < MIN_CHILD_WINDOWS // 3:
            log.info("[%s] %s-node: %d windows / %d fine — not enough yet",
                     person_id, parent, int(mask.sum()), n_fine_children)
            continue
        _fit_node(person_id, parent, feats[mask], fine[mask], provenance[mask],
                  repo, store, fset, end, excluded, force, cfg)
    return record


def _fit_node(person_id: str, node: str, feats, labels, provenance,
              repo, store, fset: str, end, excluded, force: bool,
              cfg: TrainingConfig) -> ModelRecord | None:
    """Fit + evaluate + register + gate ONE hierarchy node's classifier."""
    label_counts = {f"{prov}": int((provenance == prov).sum())
                    for prov in provenance.unique()}
    label_counts |= {f"class_{c}": int((labels == c).sum()) for c in labels.unique()}

    if labels.nunique() < 2:
        log.info("[%s] %s-node: only one class present — skip", person_id, node)
        return None

    cutoff = end - timedelta(days=cfg.val_days)
    train_mask = feats.index < cutoff
    if train_mask.sum() < cfg.min_train_windows // 2 or (~train_mask).sum() < 10:
        train_mask = feats.index < feats.index[int(len(feats) * 0.75)]
    if train_mask.sum() < 10 or (~train_mask).sum() < 5:
        log.info("[%s] %s-node: split too small — skip", person_id, node)
        return None
    X_train, y_train = feats[train_mask], labels[train_mask]
    X_val, y_val, prov_val = feats[~train_mask], labels[~train_mask], provenance[~train_mask]

    # classes missing from train can't be learned — drop from val for metrics
    known = set(y_train.unique())
    keep = y_val.isin(known)
    X_val, y_val, prov_val = X_val[keep], y_val[keep], prov_val[keep]

    params = _hyperparams(repo, f"{person_id}.{node}", fset, X_train, y_train, force, cfg)
    est = RandomForestEstimator(**params)
    age_days = (end - X_train.index).total_seconds() / 86400
    weights = 0.5 ** (age_days / cfg.recency_half_life_days)
    est.fit(X_train, y_train, sample_weight=weights.to_numpy())
    metrics = evaluate_model(est, X_val, y_val, prov_val)
    metrics["n_train"] = int(len(X_train))
    metrics["feature_count"] = int(X_train.shape[1])
    metrics["validation_status"] = validation_status(
        metrics.get("n_confirmed", 0), cfg.min_confirmed_for_validated)
    train_acc = float((est.predict_proba(X_train).idxmax(axis=1) == y_train).mean())
    metrics["accuracy_train"] = round(train_acc, 4)
    metrics["hyperparams"] = params
    if len(X_val) >= 100 and hasattr(est, "calibrate"):
        # AFTER evaluation (metrics stay honest): isotonic per class on the
        # held-out split, so 0.75 confidence actually means ~75% downstream
        est.calibrate(X_val, y_val)
        metrics["calibrated"] = True
    if excluded:
        metrics["excluded_features"] = sorted(excluded)
    try:                                   # glass-box: top-15 feature importances
        imp = est.model.feature_importances_
        ranked = sorted(zip(est.columns, imp), key=lambda kv: -kv[1])
        metrics["feature_importances"] = {c: round(float(v), 4) for c, v in ranked[:15]}
        # full vector (cheap JSON) — Sensors page sums per binding to show
        # exactly how much the model relies on each sensor
        metrics["importance_all"] = {c: round(float(v), 5) for c, v in ranked if v > 0}
        # evidence profile: where the model's weight sits across trust tiers
        from ..features.evidence import evidence_profile
        metrics["evidence_profile"] = evidence_profile(
            dict(zip(est.columns, imp)), repo.bindings())
    except Exception:                      # non-tree estimators have none
        pass

    stem = person_id if node == "root" else f"{person_id}-{node}"
    n_prior = len([m for m in repo.models(person_id) if m.node == node])
    version = f"{stem}-v{n_prior + 1}"
    record = ModelRecord(person_id=person_id, version=version, node=node,
                         algo="random_forest", feature_set=fset, trained_at=end,
                         label_counts=label_counts, metrics=metrics)
    record.path = store.save(est, record)
    record = repo.save_model(record)

    current = next((m for m in repo.models(person_id)
                    if m.promoted and m.node == node), None)
    if force or promotion_gate(record, current, cfg.promotion_margin):
        repo.promote_model(record.id)
        record.promoted = True
        log.info("[%s] %s promoted (confirmed acc: %s)", person_id, version,
                 metrics.get("accuracy_confirmed"))
    else:
        log.info("[%s] %s NOT promoted — gate failed", person_id, version)
    return record


def _hyperparams(repo, person_id: str, fset: str, X_train, y_train,
                 force: bool, cfg: TrainingConfig | None = None) -> dict:
    """Tuning policy: only with enough data; cached per person; re-tuned when
    stale, when the feature set changed, or on force. Guards against tuning-
    on-bootstrap circularity by simply not over-tuning (small grid, monthly)."""
    if cfg is None:
        cfg = load_training_config(repo)
    key = f"hyperparams.{person_id}"
    cached = repo.get_setting(key) or {}
    fresh = (cached.get("feature_set") == fset and cached.get("tuned_at")
             and (datetime.now(timezone.utc)
                  - datetime.fromisoformat(cached["tuned_at"])).days < cfg.tune_every_days)
    if len(X_train) < cfg.tune_min_windows:
        return cached.get("params", {})
    if fresh and not force:
        return cached.get("params", {})
    try:
        params = tune_hyperparams(X_train, y_train)
    except Exception:
        log.exception("tuning failed — falling back to cached/default params")
        return cached.get("params", {})
    repo.set_setting(key, {"params": params, "feature_set": fset,
                           "tuned_at": datetime.now(timezone.utc).isoformat(),
                           "n_train": int(len(X_train))})
    return params


def promotion_gate(new: ModelRecord, current: ModelRecord | None,
                   margin: float = 0.02) -> bool:
    """Promote iff new's confirmed accuracy isn't credibly worse (CI overlap,
    RESEARCH.md P6). No current model -> promote. No confirmed labels yet ->
    fall back to bootstrap-agreement comparison. `margin` is the tolerated CI
    slack (TrainingConfig.promotion_margin)."""
    if current is None:
        return True
    n_new, n_cur = new.metrics.get("n_confirmed", 0), current.metrics.get("n_confirmed", 0)
    if n_new and n_cur:
        new_lo, _ = wilson_interval(
            round(new.metrics["accuracy_confirmed"] * n_new), n_new)
        cur_lo, _ = wilson_interval(
            round(current.metrics["accuracy_confirmed"] * n_cur), n_cur)
        return new_lo >= cur_lo - margin
    a, b = new.metrics.get("accuracy_bootstrap"), current.metrics.get("accuracy_bootstrap")
    return a is None or b is None or a >= b - margin


def rollback(person_id: str, repo) -> ModelRecord | None:
    """Repoint to the previous (non-promoted) model with the highest id."""
    models = sorted((m for m in repo.models(person_id) if m.node == "root"),
                    key=lambda m: -(m.id or 0))
    current = next((m for m in models if m.promoted), None)
    prev = next((m for m in models if not m.promoted and m is not current), None)
    if prev is None:
        return None
    repo.promote_model(prev.id)
    prev.promoted = True
    log.info("[%s] rolled back to %s", person_id, prev.version)
    return prev

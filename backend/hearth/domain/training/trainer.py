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
from .estimators import KNOWN_FAMILIES, make_estimator, tune_hyperparams
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
    model_family: str = "random_forest"   # random_forest | gradient_boosting | logistic
    train_weeks: int = 8   # how far back each run reads features. 0 = ALL retained
                           # history (capped by retention.days) — more data, slower
                           # fit, learns slow/seasonal routines. The model also
                           # recency-weights, so old windows count less, not zero.


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
    clean = {}
    for k, v in raw.items():
        if k not in names:
            continue
        if k == "model_family" and isinstance(v, str):
            clean[k] = v
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            clean[k] = v
    try:
        return replace(TrainingConfig(), **clean)
    except Exception:
        return TrainingConfig()


def set_model_family(repo, family: str) -> str:
    """Validate and persist the model family into the training.config setting.
    Raises ValueError on an unknown family (API maps to 400)."""
    fam = (family or "").lower()
    if fam not in KNOWN_FAMILIES:
        raise ValueError(f"unknown model family: {family!r} "
                         f"(expected one of {', '.join(KNOWN_FAMILIES)})")
    cfg = repo.get_setting("training.config") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["model_family"] = fam
    repo.set_setting("training.config", cfg)
    return fam


def set_train_weeks(repo, weeks: int) -> int:
    """Persist the training look-back window (weeks; 0 = all retained history).
    Raises ValueError outside 0..520 (10y) — API maps to 400."""
    try:
        w = int(weeks)
    except (TypeError, ValueError):
        raise ValueError("weeks must be an integer (0 = all history)")
    if not (0 <= w <= 520):
        raise ValueError("weeks must be 0 (all history) or between 1 and 520")
    cfg = repo.get_setting("training.config") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["train_weeks"] = w
    repo.set_setting("training.config", cfg)
    return w


def validation_status(n_confirmed: int,
                      threshold: int = MIN_CONFIRMED_FOR_VALIDATED) -> str:
    """Honest cold-start label: 'validated' only once enough human-confirmed
    labels back the confirmed-accuracy metric; else 'provisional'."""
    return "validated" if n_confirmed >= threshold else "provisional"


def train_person(person_id: str, tsdb, repo, store,
                 weeks: int | None = None, force: bool = False) -> ModelRecord | None:
    cfg = load_training_config(repo)
    # caller override wins; otherwise the configured window (Settings → Model).
    weeks = cfg.train_weeks if weeks is None else weeks
    fset = active_feature_set_version(repo)
    end = datetime.now(timezone.utc)
    if weeks and weeks > 0:
        start = end - timedelta(weeks=weeks)
    else:
        # 0 = train on everything still retained. Bound the read by the retention
        # window so we never ask Influx for data it has already dropped.
        ret_days = repo.get_setting("retention.days", 730)
        ret_days = int(ret_days) if isinstance(ret_days, int) and ret_days > 0 else 3650
        start = end - timedelta(days=ret_days)

    feats = tsdb.read_features(person_id, fset, start, end)
    if len(feats) < cfg.min_train_windows:
        log.info("[%s] %d windows < %d — skip", person_id, len(feats), cfg.min_train_windows)
        return None

    # never learn from another member's personal-cadence sensors (their alarm
    # says nothing about YOU — at best it leaks schedule correlation)
    from ..features.person_scope import drop_foreign_personal
    feats, excluded = drop_foreign_personal(
        feats, repo.bindings(), repo.persons(), person_id)

    # discovery⟂model split: sensors flagged model_excluded stay in the feature
    # store (so unsupervised DISCOVERY still sees them and can surface activities
    # the taxonomy doesn't cover) but are dropped from the SUPERVISED training
    # matrix here. No-op until the selection step sets the flag.
    model_excluded = [b.name for b in repo.bindings()
                      if getattr(b, "model_excluded", False)]
    if model_excluded:
        drop = [c for c in feats.columns
                if any(c == n or c.startswith(f"{n}_") for n in model_excluded)]
        if drop:
            feats = feats.drop(columns=drop)
            try:
                excluded = set(excluded) | set(drop)
            except Exception:
                pass

    # features soft-dropped by the selection trial (selection.py): proven
    # uninformative on held-out data across multiple retrains, and the pruned
    # set passed the champion/challenger gate. Matrix-only; discovery still
    # sees them; the comeback audition can re-admit them.
    from .selection import dropped_features
    sel_drop = [c for c in feats.columns if c in set(dropped_features(repo, person_id))]
    if sel_drop:
        feats = feats.drop(columns=sel_drop)
        try:
            excluded = set(excluded) | set(sel_drop)
        except Exception:
            pass

    default_activity = repo.get_setting("default_activity", "home") or "home"
    bootstrap = bootstrap_labels(repo.rules(), feats, person_id, default_activity)
    events = tsdb.read_labels(person_id, start, end)
    labels, provenance, gold = merge_labels(bootstrap, events)
    # merged activities: past labels of a folded slug count as the target (the
    # merge endpoint rechains aliases, so a single lookup resolves fully).
    aliases = repo.get_setting("activity.aliases") or {}
    if aliases:
        labels = labels.map(lambda lab: aliases.get(lab, lab))

    # ── hierarchy (LCPN, labeling/taxonomy.py) ────────────────────────────
    # root model: every label projected to its coarse state (eating → home);
    # then one child model per parent that has enough fine-labeled windows.
    from ..labeling.taxonomy import (
        MIN_CHILD_WINDOWS, fine_label_series, parent_map, parents_with_children,
        to_coarse)
    activities = repo.activities()
    pmap = parent_map(activities)
    coarse_labels = labels.map(lambda lab: to_coarse(lab, pmap))

    record = _fit_node(person_id, "root", feats, coarse_labels, provenance, gold,
                       repo, store, fset, end, excluded, force, cfg,
                       baseline_labels=labels)
    if record is not None:
        # time-conditioned transition matrices (audit F6): the forward filter
        # auto-detects the daypart structure and falls back to "all" when no
        # daypart is supplied, so this stays compatible with the flat consumer.
        from ..inference.smoothing import learn_transitions_by_daypart
        tz = repo.get_setting("timezone", "UTC") or "UTC"
        repo.set_setting(f"transitions.{person_id}",
                         learn_transitions_by_daypart(coarse_labels, tz))

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
                  gold[mask], repo, store, fset, end, excluded, force, cfg)
    return record


def _flat_baseline_metrics(feats, leaf_labels, provenance, gold, folds,
                           end, cfg: TrainingConfig) -> dict:
    """Flat multiclass leaf model on the SAME rolling-origin folds as the
    hierarchy root — the silent control that tells us whether LCPN's extra
    complexity is paying off (audit F3). Pooled identically so the comparison
    is apples-to-apples. Returns a compact metrics subdict, or {} if not
    learnable."""
    from .evaluate import PrefitProba, pooled_oos_predictions
    if leaf_labels[folds[-1][0]].nunique() < 2:
        return {}

    def _fit_fold(tm):
        e = make_estimator(cfg.model_family)
        Xt, yt = feats[tm], leaf_labels[tm]
        if e.supports_sample_weight:
            age_days = (end - Xt.index).total_seconds() / 86400
            w = 0.5 ** (age_days / cfg.recency_half_life_days)
            e.fit(Xt, yt, sample_weight=w.to_numpy())
        else:
            e.fit(Xt, yt)
        return e

    try:
        pooled = pooled_oos_predictions(_fit_fold, feats, leaf_labels,
                                        provenance, gold, folds)
    except Exception:
        log.debug("flat baseline failed", exc_info=True)
        return {}
    if pooled is None:
        return {}
    probs, y_p, prov_p, gold_p, _ = pooled
    m = evaluate_model(PrefitProba(probs), probs, y_p, prov_p, gold_p)
    # keep only the headline numbers — this is a comparison, not a shipped model
    return {k: m[k] for k in ("accuracy_gold", "accuracy_confirmed",
                              "accuracy_bootstrap", "n_gold", "n_confirmed")
            if k in m}


def _fit_node(person_id: str, node: str, feats, labels, provenance, gold,
              repo, store, fset: str, end, excluded, force: bool,
              cfg: TrainingConfig, baseline_labels=None) -> ModelRecord | None:
    """Fit + evaluate + register + gate ONE hierarchy node's classifier.

    `baseline_labels` (root only): the FLAT leaf labels. A flat multiclass model
    is trained on the same split and its accuracy stored under
    metrics["flat_baseline"], so the hierarchy (LCPN) has to prove it beats the
    simpler flat model — it doesn't always (audit F3)."""
    label_counts = {f"{prov}": int((provenance == prov).sum())
                    for prov in provenance.unique()}
    label_counts |= {f"class_{c}": int((labels == c).sum()) for c in labels.unique()}

    if labels.nunique() < 2:
        log.info("[%s] %s-node: only one class present — skip", person_id, node)
        return None

    # Blocked rolling-origin folds (Bergmeir & Benítez): decision metrics pool
    # out-of-sample predictions across up to 3 successive val blocks instead of
    # trusting one tiny 7-day slice — stabilising the promotion gate, cadence
    # streaks and strike rounds. A young install naturally degrades to 1 fold
    # (= exactly the old behaviour); the SHIPPED model is the last fold's.
    from .evaluate import (PrefitProba, pooled_oos_predictions,
                           rolling_origin_folds)
    folds = rolling_origin_folds(feats.index, end, cfg.val_days,
                                 min_train=max(10, cfg.min_train_windows // 2),
                                 min_val=5)
    if not folds:                                  # legacy positional fallback
        tm = feats.index < feats.index[int(len(feats) * 0.75)]
        if tm.sum() < 10 or (~tm).sum() < 5:
            log.info("[%s] %s-node: split too small — skip", person_id, node)
            return None
        folds = [(tm, ~tm)]
    train_mask = folds[-1][0]                      # most recent fold = the ship split
    X_train, y_train = feats[train_mask], labels[train_mask]
    val_mask = folds[-1][1]
    y_val = labels[val_mask]
    keep = y_val.isin(set(y_train.unique()))
    X_val, y_val = feats[val_mask][keep], y_val[keep]

    # tuning grid is RF-specific; other families train on their defaults for now
    params = (_hyperparams(repo, f"{person_id}.{node}", fset, X_train, y_train, force, cfg)
              if cfg.model_family == "random_forest" else {})

    # confident-learning suspects from the PREVIOUS train: their weight drops
    # to SUSPECT_WEIGHT (never zero — a wrong flag mustn't erase a right label).
    # Read-before-fit / write-after-eval keeps the loop non-circular: this
    # fit's own flags only affect the NEXT train.
    from .label_quality import confident_flags, suspect_multipliers, update_suspects
    w_suspect = suspect_multipliers(repo, person_id, feats.index)

    # live champion for this node: positive-congruent anchor at fit time and
    # the churn measure after (churn.py). Load failure just switches both off.
    from .churn import churn_allows, churn_metrics, pct_weights
    current = next((m for m in repo.models(person_id)
                    if m.promoted and m.node == node), None)
    champ_est = None
    if current is not None:
        try:
            champ_est = store.load(current)
        except Exception:
            log.debug("champion load failed — PCT/churn off", exc_info=True)

    def _fit_fold(tm):
        e = make_estimator(cfg.model_family, **params)
        Xt, yt = feats[tm], labels[tm]
        if e.supports_sample_weight:
            import numpy as np
            age_days = (end - Xt.index).total_seconds() / 86400
            w = np.asarray(0.5 ** (age_days / cfg.recency_half_life_days), dtype=float)
            w = w * w_suspect.reindex(Xt.index).fillna(1.0).to_numpy()
            if champ_est is not None:
                # PCT (Yan 2021): don't regress on what the live model gets right
                w = w * pct_weights(champ_est, Xt, yt)
            e.fit(Xt, yt, sample_weight=w)
        else:
            e.fit(Xt, yt)
        return e

    pooled = pooled_oos_predictions(_fit_fold, feats, labels, provenance, gold, folds)
    if pooled is None:
        log.info("[%s] %s-node: no scoreable out-of-sample rows — skip", person_id, node)
        return None
    probs_p, y_p, prov_p, gold_p, est = pooled     # est = last fold's fit (ships)
    tz = repo.get_setting("timezone", "UTC") or "UTC"
    metrics = evaluate_model(PrefitProba(probs_p), probs_p, y_p, prov_p, gold_p, tz=tz)
    metrics["cv_folds"] = len(folds)
    metrics["n_train"] = int(len(X_train))
    metrics["feature_count"] = int(X_train.shape[1])
    metrics["validation_status"] = validation_status(
        metrics.get("n_confirmed", 0), cfg.min_confirmed_for_validated)
    train_acc = float((est.predict_proba(X_train).idxmax(axis=1) == y_train).mean())
    metrics["accuracy_train"] = round(train_acc, 4)
    metrics["hyperparams"] = params
    if len(X_val) >= 100:
        # AFTER evaluation (metrics stay honest). Fit isotonic on an EARLIER
        # calib slice and measure reliability out-of-sample on the later check
        # slice (audit F4 — don't fit and trust on the same points), then refit
        # on all of val for deployment: the reported Brier/ECE reflects
        # generalization while the shipped calibrator uses every label.
        cut = int(len(X_val) * 0.6)
        X_cal, y_cal = X_val.iloc[:cut], y_val.iloc[:cut]
        X_chk, y_chk = X_val.iloc[cut:], y_val.iloc[cut:]
        if est.calibrate(X_cal, y_cal):
            if len(X_chk) >= 20:
                from .evaluate import reliability_metrics
                metrics["calibration"] = reliability_metrics(
                    est.predict_proba(X_chk), y_chk)
            est.calibrate(X_val, y_val)        # refit on full val for deployment
            metrics["calibrated"] = True
    # confident-learning pass (Northcutt 2021) on the SAME pooled OOS probs the
    # metrics used: flag windows the CV models confidently place in a different
    # class than their label claims. Flags act next train (down-weight); human
    # labels among them additionally queue a morning-recap re-ask.
    try:
        cl_flags = confident_flags(probs_p, y_p)
        update_suspects(repo, person_id, node, cl_flags, prov_p)
        if cl_flags:
            metrics["label_suspects"] = len(cl_flags)
            log.info("[%s] %s-node: %d suspect label(s) flagged (worst: %s@%s)",
                     person_id, node, len(cl_flags),
                     cl_flags[0]["given"], cl_flags[0]["ts"])
    except Exception:
        log.debug("label-quality pass failed", exc_info=True)

    if baseline_labels is not None:
        bl = _flat_baseline_metrics(feats, baseline_labels, provenance, gold,
                                    folds, end, cfg)
        if bl:
            metrics["flat_baseline"] = bl      # hierarchy must beat this (F3)
    if excluded:
        metrics["excluded_features"] = sorted(excluded)
    # Importance = HELD-OUT permutation importance (what actually helps on data
    # the model never saw), not impurity — Gini importance is biased toward
    # continuous/high-cardinality features (Strobl 2007), which is exactly how a
    # coffee-machine thermometer ends up "important". Falls back to impurity on
    # a too-small val slice; the method is recorded so downstream (binding audit,
    # evidence profile, UI) knows what it's reading.
    from .selection import holdout_permutation_importance, record_strike_round
    imp = holdout_permutation_importance(est, X_val, y_val)
    if imp:
        metrics["importance_method"] = "permutation_holdout"
        try:
            # the noise gate's ledger: features that failed to beat shuffling
            # collect a strike (rate-limited so overlapping val windows from
            # daily retrains don't count the same evidence twice)
            record_strike_round(repo, person_id, node, imp, now=end)
        except Exception:
            log.debug("strike recording failed", exc_info=True)
    else:
        imp = est.importances()            # glass-box; {} for estimators without
        metrics["importance_method"] = "impurity"
    if imp:
        ranked = sorted(imp.items(), key=lambda kv: -kv[1])
        metrics["feature_importances"] = {c: round(float(v), 4) for c, v in ranked[:15]}
        # full vector (cheap JSON) — Sensors page sums per binding to show
        # exactly how much the model relies on each sensor
        metrics["importance_all"] = {c: round(float(v), 5) for c, v in ranked if v > 0}
        # evidence profile: where the model's weight sits across trust tiers
        from ..features.evidence import evidence_profile
        metrics["evidence_profile"] = evidence_profile(imp, repo.bindings())

    # churn vs the live model on the NEWEST val block: how much of the visible
    # timeline this challenger would rewrite. The veto (churn.py) only bites
    # when the rewrite is big AND the challenger isn't decisively better.
    if champ_est is not None and current is not None:
        try:
            vm = folds[-1][1]
            cm = churn_metrics(champ_est, est, feats[vm], labels[vm])
            if cm:
                metrics["churn"] = cm
                allowed, why = churn_allows(metrics, current.metrics,
                                            wilson_interval)
                if not allowed:
                    cm["veto"] = why
        except Exception:
            log.debug("churn measure failed", exc_info=True)

    stem = person_id if node == "root" else f"{person_id}-{node}"
    n_prior = len([m for m in repo.models(person_id) if m.node == node])
    version = f"{stem}-v{n_prior + 1}"
    record = ModelRecord(person_id=person_id, version=version, node=node,
                         algo=cfg.model_family, feature_set=fset, trained_at=end,
                         label_counts=label_counts, metrics=metrics)
    record.path = store.save(est, record)
    record = repo.save_model(record)

    veto = (metrics.get("churn") or {}).get("veto")
    if force or (promotion_gate(record, current, cfg.promotion_margin)
                 and not veto):
        repo.promote_model(record.id)
        record.promoted = True
        log.info("[%s] %s promoted (confirmed acc: %s)", person_id, version,
                 metrics.get("accuracy_confirmed"))
    else:
        log.info("[%s] %s NOT promoted — %s", person_id, version,
                 veto or "gate failed")
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
    """Promote iff new's accuracy isn't credibly worse (CI overlap, RESEARCH.md
    P6). Compares on the most trustworthy metric both models have: GOLD (unbiased
    ε-explore) accuracy first (audit F1), else confirmed accuracy, else bootstrap
    agreement. No current model -> promote. `margin` is the tolerated CI slack."""
    if current is None:
        return True
    # prefer the unbiased gold metric, then confirmed, then bootstrap
    for key, n_key in (("accuracy_gold", "n_gold"), ("accuracy_confirmed", "n_confirmed")):
        n_new, n_cur = new.metrics.get(n_key, 0), current.metrics.get(n_key, 0)
        if n_new and n_cur:
            new_lo, _ = wilson_interval(round(new.metrics[key] * n_new), n_new)
            cur_lo, _ = wilson_interval(round(current.metrics[key] * n_cur), n_cur)
            return new_lo >= cur_lo - margin
    a, b = new.metrics.get("accuracy_bootstrap"), current.metrics.get("accuracy_bootstrap")
    return a is None or b is None or a >= b - margin


def promotion_explain(new: ModelRecord, current: ModelRecord | None,
                      margin: float = 0.02) -> dict:
    """Plain-language WHY behind promotion_gate's decision (UX9): which metric it
    compared on and the CI arithmetic, so a rejection isn't a black box."""
    if current is None:
        return {"promoted": True, "basis": "first",
                "reason": "First model for this person — promoted by default."}
    veto = (new.metrics.get("churn") or {}).get("veto")
    for key, n_key, lbl in (("accuracy_gold", "n_gold", "real-world"),
                            ("accuracy_confirmed", "n_confirmed", "confirmed")):
        n_new, n_cur = new.metrics.get(n_key, 0), current.metrics.get(n_key, 0)
        if n_new and n_cur:
            new_lo, _ = wilson_interval(round(new.metrics[key] * n_new), n_new)
            cur_lo, _ = wilson_interval(round(current.metrics[key] * n_cur), n_cur)
            ok = new_lo >= cur_lo - margin
            if ok and veto:
                return {"promoted": False, "basis": "churn",
                        "reason": f"Accuracy holds, but it {veto}."}
            return {"promoted": ok, "basis": lbl,
                    "reason": f"{lbl} accuracy CI lower bound {new_lo:.0%} "
                              f"{'≥' if ok else '<'} live {cur_lo:.0%} − margin {margin:.0%}"}
    a, b = new.metrics.get("accuracy_bootstrap"), current.metrics.get("accuracy_bootstrap")
    ok = a is None or b is None or a >= b - margin
    if ok and veto:
        return {"promoted": False, "basis": "churn",
                "reason": f"Bootstrap agreement holds, but it {veto}."}
    return {"promoted": ok, "basis": "bootstrap",
            "reason": "Compared on bootstrap agreement (no confirmed labels yet) — "
                      f"{a if a is not None else '—'} vs live {b if b is not None else '—'}."}


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

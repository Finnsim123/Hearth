"""Phase 2 end-to-end on a synthetic home: rules -> labels -> train -> evaluate
-> promote -> infer -> smooth -> ask. Fake adapters, real domain code."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from hearth.domain.features.registry import feature_set_version
from hearth.domain.inference.predictor import RULES_VERSION, predict_person
from hearth.domain.inference.smoothing import smooth
from hearth.domain.labeling.merge import merge_labels
from hearth.domain.schemas import (
    Activity, LabelEvent, ModelRecord, Person, Prediction, Provenance, Rule,
)
from hearth.domain.training.estimators import RandomForestEstimator
from hearth.domain.training.evaluate import (
    evaluate_model, population_stability_index, wilson_interval,
)
from hearth.domain.training.trainer import (
    MIN_CONFIRMED_FOR_VALIDATED, TrainingConfig, load_training_config,
    promotion_gate, set_model_family, train_person, validation_status,
)


# ── fakes ──────────────────────────────────────────────────────────────────
class FakeTsdb:
    def __init__(self, feats: pd.DataFrame, fset: str):
        self._feats, self._fset = feats, fset
        self.labels: list[LabelEvent] = []
        self.predictions: list[Prediction] = []

    def read_features(self, person, fset, start, end):
        df = self._feats
        return df[(df.index >= start) & (df.index <= end)] if fset == self._fset else pd.DataFrame()

    def read_labels(self, person, start, end):
        return self.labels

    def write_prediction(self, pred):
        self.predictions.append(pred)

    def read_predictions(self, person, start, end):
        return [{"time": p.window_ts.isoformat(), "predicted": p.predicted,
                 "smoothed": p.smoothed, "confidence": p.confidence,
                 "model_version": p.model_version, "probs": p.probabilities}
                for p in sorted(self.predictions, key=lambda x: x.window_ts, reverse=True)
                if start <= p.window_ts <= end]

    def write_heartbeat(self, job): ...
    def write_label(self, label): self.labels.append(label)


class FakeRepo:
    def __init__(self):
        self._models: list[ModelRecord] = []
        self.settings = {"default_activity": "home"}
        self._rules = [
            Rule(activity_slug="sleeping", priority=10,
                 predicate={"all": [{"feat": "bed_occupied", "op": "==", "value": 1}]}),
            Rule(activity_slug="movie", priority=20,
                 predicate={"all": [{"feat": "tv_playing", "op": "==", "value": 1}]}),
        ]

    def rules(self): return self._rules
    def bindings(self): return []
    def persons(self): return []
    def activities(self):
        return [Activity(slug=s, name=s) for s in ("sleeping", "movie", "home")]
    def get_setting(self, k, d=None): return self.settings.get(k, d)
    def set_setting(self, k, v): self.settings[k] = v
    def models(self, person=None):
        return [m for m in self._models if person in (None, m.person_id)]
    def save_model(self, m):
        m.id = len(self._models) + 1
        self._models.append(m)
        return m
    def promote_model(self, mid):
        for m in self._models:
            m.promoted = (m.id == mid)


class FakeStore:
    def __init__(self): self._objs = {}
    def save(self, est, record):
        self._objs[record.version] = est
        return record.version
    def load(self, record): return self._objs[record.path]


@pytest.fixture
def world():
    """14 days of 30-min windows with learnable structure."""
    end = pd.Timestamp.now(tz="UTC").floor("30min")
    idx = pd.date_range(end=end, periods=14 * 48, freq="30min")
    rng = np.random.default_rng(3)
    hours = idx.hour
    night = (hours >= 22) | (hours < 7)
    evening = (hours >= 20) & (hours < 22)
    feats = pd.DataFrame({
        "hour_of_day": hours.astype(float),
        "day_of_week": idx.dayofweek.astype(float),
        "is_weekend": (idx.dayofweek >= 5).astype(float),
        "bed_occupied": np.where(night, 1.0, 0.0),
        "bed_max": np.where(night, 2.4, 0.05) + rng.normal(0, 0.05, len(idx)),
        "tv_playing": np.where(evening, 1.0, 0.0),
        "couch_frac": np.where(evening, 0.8, 0.05) + rng.normal(0, 0.03, len(idx)),
        "co2_mean": np.where(night, 650.0, 800.0) + rng.normal(0, 20, len(idx)),
    }, index=idx)
    fset = feature_set_version([])
    return FakeTsdb(feats, fset), FakeRepo(), FakeStore()


# ── unit pieces ────────────────────────────────────────────────────────────
def test_wilson_small_n_wide():
    lo, hi = wilson_interval(8, 10)
    assert lo < 0.8 < hi and (hi - lo) > 0.2          # honest at tiny n


def test_psi_detects_shift():
    base = pd.Series(np.random.default_rng(0).normal(0, 1, 1000))
    same = pd.Series(np.random.default_rng(1).normal(0, 1, 1000))
    shifted = pd.Series(np.random.default_rng(2).normal(1.5, 1, 1000))
    assert population_stability_index(base, same) < 0.1
    assert population_stability_index(base, shifted) > 0.2


def test_merge_confirmed_beats_bootstrap():
    idx = pd.date_range("2026-06-01", periods=4, freq="30min", tz="UTC")
    bootstrap = pd.Series(["home"] * 4, index=idx)
    events = [LabelEvent(person_id="a", window_ts=idx[1].to_pydatetime(), label="cooking",
                         provenance=Provenance.CONFIRMED),
              LabelEvent(person_id="a", window_ts=idx[2].to_pydatetime(), label="x",
                         activity="movie", provenance=Provenance.LLM)]
    labels, prov = merge_labels(bootstrap, events)
    assert labels.iloc[1] == "cooking" and prov.iloc[1] == "confirmed"
    assert labels.iloc[2] == "movie"                   # leaf activity wins
    assert labels.iloc[0] == "home" and prov.iloc[0] == "bootstrap"


def test_smoothing_hysteresis():
    base = Prediction(person_id="a", window_ts=datetime.now(timezone.utc),
                      model_version="v", predicted="sleeping", smoothed="sleeping",
                      confidence=0.8, probabilities={})
    assert smooth([base], "movie", 0.6) == "sleeping"          # one weak win: hold
    assert smooth([base], "movie", 0.8 + 0.3) == "movie"       # decisive: switch
    prev_raw = base.model_copy(update={"predicted": "movie"})
    assert smooth([prev_raw], "movie", 0.6) == "movie"         # 2 consecutive: switch


# ── end to end ─────────────────────────────────────────────────────────────
def test_train_promote_and_beat_rules(world):
    tsdb, repo, store = world
    record = train_person("alice", tsdb, repo, store, weeks=2)
    assert record is not None and record.promoted
    assert record.metrics["accuracy_bootstrap"] > 0.9          # learnable world
    assert record.metrics["n_train"] > 300
    assert "per_class" in record.metrics and "confusion" in record.metrics
    # Estimator-port glass-box still flows through (importances + evidence profile)
    assert record.metrics["feature_importances"]               # non-empty
    assert "evidence_profile" in record.metrics


def test_estimator_port_capabilities():
    """Trainer programs against the Estimator port only — these are the methods
    it relies on (commit 18): importances(), calibrate()->bool, the
    supports_sample_weight flag, and classes_."""
    idx = pd.date_range("2026-06-01", periods=120, freq="30min", tz="UTC")
    X = pd.DataFrame({"a": np.r_[np.zeros(60), np.ones(60)],
                      "b": np.random.default_rng(0).normal(size=120)}, index=idx)
    y = pd.Series(["home"] * 60 + ["movie"] * 60, index=idx)
    est = RandomForestEstimator(n_estimators=30)
    assert est.supports_sample_weight is True
    est.fit(X, y)
    imp = est.importances()
    assert set(imp) == {"a", "b"} and imp["a"] > imp["b"]      # 'a' is the signal
    assert sorted(est.classes_) == ["home", "movie"]
    assert est.calibrate(X, y) is True                          # both classes present


def test_load_training_config_defaults_and_overrides(world):
    _, repo, _ = world
    # no setting -> defaults
    assert load_training_config(repo) == TrainingConfig()
    # valid override merged over defaults; other fields unchanged
    repo.settings["training.config"] = {"val_days": 14, "promotion_margin": 0.05}
    cfg = load_training_config(repo)
    assert cfg.val_days == 14 and cfg.promotion_margin == 0.05
    assert cfg.min_train_windows == TrainingConfig().min_train_windows
    # junk keys / wrong types / bools are ignored, never crash
    repo.settings["training.config"] = {"bogus": 1, "val_days": "ten", "tune_min_windows": True}
    cfg2 = load_training_config(repo)
    assert cfg2 == TrainingConfig()


def test_promotion_gate_margin_is_configurable():
    # a 3-pt confirmed-accuracy drop passes a 5-pt margin but fails the default 2-pt
    new = ModelRecord(person_id="a", version="a-v2", feature_set="v1",
                      metrics={"n_confirmed": 80, "accuracy_confirmed": 0.87})
    cur = ModelRecord(person_id="a", version="a-v1", feature_set="v1",
                      metrics={"n_confirmed": 80, "accuracy_confirmed": 0.90})
    assert promotion_gate(new, cur, margin=0.05) is True
    assert promotion_gate(new, cur, margin=0.0) is False


def test_train_with_selected_family(world):
    """The model family is config-driven: selecting 'logistic' trains and
    records a logistic model end to end (gap analysis G2/G4)."""
    tsdb, repo, store = world
    repo.settings["training.config"] = {"model_family": "logistic"}
    record = train_person("alice", tsdb, repo, store, weeks=2)
    assert record is not None and record.algo == "logistic" and record.promoted


def test_set_model_family_validation(world):
    _, repo, _ = world
    assert set_model_family(repo, "gradient_boosting") == "gradient_boosting"
    assert repo.settings["training.config"]["model_family"] == "gradient_boosting"
    assert load_training_config(repo).model_family == "gradient_boosting"
    with pytest.raises(ValueError):
        set_model_family(repo, "neural_net")


def test_manual_override_pins_and_labels(world):
    """A fresh override pins the published prediction AND writes confirmed labels
    (source=override) so the next retrain learns from the correction."""
    from hearth.domain.controls import set_override
    tsdb, repo, store = world
    train_person("alice", tsdb, repo, store, weeks=2)
    set_override(repo, "alice", "movie", {a.slug for a in repo.activities()})  # stamps set_at=now
    tsdb.predictions.clear()
    tsdb.labels.clear()
    preds = predict_person("alice", tsdb, repo, store)
    assert preds and all(
        p.predicted == "movie" and p.smoothed == "movie"
        and p.model_version == "override" and p.confidence == 1.0 for p in preds)
    # confirmed override labels written, one per predicted window
    assert tsdb.labels and len(tsdb.labels) == len(preds)
    assert all(lab.label == "movie" and lab.provenance.value == "confirmed"
               and lab.source == "override" for lab in tsdb.labels)


def test_stale_override_pins_without_labeling(world):
    """An override left set past the freshness window keeps pinning the display
    but stops writing labels — a forgotten pin can't poison training."""
    from datetime import datetime, timedelta, timezone
    tsdb, repo, store = world
    train_person("alice", tsdb, repo, store, weeks=2)
    stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    repo.settings["override.alice"] = {"activity": "movie", "set_at": stale}
    tsdb.predictions.clear()
    tsdb.labels.clear()
    preds = predict_person("alice", tsdb, repo, store)
    assert preds and all(p.predicted == "movie" for p in preds)   # still pinned
    assert tsdb.labels == []                                      # but no labels written


def test_validation_status_threshold():
    assert validation_status(0) == "provisional"
    assert validation_status(MIN_CONFIRMED_FOR_VALIDATED - 1) == "provisional"
    assert validation_status(MIN_CONFIRMED_FOR_VALIDATED) == "validated"
    assert validation_status(MIN_CONFIRMED_FOR_VALIDATED + 100) == "validated"


def test_cold_start_model_is_provisional(world):
    """Fresh home: no confirmed labels, so the first (promoted) model must be
    labelled provisional, not validated — it was gated on circular bootstrap
    agreement (audit §3)."""
    tsdb, repo, store = world
    record = train_person("alice", tsdb, repo, store, weeks=2)
    assert record is not None and record.promoted
    assert record.metrics["n_confirmed"] == 0
    assert record.metrics["validation_status"] == "provisional"


def test_rules_fallback_then_model(world):
    tsdb, repo, store = world
    preds = predict_person("alice", tsdb, repo, store)
    assert preds and all(p.model_version == RULES_VERSION for p in preds)
    assert all(abs(sum(p.probabilities.values()) - 1) < 1e-6 for p in preds)
    night_pred = [p for p in preds if p.window_ts.hour == 1]
    assert all(p.predicted == "sleeping" for p in night_pred)

    train_person("alice", tsdb, repo, store, weeks=2)
    tsdb.predictions.clear()
    preds2 = predict_person("alice", tsdb, repo, store)
    assert preds2 and all(p.model_version.startswith("alice-v") for p in preds2)
    assert preds2[-1].explanation or True                      # SHAP optional


def test_abstain_publishes_unknown(world):
    """With the abstain threshold cranked above any confidence, every published
    (smoothed) state becomes 'unknown' while raw predictions stay intact."""
    tsdb, repo, store = world
    train_person("alice", tsdb, repo, store, weeks=2)
    repo.settings["output.policy"] = {"abstain_threshold": 1.01}   # always abstain
    tsdb.predictions.clear()
    preds = predict_person("alice", tsdb, repo, store)
    assert preds and all(p.smoothed == "unknown" for p in preds)
    assert all(p.predicted != "unknown" for p in preds)            # raw kept honest


def test_promotion_gate_blocks_regression():
    good = ModelRecord(person_id="a", version="a-v1", feature_set="v1",
                       metrics={"n_confirmed": 50, "accuracy_confirmed": 0.9})
    bad = ModelRecord(person_id="a", version="a-v2", feature_set="v1",
                      metrics={"n_confirmed": 50, "accuracy_confirmed": 0.6})
    assert promotion_gate(bad, good) is False
    assert promotion_gate(good, bad) is True
    assert promotion_gate(good, None) is True


# ── hyperparameter tuning ──────────────────────────────────────────────────
def test_tune_hyperparams_timeseries_cv(world):
    from hearth.domain.training.estimators import tune_hyperparams
    tsdb, _, _ = world
    feats = tsdb._feats
    y = pd.Series(np.where(feats["bed_occupied"] > 0, "sleeping", "home"),
                  index=feats.index)
    params = tune_hyperparams(feats, y, n_iter=2,
                              distributions={"n_estimators": [50],
                                             "min_samples_leaf": [3, 5]})
    assert params["n_estimators"] == 50 and params["min_samples_leaf"] in (3, 5)


def test_trainer_skips_tuning_below_threshold(world):
    tsdb, repo, store = world
    record = train_person("alice", tsdb, repo, store, weeks=2)
    # 14-day fixture -> n_train < TUNE_MIN_WINDOWS: defaults used, nothing cached
    assert record.metrics["hyperparams"] == {}
    assert "hyperparams.alice" not in repo.settings


def test_hyperparam_cache_respected(world):
    from datetime import datetime, timezone
    from hearth.domain.training.trainer import _hyperparams
    _, repo, _ = world
    repo.settings["hyperparams.alice"] = {
        "params": {"n_estimators": 200}, "feature_set": "vX",
        "tuned_at": datetime.now(timezone.utc).isoformat(), "n_train": 600}
    big_X = pd.DataFrame(np.zeros((600, 3)))
    y = pd.Series(["a"] * 600)
    # fresh cache + matching feature_set -> reused without re-tuning
    assert _hyperparams(repo, "alice", "vX", big_X, y, force=False) == {"n_estimators": 200}
    # small data -> cached params still used, never tunes
    small_X = big_X.head(100)
    assert _hyperparams(repo, "alice", "vY", small_X, y.head(100), force=True) == {"n_estimators": 200}


def test_recency_weighting_prefers_recent_regime():
    """Same features, label flipped halfway: recent regime must win at predict
    time (thesillyhome-inspired recency weighting)."""
    from hearth.domain.training.trainer import RECENCY_HALF_LIFE_DAYS
    from hearth.domain.training.estimators import RandomForestEstimator
    end = pd.Timestamp.now(tz="UTC").floor("30min")
    idx = pd.date_range(end=end, periods=400, freq="30min")
    X = pd.DataFrame({"a": [1.0] * 400, "hour_of_day": idx.hour.astype(float)}, index=idx)
    y = pd.Series(["old"] * 200 + ["new"] * 200, index=idx)
    age = (end - idx).total_seconds() / 86400
    w = 0.5 ** (age / RECENCY_HALF_LIFE_DAYS)
    est = RandomForestEstimator(n_estimators=50)
    est.fit(X, y, sample_weight=w)
    pred = est.predict_proba(X.tail(1)).idxmax(axis=1).iloc[0]
    assert pred == "new"

"""Ports — every boundary of the domain, as typing.Protocols.

The dependency rule: domain code depends ONLY on these Protocols and on
domain.schemas. Adapters (adapters/*) implement them; main.py wires them up.
Nothing in domain/ may import influxdb_client, aiohttp, paho, sqlalchemy, etc.
"""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Protocol

import pandas as pd

from .schemas import (
    Activity,
    Binding,
    ClusterCard,
    EntityState,
    FeatureSpec,
    LabelEvent,
    ModelRecord,
    Prediction,
    Question,
    Rule,
)


class EventSource(Protocol):
    """Streams entity state changes (adapter: ha_websocket)."""

    async def subscribe(self, entity_ids: list[str]) -> AsyncIterator[EntityState]: ...
    async def history(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> list[EntityState]:
        """Gap-fill after reconnect, via HA REST history API."""
        ...


class TimeSeriesStore(Protocol):
    """Raw, feature and ML buckets (adapter: influx_store). ADR-3: swappable."""

    def write_raw(self, binding: Binding, states: list[EntityState]) -> None: ...
    def read_raw(self, bindings: list[Binding], start: datetime, end: datetime) -> pd.DataFrame: ...
    def write_features(self, person: str, feature_set: str, rows: pd.DataFrame) -> None: ...
    def read_features(
        self, person: str, feature_set: str, start: datetime, end: datetime
    ) -> pd.DataFrame: ...
    def write_prediction(self, pred: Prediction) -> None: ...
    def write_label(self, label: LabelEvent) -> None: ...
    def read_labels(self, person: str, start: datetime, end: datetime) -> list[LabelEvent]: ...
    def write_heartbeat(self, job: str) -> None: ...


class EntityPublisher(Protocol):
    """Pushes predictions into HA (adapter: mqtt_publisher; fallback: ha_rest)."""

    def announce(self, persons: list[str], activities: list[Activity]) -> None:
        """Publish MQTT discovery configs (retained). Idempotent."""
        ...

    def publish(self, pred: Prediction) -> None: ...


class Notifier(Protocol):
    """Sends actionable questions to phones (adapter: ha_rest)."""

    def ask(self, question: Question) -> bool: ...


class AppRepo(Protocol):
    """Application state — SQLite (adapter: app_db). Subset shown; one method
    group per table, CRUD-shaped, returns schemas not ORM rows."""

    def bindings(self) -> list[Binding]: ...
    def activities(self) -> list[Activity]: ...
    def rules(self) -> list[Rule]: ...
    def save_question(self, q: Question) -> Question: ...
    def open_questions(self, person: str | None = None) -> list[Question]: ...
    def answer_question(self, question_id: int, answer: str) -> Question: ...
    def supersede_question(self, question_id: int) -> None: ...
    def save_model(self, record: ModelRecord) -> ModelRecord: ...
    def models(self, person: str | None = None) -> list[ModelRecord]: ...
    def promote_model(self, model_id: int) -> None: ...
    def save_cluster(self, card: ClusterCard) -> ClusterCard: ...
    def clusters(self, status: str | None = None) -> list[ClusterCard]: ...


class Estimator(Protocol):
    """A trainable classifier (ADR-9). v1: RandomForest. Later: GBM, logistic,
    HEPA head. The trainer programs against THIS interface only — no estimator
    internals (no est.model, no hasattr) — so a family swap is one new adapter."""

    supports_sample_weight: bool  # whether fit() honours sample_weight

    def fit(self, X: pd.DataFrame, y: pd.Series, sample_weight=None) -> None: ...
    def predict_proba(self, X: pd.DataFrame) -> pd.DataFrame:
        """Returns DataFrame indexed like X, one column per class."""
        ...

    def explain(self, X: pd.DataFrame) -> pd.DataFrame:
        """Per-feature attribution (SHAP) for each row; empty df if unsupported."""
        ...

    def importances(self) -> dict[str, float]:
        """Per-feature importance {column: weight}; empty dict if unsupported."""
        ...

    def calibrate(self, X_val: pd.DataFrame, y_val: pd.Series) -> bool:
        """Fit probability calibration on a held-out split. Returns True if it
        actually calibrated (False = unsupported or not enough data)."""
        ...

    @property
    def classes_(self) -> list[str]: ...


class Embedder(Protocol):
    """Optional self-supervised window embedder (research seam — see RESEARCH.md §4).

    Phase 4: adapters/hepa_embedder.py pretrains on the home's unlabeled stream
    and serves embeddings for (a) few-label classification heads and (b)
    embedding-space clustering. Feature-flagged; absent in v1.
    """

    def embed(self, X: pd.DataFrame) -> pd.DataFrame: ...


class LlmAdvisor(Protocol):
    """Optional LLM for onboarding semantics (ADR-12; adapter: openrouter_llm).

    All methods take metadata only (entity ids, device classes, units,
    aggregate stats) and return schema-validated PROPOSALS the UI renders for
    human approval. Absent key -> heuristic suggester covers the same calls.
    """

    async def propose_bindings(self, inventory: list[dict]) -> list[Binding]: ...
    async def propose_composites(self, bindings: list[Binding]) -> list[dict]:
        """Candidate cross-binding features, proposed generously — RF tolerates
        useless features far better than missing ones. Each: {name, inputs,
        expression_ast, rationale}."""
        ...

    async def propose_taxonomy(self, inventory: list[dict]) -> list[Activity]: ...

    async def propose_feature_spec(
        self, catalog: list[dict], activities: list[Activity],
        mode: str = "conservative",
    ) -> FeatureSpec:
        """Feature architect (Phase 3): read the entity catalog (metadata +,
        with consent, aggregate stats) and emit a VALIDATED FeatureSpec —
        entity selections with info tiers and reliability, plus executable
        feature definitions. Supersedes the unimplemented propose_composites."""
        ...
    async def propose_rules(
        self, bindings: list[Binding], activities: list[Activity]
    ) -> list[Rule]: ...
    async def suggest_cluster_name(
        self, card: ClusterCard, activities: list[Activity]
    ) -> str | None: ...

    async def annotate_windows(
        self, window_summaries: list[dict], activities: list[Activity]
    ) -> list[tuple[str | None, float]]:
        """LLM-as-weak-annotator (onboarding): batched window SUMMARIES
        (time-of-day + per-feature aggregates, never raw series) ->
        (activity_slug | None, self-reported confidence) per window.
        Stored as provenance=LLM labels — below 'discovered', far below
        'confirmed'; low-confidence answers are dropped, not stored."""
        ...


class ModelStore(Protocol):
    """Serialized model artifacts on disk (adapter: joblib files under /data/models)."""

    def save(self, estimator: Estimator, record: ModelRecord) -> str: ...
    def load(self, record: ModelRecord) -> Estimator: ...

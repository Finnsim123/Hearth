"""Domain schemas — the shared vocabulary of the system (pydantic v2).

Hearth has no built-in people: a *Household* is created in the UI and every
person (adults, kids, guests-mode) is user-defined. All examples in code and
docs use generic names (alice/bob). Person ids are slugs chosen by the user.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

WindowSize = Literal["30m"]  # single window size in v1; field exists to widen later


class Role(str, Enum):
    """Semantic sensor roles — feature recipes are keyed on these (ADR-8)."""

    PRESENCE = "presence"
    BED = "bed"
    POWER = "power"
    LIGHT = "light"
    MEDIA = "media"
    ENV = "env"
    PERSON = "person"
    FOCUS = "focus"
    ALARM_TIME = "alarm_time"
    DOOR = "door"
    STEPS = "steps"
    BATTERY = "battery"
    CUSTOM = "custom"


class InfoTier(str, Enum):
    """Information tier the feature architect assigns per entity (llm_layer_design
    §b). Orthogonal to Role (what kind of sensor) and to the evidence tier (how
    much to trust a prediction): the info tier says what KIND of feature to build.
    """

    LOW_INFORMATION = "T0"        # constant / stuck / diagnostic — selected out
    DISCRETE_EVENT_GATE = "T1"    # boolean state whose transitions are the signal
    STATE_MACHINE = "T2"          # small enumerated categorical (media states)
    CONTINUOUS_MEASUREMENT = "T3" # continuously varying quantity (temp, CO2, watts)
    CUMULATIVE_COUNTER = "T4"     # monotonic total; only its rate matters (kWh, steps)
    SLOW_STATE = "T5"             # rarely-changing, long-valid state (home/away)


class User(BaseModel):
    """An account (login), distinct from Person (a household member being
    modeled). They can link: a member with a login labels their own inbox.
    Password/session material never appears on this schema — hashes stay in
    the DB layer; crypto lives in hearth/security.py only."""

    id: int | None = None
    email: str
    display_name: str
    role: Literal["admin", "member"] = "member"
    person_id: str | None = None  # optional link to a household member
    disabled: bool = False


class Person(BaseModel):
    """A household member. `has_device` gates the asking policy: people without
    a phone (kids) never get notifications — their labels come from another
    member or the Inbox. Each enabled person gets their own model."""

    id: str  # slug, e.g. "alice"
    name: str
    avatar: str | None = None  # "preset:<hue>" or "upload:<path>" — UI renders both
    ha_person_entity: str | None = None
    notify_service: str | None = None  # e.g. "mobile_app_alice_phone"
    has_device: bool = True
    notify_system: bool = False  # milestones + ops alerts (admin-types opt in;
                                 # others get ONLY training questions)
    ask_budget_per_day: int = 8
    quiet_hours: tuple[int, int] = (22, 8)
    enabled: bool = True


class Household(BaseModel):
    name: str = "Home"
    timezone: str = "UTC"
    persons: list[Person] = Field(default_factory=list)


class EntityState(BaseModel):
    entity_id: str
    state: str | float | None
    attributes: dict[str, Any] = Field(default_factory=dict)
    ts: datetime


class Binding(BaseModel):
    """Maps one HA entity to a semantic role (the generalization mechanism)."""

    id: int | None = None
    entity_id: str
    role: Role
    name: str  # short slug used as feature prefix, e.g. "sofa"
    room: str | None = None
    person_id: str | None = None  # personal sensors (bed side, phone focus)
    options: dict[str, Any] = Field(default_factory=dict)  # role-specific (thresholds…)
    enabled: bool = True
    model_excluded: bool = False  # built into features + seen by DISCOVERY, but
                                  # dropped before TRAINING. Lets the unsupervised
                                  # clusterer see sensors the supervised selector
                                  # pruned, so it can surface activities the current
                                  # taxonomy doesn't cover (discovery⟂model split).


class Activity(BaseModel):
    """User-defined taxonomy node; two levels (parent=None → top level)."""

    id: int | None = None
    slug: str
    name: str
    phrase: str | None = None  # verb phrase for notifications: "watching a movie"
    icon: str = "mdi:help"
    color: str = "#888888"
    parent_id: int | None = None
    enabled: bool = True
    silent: bool = False  # never push about this activity (e.g. sleeping —
                          # nobody can answer "are you asleep?"); questions
                          # land in the Inbox for next-morning confirmation


class Rule(BaseModel):
    """A labeling function: predicate over feature columns → activity.

    predicate is a small JSON AST, e.g.
    {"all": [{"feat": "kitchen_presence_frac", "op": ">", "value": 0.3},
             {"feat": "stove_fumes_any", "op": "==", "value": 1}]}
    """

    id: int | None = None
    activity_slug: str
    person_id: str | None = None  # None = applies to all persons
    predicate: dict[str, Any]
    priority: int = 100  # lower wins
    origin: Literal["user", "discovered"] = "user"
    enabled: bool = True


class EntitySelection(BaseModel):
    """Per-entity keep / role / tier / reliability decision from the feature
    architect (llm_layer_design §c task 1). `keep=False` entities carry only a
    reason; everything else is optional for them."""

    entity_id: str
    keep: bool
    role: Role | None = None
    info_tier: InfoTier | None = None
    person_id: str | None = None
    reliability: Literal["ok", "suspect", "unusable"] = "ok"
    reason: str = ""


class FeatureDef(BaseModel):
    """One executable feature definition. A deterministic builder runs `transform`
    (a whitelist id) over `inputs` (entity ids for per-entity transforms, or
    existing feature names for composites) with `params` — no LLM, no eval
    (llm_layer_design §d)."""

    name: str  # snake_case feature column, e.g. "sofa_occupancy_fraction"
    transform: str  # whitelist transform id (validated against the active whitelist)
    inputs: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    window_min: int | None = None  # per-feature lookback; None = role/window default
    info_tier: InfoTier | None = None
    rationale: str = ""
    expected_separates: list[str] = Field(default_factory=list)  # activity slugs
    origin: Literal["llm", "heuristic", "human"] = "llm"


class FeatureSpec(BaseModel):
    """The whole output contract of the LLM data-analytics layer: a versioned,
    human-approvable, deterministically executable feature specification
    (llm_layer_design §d). Hashed into feature_set_version once a builder
    consumes it, so a spec change forces a clean retrain (ADR-7)."""

    spec_version: Literal["v1"] = "v1"
    created_at: datetime | None = None
    created_by: Literal["llm", "heuristic", "human", "llm+human"] = "llm"
    llm_model: str | None = None
    selections: list[EntitySelection] = Field(default_factory=list)
    features: list[FeatureDef] = Field(default_factory=list)


class Provenance(str, Enum):
    BOOTSTRAP = "bootstrap"  # rule engine
    LLM = "llm"  # LLM weak annotator over window summaries (onboarding)
    DISCOVERED = "discovered"  # user-named cluster
    CONFIRMED = "confirmed"  # human answer — the only ground truth



class LabelEvent(BaseModel):
    person_id: str
    window_ts: datetime
    window: WindowSize = "30m"
    label: str  # activity slug (top level)
    activity: str | None = None  # sub-activity slug
    provenance: Provenance
    source: str = "ui"  # ui | notification | bulk | rule:<id> | cluster:<id>
    gold: bool = False  # answer to a RANDOM (ε-explore) ask → an unbiased sample
                        # of the home's life, not an uncertainty-sampled hard case.
                        # The honest headline metric is measured on gold only
                        # (audit F1): uncertainty asks bias accuracy pessimistically.


class Prediction(BaseModel):
    person_id: str
    window_ts: datetime
    window: WindowSize = "30m"
    model_version: str
    predicted: str
    smoothed: str | None = None
    confidence: float
    probabilities: dict[str, float]
    explanation: list[tuple[str, float]] = Field(default_factory=list)  # (feature, shap)
    evidence: float | None = None  # direct-tier SHAP share (features/evidence.py)
    parent: str | None = None  # coarse state when predicted is a fine activity
                               # ("home" + "eating" are simultaneously true)
    coarse_confidence: float | None = None  # root model's confidence in parent


class Question(BaseModel):
    """A pending ask. Action ids round-trip through HA even on iOS (ADR-6)."""

    id: int | None = None
    person_id: str
    window_ts: datetime
    predicted: str
    confidence: float
    alternatives: list[str] = Field(default_factory=list)  # button slugs, index-mapped
    asked: list[str] = Field(default_factory=list)  # cumulative slugs offered across the
                                                    # follow-up chain — excluded from the next batch
    parent_id: int | None = None  # set on a follow-up; links back to the question it refines
    probabilities: dict[str, float] = Field(default_factory=dict)  # drives phrasing mode
    ask_reason: Literal["uncertain", "explore"] = "uncertain"  # why this was asked.
                       # "explore" = ε-greedy random query → its answer is a GOLD
                       # (unbiased) eval label; "uncertain" = active-learning hard
                       # case, great for training but biases the headline (audit F1).
    channel: Literal["notification", "inbox"] = "inbox"
    # superseded = the user tapped "No/Other", so a follow-up question replaced this one
    status: Literal["open", "answered", "expired", "superseded"] = "open"
    answer: str | None = None
    created_at: datetime | None = None


class ModelRecord(BaseModel):
    id: int | None = None
    person_id: str
    version: str  # e.g. "alice-v7"
    node: str = "root"  # hierarchy node: "root" = coarse states; a parent
                        # activity slug (e.g. "home") = the fine classifier
                        # for that state's children (LCPN, RESEARCH.md)
    algo: str = "random_forest"
    feature_set: str
    path: str | None = None
    trained_at: datetime | None = None
    label_counts: dict[str, int] = Field(default_factory=dict)  # by provenance
    metrics: dict[str, Any] = Field(default_factory=dict)
    promoted: bool = False


class ClusterCard(BaseModel):
    """A discovered pattern awaiting a human name (see ARCHITECTURE.md §6)."""

    id: int | None = None
    person_id: str = ""
    run_at: datetime | None = None
    algo: str = "hdbscan"
    n_windows: int = 0
    suggested_slug: str | None = None  # top existing-activity guess (back-compat)
    # LLM name suggestions: [{name, slug?, rationale, confidence, kind}],
    # kind ∈ {"existing","new","merge"} — rendered as tap-to-accept chips.
    suggestions: list[dict] = Field(default_factory=list)
    signature: list[tuple[str, float]] = Field(default_factory=list)  # (feature, z)
    hour_histogram: list[int] = Field(default_factory=lambda: [0] * 24)
    example_windows: list[datetime] = Field(default_factory=list)
    status: Literal["new", "named", "dismissed", "merged"] = "new"
    named_activity_slug: str | None = None

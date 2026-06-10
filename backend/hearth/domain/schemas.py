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


class Question(BaseModel):
    """A pending ask. Action ids round-trip through HA even on iOS (ADR-6)."""

    id: int | None = None
    person_id: str
    window_ts: datetime
    predicted: str
    confidence: float
    alternatives: list[str] = Field(default_factory=list)  # button slugs, index-mapped
    probabilities: dict[str, float] = Field(default_factory=dict)  # drives phrasing mode
    channel: Literal["notification", "inbox"] = "inbox"
    status: Literal["open", "answered", "expired"] = "open"
    answer: str | None = None
    created_at: datetime | None = None


class ModelRecord(BaseModel):
    id: int | None = None
    person_id: str
    version: str  # e.g. "alice-v7"
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
    suggested_slug: str | None = None  # LLM's guess, shown as a hint only
    signature: list[tuple[str, float]] = Field(default_factory=list)  # (feature, z)
    hour_histogram: list[int] = Field(default_factory=lambda: [0] * 24)
    example_windows: list[datetime] = Field(default_factory=list)
    status: Literal["new", "named", "dismissed", "merged"] = "new"
    named_activity_slug: str | None = None

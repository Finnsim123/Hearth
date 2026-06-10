"""AppRepo + ModelStore adapters — SQLite via SQLAlchemy 2.0.

Implements domain.ports.AppRepo. Returns/accepts domain schemas, never leaks
ORM rows. Secret columns follow docs/SECURITY.md via hearth.security.
v1 migrations: metadata.create_all (additive); Alembic arrives with the first
breaking change.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .. import security
from ..domain.schemas import (
    Activity, Binding, ClusterCard, ModelRecord, Person, Question, Role, Rule, User,
)


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="member")
    password_hash: Mapped[str] = mapped_column(String)
    person_id: Mapped[str | None] = mapped_column(String, nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    backoff_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_sha256: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PersonRow(Base):
    __tablename__ = "persons"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # slug
    name: Mapped[str] = mapped_column(String)
    avatar: Mapped[str | None] = mapped_column(String, nullable=True)
    ha_person_entity: Mapped[str | None] = mapped_column(String, nullable=True)
    notify_service: Mapped[str | None] = mapped_column(String, nullable=True)
    has_device: Mapped[bool] = mapped_column(Boolean, default=True)
    ask_budget_per_day: Mapped[int] = mapped_column(Integer, default=8)
    quiet_hours: Mapped[str] = mapped_column(String, default="22,8")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class BindingRow(Base):
    __tablename__ = "bindings"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(String, unique=True)
    role: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, unique=True)
    room: Mapped[str | None] = mapped_column(String, nullable=True)
    person_id: Mapped[str | None] = mapped_column(String, nullable=True)
    options_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ActivityRow(Base):
    __tablename__ = "activities"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    phrase: Mapped[str | None] = mapped_column(String, nullable=True)
    icon: Mapped[str] = mapped_column(String, default="mdi:help")
    color: Mapped[str] = mapped_column(String, default="#888888")
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class RuleRow(Base):
    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_slug: Mapped[str] = mapped_column(String)
    person_id: Mapped[str | None] = mapped_column(String, nullable=True)
    predicate_json: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    origin: Mapped[str] = mapped_column(String, default="user")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class QuestionRow(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[str] = mapped_column(String)
    window_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    predicted: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    alternatives_json: Mapped[str] = mapped_column(Text, default="[]")
    probabilities_json: Mapped[str] = mapped_column(Text, default="{}")
    channel: Mapped[str] = mapped_column(String, default="inbox")
    status: Mapped[str] = mapped_column(String, default="open", index=True)
    answer: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ModelRow(Base):
    __tablename__ = "models"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[str] = mapped_column(String, unique=True)
    algo: Mapped[str] = mapped_column(String, default="random_forest")
    feature_set: Mapped[str] = mapped_column(String)
    path: Mapped[str | None] = mapped_column(String, nullable=True)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    label_counts_json: Mapped[str] = mapped_column(Text, default="{}")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)


class ClusterRow(Base):
    __tablename__ = "clusters"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    algo: Mapped[str] = mapped_column(String, default="hdbscan")
    n_windows: Mapped[int] = mapped_column(Integer, default=0)
    signature_json: Mapped[str] = mapped_column(Text, default="[]")
    hour_hist_json: Mapped[str] = mapped_column(Text, default="[]")
    examples_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String, default="new")
    named_activity_slug: Mapped[str | None] = mapped_column(String, nullable=True)


class ConnectionRow(Base):
    __tablename__ = "connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String, unique=True)  # ha|influx|mqtt|llm
    url: Mapped[str] = mapped_column(String, default="")
    token_encrypted: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="unconfigured")
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    options_json: Mapped[str] = mapped_column(Text, default="{}")


class ApiTokenRow(Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    token_sha256: Mapped[str] = mapped_column(String, index=True)
    scope: Mapped[str] = mapped_column(String, default="integration")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SettingRow(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="null")


def _binding(r: BindingRow) -> Binding:
    return Binding(id=r.id, entity_id=r.entity_id, role=Role(r.role), name=r.name,
                   room=r.room, person_id=r.person_id,
                   options=json.loads(r.options_json), enabled=r.enabled)


class AppDb:
    """Implements domain.ports.AppRepo (Phase 1 surface + persistence used by
    later phases)."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", future=True)

    def migrate(self) -> None:
        Base.metadata.create_all(self.engine)

    # ── bindings ───────────────────────────────────────────────────────────
    def bindings(self) -> list[Binding]:
        with Session(self.engine) as s:
            return [_binding(r) for r in s.scalars(select(BindingRow)).all()]

    def save_binding(self, b: Binding) -> Binding:
        with Session(self.engine) as s:
            r = s.get(BindingRow, b.id) if b.id else None
            if r is None:
                r = BindingRow()
                s.add(r)
            r.entity_id, r.role, r.name = b.entity_id, b.role.value, b.name
            r.room, r.person_id, r.enabled = b.room, b.person_id, b.enabled
            r.options_json = json.dumps(b.options)
            s.commit()
            return _binding(r)

    def delete_binding(self, binding_id: int) -> None:
        with Session(self.engine) as s:
            r = s.get(BindingRow, binding_id)
            if r:
                s.delete(r)
                s.commit()

    # ── persons ────────────────────────────────────────────────────────────
    def persons(self) -> list[Person]:
        with Session(self.engine) as s:
            out = []
            for r in s.scalars(select(PersonRow)).all():
                qh = tuple(int(x) for x in r.quiet_hours.split(","))
                out.append(Person(id=r.id, name=r.name, avatar=r.avatar,
                                  ha_person_entity=r.ha_person_entity,
                                  notify_service=r.notify_service, has_device=r.has_device,
                                  ask_budget_per_day=r.ask_budget_per_day,
                                  quiet_hours=qh, enabled=r.enabled))
            return out

    def save_person(self, p: Person) -> Person:
        with Session(self.engine) as s:
            r = s.get(PersonRow, p.id) or PersonRow(id=p.id)
            s.add(r)
            r.name, r.ha_person_entity, r.notify_service = p.name, p.ha_person_entity, p.notify_service
            r.avatar = p.avatar
            r.has_device, r.ask_budget_per_day, r.enabled = p.has_device, p.ask_budget_per_day, p.enabled
            r.quiet_hours = f"{p.quiet_hours[0]},{p.quiet_hours[1]}"
            s.commit()
            return p

    # ── activities & rules ─────────────────────────────────────────────────
    def activities(self) -> list[Activity]:
        with Session(self.engine) as s:
            return [Activity(id=r.id, slug=r.slug, name=r.name, phrase=r.phrase,
                             icon=r.icon, color=r.color,
                             parent_id=r.parent_id, enabled=r.enabled)
                    for r in s.scalars(select(ActivityRow)).all()]

    def save_activity(self, a: Activity) -> Activity:
        with Session(self.engine) as s:
            r = (s.get(ActivityRow, a.id) if a.id
                 else s.scalars(select(ActivityRow).where(ActivityRow.slug == a.slug)).first())
            if r is None:
                r = ActivityRow()
                s.add(r)
            r.slug, r.name, r.icon, r.color = a.slug, a.name, a.icon, a.color
            r.phrase = a.phrase
            r.parent_id, r.enabled = a.parent_id, a.enabled
            s.commit()
            return Activity(id=r.id, slug=r.slug, name=r.name, icon=r.icon,
                            color=r.color, parent_id=r.parent_id, enabled=r.enabled)

    def rules(self) -> list[Rule]:
        with Session(self.engine) as s:
            return [Rule(id=r.id, activity_slug=r.activity_slug, person_id=r.person_id,
                         predicate=json.loads(r.predicate_json), priority=r.priority,
                         origin=r.origin, enabled=r.enabled)
                    for r in s.scalars(select(RuleRow).order_by(RuleRow.priority)).all()]

    def save_rule(self, rule: Rule) -> Rule:
        with Session(self.engine) as s:
            r = s.get(RuleRow, rule.id) if rule.id else None
            if r is None:
                r = RuleRow()
                s.add(r)
            r.activity_slug, r.person_id, r.priority = rule.activity_slug, rule.person_id, rule.priority
            r.predicate_json, r.origin, r.enabled = json.dumps(rule.predicate), rule.origin, rule.enabled
            s.commit()
            rule.id = r.id
            return rule

    # ── connections (secrets encrypted via security.py) ────────────────────
    def set_connection(self, kind: str, url: str, token: str, options: dict | None = None) -> None:
        with Session(self.engine) as s:
            r = s.scalars(select(ConnectionRow).where(ConnectionRow.kind == kind)).first()
            if r is None:
                r = ConnectionRow(kind=kind)
                s.add(r)
            r.url = url
            if token:
                r.token_encrypted = security.encrypt_secret(token)
            if options is not None:
                r.options_json = json.dumps(options)
            r.status = "configured"
            s.commit()

    def get_connection(self, kind: str) -> dict | None:
        """Returns {url, token (decrypted), options} or None."""
        with Session(self.engine) as s:
            r = s.scalars(select(ConnectionRow).where(ConnectionRow.kind == kind)).first()
            if r is None or r.status == "unconfigured":
                return None
            token = security.decrypt_secret(r.token_encrypted) if r.token_encrypted else ""
            return {"url": r.url, "token": token, "options": json.loads(r.options_json)}

    def mark_connection_ok(self, kind: str) -> None:
        with Session(self.engine) as s:
            r = s.scalars(select(ConnectionRow).where(ConnectionRow.kind == kind)).first()
            if r:
                r.status, r.last_ok_at = "ok", _now()
                s.commit()

    # ── settings ───────────────────────────────────────────────────────────
    def get_setting(self, key: str, default=None):
        with Session(self.engine) as s:
            r = s.get(SettingRow, key)
            return json.loads(r.value_json) if r else default

    def set_setting(self, key: str, value) -> None:
        with Session(self.engine) as s:
            r = s.get(SettingRow, key) or SettingRow(key=key)
            s.add(r)
            r.value_json = json.dumps(value)
            s.commit()

    # ── questions / models / clusters (Phase 2/3 consumers) ───────────────
    def save_question(self, q: Question) -> Question:
        with Session(self.engine) as s:
            r = QuestionRow(person_id=q.person_id, window_ts=q.window_ts, predicted=q.predicted,
                            confidence=q.confidence, channel=q.channel, status=q.status,
                            alternatives_json=json.dumps(q.alternatives),
                            probabilities_json=json.dumps(q.probabilities))
            s.add(r)
            s.commit()
            q.id = r.id
            return q

    def open_questions(self, person: str | None = None) -> list[Question]:
        with Session(self.engine) as s:
            stmt = select(QuestionRow).where(QuestionRow.status == "open")
            if person:
                stmt = stmt.where(QuestionRow.person_id == person)
            return [Question(id=r.id, person_id=r.person_id, window_ts=r.window_ts,
                             predicted=r.predicted, confidence=r.confidence,
                             alternatives=json.loads(r.alternatives_json),
                             probabilities=json.loads(r.probabilities_json),
                             channel=r.channel, status=r.status, answer=r.answer,
                             created_at=r.created_at)
                    for r in s.scalars(stmt).all()]

    def get_question(self, question_id: int) -> Question | None:
        with Session(self.engine) as s:
            r = s.get(QuestionRow, question_id)
            if r is None:
                return None
            return Question(id=r.id, person_id=r.person_id, window_ts=r.window_ts,
                            predicted=r.predicted, confidence=r.confidence,
                            alternatives=json.loads(r.alternatives_json),
                            probabilities=json.loads(r.probabilities_json),
                            channel=r.channel, status=r.status, answer=r.answer,
                            created_at=r.created_at)

    def answer_question(self, question_id: int, answer: str) -> Question:
        with Session(self.engine) as s:
            r = s.get(QuestionRow, question_id)
            r.answer, r.status = answer, "answered"
            s.commit()
            return Question(id=r.id, person_id=r.person_id, window_ts=r.window_ts,
                            predicted=r.predicted, confidence=r.confidence,
                            channel=r.channel, status=r.status, answer=r.answer)

    def questions_since(self, person: str, since: datetime) -> int:
        with Session(self.engine) as s:
            rows = s.scalars(select(QuestionRow).where(
                QuestionRow.person_id == person,
                QuestionRow.created_at >= since)).all()
            return len(rows)

    def last_question(self, person: str) -> Question | None:
        with Session(self.engine) as s:
            r = s.scalars(select(QuestionRow).where(QuestionRow.person_id == person)
                          .order_by(QuestionRow.created_at.desc())).first()
            if r is None:
                return None
            return Question(id=r.id, person_id=r.person_id, window_ts=r.window_ts,
                            predicted=r.predicted, confidence=r.confidence,
                            alternatives=json.loads(r.alternatives_json),
                            probabilities=json.loads(r.probabilities_json),
                            channel=r.channel, status=r.status, answer=r.answer,
                            created_at=r.created_at)

    def skip_question(self, question_id: int) -> None:
        with Session(self.engine) as s:
            r = s.get(QuestionRow, question_id)
            if r and r.status == "open":
                r.status = "expired"
                s.commit()

    def expire_questions(self, older_than: datetime) -> int:
        with Session(self.engine) as s:
            rows = s.scalars(select(QuestionRow).where(
                QuestionRow.status == "open",
                QuestionRow.created_at < older_than)).all()
            for r in rows:
                r.status = "expired"
            s.commit()
            return len(rows)

    def save_model(self, m: ModelRecord) -> ModelRecord:
        with Session(self.engine) as s:
            r = ModelRow(person_id=m.person_id, version=m.version, algo=m.algo,
                         feature_set=m.feature_set, path=m.path, trained_at=m.trained_at,
                         label_counts_json=json.dumps(m.label_counts),
                         metrics_json=json.dumps(m.metrics), promoted=m.promoted)
            s.add(r)
            s.commit()
            m.id = r.id
            return m

    def models(self, person: str | None = None) -> list[ModelRecord]:
        with Session(self.engine) as s:
            stmt = select(ModelRow)
            if person:
                stmt = stmt.where(ModelRow.person_id == person)
            return [ModelRecord(id=r.id, person_id=r.person_id, version=r.version, algo=r.algo,
                                feature_set=r.feature_set, path=r.path, trained_at=r.trained_at,
                                label_counts=json.loads(r.label_counts_json),
                                metrics=json.loads(r.metrics_json), promoted=r.promoted)
                    for r in s.scalars(stmt).all()]

    def promote_model(self, model_id: int) -> None:
        with Session(self.engine) as s:
            r = s.get(ModelRow, model_id)
            for other in s.scalars(select(ModelRow).where(ModelRow.person_id == r.person_id)):
                other.promoted = False
            r.promoted = True
            s.commit()

    def save_cluster(self, c: ClusterCard) -> ClusterCard:
        with Session(self.engine) as s:
            r = ClusterRow(algo=c.algo, n_windows=c.n_windows,
                           signature_json=json.dumps(c.signature),
                           hour_hist_json=json.dumps(c.hour_histogram),
                           examples_json=json.dumps([t.isoformat() for t in c.example_windows]),
                           status=c.status, named_activity_slug=c.named_activity_slug)
            s.add(r)
            s.commit()
            c.id = r.id
            return c

    def clusters(self, status: str | None = None) -> list[ClusterCard]:
        with Session(self.engine) as s:
            stmt = select(ClusterRow)
            if status:
                stmt = stmt.where(ClusterRow.status == status)
            out = []
            for r in s.scalars(stmt).all():
                out.append(ClusterCard(
                    id=r.id, run_at=r.run_at, algo=r.algo, n_windows=r.n_windows,
                    signature=[tuple(x) for x in json.loads(r.signature_json)],
                    hour_histogram=json.loads(r.hour_hist_json),
                    example_windows=[datetime.fromisoformat(t) for t in json.loads(r.examples_json)],
                    status=r.status, named_activity_slug=r.named_activity_slug))
            return out

    # ── users (auth — consumed by Phase 2 middleware) ──────────────────────
    def user_count(self) -> int:
        with Session(self.engine) as s:
            return len(s.scalars(select(UserRow)).all())

    def create_user(self, u: User, password: str) -> User:
        with Session(self.engine) as s:
            r = UserRow(email=u.email, display_name=u.display_name, role=u.role,
                        password_hash=security.hash_password(password),
                        person_id=u.person_id, disabled=u.disabled)
            s.add(r)
            s.commit()
            u.id = r.id
            return u

    def verify_login(self, email: str, password: str) -> User | None:
        with Session(self.engine) as s:
            r = s.scalars(select(UserRow).where(UserRow.email == email)).first()
            if r is None or r.disabled:
                return None
            ok, new_hash = security.verify_password(password, r.password_hash)
            if not ok:
                return None
            if new_hash:
                r.password_hash = new_hash
                s.commit()
            return User(id=r.id, email=r.email, display_name=r.display_name,
                        role=r.role, person_id=r.person_id, disabled=r.disabled)


class FileModelStore:
    """Implements domain.ports.ModelStore — joblib files on the data volume."""

    def __init__(self, models_dir: Path) -> None:
        self.models_dir = models_dir
        models_dir.mkdir(parents=True, exist_ok=True)

    def save(self, estimator, record: ModelRecord) -> str:
        path = self.models_dir / f"{record.version}.joblib"
        joblib.dump(estimator, path)
        return str(path)

    def load(self, record: ModelRecord):
        return joblib.load(record.path)

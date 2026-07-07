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
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, func, select,
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
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_codes_json: Mapped[str] = mapped_column(Text, default="[]")  # [{sha,used}]


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_sha256: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordResetRow(Base):
    __tablename__ = "password_resets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_sha256: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PersonRow(Base):
    __tablename__ = "persons"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # slug
    name: Mapped[str] = mapped_column(String)
    avatar: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    newsletter: Mapped[bool] = mapped_column(Boolean, default=False)
    ha_person_entity: Mapped[str | None] = mapped_column(String, nullable=True)
    notify_service: Mapped[str | None] = mapped_column(String, nullable=True)
    has_device: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_system: Mapped[bool] = mapped_column(Boolean, default=False)
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
    silent: Mapped[bool] = mapped_column(Boolean, default=False)


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
    asked_json: Mapped[str] = mapped_column(Text, default="[]")
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probabilities_json: Mapped[str] = mapped_column(Text, default="{}")
    ask_reason: Mapped[str] = mapped_column(String, default="uncertain")
    channel: Mapped[str] = mapped_column(String, default="inbox")
    status: Mapped[str] = mapped_column(String, default="open", index=True)
    answer: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ModelRow(Base):
    __tablename__ = "models"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[str] = mapped_column(String, unique=True)
    node: Mapped[str] = mapped_column(String, default="root")
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
    person_id: Mapped[str] = mapped_column(String, default="", index=True)
    suggested_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    algo: Mapped[str] = mapped_column(String, default="hdbscan")
    n_windows: Mapped[int] = mapped_column(Integer, default=0)
    signature_json: Mapped[str] = mapped_column(Text, default="[]")
    suggestions_json: Mapped[str] = mapped_column(Text, default="[]")
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


def _rename_pred_cols(node, cur: str, old: str):
    """Rewrite feature-column references in a rule/composite predicate from a
    `cur_` prefix to an `old_` prefix (used by relink). Matches the AST grammar
    in features/composites.py: {"all"|"any": [nodes]}, {"not": node}, and leaf
    {"feat": <column>, "op": .., "value": ..} — the column is the `feat` VALUE."""
    if not isinstance(node, dict):
        return node
    if "all" in node:
        return {"all": [_rename_pred_cols(c, cur, old) for c in node["all"]]}
    if "any" in node:
        return {"any": [_rename_pred_cols(c, cur, old) for c in node["any"]]}
    if "not" in node:
        return {"not": _rename_pred_cols(node["not"], cur, old)}
    if "feat" in node:
        feat = str(node["feat"])
        renamed = old + feat[len(cur):] if (feat == cur or feat.startswith(cur + "_")) else feat
        return {**node, "feat": renamed}
    return node


class AppDb:
    """Implements domain.ports.AppRepo (Phase 1 surface + persistence used by
    later phases)."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", future=True)

    def migrate(self) -> None:
        Base.metadata.create_all(self.engine)
        # lightweight forward migration: add columns that new versions
        # introduced to tables that already exist (SQLite ALTER ADD only)
        from sqlalchemy import inspect, text
        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                have = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name in have:
                        continue
                    ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(self.engine.dialect)}'
                    if col.default is not None and getattr(col.default, "arg", None) is not None \
                            and not callable(col.default.arg):
                        v = col.default.arg
                        v = int(v) if isinstance(v, bool) else v
                        ddl += f" DEFAULT {v!r}" if isinstance(v, str) else f" DEFAULT {v}"
                    conn.execute(text(ddl))

    def factory_reset(self) -> None:
        """Wipe ALL app state — users, household, bindings, taxonomy, rules,
        models, connections, settings, sessions. Tables stay; rows go. After
        this, user_count()==0 so the app re-enters first-run setup."""
        with self.engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):   # children first (FKs)
                conn.execute(table.delete())

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
                                  email=r.email, newsletter=r.newsletter,
                                  ha_person_entity=r.ha_person_entity,
                                  notify_service=r.notify_service, has_device=r.has_device,
                                  notify_system=r.notify_system,
                                  ask_budget_per_day=r.ask_budget_per_day,
                                  quiet_hours=qh, enabled=r.enabled))
            return out

    def save_person(self, p: Person) -> Person:
        with Session(self.engine) as s:
            r = s.get(PersonRow, p.id) or PersonRow(id=p.id)
            s.add(r)
            r.name, r.ha_person_entity, r.notify_service = p.name, p.ha_person_entity, p.notify_service
            r.avatar = p.avatar
            r.email, r.newsletter = p.email, p.newsletter
            r.has_device, r.ask_budget_per_day, r.enabled = p.has_device, p.ask_budget_per_day, p.enabled
            r.notify_system = p.notify_system
            r.quiet_hours = f"{p.quiet_hours[0]},{p.quiet_hours[1]}"
            s.commit()
            return p

    def delete_person(self, person_id: str, drop_bindings: bool = True) -> dict:
        """Remove a household member and everything the app DB holds for them:
        their rules, inbox questions, models and clusters, and (unless kept) the
        bindings for THEIR own sensors. Shared bindings (no person_id, e.g. a
        living-room motion sensor) are never touched. Returns a tally of what was
        deleted, for the confirmation + timeline event. Time-series erasure is a
        separate step (see domain.people.forget_person)."""
        counts: dict[str, int] = {}
        with Session(self.engine) as s:
            def _purge(model, count_key):
                rows = s.scalars(select(model).where(model.person_id == person_id)).all()
                counts[count_key] = len(rows)
                for r in rows:
                    s.delete(r)
            if drop_bindings:
                _purge(BindingRow, "bindings")
            _purge(RuleRow, "rules")
            _purge(QuestionRow, "questions")
            _purge(ModelRow, "models")
            _purge(ClusterRow, "clusters")
            # a login account may be tied to this member — keep the account but
            # clear the dangling link so it doesn't point at a deleted person.
            for u in s.scalars(select(UserRow).where(UserRow.person_id == person_id)).all():
                u.person_id = None
            p = s.get(PersonRow, person_id)
            if p is not None:
                s.delete(p)
                counts["person"] = 1
            s.commit()
        return counts

    def relink_person(self, current_id: str, old_id: str) -> dict:
        """Re-key a person to a PREVIOUS identity, so history orphaned under
        `old_id` (e.g. after a rename+reseed) becomes theirs again. Nothing in the
        time-series is rewritten — those series already carry `old_id` and column
        prefixes like `old_id_iphone_*`, so we instead move the person ONTO that
        id: rename their id, their bindings' person_id + name prefix, their rules'
        person_id + predicate column prefixes, and their questions/models/clusters.
        Refuses if `old_id` is already a live person (that'd be a merge, not a
        relink). Returns a tally."""
        if not old_id or old_id == current_id:
            return {"ok": False, "reason": "same_id"}
        with Session(self.engine) as s:
            cur = s.get(PersonRow, current_id)
            if cur is None:
                return {"ok": False, "reason": "unknown_person"}
            if s.get(PersonRow, old_id) is not None:
                return {"ok": False, "reason": "old_id_in_use"}
            # BindingRow.name is UNIQUE — a leftover old_id-prefixed binding (or a
            # bound person entity named old_id) would make the rename below collide.
            # Refuse rather than half-apply and raise mid-transaction.
            mine = s.scalars(select(BindingRow).where(BindingRow.person_id == current_id)).all()
            mine_ids = {b.id for b in mine}
            targets = {old_id + b.name[len(current_id):] for b in mine
                       if b.name == current_id or b.name.startswith(current_id + "_")}
            # compare by id, not person_id — a stale old_id binding may have
            # person_id NULL, and `!= current_id` never matches NULL in SQL.
            if targets and any(c.id not in mine_ids for c in s.scalars(
                    select(BindingRow).where(BindingRow.name.in_(targets))).all()):
                return {"ok": False, "reason": "old_id_bindings_exist"}
            counts = {"bindings": 0, "rules": 0, "questions": 0, "models": 0, "clusters": 0}
            for b in mine:
                if b.name == current_id or b.name.startswith(current_id + "_"):
                    b.name = old_id + b.name[len(current_id):]      # alexander_iphone → alex_iphone
                b.person_id = old_id
                counts["bindings"] += 1
            for r in s.scalars(select(RuleRow).where(RuleRow.person_id == current_id)).all():
                r.person_id = old_id
                try:
                    r.predicate_json = json.dumps(
                        _rename_pred_cols(json.loads(r.predicate_json), current_id, old_id))
                except Exception:
                    pass
                counts["rules"] += 1
            for model, key in ((QuestionRow, "questions"), (ModelRow, "models"),
                               (ClusterRow, "clusters")):
                for row in s.scalars(select(model).where(model.person_id == current_id)).all():
                    row.person_id = old_id
                    counts[key] += 1
            # keep any login account pointing at the re-keyed person
            for u in s.scalars(select(UserRow).where(UserRow.person_id == current_id)).all():
                u.person_id = old_id
            # the person row's id is the primary key → recreate it under old_id
            attrs = {c.name: getattr(cur, c.name) for c in PersonRow.__table__.columns}
            attrs["id"] = old_id
            s.delete(cur)
            s.flush()
            s.add(PersonRow(**attrs))
            s.commit()
            return {"ok": True, "counts": counts, "id": old_id, "name": attrs.get("name")}

    # ── activities & rules ─────────────────────────────────────────────────
    def activities(self) -> list[Activity]:
        with Session(self.engine) as s:
            return [Activity(id=r.id, slug=r.slug, name=r.name, phrase=r.phrase,
                             icon=r.icon, color=r.color, silent=r.silent,
                             parent_id=r.parent_id, enabled=r.enabled)
                    for r in s.scalars(select(ActivityRow)).all()]

    def save_activity(self, a: Activity) -> Activity:
        from ..domain.labeling.palette import is_unset, pick_color
        with Session(self.engine) as s:
            r = (s.get(ActivityRow, a.id) if a.id
                 else s.scalars(select(ActivityRow).where(ActivityRow.slug == a.slug)).first())
            color = a.color
            # auto-assign a distinct palette colour when none was chosen, so every
            # creation path (onboarding presets, cluster naming, the +Add box…)
            # yields a coloured activity without the caller having to pick one.
            # Compute BEFORE adding the new row so this read can't autoflush a
            # half-built (slug-less) row.
            if is_unset(color):
                used = {row.color for row in s.scalars(select(ActivityRow)).all()
                        if row is not r and not is_unset(row.color)}
                color = pick_color(a.slug, used)
            if r is None:
                r = ActivityRow()
                s.add(r)
            r.slug, r.name, r.icon, r.color = a.slug, a.name, a.icon, color
            r.phrase = a.phrase
            r.parent_id, r.enabled, r.silent = a.parent_id, a.enabled, a.silent
            s.commit()
            return Activity(id=r.id, slug=r.slug, name=r.name, phrase=r.phrase,
                            icon=r.icon, color=r.color, silent=r.silent,
                            parent_id=r.parent_id, enabled=r.enabled)

    def delete_activity(self, slug: str) -> None:
        with Session(self.engine) as s:
            r = s.scalars(select(ActivityRow).where(ActivityRow.slug == slug)).first()
            if r is not None:
                s.delete(r)
                s.commit()

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
    def _question(self, r: QuestionRow) -> Question:
        return Question(id=r.id, person_id=r.person_id, window_ts=r.window_ts,
                        predicted=r.predicted, confidence=r.confidence,
                        alternatives=json.loads(r.alternatives_json),
                        asked=json.loads(r.asked_json or "[]"), parent_id=r.parent_id,
                        probabilities=json.loads(r.probabilities_json),
                        ask_reason=getattr(r, "ask_reason", None) or "uncertain",
                        channel=r.channel, status=r.status, answer=r.answer,
                        created_at=r.created_at)

    def save_question(self, q: Question) -> Question:
        with Session(self.engine) as s:
            r = QuestionRow(person_id=q.person_id, window_ts=q.window_ts, predicted=q.predicted,
                            confidence=q.confidence, channel=q.channel, status=q.status,
                            alternatives_json=json.dumps(q.alternatives),
                            asked_json=json.dumps(q.asked), parent_id=q.parent_id,
                            probabilities_json=json.dumps(q.probabilities),
                            ask_reason=q.ask_reason)
            s.add(r)
            s.commit()
            q.id = r.id
            return q

    def open_questions(self, person: str | None = None) -> list[Question]:
        with Session(self.engine) as s:
            stmt = select(QuestionRow).where(QuestionRow.status == "open")
            if person:
                stmt = stmt.where(QuestionRow.person_id == person)
            return [self._question(r) for r in s.scalars(stmt).all()]

    def get_question(self, question_id: int) -> Question | None:
        with Session(self.engine) as s:
            r = s.get(QuestionRow, question_id)
            return self._question(r) if r is not None else None

    def answer_question(self, question_id: int, answer: str) -> Question | None:
        with Session(self.engine) as s:
            r = s.get(QuestionRow, question_id)
            if r is None:                    # unknown id -> let the API 404, not 500
                return None
            r.answer, r.status = answer, "answered"
            s.commit()
            return self._question(r)

    def supersede_question(self, question_id: int) -> None:
        """Mark a question replaced by a follow-up (user tapped No/Other). It
        leaves the inbox but isn't a real answer, so no label is written."""
        with Session(self.engine) as s:
            r = s.get(QuestionRow, question_id)
            if r and r.status == "open":
                r.status = "superseded"
                s.commit()

    def questions_since(self, person: str, since: datetime) -> int:
        with Session(self.engine) as s:
            return int(s.scalar(select(func.count()).select_from(QuestionRow).where(
                QuestionRow.person_id == person,
                QuestionRow.created_at >= since)) or 0)

    def last_question(self, person: str) -> Question | None:
        with Session(self.engine) as s:
            r = s.scalars(select(QuestionRow).where(QuestionRow.person_id == person)
                          .order_by(QuestionRow.created_at.desc())).first()
            return self._question(r) if r is not None else None

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
                         node=m.node,
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
                                node=r.node or "root",
                                feature_set=r.feature_set, path=r.path, trained_at=r.trained_at,
                                label_counts=json.loads(r.label_counts_json),
                                metrics=json.loads(r.metrics_json), promoted=r.promoted)
                    for r in s.scalars(stmt).all()]

    def promote_model(self, model_id: int) -> None:
        with Session(self.engine) as s:
            r = s.get(ModelRow, model_id)
            # one live model PER HIERARCHY NODE: promoting home-v3 must not
            # demote the root model (they answer different questions)
            for other in s.scalars(select(ModelRow).where(
                    ModelRow.person_id == r.person_id,
                    ModelRow.node == r.node)):
                other.promoted = False
            r.promoted = True
            s.commit()

    def save_cluster(self, c: ClusterCard) -> ClusterCard:
        with Session(self.engine) as s:
            r = s.get(ClusterRow, c.id) if c.id else None
            if r is None:
                r = ClusterRow()
                s.add(r)
            r.person_id, r.algo, r.n_windows = c.person_id, c.algo, c.n_windows
            r.signature_json = json.dumps(c.signature)
            r.suggestions_json = json.dumps(c.suggestions)
            r.hour_hist_json = json.dumps(c.hour_histogram)
            r.examples_json = json.dumps([t.isoformat() for t in c.example_windows])
            r.status, r.named_activity_slug = c.status, c.named_activity_slug
            r.suggested_slug = c.suggested_slug
            s.commit()
            c.id = r.id
            return c

    def _cluster(self, r: ClusterRow) -> ClusterCard:
        return ClusterCard(
            id=r.id, person_id=r.person_id or "", run_at=r.run_at, algo=r.algo,
            n_windows=r.n_windows, suggested_slug=r.suggested_slug,
            suggestions=json.loads(r.suggestions_json or "[]"),
            signature=[tuple(x) for x in json.loads(r.signature_json)],
            hour_histogram=json.loads(r.hour_hist_json),
            example_windows=[datetime.fromisoformat(t) for t in json.loads(r.examples_json)],
            status=r.status, named_activity_slug=r.named_activity_slug)

    def clusters(self, status: str | None = None,
                 person_id: str | None = None) -> list[ClusterCard]:
        with Session(self.engine) as s:
            stmt = select(ClusterRow)
            if status:
                stmt = stmt.where(ClusterRow.status == status)
            if person_id:
                stmt = stmt.where(ClusterRow.person_id == person_id)
            return [self._cluster(r) for r in s.scalars(stmt).all()]

    def get_cluster(self, cluster_id: int) -> ClusterCard | None:
        with Session(self.engine) as s:
            r = s.get(ClusterRow, cluster_id)
            return self._cluster(r) if r else None

    def clear_clusters(self, person_id: str, status: str = "new") -> int:
        """Replace-run semantics: a fresh discovery run owns the 'new' pile."""
        with Session(self.engine) as s:
            rows = s.scalars(select(ClusterRow).where(
                ClusterRow.person_id == person_id,
                ClusterRow.status == status)).all()
            for r in rows:
                s.delete(r)
            s.commit()
            return len(rows)

    # ── users (auth — consumed by Phase 2 middleware) ──────────────────────
    # ── api tokens (integration auth — docs/SECURITY.md) ──────────────────
    def create_api_token(self, name: str, scope: str = "integration") -> str:
        """Mints and stores a token; returns the PLAINTEXT exactly once."""
        plaintext, sha = security.mint_api_token(scope)
        with Session(self.engine) as s:
            s.add(ApiTokenRow(name=name, token_sha256=sha, scope=scope))
            s.commit()
        return plaintext

    def api_token_scope(self, presented: str) -> str | None:
        """Scope for a presented bearer token, or None if unknown/revoked.
        Touches last_used_at on hit."""
        import hashlib
        sha = hashlib.sha256(presented.encode()).hexdigest()
        with Session(self.engine) as s:
            r = s.scalars(select(ApiTokenRow).where(
                ApiTokenRow.token_sha256 == sha,
                ApiTokenRow.revoked_at.is_(None))).first()
            if r is None:
                return None
            r.last_used_at = _now()
            s.commit()
            return r.scope

    def api_tokens(self) -> list[dict]:
        with Session(self.engine) as s:
            return [{"id": r.id, "name": r.name, "scope": r.scope,
                     "created_at": r.created_at.isoformat() if r.created_at else None,
                     "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                     "revoked": r.revoked_at is not None}
                    for r in s.scalars(select(ApiTokenRow)).all()]

    def revoke_api_token(self, token_id: int) -> None:
        with Session(self.engine) as s:
            r = s.get(ApiTokenRow, token_id)
            if r and r.revoked_at is None:
                r.revoked_at = _now()
                s.commit()

    def user_count(self) -> int:
        with Session(self.engine) as s:
            return int(s.scalar(select(func.count()).select_from(UserRow)) or 0)

    @staticmethod
    def _norm_email(email: str) -> str:
        return email.strip().lower()

    def create_user(self, u: User, password: str) -> User:
        with Session(self.engine) as s:
            r = UserRow(email=self._norm_email(u.email),
                        display_name=u.display_name, role=u.role,
                        password_hash=security.hash_password(password),
                        person_id=u.person_id, disabled=u.disabled)
            s.add(r)
            s.commit()
            u.id = r.id
            return u

    # ── password recovery (no mail server: token minted by the recover CLI) ──
    def user_by_email(self, email: str):
        with Session(self.engine) as s:
            r = s.scalars(select(UserRow).where(
                UserRow.email == self._norm_email(email))).first()
            return (User(id=r.id, email=r.email, display_name=r.display_name,
                         role=r.role, person_id=r.person_id, disabled=r.disabled,
                         totp_enabled=r.totp_enabled)
                    if r is not None else None)

    def recent_reset_token(self, user_id: int, within_min: int = 15) -> bool:
        """True if a reset token was minted for this user within `within_min` —
        the rate-limit guard so /auth/forgot can't be used to mailbomb an inbox
        or burn the SMTP quota."""
        from datetime import timedelta
        with Session(self.engine) as s:
            r = s.scalars(select(PasswordResetRow)
                          .where(PasswordResetRow.user_id == user_id)
                          .order_by(PasswordResetRow.created_at.desc())).first()
            if r is None:
                return False
            ca = r.created_at
            if ca.tzinfo is None:
                ca = ca.replace(tzinfo=timezone.utc)
            return (_now() - ca) < timedelta(minutes=within_min)

    def create_reset_token(self, user_id: int, token_sha256: str, hours: int = 1) -> None:
        from datetime import timedelta
        with Session(self.engine) as s:
            # one live token per user — drop any previous, unredeemed ones
            for old in s.scalars(select(PasswordResetRow).where(
                    PasswordResetRow.user_id == user_id)).all():
                s.delete(old)
            s.add(PasswordResetRow(user_id=user_id, token_sha256=token_sha256,
                                   expires_at=_now() + timedelta(hours=hours)))
            s.commit()

    def reset_password_with_token(self, token_sha256: str, new_password: str) -> bool:
        """Redeem a one-time reset token: set the new password, revoke every
        session, clear the lockout counters, and consume the token. False if the
        token is unknown/expired."""
        with Session(self.engine) as s:
            r = s.scalars(select(PasswordResetRow).where(
                PasswordResetRow.token_sha256 == token_sha256)).first()
            if r is None:
                return False
            exp = r.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < _now():
                s.delete(r)
                s.commit()
                return False
            user = s.get(UserRow, r.user_id)
            if user is None:
                s.delete(r)
                s.commit()
                return False
            user.password_hash = security.hash_password(new_password)
            user.failed_logins = 0
            user.backoff_until = None
            for sess in s.scalars(select(SessionRow).where(
                    SessionRow.user_id == user.id)).all():
                s.delete(sess)
            s.delete(r)                      # one-time use
            s.commit()
            return True

    def create_session(self, user_id: int, token_sha256: str, days: int = 30) -> None:
        from datetime import timedelta
        with Session(self.engine) as s:
            s.add(SessionRow(user_id=user_id, token_sha256=token_sha256,
                             expires_at=_now() + timedelta(days=days)))
            s.commit()

    def session_user(self, token_sha256: str) -> User | None:
        with Session(self.engine) as s:
            r = s.scalars(select(SessionRow).where(
                SessionRow.token_sha256 == token_sha256)).first()
            if r is None:
                return None
            exp = r.expires_at
            if exp is not None and exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp is not None and exp < _now():
                s.delete(r)
                s.commit()
                return None
            r.last_seen_at = _now()
            u = s.get(UserRow, r.user_id)
            s.commit()
            if u is None or u.disabled:
                return None
            return User(id=u.id, email=u.email, display_name=u.display_name,
                        role=u.role, person_id=u.person_id, disabled=u.disabled,
                        totp_enabled=u.totp_enabled)

    def delete_session(self, token_sha256: str) -> None:
        with Session(self.engine) as s:
            r = s.scalars(select(SessionRow).where(
                SessionRow.token_sha256 == token_sha256)).first()
            if r:
                s.delete(r)
                s.commit()

    def change_password(self, user_id: int, current: str, new: str) -> bool:
        """Verify `current`, set `new`, revoke every session (caller re-mints
        one for the active browser). Returns False if `current` is wrong."""
        with Session(self.engine) as s:
            r = s.get(UserRow, user_id)
            if r is None:
                return False
            ok, _ = security.verify_password(current, r.password_hash)
            if not ok:
                return False
            r.password_hash = security.hash_password(new)
            for row in s.scalars(select(SessionRow).where(
                    SessionRow.user_id == user_id)).all():
                s.delete(row)
            s.commit()
            return True

    def verify_login(self, email: str, password: str) -> User | None:
        from datetime import timedelta
        LOCK_AFTER = 5            # consecutive failures before we start backing off
        now = datetime.now(timezone.utc)
        with Session(self.engine) as s:
            r = s.scalars(select(UserRow).where(
                UserRow.email == self._norm_email(email))).first()
            if r is None or r.disabled:
                return None
            # Locked out? Refuse WITHOUT running argon2 — both throttles guessing
            # and removes the unauth CPU-flood (argon2-per-request) DoS lever.
            bu = r.backoff_until
            if bu is not None:
                if bu.tzinfo is None:
                    bu = bu.replace(tzinfo=timezone.utc)
                if now < bu:
                    return None
            ok, new_hash = security.verify_password(password, r.password_hash)
            if not ok:
                r.failed_logins = (r.failed_logins or 0) + 1
                if r.failed_logins >= LOCK_AFTER:   # exponential backoff, capped 15m
                    delay = min(2 ** (r.failed_logins - LOCK_AFTER) * 5, 900)
                    r.backoff_until = now + timedelta(seconds=delay)
                s.commit()
                return None
            # success — clear the counters, UNLESS a second factor is still owed:
            # the TOTP/recovery step manages the same backoff so wrong codes can't
            # be brute-forced (note_2fa_failure / clear_auth_failures below).
            if not r.totp_enabled and (r.failed_logins or r.backoff_until is not None):
                r.failed_logins = 0
                r.backoff_until = None
            if new_hash:
                r.password_hash = new_hash
            s.commit()
            return User(id=r.id, email=r.email, display_name=r.display_name,
                        role=r.role, person_id=r.person_id, disabled=r.disabled,
                        totp_enabled=r.totp_enabled)

    def _bump_backoff(self, r: "UserRow", now: datetime) -> None:
        from datetime import timedelta
        LOCK_AFTER = 5
        r.failed_logins = (r.failed_logins or 0) + 1
        if r.failed_logins >= LOCK_AFTER:
            delay = min(2 ** (r.failed_logins - LOCK_AFTER) * 5, 900)
            r.backoff_until = now + timedelta(seconds=delay)

    def note_2fa_failure(self, user_id: int) -> None:
        """A wrong TOTP/recovery code counts toward the same lockout as a wrong
        password, so the second factor can't be brute-forced once the password
        is known. The next login attempt is refused by verify_login's backoff."""
        with Session(self.engine) as s:
            r = s.get(UserRow, user_id)
            if r is not None:
                self._bump_backoff(r, datetime.now(timezone.utc))
                s.commit()

    def clear_auth_failures(self, user_id: int) -> None:
        """Clear lockout counters after a fully successful login (password + 2FA)."""
        with Session(self.engine) as s:
            r = s.get(UserRow, user_id)
            if r is not None and (r.failed_logins or r.backoff_until is not None):
                r.failed_logins = 0
                r.backoff_until = None
                s.commit()

    def check_password(self, user_id: int, password: str) -> bool:
        """Verify a password with NO side effects (no lockout counters) — for
        re-auth on sensitive actions like disabling 2FA."""
        with Session(self.engine) as s:
            r = s.get(UserRow, user_id)
            if r is None:
                return False
            ok, _ = security.verify_password(password, r.password_hash)
            return ok

    # ── two-factor (TOTP) ───────────────────────────────────────────────────
    def set_totp_pending(self, user_id: int, secret_plain: str) -> None:
        """Store a not-yet-confirmed TOTP secret (encrypted). Enabled stays off
        until the user proves a code via enable_totp()."""
        with Session(self.engine) as s:
            r = s.get(UserRow, user_id)
            if r is None:
                return
            r.totp_secret_encrypted = security.encrypt_secret(secret_plain)
            r.totp_enabled = False
            s.commit()

    def totp_secret(self, user_id: int) -> str | None:
        with Session(self.engine) as s:
            r = s.get(UserRow, user_id)
            if r is None or not r.totp_secret_encrypted:
                return None
            try:
                return security.decrypt_secret(r.totp_secret_encrypted)
            except Exception:
                return None

    def enable_totp(self, user_id: int, recovery_shas: list[str]) -> None:
        with Session(self.engine) as s:
            r = s.get(UserRow, user_id)
            if r is None:
                return
            r.totp_enabled = True
            r.recovery_codes_json = json.dumps([{"sha": h, "used": False} for h in recovery_shas])
            s.commit()

    def disable_totp(self, user_id: int) -> None:
        with Session(self.engine) as s:
            r = s.get(UserRow, user_id)
            if r is None:
                return
            r.totp_enabled = False
            r.totp_secret_encrypted = None
            r.recovery_codes_json = "[]"
            s.commit()

    def consume_recovery_code(self, user_id: int, code_plain: str) -> bool:
        """Mark a matching unused recovery code used. True if one matched."""
        import hmac
        sha = security.recovery_sha(code_plain)
        with Session(self.engine) as s:
            r = s.get(UserRow, user_id)
            if r is None:
                return False
            codes = json.loads(r.recovery_codes_json or "[]")
            for c in codes:
                if not c.get("used") and hmac.compare_digest(str(c.get("sha", "")), sha):
                    c["used"] = True
                    r.recovery_codes_json = json.dumps(codes)
                    s.commit()
                    return True
            return False


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

"""TimeSeriesStore adapter — InfluxDB 2.x (ADR-3).

Schema: docs/DATA_MODEL.md §1. Invariants enforced here:
- one value type per measurement (num XOR str field) — no Flux type collisions
- slow roles (person, alarm_time) get an extended read lookback
- single client, injected; domain code never constructs one
"""
from __future__ import annotations

import logging
import warnings
from datetime import datetime, timedelta, timezone

from influxdb_client.client.warnings import MissingPivotFunction

warnings.simplefilter("ignore", MissingPivotFunction)

import pandas as pd
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from ..domain.schemas import Binding, EntityState, LabelEvent, Prediction, Role

log = logging.getLogger(__name__)

RAW_BUCKET, FEAT_BUCKET, ML_BUCKET = "hearth_raw", "hearth_features", "hearth_ml"

# Roles whose state maps to a float `num` field; everything else -> `str`.
NUMERIC_ROLES = {Role.PRESENCE, Role.BED, Role.POWER, Role.ENV, Role.DOOR,
                 Role.FOCUS, Role.STEPS, Role.BATTERY, Role.LIGHT, Role.CUSTOM}
# State-change-only writers: read with a long lookback so the last value is found.
SLOW_ROLES = {Role.PERSON, Role.ALARM_TIME}
SLOW_LOOKBACK = timedelta(days=7)

_ON_STATES = {"on", "true", "open", "detected", "home", "playing", "occupied"}


def coerce_value(binding: Binding, state) -> tuple[str, float | str | None]:
    """-> (field_name 'num'|'str', value). None = unrecordable (unknown/unavailable)."""
    if state is None or state in ("unknown", "unavailable", ""):
        return ("num" if binding.role in NUMERIC_ROLES else "str"), None
    if binding.role in NUMERIC_ROLES:
        if isinstance(state, (int, float)):
            return "num", float(state)
        s = str(state).strip().lower()
        if s in _ON_STATES:
            return "num", 1.0
        if s in {"off", "false", "closed", "clear", "not_home", "idle", "empty"}:
            return "num", 0.0
        try:
            return "num", float(s)
        except ValueError:
            return "num", None
    return "str", str(state)


def inspect_influx(url: str, org: str, token: str, max_buckets: int = 25) -> dict:
    """Staged connection check for the wizard: reachable -> authed -> buckets
    -> per-bucket data stats. Never raises; every stage reports its own truth.
    """
    out: dict = {"reachable": False, "authed": False, "buckets": [], "error": None}
    try:
        client = InfluxDBClient(url=url, token=token, org=org, timeout=10_000)
    except Exception as exc:
        out["error"] = str(exc)
        return out
    try:
        out["reachable"] = bool(client.ping())
        if not out["reachable"]:
            out["error"] = "No InfluxDB at this URL"
            return out
        try:
            buckets = client.buckets_api().find_buckets(limit=max_buckets).buckets
            out["authed"] = True
        except Exception as exc:
            out["error"] = f"Token rejected or wrong org: {exc}"
            return out
        q = client.query_api()
        for b in buckets:
            if b.name.startswith("_"):
                continue
            info = {"name": b.name, "measurements": None,
                    "points_24h": None, "earliest": None}
            try:
                flux = (f'import "influxdata/influxdb/schema" '
                        f'schema.measurements(bucket: "{b.name}")')
                info["measurements"] = sum(len(t.records) for t in q.query(flux))
            except Exception:
                pass
            try:
                flux = (f'from(bucket: "{b.name}") |> range(start: -24h) '
                        f'|> group() |> count()')
                for t in q.query(flux):
                    for rec in t.records:
                        info["points_24h"] = int(rec.get_value() or 0)
            except Exception:
                pass
            try:
                flux = (f'from(bucket: "{b.name}") |> range(start: -2y) '
                        f'|> group() |> first() |> keep(columns: ["_time"])')
                for t in q.query(flux):
                    for rec in t.records:
                        info["earliest"] = rec["_time"].isoformat()
            except Exception:
                pass
            out["buckets"].append(info)
    finally:
        client.close()
    return out


class InfluxStore:
    """Implements domain.ports.TimeSeriesStore."""

    def __init__(self, url: str, org: str, token: str) -> None:
        self.org = org
        self.client = InfluxDBClient(url=url, token=token, org=org, timeout=30_000)
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()

    def ping(self) -> bool:
        try:
            return self.client.ping()
        except Exception:
            return False

    def ensure_buckets(self) -> None:
        api = self.client.buckets_api()
        existing = {b.name for b in api.find_buckets().buckets}
        retention = {RAW_BUCKET: 180 * 86400, FEAT_BUCKET: 365 * 86400, ML_BUCKET: 0}
        for name, secs in retention.items():
            if name not in existing:
                rules = [{"type": "expire", "everySeconds": secs}] if secs else []
                api.create_bucket(bucket_name=name, retention_rules=rules, org=self.org)
                log.info("Created bucket %s", name)

    # ── raw ────────────────────────────────────────────────────────────────
    def write_raw(self, binding: Binding, states: list[EntityState]) -> None:
        points = []
        for st in states:
            field, value = coerce_value(binding, st.state)
            if value is None:
                continue
            p = (Point(f"raw_{binding.name}")
                 .tag("entity_id", binding.entity_id)
                 .tag("role", binding.role.value)
                 .time(st.ts))
            if binding.room:
                p = p.tag("room", binding.room)
            if binding.person_id:
                p = p.tag("person", binding.person_id)
            points.append(p.field(field, value))
        if points:
            self.write_api.write(bucket=RAW_BUCKET, record=points)

    def read_raw(self, bindings: list[Binding], start: datetime, end: datetime,
                 freq: str = "1m") -> pd.DataFrame:
        """Wide 1-min DataFrame, one column per binding.name (UTC index)."""
        series: dict[str, pd.Series] = {}
        for b in bindings:
            b_start = start - SLOW_LOOKBACK if b.role in SLOW_ROLES else start
            field = "num" if b.role in NUMERIC_ROLES else "str"
            flux = f'''
from(bucket: "{RAW_BUCKET}")
  |> range(start: {b_start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "raw_{b.name}" and r._field == "{field}")
  |> aggregateWindow(every: {freq}, fn: last, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
'''
            try:
                df = self.query_api.query_data_frame(flux)
            except Exception as exc:
                log.warning("read_raw %s failed: %s", b.name, exc)
                continue
            if isinstance(df, list):
                df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
            if df.empty:
                continue
            s = (df[["_time", "_value"]].assign(_time=lambda d: pd.to_datetime(d["_time"], utc=True))
                 .set_index("_time")["_value"].sort_index())
            series[b.name] = s[~s.index.duplicated(keep="last")]
        if not series:
            return pd.DataFrame()
        wide = pd.concat(series, axis=1)
        wide.index.name = "time"
        return wide

    # ── features ───────────────────────────────────────────────────────────
    def write_features(self, person: str, feature_set: str, rows: pd.DataFrame) -> None:
        points = []
        for ts, row in rows.iterrows():
            p = (Point("features").tag("person", person)
                 .tag("feature_set", feature_set).tag("window", "30m").time(ts))
            for col, val in row.items():
                p = p.field(col, float(val))
            points.append(p)
        if points:
            self.write_api.write(bucket=FEAT_BUCKET, record=points)

    def read_features(self, person: str, feature_set: str,
                      start: datetime, end: datetime) -> pd.DataFrame:
        flux = f'''
from(bucket: "{FEAT_BUCKET}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "features" and r.person == "{person}"
                       and r.feature_set == "{feature_set}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
        df = self.query_api.query_data_frame(flux)
        if isinstance(df, list):
            df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
        if df.empty:
            return pd.DataFrame()
        df["_time"] = pd.to_datetime(df["_time"], utc=True)
        drop = [c for c in ("result", "table", "_start", "_stop", "_measurement",
                            "person", "feature_set", "window") if c in df.columns]
        return df.drop(columns=drop).set_index("_time").sort_index()

    def last_feature_time(self, person: str, feature_set: str) -> datetime | None:
        end = datetime.now(timezone.utc)
        df = self.read_features(person, feature_set, end - timedelta(days=7), end)
        return None if df.empty else df.index[-1].to_pydatetime()

    # ── ml ─────────────────────────────────────────────────────────────────
    def write_prediction(self, pred: Prediction) -> None:
        p = (Point("predictions").tag("person", pred.person_id)
             .tag("model_version", pred.model_version).time(pred.window_ts)
             .field("predicted", pred.predicted)
             .field("smoothed", pred.smoothed or pred.predicted)
             .field("confidence", float(pred.confidence)))
        for cls, prob in pred.probabilities.items():
            p = p.field(f"prob_{cls}", float(prob))
        self.write_api.write(bucket=ML_BUCKET, record=p)

    def write_label(self, label: LabelEvent) -> None:
        p = (Point("labels").tag("person", label.person_id)
             .tag("provenance", label.provenance.value).tag("source", label.source)
             .time(datetime.now(timezone.utc))
             .field("label", label.label)
             .field("window_ts", label.window_ts.timestamp()))
        if label.activity:
            p = p.field("activity", label.activity)
        self.write_api.write(bucket=ML_BUCKET, record=p)

    def read_labels(self, person: str, start: datetime, end: datetime) -> list[LabelEvent]:
        from ..domain.schemas import Provenance
        flux = f'''
from(bucket: "{ML_BUCKET}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "labels" and r.person == "{person}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
        df = self.query_api.query_data_frame(flux)
        if isinstance(df, list):
            df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
        out: list[LabelEvent] = []
        if df.empty or "label" not in df.columns:
            return out
        for _, r in df.iterrows():
            out.append(LabelEvent(
                person_id=person,
                window_ts=datetime.fromtimestamp(float(r["window_ts"]), tz=timezone.utc),
                label=str(r["label"]),
                activity=str(r["activity"]) if "activity" in df.columns and pd.notna(r.get("activity")) else None,
                provenance=Provenance(r["provenance"]),
                source=str(r.get("source", "ui"))))
        return out

    def read_predictions(self, person: str, start: datetime, end: datetime) -> list[dict]:
        """Prediction history for the dashboard/API — newest first.
        Each: {time, predicted, smoothed, confidence, model_version, probs{}}."""
        flux = f"""
from(bucket: "{ML_BUCKET}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "predictions" and r.person == "{person}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        df = self.query_api.query_data_frame(flux)
        if isinstance(df, list):
            df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
        if df.empty or "predicted" not in df.columns:
            return []
        out = []
        for _, r in df.sort_values("_time", ascending=False).iterrows():
            probs = {c[5:]: float(r[c]) for c in df.columns
                     if c.startswith("prob_") and pd.notna(r[c])}
            out.append({
                "time": pd.to_datetime(r["_time"]).isoformat(),
                "predicted": str(r["predicted"]),
                "smoothed": str(r.get("smoothed", r["predicted"])),
                "confidence": float(r["confidence"]),
                "model_version": str(r.get("model_version", "")),
                "probs": probs,
            })
        return out

    def first_raw_time(self) -> datetime | None:
        """Earliest raw point — powers the cold-start journey day counter."""
        flux = f"""
from(bucket: "{RAW_BUCKET}")
  |> range(start: -180d)
  |> filter(fn: (r) => r._field == "num" or r._field == "str")
  |> group()
  |> first()
  |> keep(columns: ["_time"])
"""
        try:
            for table in self.query_api.query(flux):
                for rec in table.records:
                    return rec["_time"]
        except Exception:
            return None
        return None

    def count_raw_events(self, hours: int = 24) -> int:
        flux = f"""
from(bucket: "{RAW_BUCKET}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r._field == "num" or r._field == "str")
  |> group()
  |> count()
"""
        try:
            for table in self.query_api.query(flux):
                for rec in table.records:
                    return int(rec.get_value() or 0)
        except Exception:
            return 0
        return 0

    def write_heartbeat(self, job: str) -> None:
        self.write_api.write(bucket=ML_BUCKET,
                             record=Point("heartbeat").tag("job", job).field("alive", 1))

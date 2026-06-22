"""TimeSeriesStore adapter — InfluxDB 2.x (ADR-3).

Schema: docs/DATA_MODEL.md §1. Invariants enforced here:
- one value type per measurement (num XOR str field) — no Flux type collisions
- slow roles (person, alarm_time) get an extended read lookback
- single client, injected; domain code never constructs one
"""
from __future__ import annotations

import json

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

# Raw-signal retention default (days). Raw is only the SOURCE features are built
# from + the window the live health/behaviour views look back over — it is NOT
# the model corpus, so it doesn't need to be kept forever. The FEATURE bucket
# (the actual model data) and the ML bucket (predictions/labels) are ALWAYS kept
# forever; this knob bounds raw only. Trade-off: feature rebuilds after a
# feature-set change reach back only as far as raw is retained. Live value is the
# 'retention.days' setting (Settings → Model), applied on boot via set_retention().
DEFAULT_RAW_RETENTION_DAYS = 90   # ~a quarter of raw; covers the 4-week views + rebuild headroom


def _flux_tag(value: str) -> str:
    """Escape a string for safe interpolation into a Flux double-quoted
    literal — backslash and double-quote only (Flux string rules). Tag values
    (person ids) are slugs in practice, but never trust that at the boundary."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')

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
            name = _flux_tag(b.name)   # bucket names come from the remote Influx — escape
            try:
                flux = (f'import "influxdata/influxdb/schema" '
                        f'schema.measurements(bucket: "{name}")')
                info["measurements"] = sum(len(t.records) for t in q.query(flux))
            except Exception:
                pass
            try:
                flux = (f'from(bucket: "{name}") |> range(start: -24h) '
                        f'|> group() |> count()')
                for t in q.query(flux):
                    for rec in t.records:
                        info["points_24h"] = int(rec.get_value() or 0)
            except Exception:
                pass
            try:
                flux = (f'from(bucket: "{name}") |> range(start: -2y) '
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
        # 60 s: big homes (1000+ entities) make heavy feature reads that can run
        # past the old 30 s default and time out mid-query. Generous but bounded.
        self.client = InfluxDBClient(url=url, token=token, org=org, timeout=60_000)
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()

    def ping(self) -> bool:
        try:
            return self.client.ping()
        except Exception:
            return False

    def wipe_all(self) -> None:
        """Factory reset of the time-series side: drop and recreate the three
        Hearth buckets, erasing all raw events, features and predictions."""
        api = self.client.buckets_api()
        for name in (RAW_BUCKET, FEAT_BUCKET, ML_BUCKET):
            try:
                b = api.find_bucket_by_name(name)
                if b is not None:
                    api.delete_bucket(b)
                    log.info("Wiped bucket %s", name)
            except Exception as exc:
                log.warning("wipe_all: could not drop %s: %s", name, exc)
        self.ensure_buckets()

    def ensure_buckets(self, raw_retention_days: int = DEFAULT_RAW_RETENTION_DAYS) -> None:
        api = self.client.buckets_api()
        existing = {b.name for b in api.find_buckets().buckets}
        secs = max(0, int(raw_retention_days)) * 86400
        # features (the model corpus) and ml (predictions/labels — ground truth)
        # are kept FOREVER; only raw expires, since it's just the source features
        # are built from and the look-back for the live views.
        retention = {RAW_BUCKET: secs, FEAT_BUCKET: 0, ML_BUCKET: 0}
        for name, secs in retention.items():
            if name not in existing:
                rules = [{"type": "expire", "everySeconds": secs}] if secs else []
                api.create_bucket(bucket_name=name, retention_rules=rules, org=self.org)
                log.info("Created bucket %s", name)

    def set_retention(self, raw_retention_days: int) -> dict:
        """Apply a retention window (days; <=0 = keep forever) to the RAW bucket
        only — features + ml are kept forever and are realigned to 'forever' here
        too, in case an older install had them on a shared expiry. Updates buckets
        in place if they exist. Returns {days, buckets} for those changed."""
        from influxdb_client import BucketRetentionRules
        secs = max(0, int(raw_retention_days)) * 86400
        api = self.client.buckets_api()
        applied: list[str] = []
        targets = {RAW_BUCKET: secs, FEAT_BUCKET: 0, ML_BUCKET: 0}   # features/ml: forever
        for name, want in targets.items():
            b = api.find_bucket_by_name(name)
            if b is None:
                continue
            b.retention_rules = ([BucketRetentionRules(type="expire", every_seconds=want)]
                                 if want else [])
            api.update_bucket(bucket=b)
            applied.append(name)
            log.info("Set retention on %s to %d days", name, want // 86400 if want else 0)
        return {"days": raw_retention_days, "buckets": applied}

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
        """Wide 1-min DataFrame, one column per binding.name (UTC index).

        Batched: one Flux round-trip per lookback group (slow roles get the
        extended window) instead of one per binding — so a 100-sensor home is
        ~2 queries per build, not 100. Keeping `_measurement` (not `_field`)
        leaves each entity's series in its own table, so num and str never
        collide in a shared `_value` column.
        """
        # group bindings by their effective start (slow roles read further back)
        groups: dict[datetime, list[Binding]] = {}
        for b in bindings:
            b_start = start - SLOW_LOOKBACK if b.role in SLOW_ROLES else start
            groups.setdefault(b_start, []).append(b)

        series: dict[str, pd.Series] = {}
        for b_start, group in groups.items():
            meas_set = "[" + ", ".join(f'"raw_{_flux_tag(b.name)}"' for b in group) + "]"
            flux = f'''
from(bucket: "{RAW_BUCKET}")
  |> range(start: {b_start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => contains(value: r._measurement, set: {meas_set})
                       and (r._field == "num" or r._field == "str"))
  |> aggregateWindow(every: {freq}, fn: last, createEmpty: false)
  |> keep(columns: ["_time", "_value", "_measurement"])
'''
            try:
                df = self.query_api.query_data_frame(flux)
            except Exception as exc:
                log.warning("read_raw batch failed (%d sensors): %s", len(group), exc)
                continue
            if isinstance(df, list):
                df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
            if df.empty or "_measurement" not in df.columns:
                continue
            df = df.assign(_time=lambda d: pd.to_datetime(d["_time"], utc=True))
            for meas, sub in df.groupby("_measurement"):
                name = str(meas)[4:]            # strip "raw_"
                s = sub.set_index("_time")["_value"].sort_index()
                series[name] = s[~s.index.duplicated(keep="last")]
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
        # drop tag/system columns BEFORE the pivot: smaller payload over the wire
        # and a narrower pivot (these aren't needed — we filtered on them already).
        flux = f'''
from(bucket: "{FEAT_BUCKET}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "features" and r.person == "{_flux_tag(person)}"
                       and r.feature_set == "{_flux_tag(feature_set)}")
  |> drop(columns: ["_start", "_stop", "_measurement", "person", "feature_set", "window"])
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
        """Latest feature-window timestamp for (person, feature_set).

        This runs every 5 minutes per person, so it must be cheap. It asks Influx
        for ONE timestamp — no pivot (the costliest Flux op), no value transfer —
        and probes a short window first (the builder keeps features minutes-fresh),
        widening only after downtime. The old implementation read 7 days of every
        feature column and pivoted it just to take the last index, which on a
        large home dominated DB load and timed out."""
        end = datetime.now(timezone.utc)
        for days in (1, 7, 90):
            start = end - timedelta(days=days)
            flux = f'''
from(bucket: "{FEAT_BUCKET}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "features" and r.person == "{_flux_tag(person)}"
                       and r.feature_set == "{_flux_tag(feature_set)}")
  |> keep(columns: ["_time"])
  |> group()
  |> max(column: "_time")
'''
            try:
                df = self.query_api.query_data_frame(flux)
            except Exception:
                log.warning("last_feature_time probe failed", exc_info=True)
                return None
            if isinstance(df, list):
                df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
            if not df.empty and "_time" in df.columns and pd.notna(df["_time"].iloc[0]):
                return pd.to_datetime(df["_time"].iloc[0], utc=True).to_pydatetime()
        return None

    # ── ml ─────────────────────────────────────────────────────────────────
    def write_prediction(self, pred: Prediction) -> None:
        # model version is a FIELD, not a tag: tags define series identity and
        # would duplicate the same window across versions (the ribbon bug)
        p = (Point("predictions").tag("person", pred.person_id)
             .time(pred.window_ts)
             .field("model", pred.model_version)
             .field("predicted", pred.predicted)
             .field("smoothed", pred.smoothed or pred.predicted)
             .field("confidence", float(pred.confidence)))
        if pred.evidence is not None:
            p = p.field("evidence", float(pred.evidence))
        if pred.parent:
            p = p.field("parent", pred.parent)
        if pred.explanation:
            import json as _json
            p = p.field("explanation", _json.dumps(pred.explanation[:3]))
        if pred.coarse_confidence is not None:
            p = p.field("coarse_confidence", float(pred.coarse_confidence))
        for cls, prob in pred.probabilities.items():
            p = p.field(f"prob_{cls}", float(prob))
        self.write_api.write(bucket=ML_BUCKET, record=p)

    def write_label(self, label: LabelEvent) -> None:
        p = (Point("labels").tag("person", label.person_id)
             .tag("provenance", label.provenance.value).tag("source", label.source)
             .time(datetime.now(timezone.utc))
             .field("label", label.label)
             .field("gold", bool(label.gold))
             .field("window_ts", label.window_ts.timestamp()))
        if label.activity:
            p = p.field("activity", label.activity)
        self.write_api.write(bucket=ML_BUCKET, record=p)

    def read_labels(self, person: str, start: datetime, end: datetime) -> list[LabelEvent]:
        from ..domain.schemas import Provenance
        flux = f'''
from(bucket: "{ML_BUCKET}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "labels" and r.person == "{_flux_tag(person)}")
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
                source=str(r.get("source", "ui")),
                gold=bool(r["gold"]) if "gold" in df.columns and pd.notna(r.get("gold")) else False))
        return out

    def read_predictions(self, person: str, start: datetime, end: datetime) -> list[dict]:
        """Prediction history for the dashboard/API — newest first.
        Each: {time, predicted, smoothed, confidence, model_version, probs{}}."""
        flux = f"""
from(bucket: "{ML_BUCKET}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._measurement == "predictions" and r.person == "{_flux_tag(person)}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        df = self.query_api.query_data_frame(flux)
        if isinstance(df, list):
            df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
        if df.empty or "predicted" not in df.columns:
            return []
        # dedupe legacy duplicates (model_version-as-tag era): one row per
        # window, preferring rows written with the new 'model' field
        if "model" in df.columns:
            df["_pref"] = df["model"].notna().astype(int)
        else:
            df["_pref"] = 0
        df = (df.sort_values(["_time", "_pref"])
                .drop_duplicates(subset="_time", keep="last"))
        out = []
        for _, r in df.sort_values("_time", ascending=False).iterrows():
            probs = {c[5:]: float(r[c]) for c in df.columns
                     if c.startswith("prob_") and pd.notna(r[c])}
            version = r.get("model")
            if version is None or pd.isna(version):
                version = r.get("model_version", "")
            out.append({
                "time": pd.to_datetime(r["_time"]).isoformat(),
                "predicted": str(r["predicted"]),
                "smoothed": str(r.get("smoothed", r["predicted"])),
                "confidence": float(r["confidence"]),
                "model_version": str(version),
                "probs": probs,
                "evidence": (float(r["evidence"])
                             if "evidence" in df.columns and pd.notna(r.get("evidence"))
                             else None),
                "parent": (str(r["parent"])
                           if "parent" in df.columns and pd.notna(r.get("parent"))
                           else None),
                "explanation": (json.loads(r["explanation"])
                                if "explanation" in df.columns
                                and pd.notna(r.get("explanation")) else []),
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

    def raw_event_counts(self, names: list[str], days: int = 7) -> dict:
        """{binding.name: observation count} over the last `days` — counts raw
        state writes per sensor (one point per reported state)."""
        if not names:
            return {}
        flux = f"""
from(bucket: "{RAW_BUCKET}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._field == "num" or r._field == "str")
  |> group(columns: ["_measurement"])
  |> count()
"""
        out: dict = {}
        try:
            for table in self.query_api.query(flux):
                for rec in table.records:
                    meas = rec.values.get("_measurement", "")
                    if meas.startswith("raw_"):
                        out[meas[4:]] = int(rec.get_value() or 0)
        except Exception as exc:
            log.warning("raw_event_counts failed: %s", exc)
        return {n: out.get(n, 0) for n in names}

    def raw_traces(self, names: list[str], start: datetime, end: datetime,
                   buckets: int = 60) -> dict[str, list[float]]:
        """{binding.name: [downsampled numeric values]} over [start, end], ~buckets
        points each. Numeric field only — string-state sensors return nothing.
        Drives the Sensors-page sparkline so it always has dense data over the
        chosen window (independent of feature-store / feature-set churn)."""
        if not names:
            return {}
        span = max((end - start).total_seconds(), 60.0)
        every = max(60, int(span // max(buckets, 1)))
        flux = f"""
from(bucket: "{RAW_BUCKET}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r._field == "num")
  |> aggregateWindow(every: {every}s, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value", "_measurement"])
"""
        series: dict[str, list[tuple]] = {}
        try:
            for table in self.query_api.query(flux):
                for rec in table.records:
                    meas = rec.values.get("_measurement", "")
                    if meas.startswith("raw_"):
                        series.setdefault(meas[4:], []).append((rec.get_time(), rec.get_value()))
        except Exception as exc:
            log.warning("raw_traces failed: %s", exc)
            return {}
        wanted = set(names)
        out: dict[str, list[float]] = {}
        for name, pts in series.items():
            if name in wanted:
                pts.sort(key=lambda p: p[0])
                out[name] = [float(v) for _, v in pts if v is not None]
        return out

    def recent_active_names(self, minutes: int = 15) -> set[str]:
        """Binding names that wrote at least one raw point in the last `minutes`
        — the live 'heartbeat' for the coverage map (which dots/rooms pulse)."""
        flux = f"""
from(bucket: "{RAW_BUCKET}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r._field == "num" or r._field == "str")
  |> group(columns: ["_measurement"])
  |> count()
"""
        out: set[str] = set()
        try:
            for table in self.query_api.query(flux):
                for rec in table.records:
                    meas = rec.values.get("_measurement", "")
                    if meas.startswith("raw_") and (rec.get_value() or 0) > 0:
                        out.add(meas[4:])
        except Exception as exc:
            log.warning("recent_active_names failed: %s", exc)
        return out

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

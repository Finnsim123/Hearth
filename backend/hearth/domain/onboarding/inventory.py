"""Entity inventory builder — the onboarding assistant's input artifact.

Builds one JSON document describing everything a home has (DATA_MODEL.md §4):
  - GET /api/states                      every entity + attributes
  - WS config/entity_registry/list      device_class, area, device, disabled
  - WS config/area_registry/list        room candidates
  - + aggregate stats per entity over the last `stats_days` when a history
    source exists (HA recorder or an existing HA->Influx bucket)

Privacy contract: this document (metadata + aggregates) is the ONLY thing
ever shared with an LLM. Raw time series never leave the stack. The wizard
renders it and offers a download button.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..ports import TimeSeriesStore

_ENUM_MAX = 12  # at most this many distinct values still counts as an enum


async def build_inventory(events, tsdb: TimeSeriesStore | None, stats_days: int = 14) -> list[dict]:
    """events: HaWebSocketSource (EventSource + discover_entities()).
    Returns the inventory list; stats fields are None without history."""
    raise NotImplementedError


def value_type_of(series) -> str:
    """Classify an entity's value space from its observed states: boolean,
    enum, numeric_discrete, numeric_continuous, string, or unknown. This is
    what drives the LLM's information-tier assignment (llm_layer_design §a/§b)."""
    s = series.dropna() if series is not None else pd.Series(dtype=object)
    if len(s) == 0:
        return "unknown"
    num = pd.to_numeric(s, errors="coerce")
    distinct = int(s.nunique())
    if float(num.notna().mean()) >= 0.95:           # overwhelmingly numeric
        vals = num.dropna()
        if set(np.unique(vals.to_numpy())) <= {0.0, 1.0}:
            return "boolean"
        if bool((vals == vals.round()).all()) and distinct <= _ENUM_MAX:
            return "numeric_discrete"
        return "numeric_continuous"
    return "enum" if distinct <= _ENUM_MAX else "string"


def _change_count(s) -> int:
    v = s.to_numpy()
    return int((v[1:] != v[:-1]).sum()) if len(v) > 1 else 0


def entity_stats(series, days: int, end=None) -> dict:
    """Aggregate, privacy-safe statistics for ONE entity from its state-change
    history `series` (a datetime-indexed pandas Series of raw states). Never
    returns raw values. `end` is the observation end (for staleness/gap). The
    reliability auditor and tier assignment read these (llm_layer_design §a).
    """
    s = series.dropna() if series is not None else pd.Series(dtype=object)
    days = max(int(days), 1)
    vt = value_type_of(s)
    out: dict = {
        "window_days": days,
        "value_type": vt,
        "distinct_values": int(s.nunique()),
        "changes_per_day": round(_change_count(s) / days, 3),
        "active_hours_hist": [0.0] * 24,
        "top_states": None,
        "numeric": None,
        "median_seconds_between_changes": None,
        "longest_gap_hours": None,
        "flatline_frac": None,
        "last_changed_age_hours": None,
    }
    if len(s) == 0:
        return out

    if isinstance(s.index, pd.DatetimeIndex):
        counts = (pd.Series(s.index.hour).value_counts()
                  .reindex(range(24), fill_value=0).to_numpy(dtype=float))
        total = counts.sum()
        if total:
            out["active_hours_hist"] = [round(float(c / total), 4) for c in counts]
        deltas = s.index.to_series().diff().dropna().dt.total_seconds()
        longest = float(deltas.max()) if len(deltas) else 0.0
        if len(deltas):
            out["median_seconds_between_changes"] = round(float(deltas.median()), 1)
        span = (s.index[-1] - s.index[0]).total_seconds()
        if end is not None:
            tail = max((pd.Timestamp(end) - s.index[-1]).total_seconds(), 0.0)
            longest = max(longest, tail)
            span = max(span, (pd.Timestamp(end) - s.index[0]).total_seconds())
            out["last_changed_age_hours"] = round(tail / 3600.0, 2)
        out["longest_gap_hours"] = round(longest / 3600.0, 2)
        if out["distinct_values"] <= 1:
            out["flatline_frac"] = 1.0                 # never moved = stuck/constant
        elif span > 0:
            out["flatline_frac"] = round(min(longest / span, 1.0), 4)
        else:
            out["flatline_frac"] = 0.0

    if vt in ("boolean", "numeric_discrete", "numeric_continuous"):
        num = pd.to_numeric(s, errors="coerce").dropna()
        if len(num):
            diffs = np.diff(num.to_numpy())
            mono = float((diffs >= 0).mean()) if len(diffs) else 1.0
            out["numeric"] = {
                "min": round(float(num.min()), 4),
                "p05": round(float(num.quantile(0.05)), 4),
                "median": round(float(num.median()), 4),
                "p95": round(float(num.quantile(0.95)), 4),
                "max": round(float(num.max()), 4),
                "monotonic_increasing_frac": round(mono, 4),
            }
    else:
        vc = s.astype(str).value_counts(normalize=True)
        out["top_states"] = [{"value": str(k), "frac": round(float(v), 4)}
                             for k, v in vc.head(8).items()]
    return out


# ── aggregate-stats consent (the explicit yes/no privacy lever) ──────────────
# The user decides whether the LLM may see per-entity aggregate statistics and a
# few sample states. With consent off it sees metadata only (names/types/units),
# the reliability auditor can't run, and feature choices are guessed from names.
# Default is "undecided" -> treated as NO until the user chooses (forced in the
# wizard). (llm_layer_design §e; user requirement: explicit yes/no with implications)

def stats_consent(repo) -> bool:
    """True only when the user has explicitly opted IN to sharing aggregate stats."""
    try:
        return repo.get_setting("llm.share_stats") == "yes"
    except Exception:
        return False


def stats_consent_decided(repo) -> bool:
    """True once the user has made an explicit yes/no choice (drives the forced
    wizard prompt: ask until decided)."""
    try:
        return repo.get_setting("llm.share_stats") in ("yes", "no")
    except Exception:
        return False


def set_stats_consent(repo, value) -> bool:
    """Persist the consent choice. Accepts a bool or a yes/no-ish string; raises
    ValueError on anything else (the API maps that to a 400)."""
    if isinstance(value, bool):
        share = value
    elif isinstance(value, str) and value.strip().lower() in ("yes", "true", "1", "on"):
        share = True
    elif isinstance(value, str) and value.strip().lower() in ("no", "false", "0", "off"):
        share = False
    else:
        raise ValueError(f"share must be a yes/no choice, got {value!r}")
    repo.set_setting("llm.share_stats", "yes" if share else "no")
    return share


def recent_samples(series, n: int = 5) -> list[dict]:
    """Up to `n` recent (timestamp, state) pairs to GROUND the LLM in what a real
    value looks like (e.g. 'playing' not '1'). Strings truncated. Only included
    in a catalog record when the user consented to stats sharing."""
    if series is None or len(series) == 0:
        return []
    out = []
    for ts, val in series.dropna().tail(n).items():
        if isinstance(val, str):
            val = val[:32]
        try:
            tss = ts.isoformat()
        except Exception:
            tss = str(ts)
        out.append({"ts_local": tss, "state": val})
    return out


def catalog_record(meta: dict, *, series=None, days: int = 14, end=None,
                   current_binding: dict | None = None,
                   share_stats: bool = False) -> dict:
    """Assemble one entity-catalog record (llm_layer_design §a). Metadata is
    always present; the `stats` and `samples` blocks are populated only when the
    user consented AND history is available, else None."""
    eid = meta.get("entity_id")
    md = {
        "domain": meta.get("domain") or (eid.split(".")[0] if eid else None),
        "friendly_name": meta.get("friendly_name"),
        "device_class": meta.get("device_class"),
        "state_class": meta.get("state_class"),
        "unit_of_measurement": meta.get("unit") or meta.get("unit_of_measurement"),
        "area": meta.get("area"),
        "device": meta.get("device"),
        "entity_category": meta.get("entity_category"),
        "disabled": bool(meta.get("disabled", False)),
        "hidden": bool(meta.get("hidden", False)),
    }
    rec = {"entity_id": eid, "metadata": md, "stats": None, "samples": None,
           "current_binding": current_binding}
    if share_stats and series is not None and len(series) > 0:
        rec["stats"] = entity_stats(series, days, end=end)
        rec["samples"] = recent_samples(series)
    return rec


def build_catalog(inventory: list[dict], *, share_stats: bool = False,
                  series_by_entity: dict | None = None, days: int = 14, end=None,
                  bindings_by_entity: dict | None = None) -> list[dict]:
    """Build the entity catalog the LLM reads from the discovered inventory.
    `series_by_entity` and `bindings_by_entity` are optional maps keyed on
    entity id. Honours the stats-sharing consent (`share_stats`)."""
    series_by_entity = series_by_entity or {}
    bindings_by_entity = bindings_by_entity or {}
    return [
        catalog_record(meta, series=series_by_entity.get(meta.get("entity_id")),
                       days=days, end=end,
                       current_binding=bindings_by_entity.get(meta.get("entity_id")),
                       share_stats=share_stats)
        for meta in inventory
    ]

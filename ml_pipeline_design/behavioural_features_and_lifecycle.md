# Behavioural features, insight surfaces & the person lifecycle

Consolidation doc for the batch of work that added CDR-inspired behavioural
features, the insight surfaces that expose them, lead/lag discovery, and the
per-person data lifecycle (forget / relink / disable) with Hearth self-recognition.
It records what was built, the design decisions, and the known limitations that a
verification sweep surfaced — so the next person doesn't re-derive them.

Source inspiration for the feature ideas: Björkegren & Grosman's mobile-phone
credit-scoring feature methodology (atomic events → per-characteristic count
vectors → summary statistics; ~5,500 features). The transferable ideas were
adopted; the *scale* was not — Hearth trains per-home on a few thousand windows,
so each idea is a handful of principled features, not a factory, and everything
degrades cleanly on sparse homes.

## 1. Feature pipeline additions (`domain/features/pipeline.py`)

All are global, per-window columns computed in `extract_windows` / `impute`,
alongside the existing `evt_*` event-dynamics block. `PIPELINE_VERSION` was bumped
2 → 6 across these (each column-set change forces a clean rebuild + retrain so
train/serve never mix — `registry.feature_set_version`).

**Home mobility (set-based).** From each window's per-sensor change counts folded
into per-room counts (`Binding.room`):
- `mob_rooms_active` — distinct rooms with activity (range)
- `mob_top_room_frac` — busiest room's share (concentration)
- `mob_room_entropy` — normalised Shannon entropy, 0 = one room … 1 = even (roaming)
- `mob_room_switches` — order-aware room transitions within the window (pacing)

**Anchor distance.** `dist_to_<anchor>` = BFS hop-distance from the window's busiest
room to an anchor room, on a room-adjacency graph learned from observed transitions
(`refresh_room_graph`, cached in `room.graph`, refreshed ~daily from
`build_latest_windows`). Anchors are detected by sensor ROLE then room-NAME
(`detect_anchors`) — so a bedroom with only a motion sensor still anchors `bed`.
This *synthesises* proximity signals that fill sensor gaps: `dist_to_bed`
collapsing at night is a sleep cue with no bed occupancy sensor. `DIST_CAP` when
the room is unknown/unreachable.

**Missingness indicators.** `<binding>_missing` (1/0), computed in `impute()`
BEFORE the sentinel fill, so the model can tell "sensor observed absent/off" from
"no reading at all" instead of trusting an imputed −1/0. One flag per binding (not
per suffix) to keep the count modest on small data. Note: a within-ffill-limit
dropout is *not* flagged (the last state is legitimately carried forward).

Degradation: single-sensor / unlabelled homes yield zeros/caps for all of the
above — no crash, just no signal. Confirmed the columns are emitted uniformly on
both the aligned and non-aligned window paths, so no ragged schema.

## 2. "Why" integration

`mob_*` / `dist_to_*` / `<binding>_missing` are classified as tier-2 **behavioural**
evidence (not tier-0 priors) in `features/evidence.py::tier_of_column`, and
humanised in `discovery/lexicon.py` ("Roaming between rooms", "Near the bed", "No
reading from Bedroom") so the Patterns evidence cards and evidence-tier profile
read them in plain language.

## 3. Insight surfaces (`domain/behaviour/`, Behaviour page)

Deliberately descriptive, never framed as a health signal.
- **footprint.py** — "Home footprint": rooms/active-spell, roaming, pacing, WoW
  trend, from the `mob_*` columns.
- **rhythm.py** — "Daily rhythm": autocorrelation at 24h/168h + dominant period via
  FFT over ~4 weeks of hourly activity → "a very regular daily rhythm … about a
  day". Periodicity is an *insight, not a model feature* — a per-person periodicity
  value is constant across that person's windows and can't discriminate within
  their model.

## 4. Lead/lag discovery (`domain/discovery/leadlag.py`)

The home's temporal *wiring*: lagged cross-correlation of per-minute activity
recovers directed A→B edges with a lag (kitchen → hob ~5 min). Capped to the 16
most-active sensors + 15-min max lag; endpoint `GET /bindings/leadlag` caches ~6h.
Surfaced on the Sensors page ("How your home flows").

**Fed into markers.** `markers.suggest_markers_from_leadlag` turns an edge whose
TARGET sensor defines a state (`bed → asleep`, tracker `→ away`) into a marker
suggestion with `lead_min = τ` — never auto-created; the user confirms via the
existing marker upsert. Surfaced as "Add marker" actions in the wiring card.

## 5. Person lifecycle (`domain/people.py`, `adapters/app_db.py`)

Identity is `Person.id` (a stable slug minted once); the display name is a mutable
label, so **rename is lossless** (it only touches `name`). The destructive ops:
- **forget** (`forget_person`) — erase everything that's a departing member's:
  their sensors' raw history + features/labels/predictions (`influx.purge_person`,
  keyed on the `person` tag so shared sensors survive), and the app-DB cascade
  (`delete_person`: rules, questions, models, clusters, their bindings, the person
  row, and clearing any `UserRow.person_id` link). Remaining members are retrained
  in the background so they stop leaning on a ghost.
- **relink** (`relink_person`) — reclaim history orphaned under a previous identity
  by *adopting the old id*: re-key the person's id, their bindings' `person_id` +
  name prefix, rules' `person_id` + predicate column prefixes, questions/models/
  clusters, and the user link. No time-series is rewritten (the series already
  carry the old id). Refuses if the old id is a live person or if a stale old-id
  binding name would collide.
- **disable** — the existing `enabled` flag; reversible, keeps everything.

Predicate rewriting (`_rename_pred_cols`) walks the real AST grammar
(`features/composites.py`): `{"all"|"any": [..]}`, `{"not": ..}`, leaf
`{"feat": col, "op", "value"}` — the column is the `feat` VALUE.

## 6. Self-recognition (`onboarding/advisor.py`, `hierarchy.py`)

Hearth publishes its predictions back to HA (MQTT: `sensor.hearth_<p>_activity`,
`_confidence`, `switch/select.hearth_<p>_*`, `binary_sensor.hearth_alive`, device
"Hearth"). `is_hearth_own` recognises these by object-id suffix + device name and
gates them out of `suggest_role` / `is_noise` / `is_bindable` and the hierarchy
`device_relevance` / `relevance_of`, so the model can never train on its own
output (a feedback loop) and they never appear as a "new device?" prompt.

## 7. Known limitations & decisions

- **`dist_to_*` is not in the feature-set hash.** Its columns/values depend on
  `room.graph` (refreshed daily) and `detect_anchors` (from binding rooms/roles),
  neither hashed into `feature_set_version`. So the graph updating or a room
  changing shifts `dist_*` under a stable version — the same class as adding/moving
  a binding, which is already handled by column alignment at serve time and picked
  up on the next retrain. Accepted as low-risk; revisit if it causes drift.
- **`is_hearth_own` precision.** Matches `hearth_*_{activity,confidence,questions,
  override}` + `hearth_alive` + device named "Hearth". A user entity that happened
  to match (e.g. a `binary_sensor.hearth_alive` fireplace) would be excluded. Narrow;
  the device-name path is authoritative. No false-negatives on the known published set.
- **Small-data posture.** We did NOT adopt the paper's generate-everything approach;
  a dozen principled features, letting the existing importance/evidence machinery
  drop the ones that don't earn their place.
- **Refresh cadences.** `room.graph` ~daily (throttled in `build_latest_windows`);
  lead/lag cached ~6h; footprint/rhythm computed on request from the feature store.

## 8. Verification status

Pure logic is unit-tested inline against real SQLite / synthetic DataFrames:
mobility stats + switches, anchor detection + BFS + the no-bed-sensor sleep cue,
missingness (incl. the ffill-carried non-flag case), footprint labels + WoW trend,
rhythm ACF/FFT, lead/lag edge recovery, marker suggestions, and the full
forget/relink/delete cascade (predicate re-key, user link, collision guard). A
verification sub-agent sweep caught three relink bugs (wrong predicate grammar,
untouched user link, SQL-NULL in the clash guard) — all fixed here. Frontend files
esbuild-transform clean. `npm run typecheck` + the pytest suite should be run on a
non-flaky checkout (the OneDrive mount deadlocks pytest's file collection).

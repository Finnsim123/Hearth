# Implementation notes — governor/observability + blind-spot advisor

What was built directly (additive, tested), and the exact wiring/edits left for you.
New files can't break existing code; the remaining items touch large core files
(`trainer.py`, `router.py`, `scheduler.py`, frontend) so they're specified, not
applied blind.

## Shipped in this pass (new files, 18 tests passing)

**Observability + governor** (`system_observability_and_governor_design.md`):
- `domain/system/__init__.py`
- `domain/system/vitals.py` — `Vitals` snapshot, `GovernorConfig`, `heaviness_index()`,
  and the `ResourceMonitor` / `PowerMeter` / `InfluxHealth` Protocols.
- `domain/system/governor.py` — `GovernorState` (NORMAL→CRITICAL), `decide_state()`
  with hysteresis + thermal/disk hard triggers + thermal cooldown, the
  `DegradationPlan` ladder, `admit(kind, state)`, `should_yield(state)`.
- `adapters/psutil_monitor.py` — `PsutilResourceMonitor` (best-effort psutil; Pi
  thermal-zone fallback), `RaplPowerMeter` (Intel RAPL energy), `EstimatedPowerMeter`
  (CodeCarbon-style fallback). All degrade to None, never crash.
- `api/system_routes.py` — `/api/system/vitals`, `/state`, `/mode` with a `bind()` seam.
- `tests/test_governor.py` — heaviness, band thresholds, hysteresis, thermal
  ceiling + cooldown, disk floor, admission ladder.

**Blind-spot advisor** (`llm_vs_statistics_and_discovery_audit §5`):
- `domain/coverage/__init__.py`
- `domain/coverage/advisor.py` — `SensorGap`, `confused_pairs()`, `room_roles()`,
  `detect_gaps()` (confused-pair × room-coverage, weak-evidence, ghost-room), and
  `phrase_gap()` (deterministic; pass the structured gap to the LLM for warmer wording).
- `tests/test_coverage_advisor.py` — the cooking-vs-eating-in-kitchen case yields a
  "add a sensor in the kitchen" recommendation.

Run: `cd backend && PYTHONPATH=. pytest hearth/tests/test_governor.py hearth/tests/test_coverage_advisor.py`

## STATUS — wiring now done (this pass)

Backend wiring is in place and compiles; the new test suite (18) passes and the
wired modules import + run with fakes. Run the full suite on your box (it has the
sklearn/influx/apscheduler tree) to confirm against the 267.

- ✅ `domain/system/runtime.py` — shared governor state holder (api + scheduler use it).
- ✅ `main.py` — instantiates `PsutilResourceMonitor`, `system_routes.bind(monitor, repo)`,
  `app.include_router(system_routes.router)`.
- ✅ `scheduler.py` — `_governor_tick` (60 s: refresh, persist a 180-point vitals
  history to `system.vitals.history`, raise/clear the `system_heavy` buddy issue at
  HIGH/CRITICAL) + `_admit(kind)` guards on weekly training, first-train, discovery,
  and inventory-sync (inference is never gated).
- ✅ `api/system_routes.py` — `GET /api/system/vitals|state`, `POST /api/system/mode`,
  `GET /api/system/coverage` (blind-spot advisor).
- ✅ `domain/coverage/advisor.py` — `gaps_from_home(repo)` assembles confusion from
  promoted root models × room coverage.
- ✅ discovery⟂model split: `Binding.model_excluded` flag (additive, default False);
  `trainer.py` drops model-excluded columns before fitting; `clustering.py` keeps them.
  No-op until the selection step sets the flag.

Verify on your machine:
`cd backend && pytest`   (and the two new files specifically:
`pytest hearth/tests/test_governor.py hearth/tests/test_coverage_advisor.py`)

### Remaining (not done here)
- Set `model_excluded` in the selection/inventory step (LLM/heuristic: `keep=false`
  AND reliability ok → `model_excluded=True` instead of disabling). This activates the
  split.
- Per-activity room + ambient enrichment for the advisor (join named-cluster evidence
  / evidence_profile) so confused-pair advice names the room; a room/area inventory for
  ghost-room detection. Today advice is room-agnostic for confused pairs.
- `n_jobs` cap threading from `plan_for(state).n_jobs_cap` into RF/Influx concurrency,
  and `should_yield` checkpoints between trainer stages.
- Full Influx telemetry (`hearth_system` measurement) + `InfluxHealth.query_load` for a
  real influx-pressure signal (today `influx_query_load` stays 0).
- Frontend: the Vitals page (mockup in chat) from `/api/system/vitals` + the
  `system.vitals.history` setting; a "Where I'm blind" card from `/api/system/coverage`;
  a buddy `get_coverage_gaps` / `get_vitals` tool.
- LLM → cold-start prior / statistics standing-authority policy (audit §2/§4).

## (Original) wiring plan

### 1. Governor — main.py + scheduler (small)
- main.py: `from .adapters.psutil_monitor import PsutilResourceMonitor; from .api import
  system_routes; system_routes.bind(PsutilResourceMonitor(data_path=settings.data_dir,
  influx_health=tsdb), repo); app.include_router(system_routes.router)`.
- scheduler.py: at the top of each tick call `system_routes.refresh()`, and before
  every heavy job (`train_person`, `run_discovery`, `tune`, import) guard with
  `if not governor.admit(KIND, system_routes.current_state()): skip/defer`. Pass
  `plan_for(state).n_jobs_cap` into RF/Influx concurrency. In `trainer._fit_node`,
  between stages: `if should_yield(state): raise Yield()` (clean pause).
- Alerts: on a state transition, emit through the existing notifier (same path as
  questions): `notifier.ask(...)` / a system-event variant → "running hot, training
  paused."
- Telemetry: in the sampler, `tsdb.write_features`-style write a `hearth_system`
  measurement so the Vitals page history + Grafana have data. Add `InfluxHealth`
  methods (`query_load`, `snapshot`) to `influx_store`.

### 2. Blind-spot advisor — assemble inputs + surface (small/medium)
`detect_gaps()` is pure; feed it from data you already have:
- `confusion` = promoted root model's `metrics["confusion"]`.
- `activity_room` = for each activity, the room of its top-importance binding (join
  `metrics["importance_all"]` → `binding.room`), or the cluster signature `where`.
- `activity_ambient_share` = from `metrics["evidence_profile"]` (ambient tier share),
  or 1 − direct-tier share per class.
- `bindings` = `repo.bindings()`; `referenced_rooms` = rooms any activity maps to.
Add `GET /api/system/coverage` returning the ranked gaps, render a "Where I'm blind"
card on the Sensors page, and give the buddy a `get_coverage_gaps` read tool so it can
answer "is it worth adding anything?" Gate on validated models (don't advise from a
provisional one).

### 3. Discovery ⟂ model feature-space split (the deepest fix — medium, do carefully)
Goal: the model trains on the LLM-relevance-filtered subset, but **discovery clusters
on a junk-filtered superset**, so dropped-but-reliable sensors can still surface new
activities. Recommended minimal change (kept reversible):
1. Schema: add `model_excluded: bool = False` to the selection/binding the spec
   applies. Set it when the LLM/heuristic says `keep=false` BUT reliability is "ok"
   (i.e. not junk) — instead of fully dropping such a sensor, keep its binding enabled
   and build its features, just flag it.
2. `features/pipeline.py`: build features for `enabled` bindings as today (so excluded-
   but-reliable columns exist in the stored matrix).
3. `training/trainer.py`: after `read_features`, drop columns belonging to
   `model_excluded` bindings before fitting (one `feats.drop(columns=…)`), mirroring
   the existing `drop_foreign_personal` call.
4. `discovery/clustering.py`: do NOT drop them — discovery already uses all non-constant
   columns, so leaving them in is the whole point. (Optionally weight them lower.)
This makes "the predictor sees what's relevant; discovery sees the whole home,"
directly serving "truly understand the home." Junk (T0/flatline/mostly-missing) is
still dropped for both via the existing reliability path.

### 4. LLM → cold-start prior, statistics → standing authority (medium, policy)
Per the audit: keep the LLM for selection/typing at cold start + composites +
phrasing, but once confirmed labels cross a threshold, re-derive kept-set and feature
value from data (RF importance + permutation + mutual information; CV-tuned windows).
The revision loop already does part of this — extend it to periodically recompute the
spec from data rather than re-asking the LLM, and expose the authority on the Models
page ("selection: data, N labels" vs "LLM-prior, cold start"). Add a cheap deterministic
pre-filter (variance/flatline/metadata-tier) before the LLM so it only rates ambiguous
entities.

### 5. Frontend
- Vitals page: the mockup in chat is the target — heaviness gauge, KPI cards, live job
  panel, energy/cost, events log, mode control. Data from `/api/system/vitals` (+ a
  `/vitals/history` once `hearth_system` telemetry lands) and `/ws/system`.
- "Where I'm blind" card on Sensors from `/api/system/coverage`.

## Notes
- The earlier ML audit's eval-bias finding is already half-mitigated: epsilon-explore
  asks (`active.py`) produce unbiased gold labels. To finish it, tag those and compute
  the headline accuracy + promotion gate on the epsilon subset only.
- On review, the `phrasing.py` (0.15) vs `active.py` (0.25) margins are *not* a bug:
  asked-due-to-low-confidence windows with a wide top-2 gap are legitimately phrased
  confidently. Disregard that point from the audit.

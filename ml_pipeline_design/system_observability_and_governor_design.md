# Hearth — System Self-Awareness: Observability + Resource Governor

> **Superseded premise (June 2026):** this proposal assumes "the Grafana you
> already ship/bundle." Grafana has since been **removed** from the stack — all
> dashboards/visuals are custom and live in the Hearth app. Read every "delegate
> to / deep-link to Grafana" below as "render natively in the app." The
> OpenTelemetry/Prometheus `/metrics` endpoint idea can stand on its own for
> power users who bring their own Grafana, but Hearth no longer bundles one.

Status: design proposal, June 2026. A subsystem that lets Hearth **sense its own
load** (compute, power, thermal, I/O, Influx) and **regulate it** — throttle, defer,
or safe-stop under pressure — alerting through the *same* Home-Assistant feedback
channel it already uses for questions, and feeding the buddy so it can reason about
the system's own health.

Two layers, deliberately separated (the classic split):

- **Observability** — *measure* what's happening (telemetry). Read-only.
- **Governor (homeostasis)** — *act* on it (backpressure, degradation, brakes). The
  control loop.

Design constraints inherited from Hearth: **local-first** (no cloud telemetry),
**hexagonal** (new ports + adapters, no I/O in the domain), **reuse** what exists
(scheduler, notifier, InfluxDB, the bundled Grafana, `ModelRecord`, the buddy).

---

## 1. State of the art, and what actually fits a homelab box

The industry-standard observability stack is **OpenTelemetry → Prometheus → Grafana**
for metrics, and an **experiment tracker** (MLflow / Weights & Biases / TensorBoard)
for training insight. For energy specifically the references are **RAPL** (Intel
power-capping counters), **Scaphandre** (RAPL→Prometheus), **CodeCarbon** (per-job
energy + carbon estimate, Python), and **Kepler** (eBPF power, Kubernetes — overkill
here).

What fits Hearth's local-first, single-box ethos:

| Need | SOTA tool | Hearth-fit choice |
|---|---|---|
| Host metrics (CPU, mem, temp, IO) | node_exporter, psutil | **psutil in-process** (zero extra services) + cgroup v2 reads |
| Container metrics | cAdvisor | **cgroup v2** (`cpu.stat`, `memory.current`) — already in Docker |
| Metric store | Prometheus | **reuse InfluxDB** — write a `hearth_system` measurement (no new DB) |
| Dashboards | Grafana | **Grafana you already ship** + a curated native Settings page |
| Power / energy | RAPL, Scaphandre, CodeCarbon | **RAPL if present → Pi `vcgencmd` → smart-plug via HA → modelled estimate** (ladder) |
| Training insight | MLflow / W&B | **extend `ModelRecord`** with per-run resource cost + per-stage spans (local, no new service) |
| Scrape interop | OpenTelemetry / Prometheus | **expose `/metrics`** so power users can point their own Prometheus at it |

The principle: **don't add a second database or a cloud account.** psutil + cgroups
+ Influx + Grafana already on the box covers 95% of it; expose a Prometheus endpoint
for the 5% who want their own stack.

**Power, the elegant dogfood:** the cleanest ground-truth on a smart home is a
**smart plug the server is plugged into, read back through Home Assistant** — Hearth
already talks to HA, so it can read its *own* wattage as just another sensor. Offer a
"link my server's power plug" option; fall back to RAPL, then to a modelled estimate
(CodeCarbon-style: `power ≈ cpu_util × cpu_TDP + ram_GB × k`).

---

## 2. What to measure — the metric catalog

Grouped by layer. Everything is sampled on a light cadence (host every ~10 s,
job-level per stage) and written to `hearth_system` in Influx with a short retention.

### Host / hardware
- **CPU**: total %, per-core %, load average (1/5/15), current frequency, **throttle
  state** (Intel thermal pressure; Pi `get_throttled` bitmask).
- **Thermal**: CPU package temperature (°C) — the single most important safety signal
  on a fanless Pi/NUC.
- **Memory**: system used %, swap used (swapping = the classic "about to fall over"),
  Hearth process RSS.
- **Disk**: free space (Influx grows unbounded without retention), IO throughput,
  IO wait.
- **Power**: instantaneous watts, cumulative Wh/kWh (from RAPL / plug / estimate),
  fan RPM if exposed.

### Container / process (cgroup v2)
- Per-container CPU-seconds, memory.current vs memory.max, throttled time
  (`cpu.stat` `nr_throttled`), OOM events.

### Application — job-level (the "training insight" the user asked for)
Per scheduler job (train / discovery / tune / feature-build / import / inference):
- **stage** (feature-build → label-merge → tune → fit → evaluate → calibrate), with
  a progress fraction + **ETA**;
- wall-clock, **CPU-seconds consumed**, peak RSS, **energy (Wh)** attributed to the
  run, #windows, #features;
- outcome (promoted? metrics delta) — joins to `ModelRecord`.

### InfluxDB awareness
- query rate, **bytes scanned / returned per query**, query latency p50/p95, **slow-
  query log**, write throughput, **series cardinality** (the silent Influx killer),
  bucket size on disk, dropped-points/backpressure events.

### Derived / SOTA
- **Heaviness index** — one normalized 0–1 number (§4) = "how heavy am I right now."
- **Compute budget**: CPU-hours/day and **kWh/day** used vs a configured cap.
- **Energy economics**: kWh today/week, **cost** (user sets €/kWh), **carbon**
  (kWh × grid intensity — manual offline factor, or optional electricityMaps/WattTime
  if the user opts in), **energy per 1 000 predictions**, **energy per training run**.
- **Efficiency / "is it worth it"**: accuracy gain per training-kWh — surfaces when a
  weekly retrain is burning energy for no measurable improvement (ties to the
  promotion gate: if the gate keeps rejecting, stop spending energy retraining).

---

## 3. API surface

REST under `/api/system/*` (consistent with the existing router), plus a WebSocket
for the live page and a Prometheus scrape endpoint.

```
GET  /api/system/vitals              # current snapshot: cpu, mem, temp, watts,
                                     # heaviness index, governor state, uptime
GET  /api/system/vitals/history      # ?metric=cpu|temp|watts|... &range=24h  (from Influx)
GET  /api/system/jobs                # running + queued jobs: stage, progress, ETA
GET  /api/system/jobs/{run_id}       # per-run detail: stages, cpu-s, peak RSS, Wh, delta
GET  /api/system/budget              # compute/energy caps vs usage today/week
GET  /api/system/influx              # query rate, bytes, latency, cardinality, disk
GET  /api/system/events              # governor trips, throttles, thermal pauses (log)
POST /api/system/mode                # {mode: normal|eco|throttled|safe} manual override
POST /api/system/budget              # set caps (cpu_hours_day, kwh_day, max_temp_c, …)
WS   /ws/system                      # live stream of vitals for the Settings gauges
GET  /metrics                        # Prometheus exposition (opt-in, for power users)
```

`POST /api/system/mode safe` is the **kill switch**: pause all ML, keep serving the
last promoted model + rules — predictions never stop, only *learning* does.

---

## 4. The Governor — brakes & emergency procedures

This is homeostasis. It reuses Hearth's own idioms: **states with hysteresis** (like
the prediction smoother) and the **notifier** (like the question loop).

### Heaviness index → states
A single normalized score, the worst-case headroom across resources:

```
heaviness = max(
    cpu_util,
    mem_used_pct,
    (temp - temp_warn) / (temp_max - temp_warn),   # thermal, clamped ≥0
    swap_used_pct * 1.5,                            # swapping weighted heavier
    disk_used_pct,
    influx_query_load,
)
```

Mapped to states with **hysteresis** (enter high at 0.85, leave at 0.70 — no
flapping, exactly like the smoother's k-window rule):

| State | Trigger | Behaviour |
|---|---|---|
| **NORMAL** | heaviness < 0.7 | full speed |
| **ELEVATED** | 0.7–0.85 | defer *optional* work (discovery, tuning); inference untouched |
| **HIGH** | 0.85–0.95 | reduce parallelism (`n_jobs` cap), bigger scheduler intervals, chunk Influx reads, pause training |
| **CRITICAL** | >0.95 **or** temp ≥ max **or** disk < 1 GB | safe mode: inference-only on the last model; halt ingest backfill; alert |

### The graceful-degradation ladder (what to shed, in order)
Shed the least user-visible work first; **never** take down live predictions:

1. **Defer** non-urgent jobs — discovery, hyper-parameter tuning, history import.
2. **De-parallelise** — drop RF/Influx `n_jobs`/concurrency so the box stays
   responsive (a slower train beats an unresponsive home).
3. **Throttle** — widen scheduler intervals; shrink batch sizes; **chunk Influx reads
   into bounded time-slices** (you already do bounded reads — make the slice adaptive
   to load).
4. **Pause training** — serve the last promoted model; queue the retrain for a calmer
   window.
5. **Cap ingest** — token-bucket rate-limit the importer; if a flood arrives, accept
   it slowly and notify, never OOM.

### The specific scenarios the user named
- **"User suddenly feeds a lot of data."** The importer runs under a **token-bucket
  rate limiter** + bounded-slice reads, so a 2-year backfill becomes a *throttled
  background drip*, not a thundering herd. The governor watches heaviness; if it
  climbs, the import slows further and the buddy says *"big import detected — I'm
  spreading it over tonight so the home stays responsive."*
- **"The system overheats."** Thermal is a first-class trigger: temp ≥ `temp_max` →
  jump to CRITICAL immediately, pause heavy jobs, drop to inference-only, alert, and
  **require a cooldown below `temp_warn`** before resuming (hysteresis). On a Pi,
  read `get_throttled` to catch hardware throttling even before the temp ceiling.
- **"Influx is pulling a lot."** A per-query **cost budget**: estimate bytes/cardinality
  before running; if a query would scan too much, chunk it or refuse + downgrade
  (coarser range). Monitor cardinality and bucket disk; near-full disk → enforce
  retention, stop writes, alert *before* corruption.

### Cooperative cancellation
APScheduler jobs are coarse, so instrument the trainer's **stage boundaries**
(feature-build / tune / fit / evaluate) with a `governor.should_yield()` check —
between stages a job can pause/abort cleanly instead of being killed mid-fit.

### Alerting — reuse the feedback channel
Every governor transition emits through the **existing notifier** (HA event + MQTT +
push), the same path as questions:
> *"Hearth is running hot (78 °C) — I've paused training and will resume when it
> cools. Predictions are still live."*

So in Home Assistant these become entities/events you can automate on (e.g. turn on
a fan, or notify your phone) — the system's health is just more signal in the home.

### Quiet-compute hours
A scheduling lever (mirrors the existing quiet-hours for questions): only run heavy
training when the home is **away or asleep** and/or when **electricity is cheap**
(if a tariff sensor exists in HA). Less heat when you're home, lower cost, same model.

---

## 5. Visuals — Settings → System (Vitals)

A new Settings page, in the DESIGN.md language (ember on slate, one accent, calm). Top
to bottom:

1. **Status hero** — a single **heaviness gauge** (0–1) + a state badge
   (Normal/Elevated/High/Critical), the at-a-glance "how heavy am I." Colour is the
   *only* place the accent escalates to amber/red.
2. **KPI cards** — CPU %, temperature, RAM, **power (W now / kWh today)**, disk free,
   Influx size. (Metric-card style.)
3. **"Happening now"** — the live job: name, stage, progress bar, ETA, cores in use,
   energy so far. This is the training-insight panel.
4. **Trends (24 h / 7 d)** — CPU/temp/power line charts; a **training-runs timeline**
   (each run a bar: duration × energy); inference latency.
5. **Energy & cost** — kWh + € today/week, carbon estimate, **energy per 1 000
   predictions**, **energy per training run**, and the efficiency line *"accuracy
   gained per kWh"*.
6. **Events log** — governor trips, thermal pauses, throttle events, with timestamps
   and plain-language reasons.
7. **Controls** — mode selector (Normal / Eco / Throttled / **Safe**), budget caps
   (CPU-hours/day, kWh/day, max temp), quiet-compute hours, "link server power plug,"
   and the Prometheus/Grafana toggle for power users.

Live data over the `/ws/system` socket; history from Influx; the heavy/exploratory
dashboards delegate to the **Grafana you already bundle** (deep-link from the page).

---

## 6. Buddy integration — a system-aware assistant

Feed the vitals snapshot, recent events, and budget into the **buddy's** context, and
give it two read tools (`get_vitals`, `get_jobs`) and one *guarded* action
(`set_mode`, requiring user confirmation). Now the buddy can:

- **Answer**: *"Why was the home slow at 8pm?"* → "A history import spiked CPU to
  95 °C-adjacent; I throttled it." / *"How much energy did Hearth use this week?"* /
  *"Is it safe to import 2 years now?"* → checks headroom, recommends tonight.
- **Warn proactively**: *"Disk is 92% full — Influx will start dropping data in ~3
  days; want me to tighten retention?"*
- **Explain governor actions** in plain language and **recommend** (schedule the big
  job for off-peak/away hours; lower retrain frequency because the gate keeps
  rejecting and it's wasting energy).

This turns the buddy from a setup helper into an **operator** that understands the
machine it runs on.

---

## 7. Architecture — ports, adapters, control loop

Hexagonal, consistent with the codebase:

- **Ports (domain):** `ResourceMonitor` (host/process/cgroup reads), `PowerMeter`
  (watts/Wh), `InfluxHealth` (query/cardinality/disk stats).
- **Adapters:** `psutil_monitor`, `cgroup_monitor`, `rapl_power` / `pi_power` /
  `ha_plug_power` / `estimated_power`, `influx_health`.
- **Domain service:** `Governor` — pure function of the latest vitals + config →
  desired `mode` + actions; the scheduler asks it `admit(job)?` before running any
  heavy job, and trainer stages call `should_yield()`.
- **Telemetry sink:** a sampler task writes `hearth_system` to Influx; the WebSocket
  fans out the latest snapshot.
- **Reuse:** `notifier` for alerts, `scheduler` as the enforcement point (job
  admission), `ModelRecord` extended with per-run cost, Grafana for deep dashboards.

The control loop, once a cycle: **sample → compute heaviness → governor decides mode
(hysteresis) → scheduler/admission + degradation ladder enforce it → notifier +
buddy report → write telemetry.**

---

## 8. Build order (each phase shippable)

1. **Sense** — `ResourceMonitor` (psutil + cgroup), `/api/system/vitals`, write
   `hearth_system` to Influx, the Vitals page hero + KPI cards. (Observability only,
   zero behaviour change.)
2. **Insight** — per-job stage instrumentation + ETA, the "happening now" panel,
   `ModelRecord` resource cost, trends from Influx.
3. **Govern** — heaviness index, states with hysteresis, the degradation ladder,
   thermal trigger, `should_yield()` checkpoints, notifier alerts, the mode/safe
   switch.
4. **Energy** — power ladder (RAPL/Pi/plug/estimate), kWh/cost/carbon, efficiency
   metrics, quiet-compute hours.
5. **Influx awareness** — query cost budget, cardinality/disk monitor, adaptive
   chunking, importer token-bucket.
6. **Buddy + Prometheus** — wire vitals into the buddy with the guarded action;
   expose `/metrics` and ship a Grafana dashboard.

---

## 9. Bottom line

Build it as **two clean layers on the ports you already have**: a read-only sensing
layer (psutil + cgroups + Influx + the Grafana you ship — no new database, no cloud),
and a governor that turns one **heaviness index** into **hysteresis-gated modes**
driving a **graceful-degradation ladder** that always protects live inference and
never lets a data flood or a thermal spike take the box down. Surface it as a calm
Vitals page (one gauge, KPI cards, a live job panel, energy/cost/carbon, an events
log, and a safe-mode switch), alert through the **same HA feedback channel** as
questions so health becomes automatable signal in the home, and feed it all into the
**buddy** so it can explain, warn, and — with your confirmation — act. The SOTA here
isn't a heavyweight MLOps stack; it's OpenTelemetry-shaped instrumentation kept local,
plus old, proven reliability patterns (backpressure, circuit breakers, load shedding,
hysteresis) applied to a home box that has to stay cool, quiet, and responsive.

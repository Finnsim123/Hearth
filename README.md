<p align="center">
  <img src="brand/logo.svg" width="88" alt="Hearth logo — house outline with a glowing ember" />
</p>

<h1 align="center">hearth</h1>

<p align="center"><strong>Self-hosted human activity recognition for Home Assistant.</strong></p>

<p align="center">
Hearth learns what's happening in your home — sleeping, cooking, watching a movie,
working — from the sensors you already have, asks you when it isn't sure, and gets
smarter every week. Predictions flow back into Home Assistant as entities you can
automate on.
</p>

```
HA sensors ──► Hearth pipeline ──► features ──► model ──► predictions ──► HA entities
                                                  ▲                          │
                                                  └── your feedback ◄────────┘
```

- **Local-first.** Everything runs on your own box. Your accounts, your data,
  no cloud anywhere.
- **Your activities, not ours.** Define your own taxonomy (cooking, gaming,
  chilling…) in the UI. Hearth's clustering proposes patterns it found in your
  data — you name them, Hearth learns them.
- **Glass-box ML.** The UI shows accuracy, per-class AUC/F1, confusion matrices,
  SHAP explanations for every prediction, model-version trends, and what each
  prediction rests on — and a model stays marked *provisional* until enough of
  your own confirmations have validated it.
- **AI sets up the model, you keep control.** Point an optional LLM key at your
  home and it reads your sensors *once* — selects what's worth using, classifies
  each signal, designs the features (with reasons you can read), drafts rules,
  and flags sensors that look broken. New sensors are never pulled in silently:
  Hearth asks before analysing or retraining. After the first model trains,
  predictions are 100% local and the key is dead weight.
- **Shippable stack.** One `docker compose up` brings up the Hearth backend +
  web UI, optionally bundled InfluxDB, Grafana and Mosquitto.
- **Calm by design.** Warm ember on cool slate, one accent that always means
  something, dark/light/system themes — the full design language lives in
  [`docs/DESIGN.md`](docs/DESIGN.md).

## Status

🔥 **Working product, running live.** All three pillars are implemented and
shipping: data pipeline (HA WebSocket ingest + history import + a feature engine
that runs role recipes *and* an executable, validated feature spec), models
(hierarchical state→activity classifiers with honest metrics, calibration,
learned transition smoothing), and the feedback loop (notification questions via
the HA integration, Inbox, ribbon corrections, weekly retrains with promotion
gates). Plus: an **AI feature architect** (optional, BYO key) that selects
entities, assigns information tiers, designs features and flags unreliable
sensors, with a one-time cost estimate and an explicit data-sharing choice;
**detect-then-ask** approval for newly discovered sensors; a **selectable model
family** (random forest, gradient-boosted, logistic); an **abstain “unknown”
state** so automations never act on a shaky guess; a model-feedback loop that
proposes features to separate the classes a model confuses; pattern discovery
(HDBSCAN cards you name), evidence tiers, in-app updates, and CI. Remaining work
lives in [`docs/ROADMAP.md`](docs/ROADMAP.md) (hardening + packaging, and the
research bets).

## Quickstart

```bash
git clone https://github.com/Finnsim123/Hearth && cd Hearth
bash install.sh
```

Hearth includes InfluxDB. The setup wizard lets you use the bundled one (one
click, no configuration) or connect an existing instance; connect your own and
the bundled InfluxDB simply sits idle.

The installer generates secrets, builds the stack, waits for it to come up,
and prints exactly where to go:

```
  Install is complete.

  Go to:  http://192.168.1.241:8420  to set up your Hearth instance
```

The 10-step wizard walks you through everything else: create your account →
connect Home Assistant → choose existing-or-bundled InfluxDB → define your
household (per-person notification budgets) → automatic sensor inventory →
optional AI-assisted mapping (any language) → pick your activities → mint a
token for the HA integration. **Fast track:** if your InfluxDB already has
history, Hearth imports it, builds features, and trains models during setup —
predictions on the dashboard within minutes. Fresh installs record for ~3
days, then pattern cards appear for naming and the first model trains.

## Documentation

| Doc | What's in it |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design, the three pillars, all ADRs — **start here** |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Buckets, schemas, entity inventory, data-duration guidance |
| [`docs/UI_SPEC.md`](docs/UI_SPEC.md) | Every page of the web UI, wizard flow, API surface |
| [`docs/DESIGN.md`](docs/DESIGN.md) | Design language: tokens, components, icons, theming, voice |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Accounts, sessions, where every secret lives |
| [`docs/RESEARCH.md`](docs/RESEARCH.md) | Hard problems, prior art, the HEPA & LLM bets |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Phased build plan with acceptance criteria |

## Repository layout

```
hearth/
├── docs/            Architecture, research, data model, UI spec, design, security
├── docker-compose.yml
├── backend/         Python modular monolith (FastAPI + APScheduler)
│   └── hearth/
│       ├── api/         REST + WebSocket endpoints (thin)
│       ├── domain/      Pure logic: features, labeling, training, inference, discovery
│       ├── adapters/    HA, InfluxDB, MQTT, LLM, SQLite — all I/O lives here
│       ├── security.py  ALL crypto, in one reviewable place
│       └── main.py      Composition root
├── frontend/        React + TypeScript SPA (served by the backend in prod)
│   └── src/
│       ├── theme.css    Design tokens (dark / light / system)
│       ├── icons.tsx    The 47-icon set, one stroke language with the logo
│       └── onboarding/  The 10-step wizard (implemented)
├── custom_components/   HA integration (HACS layout) — config flow, activity sensors, action listener
├── brand/           Ember logo, wordmark, usage rules
└── grafana/         Optional pre-provisioned dashboards
```

## License

MIT

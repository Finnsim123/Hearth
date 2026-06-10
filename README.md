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
  SHAP explanations for every prediction, and drift over time.
- **Shippable stack.** One `docker compose up` brings up the Hearth backend +
  web UI, optionally bundled InfluxDB, Grafana and Mosquitto.
- **Calm by design.** Warm ember on cool slate, one accent that always means
  something, dark/light/system themes — the full design language lives in
  [`docs/DESIGN.md`](docs/DESIGN.md).

## Status

🚧 **Design + skeleton stage.** The architecture is fully specified in
[`docs/`](docs/); code is a stubbed skeleton that boots but predicts nothing yet
(the onboarding wizard UI is already real). Implementation order lives in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## Quickstart (target UX)

```bash
git clone https://github.com/you/hearth && cd hearth

# Already running InfluxDB somewhere? (the wizard will ask for URL + token)
bash install.sh

# No InfluxDB yet? Include the bundled one:
bash install.sh --with-influxdb
```

The installer generates secrets, builds the stack, waits for it to come up,
and prints exactly where to go:

```
  Install is complete.

  Go to:  http://192.168.1.241:8420  to set up your Hearth instance
```

The 10-step wizard walks you through everything else: create your account →
connect Home Assistant → choose existing-or-bundled InfluxDB → define your
household → automatic sensor inventory → optional AI-assisted mapping → pick
your activities → connect the HA integration. Hearth records for a few days,
patterns appear, you name them, the first model trains, predictions go live.

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

## Relation to `har-homelab`

Hearth is the generalized successor of a working single-home prototype
(`har-homelab`): Random Forest on 30-min windows, active-learning notifications,
weekly retrain. The prototype's lessons — iOS notification limits, bootstrap-label
circularity, sentinel imputation, slow-sensor lookback — are baked into the design
(see `docs/RESEARCH.md` §Lessons). Its feature extractors will be ported as the
first device-class recipes.

## License

MIT

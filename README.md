# Hearth

**Self-hosted human activity recognition for Home Assistant.**
Hearth learns what's happening in your home — sleeping, cooking, watching a movie,
working — from the sensors you already have, asks you when it isn't sure, and gets
smarter every week. Predictions flow back into Home Assistant as entities you can
automate on.

```
HA sensors ──► Hearth pipeline ──► features ──► model ──► predictions ──► HA entities
                                                  ▲                          │
                                                  └── your feedback ◄────────┘
```

- **Local-first.** Everything runs on your own box. No cloud, no accounts.
- **Your activities, not ours.** Define your own taxonomy (cooking, gaming,
  chilling…) in the UI. Hearth's clustering proposes patterns it found in your
  data — you name them, Hearth learns them.
- **Glass-box ML.** The UI shows accuracy, per-class AUC/F1, confusion matrices,
  SHAP explanations for every prediction, and drift over time.
- **Shippable stack.** One `docker compose up` brings up InfluxDB, the Hearth
  backend + web UI, and (optionally) Grafana and Mosquitto.

## Status

🚧 **Design + skeleton stage.** The architecture is fully specified in
[`docs/`](docs/); code is a stubbed skeleton that boots but predicts nothing yet.
Implementation order lives in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Quickstart (target UX)

```bash
git clone https://github.com/you/hearth && cd hearth
cp .env.example .env                        # set HEARTH_SECRET, done

# Already running InfluxDB somewhere? (the wizard will ask for URL + token)
docker compose up -d

# No InfluxDB yet? Include the bundled one:
docker compose --profile influxdb up -d

open http://<host>:8420   # create your admin account, wizard takes it from here
```

The wizard walks you through: connect Home Assistant (URL + long-lived token) →
pick sensors (auto-suggested by device class) → assign rooms & people → choose a
starter activity set → Hearth records for a few days → review discovered patterns
→ first training run → predictions go live.

## Repository layout

```
hearth/
├── docs/            Architecture, research, data model, roadmap, UI spec
├── docker-compose.yml
├── backend/         Python modular monolith (FastAPI + APScheduler)
│   └── hearth/
│       ├── api/         REST + WebSocket endpoints (thin)
│       ├── domain/      Pure logic: features, labeling, training, inference, discovery
│       ├── adapters/    HA, InfluxDB, MQTT, SQLite — all I/O lives here
│       └── main.py      Composition root
├── frontend/        React + TypeScript SPA (served by the backend in prod)
├── integration/     Thin HA custom integration (HACS) — host+token config flow
└── grafana/         Optional pre-provisioned dashboards
```

Start with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Relation to `har-homelab`

Hearth is the generalized successor of a working single-home prototype
(`har-homelab`): Random Forest on 30-min windows, active-learning notifications,
weekly retrain. The prototype's lessons — iOS notification limits, bootstrap-label
circularity, sentinel imputation, slow-sensor lookback — are baked into the design
(see `docs/RESEARCH.md` §Lessons). Its feature extractors will be ported as the
first device-class recipes.

## License

MIT

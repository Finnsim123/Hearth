<p align="center">
  <img src="brand/logo.svg" width="96" alt="Hearth logo — house outline with a glowing ember" />
</p>

<h1 align="center">hearth</h1>

<p align="center"><strong>Self-hosted human-activity recognition for Home Assistant.</strong></p>

<p align="center">
Hearth learns what's happening in your home — sleeping, cooking, watching a movie,
working — from the sensors you already have, asks you when it isn't sure, and gets
smarter every week. Predictions flow back into Home Assistant as entities you can
automate on. Everything runs on your own hardware.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-F59E0B?labelColor=161A21" alt="MIT license" />
  <img src="https://img.shields.io/badge/self--hosted-local--first-34D399?labelColor=161A21" alt="Local-first" />
  <img src="https://img.shields.io/badge/Home%20Assistant-integration-60A5FA?labelColor=161A21" alt="Home Assistant integration" />
  <img src="https://img.shields.io/badge/AI-optional%2C%20BYO%20key-9AA3B2?labelColor=161A21" alt="Optional AI assistant" />
</p>

<p align="center">
  <a href="#-quickstart">Quickstart</a> ·
  <a href="#-the-setup-wizard">Setup</a> ·
  <a href="#-living-with-hearth">The app</a> ·
  <a href="#-the-ai-assistant-optional">AI assistant</a> ·
  <a href="#-privacy--control">Privacy</a> ·
  <a href="#-documentation">Docs</a>
</p>

```
HA sensors ──► Hearth pipeline ──► features ──► model ──► predictions ──► HA entities
                                                  ▲                          │
                                                  └────────── your feedback ◄┘
```

---

## 🔥 Why Hearth

- **🏠 Local-first.** Everything runs on your own box. Your accounts, your data, no
  cloud anywhere, no account to sign up for.
- **🗣️ Your activities, not ours.** Define your own taxonomy (cooking, gaming,
  chilling…) in the UI. Hearth clusters patterns it finds in your data and you name
  them, in a tap.
- **🔍 Glass-box ML.** The UI shows accuracy with confidence intervals, per-class
  F1 and AUC, confusion matrices, the sensors behind every prediction, and how the
  model trends across versions. A model stays marked *provisional* until enough of
  your own confirmations have validated it.
- **🧠 AI sets up, you stay in control.** An optional language-model assistant reads
  your sensors *once* — it picks what's worth using, classifies each signal, designs
  the features (with reasons you can read), and flags sensors that look broken. New
  sensors are never pulled in silently. After the first model trains, predictions are
  100% local and the key is dead weight.
- **📦 Shippable stack.** One command brings up the Hearth backend, web UI and a
  bundled InfluxDB, with optional Grafana and Mosquitto.
- **🕯️ Calm by design.** Warm ember on cool slate, one accent that always means
  something, dark/light/system themes. The full design language lives in
  [`docs/DESIGN.md`](docs/DESIGN.md).

---

## 📋 Requirements

| Need | Why |
|---|---|
| **Docker + Docker Compose** | Runs the whole stack. (`curl -fsSL https://get.docker.com \| sh`) |
| **Home Assistant** | Source of sensor data and where predictions land. You'll need its URL and a long-lived access token. |
| **InfluxDB** *(optional)* | Where time-series data lives. Hearth bundles one; or point it at your existing instance in the wizard. |
| **An LLM API key** *(optional)* | OpenRouter or any OpenAI-compatible endpoint. Used only at setup to tailor sensor mapping and features. Heuristics cover everything without it. |

A typical homelab box (a NAS, a mini-PC, a Pi 4/5) is plenty. No GPU.

---

## 🚀 Quickstart

```bash
git clone https://github.com/Finnsim123/Hearth && cd Hearth
bash install.sh
```

The installer generates secrets, builds the stack, waits for it to come up, and
prints exactly where to go:

```
  Install is complete.

  Go to:  http://192.168.1.241:8420  to set up your Hearth instance
```

That address is your own machine's LAN IP. Hearth listens on every interface, so
`http://localhost:8420` works from the box itself too. Everything else happens in
the browser.

---

## 🧭 The setup wizard

A ten-step, resumable wizard walks you through everything. Each step explains itself
in plain language, and you can close the tab and pick up where you left off.

1. **Create your account** — first boot has no users; you set the admin login.
2. **Connect Home Assistant** — URL + token, with a live connection check.
3. **Time-series database** — use the InfluxDB bundled with Hearth (one click, no
   configuration) or connect your own. Connect your own and the bundled one sits idle.
4. **MQTT** *(optional)* — reuse Home Assistant's broker or skip.
5. **Household** — add the people Hearth predicts for, each with optional phone
   notifications, a daily question budget and quiet hours.
6. **Sensor inventory** — Hearth scans every entity and, where history exists,
   computes per-sensor statistics. Downloadable so you can see exactly what's used.
7. **AI assist** *(optional)* — paste a key to let the assistant design your setup,
   with an explicit data-sharing choice and a one-time cost estimate, or skip and use
   the built-in heuristics.
8. **Activities** — pick a starter set (sleeping/away/home, plus cooking/movie/…) or
   define your own. Fully editable later.
9. **Connect output to Home Assistant** — one-click links install the Hearth
   integration and add it in HA; paste the token shown here.
10. **Done** — recording starts.

**Fast track:** if your InfluxDB already holds history, Hearth imports it, builds
features and trains a first model during setup, so predictions appear within minutes.
A fresh install records for about three days, then surfaces pattern cards to name and
trains its first model.

---

## 🛋️ Living with Hearth

After setup, the UI is insight, settings and a feedback loop:

| Page | What it's for |
|---|---|
| **Dashboard** | What Hearth thinks each person is doing right now, the day's activity ribbon, and anything that needs you. |
| **Inbox** | The questions Hearth asks when it's unsure. One tap to answer; bulk-label a time range. |
| **Activities** | Your activity taxonomy and the labeling rules behind it. |
| **Patterns** | Recurring routines Hearth discovered but can't name yet. Name one and it labels weeks of history at once. |
| **Models** | The glass box: honest metrics, confusion matrix, feature importances, version trends, and Train now. |
| **Sensors** | Every bound sensor, its role and reliability, newly discovered sensors waiting for approval, and the AI's feature design. |
| **Settings** | Connections, the household, model and AI levers, themes, accounts, and updates. |
| **Methodology** | A plain-language, personalised walkthrough of how Hearth turns your sensors into "what you're doing". |

### Getting predictions out

**Home Assistant (recommended).** The integration creates one device per person
with `sensor.hearth_<person>_activity` (state = the predicted activity, or `unknown`
when Hearth isn't confident enough to commit). For instant automations it also fires
a `hearth_activity_changed` event the moment a state flips, so you can dim the lights
*as* the movie starts, with no polling lag. Tapping ✓/✗ on a notification feeds the
answer straight back into training. This path needs no MQTT broker.

**Other hubs, or broker-based setups (MQTT).** Configure an MQTT broker and Hearth
publishes the same per-person activity and confidence as retained Home-Assistant
discovery entities, so any hub that speaks HA-style MQTT discovery, Homey, Node-RED,
openHAB, picks them up automatically. Use this when you're not on Home Assistant or
prefer broker wiring; if you use the HA integration, leave MQTT off.

---

## 🧠 The AI assistant (optional)

Hearth works fully without an LLM — heuristics map sensors to roles and a fixed set
of feature recipes drives the model. Add a key and the assistant becomes a one-time
**feature architect**:

- selects which entities carry real activity signal and skips the noise,
- classifies each into an information tier (event gate, measurement, counter…),
- designs an executable feature spec from a safe, vetted transform set (it never runs
  arbitrary code),
- proposes cross-sensor features ("sofa + TV + low light = movie"),
- flags sensors that look unreliable (stuck, flatlined, mostly missing),
- and drafts your activity taxonomy and starter rules.

You see a **cost estimate before any run**, choose **whether the assistant may see
aggregate sensor statistics** (a clear yes/no, default off), and approve every
proposal. When you add a new sensor later, Hearth asks before analysing or retraining
— a sensor you plug in to test never silently spends tokens or changes the model.

The key is used only at setup and re-analysis. Predictions never call it.

---

## 🔒 Privacy & control

- **Nothing leaves your hardware.** No cloud, no telemetry, no account.
- **The LLM, if you enable it, sees metadata and aggregate statistics only** — never
  your raw sensor history or a timeline of your life — and only with your explicit
  consent. You can run entirely without it.
- **Honest by default.** A model is labelled *provisional* until enough confirmed
  labels validate it, and Hearth publishes `unknown` rather than guess when it isn't
  sure, so an automation never fires on a shaky read.
- **You own the levers.** Model family (random forest, gradient-boosted, logistic),
  feature-engineering power, the commit/abstain threshold, question budgets and quiet
  hours are all editable in Settings.
- **Delete anything.** Sensors, labels, models — and a factory reset (`bash
  install.sh --reset`) returns you to the wizard without touching your time-series data.

---

## 🔄 Updating

Hearth updates itself from within the app (Settings → System), or by hand:

```bash
git pull && docker compose up -d --build
```

---

## 🩹 Troubleshooting

- **The URL won't load.** Check `docker compose logs -f hearth`. From the host you can
  always use `http://localhost:8420`.
- **No predictions yet.** A fresh home needs a few days of data (or import history via
  an existing InfluxDB bucket). The dashboard shows the cold-start progress.
- **A sensor shows "no data".** Make sure Home Assistant is writing it to InfluxDB and
  it isn't disabled in HA. The Sensors page flags stuck or silent sensors.
- **It can't predict "away".** Link each person to their `person.*` home/away entity
  on the Sensors page (Auto-link with AI, or by hand).

---

## 📚 Documentation

| Doc | What's in it |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design, the three pillars, the AI feature layer, all ADRs — **start here** |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Buckets, schemas, the entity catalog, data-duration guidance |
| [`docs/UI_SPEC.md`](docs/UI_SPEC.md) | Every page of the web UI, the wizard, the API surface |
| [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) | How sensors become activities, in plain language |
| [`docs/DESIGN.md`](docs/DESIGN.md) | Design language: tokens, components, icons, theming, voice |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Accounts, sessions, where every secret lives |
| [`docs/RESEARCH.md`](docs/RESEARCH.md) | Hard problems, prior art, the HEPA / world-model and LLM bets |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Phased build plan with acceptance criteria |

---

## 🛠️ Tech & development

Python backend (FastAPI + APScheduler, scikit-learn), React + TypeScript SPA, InfluxDB
for time series and SQLite for application state. Hexagonal core: pure domain logic
behind ports, all I/O in adapters.

```
backend/    Python modular monolith (api · domain · adapters · security.py)
frontend/   React + TypeScript SPA (served by the backend in prod)
custom_components/hearth/   Home Assistant integration (HACS layout)
docs/ · brand/ · grafana/
```

Run the backend tests:

```bash
cd backend && pip install -e ".[dev]" && pytest
```

Type-check / build the UI:

```bash
cd frontend && npm install && npm run typecheck && npm run build
```

---

## 👤 Author

Built and maintained by **Finn** ([@Finnsim123](https://github.com/Finnsim123)).
Contributions, issues and ideas are welcome.

## License

[MIT](LICENSE) © Finn

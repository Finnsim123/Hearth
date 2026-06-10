# Hearth — Web UI Specification

> Part of the [Hearth](../README.md) docs · design language in [DESIGN.md](DESIGN.md)

React + TypeScript SPA at `:8420`, served by the backend. REST for CRUD,
WebSocket (`/ws`) for live predictions, ingest status, and training logs.
Design language: dark-first dashboard, dense but calm; every ML number links to
an explanation of how it was computed.

## Navigation

```
⌂ Dashboard · ✉ Inbox · ⊞ Activities · ✦ Patterns · ⚙ Models · ⌁ Sensors · ⚙ Settings
```

## 1. Onboarding wizard (first boot, resumable)

**Implemented** in `frontend/src/onboarding/` (`Wizard.tsx` + `ui.tsx`); backend
calls stubbed until Phase 1/2. UX pattern, identical on every step: progress
bar (ember fill) -> "Step n of 10" label -> title -> one-paragraph explainer in
plain language -> fields with inline hints ("where do I find this") and inline
validation -> a "what's happening" callout explaining what Hearth does with the
input -> footer with Back / Continue (+ prominent Skip on optional steps).
Continue stays disabled until the step is genuinely complete (e.g. connection
test green). State persists in localStorage so a closed tab resumes mid-flow;
passwords are never persisted.

1. **Create your account** — first boot has no users: set admin email +
   password (argon2id, see docs/SECURITY.md). Additional accounts later in
   Settings → Users.
2. **Connect Home Assistant** — URL + long-lived token; live validation; shows
   discovered entity count.
3. **Time-series database** — explicit fork:
   - "I already run InfluxDB" → URL + org + token form, then a STAGED check
     (real endpoint /api/influx/inspect): instance reachable ✓ → token
     accepted ✓ → N buckets found ✓. Then a source-bucket picker for history
     import (auto-suggests the busiest bucket, shows measurements · points/24h
     · history-since per bucket, "no import" opt-out). Hearth's own three
     buckets are created automatically — never asked for.
   - "Set it up for me" → wizard checks whether the bundled container is
     running (compose `influxdb` profile). If yes: auto-connects, done. If
     not: shows the exact command (`docker compose --profile influxdb up -d`)
     with a copy button and re-checks until it appears.
4. **Connect MQTT** (optional, "use HA's broker" default).
5. **Household** — create the family: add any number of members (adults, kids,
   roommates), each with optional `person.*` entity, optional notify service,
   ask budget and quiet hours. Members without a phone (`has_device=false`,
   typical for kids) are never notified — their activity is labeled by other
   members or via the Inbox. Every enabled member gets their own model;
   members can be disabled (guest room, infant) without deleting history.
6. **Inventory** — automatic: Hearth pulls every entity (states + entity/area
   registry) and computes per-entity stats from available history (DATA_MODEL
   §4). Shows a summary ("214 entities, 31 with useful signal, 14 d of
   history") with a **Download inventory.json** button so users can inspect
   exactly what an LLM would see.
6b. **AI assist (optional)** — paste an OpenRouter / OpenAI-compatible API key
   to let an LLM pre-fill the next steps from the inventory: bindings,
   candidate composite features, taxonomy and draft rules, plus its
   clarifying questions ("two media players — which is the living-room
   TV?"). Estimated cost shown up front; "Skip — use built-in heuristics" is
   equally prominent. Key stored encrypted, reusable later for cluster-naming
   hints, removable in Settings. Once the first model is trained the LLM is
   no longer needed.
7. **Sensors** — table of HA entities with *suggested* role/room/person
   (heuristics, or LLM proposals with a reason per row — badge shows which);
   user confirms/edits; unbound entities are simply ignored. One-click
   "import history" if an existing HA→Influx bucket is detected.
8. **Activities** — starter taxonomy presets (minimal: sleeping/away/home;
   standard: +cooking/eating/movie/working; custom), or the LLM's inventory-
   tailored proposal — fully editable later.
9. **Connect output to HA** — one-click, in the user's own HA: buttons
   deep-link via `{ha_url}/_my_redirect/hacs_repository?...` (opens HACS's
   add-repo dialog) and `/_my_redirect/config_flow_start?domain=hearth`
   (opens the add-integration flow) — same endpoints the my.home-assistant.io
   buttons use, but pointed at the HA URL from step 2. Because the backend
   announces `_hearth._tcp.local.` over mDNS, the config flow arrives with
   the host pre-filled; the user only pastes the token generated here (shown
   once). Manual fallback instructions in a callout; MQTT remains the
   alternative channel.
10. **Done** — ingest starts; card explains what happens over the next days
   (record → patterns appear → name them → first training).

## 2. Dashboard (implemented in pages/Dashboard.tsx)

Answers one question in two seconds: "what does Hearth think is happening —
and can I trust it?" Two modes:

**Cold start** (no predictions yet): a single "Hearth is learning your home"
journey card — day counter, events/24 h, sensors bound, progress bar to day 7,
three milestone rows (recording → first patterns → first model) that tick off
as they fire. Paired with milestone PHONE NOTIFICATIONS (domain/milestones.py):
"recording started", "first patterns — come name them", "Hearth is live ✨".
The closing wizard screen sets the expectation: go live your life, we'll ping you.

**Steady state**:
- **Hero: avatar scene cards per person** — the member's avatar (photo or
  preset disc) badged onto the current activity's icon tile in its palette
  color: you SEE Alice on the bed, not just text. Confidence micro-bar,
  since-when, "because" SHAP strip (Phase 2).
- **Today ribbon** per person (smoothed, opacity = confidence), tap a segment
  to correct → highest-volume labeling surface. Badged "rule-based until
  trained" while inference runs on bootstrap rules (model_version rules-*).
- **Needs you** (max 3, hidden when empty): top open questions answered inline.
- **Trust strip** (Phase 2): confirmed accuracy ± CI, labels this week, next
  retrain, drift dot.
- **System pulse** footer: database / ingest / bindings dots — quiet when
  green, loud when red.

## 3. Inbox (the feedback loop surface)

- Queue of open questions (uncertain windows, random exploration asks) and
  recent low-confidence segments. Each card: time range, sensor summary,
  predicted activity + confidence, one-tap activity buttons (taxonomy-driven),
  "split window" and "skip".
- **Bulk labeler**: calendar/timeline picker → select range → assign activity
  (provenance `confirmed`, flagged `bulk`).
- Notification deep links land here (`/inbox?q=<question_id>`).

## 4. Activities

- Taxonomy editor: two-level tree, CRUD, icon/color, per-person enable,
  and a **notification phrase** per activity ("watching a movie") used by
  the dynamic question engine (labeling/phrasing.py).
- **Rules tab** per activity: list of labeling rules (predicate builder UI —
  feature dropdown, operator, threshold, AND/OR groups), priority ordering,
  origin badge (user / discovered), live "matches last 7 d: 23 windows" preview.
- Class health: confirmed-label count per class, warning when a class is
  untrainable (<min samples).

## 5. Patterns (discovery)

- Grid of cluster cards: time-of-day histogram, top distinguishing features
  ("sofa ↑ · media playing ↑ · evening"), window count, example windows.
- Actions: **Name it** (pick/create activity → windows become `discovered`
  labels + drafted rule shown for approval), **Merge** with another card,
  **Dismiss** (noise), **Snooze**.
- Embedding scatter (UMAP) with cluster coloring for the curious.

## 6. Models

- **Registry table**: version, person, algo, feature_set, trained_at, n_train,
  n_confirmed, accuracy_confirmed (with CI), accuracy_bootstrap, promoted badge.
- **Detail view**: per-class precision/recall/F1/AUC bars, confusion matrix
  heatmap, ROC curves, global SHAP importance, calibration plot, label-provenance
  breakdown, drift panel (PSI per feature, confirmed-accuracy trend).
- **Actions**: Train now (per person; streams logs over WS), Promote, Rollback,
  Compare two versions side-by-side.

## 7. Sensors

- Bindings table: entity, role, room, person, freshness dot (last point age),
  7-d sparkline, enable toggle. Add-binding flow = mini step 5 of wizard.
- Ingest health: WebSocket connection state, events/min, gap-fill runs,
  per-binding last-write.

## 8. Settings

- Connections (HA / MQTT / Influx / LLM advisor) with test buttons and token
  rotation; per-call cost log for the LLM.
- **API tokens** — mint/revoke scoped tokens for the HA integration (and
  future external consumers); plaintext shown once at mint; last-used
  timestamps; masked everywhere else (docs/SECURITY.md).
- **Users** — accounts (admin/member), create/disable, reset password, active
  sessions with revoke; optional link account ↔ household member so inbox
  answers record who confirmed.
- Asking policy: confidence threshold, ε random-ask rate, daily budget,
  quiet hours, cooldowns — per person.
- Schedules: window builder cadence, training schedule, discovery schedule.
- Data: retention, export (features/labels CSV), danger zone (reset).
- Appearance: theme — System (default) / Light / Dark (also cyclable from the
  nav); follows docs/DESIGN.md §7.
- About: version, docs links, anonymized-stats opt-in (default off).

## API surface (consumed by the SPA)

```
POST /api/auth/setup (first boot only)    POST /api/auth/login | /logout
GET  /api/auth/me                         CRUD /api/users (admin)
GET  /api/health                          GET  /api/persons
CRUD /api/connections                     CRUD /api/bindings (+ /suggest)
CRUD /api/activities  /api/rules          GET  /api/predictions?person&range
GET  /api/features/schema                 GET  /api/inbox     POST /api/inbox/{id}/answer
POST /api/labels/bulk                     GET  /api/clusters  POST /api/clusters/{id}/name
GET  /api/models      POST /api/models/train|promote|rollback
GET  /api/system/status                   POST /api/feedback/action   (HA webhook)
WS   /ws                                  (predictions, ingest, training logs)
```

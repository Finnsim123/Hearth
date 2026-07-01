# Hearth — the full HA hierarchy: integrations → devices → entities

Status: design, June 2026. Supersedes `device_aware_design.md` (devices-only). Trigger:
adding an Oral-B (a strong signal) exposed that Hearth is **entity-only** — it never
reads the device or config-entry registries, so it can't tell that an entity belongs to
an Oral-B, or that a whole cloud integration is irrelevant. Doing this *properly* means
modelling all three HA levels and letting relevance cascade.

## 1. The three levels (what HA actually exposes)
- **Integration = config entry** (`config_entries/get`): the *system* an entity comes
  from — `zwave_js`, `hue`, `mobile_app`, `met` (weather), `openweathermap`, `sun`,
  `backup`, `hacs`, `google_translate`… Has `entry_id`, `domain`, `title`, `state`.
- **Device** (`device_registry`): a physical thing — manufacturer, model, area,
  `via_device_id`, `identifiers`, `entry_type` (`"service"` = a cloud/service with no
  hardware), and `config_entries[]` linking it up to its integration(s).
- **Entity** (`entity_registry`): one signal — `entity_id`, `device_id`,
  `config_entry_id`, `platform` (the integration domain), area. Hearth already reads
  this registry; it just ignores `device_id`/`config_entry_id`.

Chain: **config entry ⊃ devices ⊃ entities**. Not everything is fully nested — some
entities have no device (helpers, integration-level entities); some devices are
services with no real hardware. The model must tolerate gaps.

## 2. Why each level earns its keep (the user's own examples)
- **Integration level — the cheapest, highest-leverage filter.** A whole integration
  can be wholesale irrelevant: an **OpenRouter / weather / calendar / backup** integration
  has *no information about the home* → skip everything under it in one decision, before
  looking at a single device. Conversely `mobile_app` (your phone: steps, battery,
  charging) or `zwave_js`/`zigbee` are keepers wholesale.
- **Device level.** Within a kept integration, separate the real end-device from
  **infrastructure**: a **Matter border router / Zigbee coordinator / bridge / hub** is
  plumbing, not a sensor of *you* → skip; the Zigbee end-devices behind it → keep.
- **Entity level (the blend you flagged).** One kept device still exposes a *mix*: a
  Zigbee plug gives real power (**keep**) plus a firmware-updater and an RSSI
  (**skip**); a phone gives steps (**keep**) and storage-free-space (**skip**). So the
  final keep/skip is still per-entity — the device just sets the default.

## 3. The model: cascading relevance with per-level override
Relevance of an entity = **the most specific decision available**, walking up until one
is set:
```
entity decision  >  device decision  >  integration decision  >  heuristic default
```
- Each node (integration / device / entity) can carry `keep | skip | unsure`, from
  `heuristic | llm | user`.
- A user (or the LLM) can override at any level: skip a whole integration in one tap;
  keep a device an integration-level skip would have dropped; flip one entity.
- **Entities stay the feature unit** — features are per-entity. The hierarchy only drives
  *relevance*, *role hints*, and *grouping*. This keeps the ML unchanged; it's a smarter
  funnel in front of it.

## 4. Heuristic cascade (no key), LLM for the `unsure` tail (with key)
Same rule as everywhere: heuristic always, LLM when a key exists.
- **Integration heuristic:** a domain allow/deny map — deny `met, openweathermap,
  accuweather, sun, moon, backup, hacs, update, system_health, cloud, google_translate,
  tts, radio_browser, …`; allow the home-sensor domains `zwave_js, zha, zigbee2mqtt,
  matter, mobile_app, esphome, hue, deconz, shelly, tasmota, …`; unknown → unsure.
- **Device heuristic:** deny infra (`coordinator|bridge|hub|gateway|router|dongle|
  border|conbee|zbdongle|slzb`) and `entry_type == "service"`; else keep.
- **Entity heuristic:** the existing `suggest_role` / `is_noise`.
- **LLM only on the `unsure` set,** and a hierarchy catalog (integration → device →
  entities) is a *much* cheaper, richer prompt than 1700 raw entity names — it can judge
  relevance and propose roles with real context ("Oral-B IO → toothbrush → keep its
  activity entity"). Privacy contract unchanged: metadata only.

## 5. Storage (consistent with how entities already work)
Entities aren't stored as a table today — they're discovered live from HA and only their
*bindings* persist. Devices/integrations follow the same pattern: **discovered live +
cached**, with per-node *decisions* persisted (keyed by id) — the hierarchy analog of
bindings. New settings: `ha.integrations`, `ha.devices` (caches), and
`ha.relevance = {integration:{id:decision}, device:{...}, entity:{...}}`.

## 6. The intuitive moment: "something new appeared"
Extend the daily `inventory_sync` to diff **integrations and devices**, not just
entities. On a genuinely new node:
1. classify it (heuristic; LLM for the `unsure` tail);
2. raise an **advisory + HA push** — *"New device: Oral-B IO toothbrush. Use it for
   predictions?"* (or *"New integration: Zigbee2MQTT — 14 devices. Include?"*) with
   **Integrate / Not now**;
3. on **Integrate** → the scoped pipeline (bind the useful entities, build features,
   retrain); on **Not now** → remember, don't re-ask.

Reuses the pending-sensors approve flow + advisory/notifier — mostly wiring, and it
*feels* smart: plug in a gadget and Hearth notices.

## 7. Related: notify through HA when the AI key runs dry (separate small commit)
Today an exhausted key (HTTP 402 / 429) only shows on the in-app buddy (`llm.status`).
Add a **one-shot HA push** when it flips to no-credit/rate-limited, so you find out
without opening the app — deduped by a flag so it fires once per outage, cleared when the
key recovers. Independent of the hierarchy work; ships on its own.

## 8. Build order (commit per commit)
1. **fix**: forecast blocklist word-boundary (done — unblocks CI).
2. **discovery adapter**: read `config_entries` + `device_registry`; add
   `device_id`/`config_entry_id` to entities. Ports + the WS adapter.
3. **domain `hierarchy.py`**: Integration/Device models, the heuristic cascade
   (`relevance_of(entity)`), decision storage. Tested, pure.
4. **API + Sensors UI**: group entities by integration → device; show/override relevance.
5. **new-node detection** in `inventory_sync` → "integrate this?" advisory + HA push →
   approve → scoped pipeline. (The headline feature.)
6. **LLM tail** for `unsure` nodes (with a key).
7. **AI-key-exhausted HA push** (§7) — standalone.

## 9. Decisions (settled with you)
1. **Blend, not either/or:** entities remain the feature unit; integration+device drive
   relevance/role/grouping, with per-entity override. ✔
2. **Always ask** before integrating a new node (push + one tap). ✔
3. **Heuristic without a key; LLM when a key exists.** ✔

# Hearth — device-aware scanning & "integrate this new device?"

Status: design / brainstorm, June 2026. Trigger: adding an Oral-B toothbrush — a
strong activity signal — surfaced two gaps. (1) Hearth is entirely **entity-based**;
it never scans the HA **device registry**, so it doesn't know an entity *belongs to an
Oral-B*. (2) There's no "a new device appeared — want me to use it?" moment. This is a
brainstorm of the implications before building.

## 1. Why device identity beats entity names for judging usefulness
An entity says *a value* (`sensor.oral_b_io_state = running`). A **device** says *what
the thing is* (manufacturer "Oral-B", model "IO Series 9"). That semantic identity is
exactly what you need to decide relevance:
- **Oral-B toothbrush** → unmistakably a person-activity signal (brushing). Keep.
- **OpenRouter / weather / calendar integration** → a cloud service, no physical
  presence in the home → almost never activity-relevant. Skip.
- **Matter/Thread border router, Zigbee coordinator, a hub/bridge** → infrastructure,
  not a sensor of *you*. Skip.
- **A Zigbee end-device** (motion, plug, contact) behind that coordinator → keep.

The entity name alone often can't tell these apart (`sensor.node_12_state`); the device
(manufacturer/model/area) usually can. So device metadata is the single biggest lever
on relevance accuracy — and it makes the **LLM call cheaper and better**: judging ~80
named devices is far more informative and far fewer tokens than 1700 cryptic entities.

## 2. The key architectural decision
**Keep entities as the feature unit; add devices as a context/relevance layer above
them.** Features are inherently per-entity (a window feature comes from one entity's
stream), so devices shouldn't replace entities. Instead a device carries a *relevance
+ role hint* that **cascades to its entities**: classify the Oral-B once → its
`running`/activity entity inherits "keep, activity-relevant". Entities with **no
device** (template sensors, helpers, cloud integrations) still go through the existing
entity-based path — device-awareness is additive, never a hard dependency.

## 3. Data model
Scan `config/device_registry/list` (Hearth already pulls `entity_registry` + areas in
the same WS session — one extra command). Store in the DB alongside entities:
```
Device: id (HA device_id), name, manufacturer, model, area, via_device_id,
        identifiers (zigbee/matter/…), entity_ids[], relevance (keep|skip|unsure),
        source (heuristic|llm|user), integrated (bool)
```
Link each entity to its `device_id` (the entity registry already has it — we just
weren't reading it). This makes the Sensors page groupable by device, and lets the
relevance verdict flow device → entities.

## 4. Relevance classification (device-level)
- **Heuristic first (no LLM):** manufacturer/model keyword map — wearables/toothbrush/
  scale/appliance → keep; "coordinator/bridge/hub/router/gateway/service" and cloud
  brands → skip; unknown → unsure. `via_device` present but device is itself a
  coordinator → infra.
- **LLM for the `unsure` tail:** a device catalog (name+manufacturer+model+area+entity
  list) is a compact, information-rich prompt — cheap, and it can also propose the role
  for each entity with far more context than the name gives.

## 5. The intuitive moment: "a new device appeared"
Extend the daily `inventory_sync` to also diff **devices**. On a genuinely new device:
1. classify it (heuristic, LLM for the tail);
2. raise an **advisory + push** through the existing channel: *"New device — Oral-B IO
   toothbrush. Use it for predictions?"* with **Integrate / Not now**;
3. on **Integrate** → run the scoped pipeline we already have for new sensors: bind its
   entities to roles, build features, retrain. On **Not now** → remember the choice so
   it isn't re-asked.

This reuses the pending-sensors approve flow, the advisory/buddy surface, and the
notifier — it's mostly wiring, and it *feels* smart: plug in a gadget, Hearth notices
and offers to learn from it.

## 6. Ripple effects (the fun ones)
- **Discovery & markers:** knowing a device is an Oral-B makes "brushing" an obvious
  micro-activity, and its on-edge a natural **transition marker** for the morning/night
  routine (ties into markers.py + the lead/lag work).
- **Onboarding triage** gets cleaner: cluster/keep at *device* granularity (80 devices
  vs 1700 entities) — a shorter, friendlier bubble cloud, and the LLM shortlist is
  cheaper and sharper.
- **Coverage advisor**: "which rooms have devices but no useful sensor" becomes crisper.
- **Reliability**: device battery / last-seen informs the reliability gate.

## 7. Caveats / implications to weigh
- Not every entity has a device (helpers, templates, cloud services) → entity path stays.
- Device names can be user-renamed to junk → fall back to model/manufacturer, then entity.
- One device → many entities of different value (a phone: steps=keep, storage=skip) →
  relevance must still be refine-able per entity; device sets the default, entity can
  override.
- Privacy contract unchanged: only metadata (names/models), never raw history, is ever
  shown to the LLM.

## 8. Build order
1. **Scan + store devices** (adapter `discover_devices`; DB table; link entities via the
   registry's `device_id`). Show devices on the Sensors page, grouped.
2. **Device relevance classifier** (heuristic + LLM tail); cascade to entities.
3. **New-device detection** in `inventory_sync` → "integrate this device?" advisory +
   push → approve → scoped pipeline. (The headline feature.)
4. Onboarding: device-granular triage (optional polish).
5. Ripple: device-seeded markers / activities.

## 9. Decisions (for you)
1. **Devices as metadata layer over entities (recommended)** vs devices as the primary
   modelling unit? Lean: metadata layer — features stay per-entity, devices drive
   relevance/role/grouping.
2. **Auto-integrate obviously-useful devices**, or always ask? Lean: always ask (a push
   with one tap) — it's the intuitive moment and keeps you in control.
3. **Heuristic-only if no AI key?** Yes — the keyword map handles the clear cases; the
   `unsure` tail just stays "review on the Sensors page" without a key.

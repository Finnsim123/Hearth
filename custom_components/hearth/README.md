# Hearth — Home Assistant integration

Connects HA to your local Hearth instance and closes the loop, no YAML, no automations:

- **One device per household member** with `sensor.hearth_<person>_activity`:
  state = predicted activity (`sleeping`, `cooking`, …), or `unknown` when Hearth
  isn't confident enough to commit (the abstain state — tune or disable it in
  Settings). Attributes = confidence, per-class probabilities, model version,
  window time. Automate on it directly:

  ```yaml
  trigger:
    - platform: state
      entity_id: sensor.hearth_alice_activity
      to: "sleeping"
  action:
    - service: light.turn_off
      target: { entity_id: light.all_lights }
  ```

  **Instant automations** (no polling lag): Hearth fires a `hearth_activity_changed`
  event on HA's bus the moment a state flips — trigger on that for sub-10-second
  response:

  ```yaml
  trigger:
    - platform: event
      event_type: hearth_activity_changed
      event_data:
        person: alice          # optional; omit to match anyone
        state: movie
  action:
    - service: light.turn_off
      target: { entity_id: light.living_room }
  ```

  Event data: `person`, `person_name`, `state` (coarse, stable — home/away/
  sleeping), `activity` (fine if a child model is live — e.g. eating), `confidence`.
  The `sensor.hearth_<person>_activity` entity carries the state for dashboards
  and state-based triggers; the event is the low-latency path.

- **Two-way controls** per member, on the same device:
  - `switch.hearth_<person>_questions` — turn off to stop Hearth asking that
    person training questions.
  - `select.hearth_<person>_override` — pin the activity to a chosen one
    (`auto` = let the model decide). While the override is fresh it's also fed
    back as a confirmed label, so the model learns the correction. Identical
    behaviour to the MQTT controls, and predictions stay 100% local.

- **Feedback forwarding**: when someone taps ✓/✗ on a Hearth training question,
  the integration catches the `mobile_app_notification_action` event and POSTs it
  to Hearth with its API token. That's the whole active-learning loop.

- **Model diagnostics** (in the devices' Diagnostics sections — the honesty,
  where you can automate on it):
  - `sensor.hearth_<person>_accuracy` — the model's honest headline accuracy (%).
    Attributes name the *basis* (real-world spot-checks vs answers-so-far),
    validation status, training phase (daily-while-learning → weekly-when-stable),
    model version and training size. Gate automations on model health, or alert
    when accuracy drops.
  - `binary_sensor.hearth_attention` (device_class `problem`, on the Hearth hub
    device) — ON while Hearth has active advisories (model leaning on the wrong
    sensors, a new device to integrate, a demoted sensor…), titles in the
    attributes. One automation — `to: "on"` → notify — and Hearth can always
    reach you.

## Install

1. HACS → custom repositories → add this repo (category: Integration) → install → restart HA.
2. Settings → Devices & services → Add integration → **Hearth**.
3. Enter your Hearth address (`http://<ip>:8420`) and an API token from
   Hearth's setup wizard (step 9) or Settings → API tokens.

If Hearth runs with host networking it also announces itself via mDNS and HA
discovers it automatically; with the default Docker bridge, enter the address by hand.

Rotated or revoked the token? HA prompts to reconnect — paste a fresh one.

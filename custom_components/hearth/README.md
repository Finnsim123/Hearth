# Hearth — Home Assistant integration

Connects HA to your local Hearth instance and closes the loop, no YAML, no automations:

- **One device per household member** with `sensor.hearth_<person>_activity`:
  state = predicted activity (`sleeping`, `cooking`, …), attributes = confidence,
  per-class probabilities, model version, window time. Automate on it directly:

  ```yaml
  trigger:
    - platform: state
      entity_id: sensor.hearth_alice_activity
      to: "sleeping"
  action:
    - service: light.turn_off
      target: { entity_id: light.all_lights }
  ```

- **Feedback forwarding**: when someone taps ✓/✗ on a Hearth training question,
  the integration catches the `mobile_app_notification_action` event and POSTs it
  to Hearth with its API token. That's the whole active-learning loop.

## Install

1. HACS → custom repositories → add this repo (category: Integration) → install → restart HA.
2. Settings → Devices & services → Add integration → **Hearth**.
3. Enter your Hearth address (`http://<ip>:8420`) and an API token from
   Hearth's setup wizard (step 9) or Settings → API tokens.

If Hearth runs with host networking it also announces itself via mDNS and HA
discovers it automatically; with the default Docker bridge, enter the address by hand.

Rotated or revoked the token? HA prompts to reconnect — paste a fresh one.

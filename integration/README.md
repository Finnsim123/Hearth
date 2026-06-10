# Hearth — Home Assistant integration

Thin HACS-distributed integration (the Frigate pattern): all intelligence
lives in the Hearth stack; this component only mirrors it into HA.

Install (target UX):
1. HACS → Integrations → add this repo → install "Hearth".
2. Settings → Devices & services → Add integration → **Hearth**.
3. Enter host (e.g. `192.168.1.50:8420`) and an API token from
   Hearth → Settings → API tokens.
4. One device per household member appears, with activity/confidence sensors,
   a manual-override select and a questions on/off switch.

Transport: Hearth's authenticated WebSocket (`/ws`), local push — no cloud,
no polling, no MQTT broker required. (MQTT discovery remains available as an
alternative output channel; don't enable both for the same household.)

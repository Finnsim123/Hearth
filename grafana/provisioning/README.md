# Grafana provisioning (optional profile)

`docker compose --profile grafana up -d` mounts this directory. Phase 5 ships:

- `datasources/influxdb.yaml` — pre-wired to the bundled InfluxDB
- `dashboards/hearth-ops.json` — heartbeats, ingest lag, prediction cadence
- `dashboards/hearth-features.json` — any feature column over time
- `dashboards/hearth-model.json` — accuracy trend, label counts by provenance

The web UI covers day-to-day use; Grafana is for power users and alerting.

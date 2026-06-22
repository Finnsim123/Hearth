"""System self-awareness — observability (vitals) + homeostasis (governor).

Two layers, deliberately separated (system_observability_and_governor_design.md):
- vitals.py   : read-only sensing — a Vitals snapshot + the heaviness index.
- governor.py : the control loop — heaviness → state (hysteresis) → what to shed.

Pure domain: no psutil/influx imports here. Adapters (adapters/psutil_monitor.py)
implement the ResourceMonitor / PowerMeter / InfluxHealth Protocols.
"""

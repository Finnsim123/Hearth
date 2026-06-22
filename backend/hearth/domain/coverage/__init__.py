"""Coverage — the blind-spot advisor (llm_vs_statistics_and_discovery_audit §5).

Statistics DETECT where the model is blind (confused activity pairs × room sensor
coverage × evidence reliance × ghost rooms); the LLM (optionally) PHRASES the
recommendation. This module is the pure-stats detector; phrasing has a
deterministic default so it works with no LLM.
"""

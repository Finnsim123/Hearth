/**
 * Logs — recent backend activity from the in-memory ring buffer (GET /api/logs).
 * Level filter, text search, auto-refresh, copy-to-clipboard. Lets the operator
 * see what Hearth is doing without docker access. Spec: docs/UI_SPEC.md §Logs.
 */
import { useEffect, useMemo, useRef, useState } from "react";

type Rec = { seq: number; ts: number; level: string; levelno: number;
             logger: string; message: string };

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"] as const;
type Level = (typeof LEVELS)[number];

const LEVEL_COLOR: Record<string, string> = {
  DEBUG: "var(--text-dim)",
  INFO: "var(--text)",
  WARNING: "var(--warn, #b8860b)",
  ERROR: "var(--danger, #d9534f)",
};

const fmtTime = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString([], { hour12: false }) +
  "." + String(Math.floor((ts % 1) * 1000)).padStart(3, "0");

export default function Logs() {
  const [records, setRecords] = useState<Rec[]>([]);
  const [level, setLevel] = useState<Level>("INFO");
  const [query, setQuery] = useState("");
  const [paused, setPaused] = useState(false);
  const [err, setErr] = useState(false);
  const cursor = useRef(0);          // highest seq seen → incremental polling
  const levelRef = useRef(level);
  levelRef.current = level;

  const load = async (reset: boolean) => {
    try {
      const since = reset ? "" : `&since_seq=${cursor.current}`;
      const r = await fetch(`/api/logs?level=${levelRef.current}&limit=2000${since}`);
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      const fresh: Rec[] = data.records ?? [];
      setErr(false);
      if (fresh.length) cursor.current = fresh[fresh.length - 1].seq;
      setRecords((prev) => (reset ? fresh : [...prev, ...fresh]).slice(-2000));
    } catch { setErr(true); }
  };

  // reload from scratch whenever the level filter changes
  useEffect(() => { cursor.current = 0; load(true); /* eslint-disable-line */ }, [level]);

  // incremental poll every 3s unless paused
  useEffect(() => {
    if (paused) return;
    const id = setInterval(() => load(false), 3000);
    return () => clearInterval(id);
  }, [paused]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return records;
    return records.filter((r) =>
      r.message.toLowerCase().includes(q) || r.logger.toLowerCase().includes(q));
  }, [records, query]);

  const copy = () => navigator.clipboard?.writeText(
    shown.map((r) => `${fmtTime(r.ts)} ${r.level} ${r.logger} ${r.message}`).join("\n"));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <h1 style={{ margin: 0 }}>Logs</h1>
        <p style={{ color: "var(--text-dim)", fontSize: 14, marginTop: 6 }}>
          Recent backend activity — the last {records.length} lines Hearth has emitted
          since it started. Held in memory only; full history lives in your container logs.
        </p>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ display: "inline-flex", border: "1px solid var(--border)",
                      borderRadius: 999, overflow: "hidden" }}>
          {LEVELS.map((l) => (
            <button key={l} onClick={() => setLevel(l)}
              style={{ border: "none", cursor: "pointer", padding: "6px 12px", fontSize: 12.5,
                       fontWeight: 600, background: level === l ? "var(--accent)" : "transparent",
                       color: level === l ? "#fff" : "var(--text-dim)" }}>
              {l}
            </button>
          ))}
        </div>
        <input value={query} onChange={(e) => setQuery(e.target.value)}
               placeholder="Filter messages…"
               style={{ flex: "1 1 200px", minWidth: 140 }} />
        <button className="btn btn-ghost" style={{ fontSize: 12.5 }}
                onClick={() => setPaused((p) => !p)}>
          {paused ? "Resume" : "Pause"}
        </button>
        <button className="btn btn-secondary" style={{ fontSize: 12.5 }} onClick={copy}>
          Copy
        </button>
      </div>

      {err && (
        <div style={{ fontSize: 13, color: "var(--danger, #d9534f)" }}>
          Couldn't reach the log endpoint — retrying.
        </div>
      )}

      <div style={{ background: "var(--surface)", border: "1px solid var(--border)",
                    borderRadius: 10, padding: "10px 12px", fontFamily:
                    "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12.5,
                    lineHeight: 1.55, maxHeight: "70vh", overflow: "auto" }}>
        {shown.length === 0 ? (
          <div style={{ color: "var(--text-dim)", padding: "20px 4px" }}>
            No log lines at this level yet.
          </div>
        ) : shown.map((r) => (
          <div key={r.seq} style={{ display: "flex", gap: 10, whiteSpace: "pre-wrap",
                                    wordBreak: "break-word", padding: "1px 0" }}>
            <span style={{ color: "var(--text-dim)", flexShrink: 0 }}>{fmtTime(r.ts)}</span>
            <span style={{ color: LEVEL_COLOR[r.level] ?? "var(--text)", flexShrink: 0,
                           fontWeight: 600, width: 64 }}>{r.level}</span>
            <span style={{ color: "var(--text-dim)", flexShrink: 0 }}>{r.logger}</span>
            <span style={{ color: LEVEL_COLOR[r.level] ?? "var(--text)" }}>{r.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

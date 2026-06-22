/**
 * System — compute/power observability + the resource governor.
 * Live vitals (CPU, memory, temp, power, heaviness), the governor state and what
 * it currently permits, a rolling sparkline, blind-spots, and a safe-mode switch.
 * Sources: /api/system/{vitals,history,coverage}; POST /api/system/mode.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Card from "../components/Card";

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };

type Vitals = {
  ts: string; cpu_pct: number; load1: number; mem_pct: number; swap_pct: number;
  temp_c: number | null; disk_free_gb: number; disk_used_pct: number;
  watts: number | null; influx_query_load: number; process_rss_mb: number;
};
type Plan = { state: number; admitted: string[]; n_jobs_cap: number | null;
              interval_multiplier: number; influx_chunk_factor: number;
              pause_training: boolean; reason: string };
type VitalsResp = { vitals: Vitals; heaviness: number; state: string; plan: Plan };
type Hist = { t: string; cpu: number; temp: number | null; mem: number; watts: number | null; h: number; state: string };
type Gap = { kind: string; severity: number; room: string | null; recommendation: string };
type Cfg = { temp_warn: number; temp_max: number; enter_elevated: number; enter_high: number;
             enter_critical: number; leave_margin: number; min_disk_gb: number; swap_weight: number };

const STATE_COLOR: Record<string, string> = {
  normal: "var(--ok, #34D399)", elevated: "var(--accent)",
  high: "#f59e0b", critical: "var(--danger)",
};
const stateColor = (s: string) => STATE_COLOR[s] || "var(--text-dim)";
const pct = (n: number) => `${Math.round(n)}%`;

export default function System() {
  const [v, setV] = useState<VitalsResp | null>(null);
  const [hist, setHist] = useState<Hist[]>([]);
  const [gaps, setGaps] = useState<Gap[]>([]);
  const [busy, setBusy] = useState(false);
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [defaults, setDefaults] = useState<Cfg | null>(null);
  const [advOpen, setAdvOpen] = useState(false);
  const [cfgMsg, setCfgMsg] = useState("");
  const timer = useRef<number>();

  const loadSlow = useCallback(() => {
    fetch("/api/system/history").then(j).then((d) => setHist(d.history || [])).catch(() => {});
    fetch("/api/system/coverage").then(j).then((d) => setGaps(d.gaps || [])).catch(() => {});
    fetch("/api/system/config").then(j).then((d) => { setCfg(d.config); setDefaults(d.defaults); }).catch(() => {});
  }, []);

  const saveCfg = () => {
    if (!cfg) return;
    setBusy(true); setCfgMsg("");
    fetch("/api/system/config", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg) })
      .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || "invalid"); return r.json(); })
      .then((d) => { setCfg(d.config); setCfgMsg("Saved — applies on the next check."); })
      .catch((e) => setCfgMsg(String(e.message || e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => {
    let alive = true;
    const tick = () => {
      fetch("/api/system/vitals").then(j).then((d) => { if (alive) setV(d); }).catch(() => {})
        .finally(() => { timer.current = window.setTimeout(tick, 5000); });
    };
    tick(); loadSlow();
    return () => { alive = false; window.clearTimeout(timer.current); };
  }, [loadSlow]);

  const setMode = (mode: "safe" | "normal") => {
    setBusy(true);
    fetch("/api/system/mode", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }) }).then(j)
      .then(() => fetch("/api/system/vitals").then(j).then(setV))
      .catch(() => {}).finally(() => setBusy(false));
  };

  if (!v) return <p style={{ color: "var(--text-dim)" }}>Loading…</p>;
  const vit = v.vitals;
  const safe = v.state === "critical";

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 18, maxWidth: 820 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>System</h2>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 7, padding: "3px 11px",
          borderRadius: 999, fontSize: 13, fontWeight: 600,
          background: `color-mix(in srgb, ${stateColor(v.state)} 16%, var(--surface))`,
          border: `1px solid ${stateColor(v.state)}` }}>
          <span style={{ width: 9, height: 9, borderRadius: "50%", background: stateColor(v.state) }} />
          {v.state}
        </span>
        <span style={{ flex: 1 }} />
        <button className="btn btn-secondary" disabled={busy}
          onClick={() => setMode(safe ? "normal" : "safe")}>
          {safe ? "Resume (auto)" : "Safe mode"}
        </button>
      </div>

      {/* KPI cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
        <Kpi label="Heaviness" value={pct(v.heaviness * 100)} frac={v.heaviness} color={stateColor(v.state)}
             hint="worst headroom across resources" />
        <Kpi label="CPU" value={pct(vit.cpu_pct)} frac={vit.cpu_pct / 100} />
        <Kpi label="Memory" value={pct(vit.mem_pct)} frac={vit.mem_pct / 100}
             hint={vit.swap_pct > 0 ? `swap ${pct(vit.swap_pct)}` : undefined} />
        <Kpi label="Temperature" value={vit.temp_c == null ? "n/a" : `${Math.round(vit.temp_c)}°C`}
             frac={vit.temp_c == null ? 0 : Math.min(1, vit.temp_c / 90)} />
        <Kpi label="Power" value={vit.watts == null ? "n/a" : `${Math.round(vit.watts)} W`} frac={0} />
        <Kpi label="Disk free" value={`${vit.disk_free_gb.toFixed(0)} GB`} frac={vit.disk_used_pct / 100}
             hint={`${pct(vit.disk_used_pct)} used`} />
      </div>

      {/* history sparkline */}
      {hist.length > 1 && (
        <Card title="Last few hours" sub="Heaviness (filled) with CPU and memory. One point per minute.">
          <Spark hist={hist} />
        </Card>
      )}

      {/* governor plan */}
      <Card title="What the governor is allowing"
        sub={v.plan.reason || "Heavy work is gated automatically when the box is under pressure."}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 7, marginBottom: 10 }}>
          {["inference", "training", "tuning", "discovery", "import"].map((k) => {
            const on = v.plan.admitted.includes(k);
            return (
              <span key={k} style={{ fontSize: 12.5, padding: "3px 9px", borderRadius: 999,
                border: `1px solid ${on ? "var(--ok, #34D399)" : "var(--border)"}`,
                color: on ? "var(--text)" : "var(--text-dim)",
                background: on ? "color-mix(in srgb, var(--ok, #34D399) 12%, transparent)" : "transparent",
                textDecoration: on ? "none" : "line-through" }}>{k}</span>
            );
          })}
        </div>
        <div style={{ fontSize: 12.5, color: "var(--text-dim)", display: "flex", gap: 18, flexWrap: "wrap" }}>
          {v.plan.pause_training && <span>training paused</span>}
          {v.plan.n_jobs_cap != null && <span>max {v.plan.n_jobs_cap} job(s) at once</span>}
          {v.plan.interval_multiplier !== 1 && <span>schedules ×{v.plan.interval_multiplier} slower</span>}
          {v.plan.influx_chunk_factor !== 1 && <span>Influx chunks ×{v.plan.influx_chunk_factor}</span>}
          <span>Influx load {pct(vit.influx_query_load * 100)}</span>
        </div>
      </Card>

      {/* advanced: governor thresholds */}
      {cfg && defaults && (
        <Card title="Advanced — governor thresholds"
          sub="When Hearth eases off. Changes apply on the next check (every 60s); no restart."
          action={<button className="btn btn-ghost" onClick={() => setAdvOpen((o) => !o)}>{advOpen ? "Hide" : "Edit"}</button>}>
          {advOpen && (
            <CfgEditor cfg={cfg} defaults={defaults} setCfg={setCfg}
                       onSave={saveCfg} onReset={() => { setCfg(defaults); setCfgMsg(""); }}
                       busy={busy} msg={cfgMsg} />
          )}
        </Card>
      )}

      {/* blind spots */}
      {gaps.length > 0 && (
        <Card title="Blind spots" sub="Where another sensor would sharpen recognition.">
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {gaps.slice(0, 5).map((g, i) => (
              <div key={i} style={{ fontSize: 13 }}>
                <span style={{ color: "var(--text-dim)" }}>· </span>{g.recommendation}
              </div>
            ))}
          </div>
        </Card>
      )}
    </section>
  );
}

const CFG_GROUPS: { title: string; fields: { key: keyof Cfg; label: string; step: number; unit?: string }[] }[] = [
  { title: "Thermal", fields: [
    { key: "temp_warn", label: "Warn at", step: 1, unit: "°C" },
    { key: "temp_max", label: "Critical at", step: 1, unit: "°C" }] },
  { title: "Heaviness bands (0–1)", fields: [
    { key: "enter_elevated", label: "Elevated above", step: 0.01 },
    { key: "enter_high", label: "High above", step: 0.01 },
    { key: "enter_critical", label: "Critical above", step: 0.01 },
    { key: "leave_margin", label: "Step-down margin", step: 0.01 }] },
  { title: "Safety", fields: [
    { key: "min_disk_gb", label: "Min disk free", step: 0.5, unit: "GB" },
    { key: "swap_weight", label: "Swap weight", step: 0.1 }] },
];

function CfgEditor({ cfg, defaults, setCfg, onSave, onReset, busy, msg }: {
  cfg: Cfg; defaults: Cfg; setCfg: (c: Cfg) => void;
  onSave: () => void; onReset: () => void; busy: boolean; msg: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {CFG_GROUPS.map((g) => (
        <div key={g.title}>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 6 }}>{g.title}</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 10 }}>
            {g.fields.map((f) => (
              <label key={f.key} style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 13 }}>
                <span>{f.label}{f.unit ? ` (${f.unit})` : ""}</span>
                <input type="number" step={f.step} value={cfg[f.key]}
                  onChange={(e) => setCfg({ ...cfg, [f.key]: Number(e.target.value) })} />
                <span style={{ fontSize: 11, color: "var(--text-dim)" }}>default {defaults[f.key]}</span>
              </label>
            ))}
          </div>
        </div>
      ))}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button className="btn btn-primary" disabled={busy} onClick={onSave}>Save</button>
        <button className="btn btn-ghost" disabled={busy} onClick={onReset}>Reset to defaults</button>
        {msg && <span style={{ fontSize: 12.5, color: msg.startsWith("Saved") ? "var(--ok, #34D399)" : "var(--danger)" }}>{msg}</span>}
      </div>
    </div>
  );
}

function Kpi({ label, value, frac, color = "var(--accent)", hint }: {
  label: string; value: string; frac: number; color?: string; hint?: string;
}) {
  return (
    <div className="card" style={{ padding: 14 }}>
      <div style={{ fontSize: 12, color: "var(--text-dim)" }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, margin: "2px 0 8px" }}>{value}</div>
      <div style={{ height: 6, borderRadius: 4, background: "var(--surface-2)", overflow: "hidden" }}>
        <div style={{ width: `${Math.round(Math.min(1, Math.max(0, frac)) * 100)}%`, height: "100%", background: color }} />
      </div>
      {hint && <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 5 }}>{hint}</div>}
    </div>
  );
}

function Spark({ hist }: { hist: Hist[] }) {
  const n = hist.length;
  const W = 100, H = 40;
  const x = (i: number) => (n <= 1 ? 0 : (i / (n - 1)) * W);
  const yFrac = (f: number) => H - Math.min(1, Math.max(0, f)) * H;
  const line = (vals: number[]) => vals.map((val, i) => `${x(i)},${yFrac(val)}`).join(" ");
  const hVals = hist.map((p) => p.h);
  const cpuVals = hist.map((p) => p.cpu / 100);
  const memVals = hist.map((p) => p.mem / 100);
  const last = hist[n - 1];
  const area = `0,${H} ` + line(hVals) + ` ${W},${H}`;
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
           style={{ width: "100%", height: 90, display: "block" }}>
        <polygon points={area} fill={`color-mix(in srgb, ${stateColor(last.state)} 22%, transparent)`} />
        <polyline points={line(hVals)} fill="none" stroke={stateColor(last.state)} strokeWidth={1} vectorEffect="non-scaling-stroke" />
        <polyline points={line(cpuVals)} fill="none" stroke="var(--text-dim)" strokeWidth={0.6} vectorEffect="non-scaling-stroke" opacity={0.7} />
        <polyline points={line(memVals)} fill="none" stroke="var(--accent)" strokeWidth={0.6} vectorEffect="non-scaling-stroke" opacity={0.55} />
      </svg>
      <div style={{ display: "flex", gap: 16, fontSize: 11.5, color: "var(--text-dim)", marginTop: 4 }}>
        <Legend color={stateColor(last.state)} label="heaviness" />
        <Legend color="var(--text-dim)" label="CPU" />
        <Legend color="var(--accent)" label="memory" />
      </div>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      <span style={{ width: 12, height: 3, background: color, borderRadius: 2 }} />{label}
    </span>
  );
}

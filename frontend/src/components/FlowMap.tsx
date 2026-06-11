/**
 * FlowMap — the live data-flow map. Travelling dots flow node-to-node at the
 * real throughput (from GET /api/flow), each node shows this instance's number
 * and links to the relevant page. Full version is the hero of How-it-works
 * (nodes deep-link to steps); compact=true renders the dashboard mini.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { usePrefersReducedMotion } from "../useMedia";

type NodeData = { label: string; value: string; status: string; href: string; step: string };
type EdgeData = { rate: number; status: string; label?: string };
type Flow = { phase: string; tone: string; nodes: Record<string, NodeData>; edges: Record<string, EdgeData> };

const N: Record<string, { x: number; y: number; w: number; h: number; color: string; desc: string }> = {
  ha:          { x: 30,  y: 84,  w: 120, h: 54, color: "#34D399", desc: "Every sensor Hearth listens to." },
  raw:         { x: 230, y: 84,  w: 120, h: 54, color: "#60A5FA", desc: "Raw events kept in InfluxDB (180-day retention)." },
  features:    { x: 440, y: 84,  w: 120, h: 54, color: "#60A5FA", desc: "30-minute windows turned into model features." },
  model:       { x: 640, y: 84,  w: 120, h: 54, color: "#F59E0B", desc: "The Random Forest that predicts your activity." },
  predictions: { x: 820, y: 84,  w: 130, h: 54, color: "#34D399", desc: "Live states, pushed onto Home Assistant's bus." },
  you:         { x: 820, y: 273, w: 130, h: 54, color: "#F472B6", desc: "You confirm — the ground truth it learns from." },
  discovery:   { x: 440, y: 273, w: 120, h: 54, color: "#FB923C", desc: "Recurring routines it found for you to name." },
};
const E: { id: string; d: string; dot: string }[] = [
  { id: "ha_raw", d: "M150 111 L230 111", dot: "#F59E0B" },
  { id: "raw_features", d: "M350 111 L440 111", dot: "#F59E0B" },
  { id: "features_model", d: "M560 111 L640 111", dot: "#7F77DD" },
  { id: "model_predictions", d: "M760 111 L820 111", dot: "#34D399" },
  { id: "predictions_you", d: "M885 138 L885 273", dot: "#F472B6" },
  { id: "you_model", d: "M820 320 L700 320 L700 138", dot: "#F472B6" },
  { id: "features_discovery", d: "M500 138 L500 273", dot: "#FB923C" },
];
const MAIN_N = ["ha", "raw", "features", "model", "predictions"];
const MAIN_E = ["ha_raw", "raw_features", "features_model", "model_predictions"];
const cx = (id: string) => N[id].x + N[id].w / 2;
const cy = (id: string) => N[id].y + N[id].h / 2;
const STATUS_STROKE: Record<string, string> = {
  alert: "var(--danger)", ask: "var(--accent)", work: "var(--accent)", idle: "var(--border)",
};

export default function FlowMap({ compact = false }: { compact?: boolean }) {
  const [f, setF] = useState<Flow | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const navigate = useNavigate();
  const reduce = usePrefersReducedMotion();
  const timer = useRef<number>();

  useEffect(() => {
    let alive = true;
    const tick = () => {
      if (document.hidden) { timer.current = window.setTimeout(tick, 5000); return; }
      fetch("/api/flow").then((r) => r.json()).then((d: Flow) => {
        if (alive) setF(d);
        timer.current = window.setTimeout(tick, compact ? 60000 : 15000);
      }).catch(() => { timer.current = window.setTimeout(tick, 30000); });
    };
    tick();
    return () => { alive = false; window.clearTimeout(timer.current); };
  }, [compact]);

  if (!f) return null;

  const edges = compact ? E.filter((e) => MAIN_E.includes(e.id)) : E;
  const nodeIds = Object.keys(N).filter((id) => !compact || MAIN_N.includes(id));

  const svg = (
    <svg viewBox={compact ? "20 72 940 84" : "0 0 980 380"} role="img"
         style={{ width: "100%", height: "auto", display: "block" }}>
      <defs>
        <marker id="fm-ar" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0 0 L6 3 L0 6 Z" fill="var(--text-dim)" />
        </marker>
      </defs>

      {edges.map((e) => {
        const ed = f.edges[e.id] || { rate: 0, status: "ok" };
        const dead = ed.rate === 0 || ed.status === "idle";
        const stroke = ed.status === "alert" ? "var(--danger)" : "var(--text-dim)";
        const dur = ed.rate >= 3 ? 1.2 : ed.rate === 2 ? 1.9 : 2.8;
        return (
          <g key={e.id}>
            <path d={e.d} fill="none" stroke={stroke} strokeWidth={1.6}
                  strokeOpacity={dead ? 0.28 : 0.8} markerEnd="url(#fm-ar)" />
            {!reduce && Array.from({ length: ed.rate }).map((_, i) => (
              <circle key={i} r={compact ? 2.6 : 3.4} fill={e.dot}>
                <animateMotion dur={`${dur}s`} begin={`${((i * dur) / ed.rate).toFixed(2)}s`}
                               repeatCount="indefinite" path={e.d} />
              </circle>
            ))}
          </g>
        );
      })}

      {nodeIds.map((id) => {
        const n = N[id];
        const nd = f.nodes[id];
        if (!nd) return null;
        const ring = STATUS_STROKE[nd.status] || n.color;
        const dim = nd.status === "idle";
        return (
          <g key={id} style={{ cursor: compact ? "pointer" : "pointer", opacity: dim ? 0.6 : 1 }}
             onClick={() => (compact ? navigate("/methodology") : navigate(nd.href))}
             onMouseEnter={() => !compact && setHover(id)} onMouseLeave={() => setHover(null)}>
            <rect x={n.x} y={n.y} width={n.w} height={n.h} rx={9}
                  fill="var(--surface-2)" stroke={ring}
                  strokeWidth={nd.status === "alert" || hover === id ? 2 : 1.4} />
            <text x={cx(id)} y={n.y + 23} textAnchor="middle" fontSize={13} fontWeight={600}
                  fill="var(--text)" style={{ pointerEvents: "none" }}>{nd.label}</text>
            <text x={cx(id)} y={n.y + 41} textAnchor="middle" fontSize={11.5}
                  fill={nd.status === "ask" ? "var(--accent)" : "var(--text-dim)"}
                  style={{ pointerEvents: "none" }}>{nd.value}</text>
          </g>
        );
      })}

      {/* edge throughput label (ingest rate) */}
      {f.edges.ha_raw?.label && (
        <text x={190} y={100} textAnchor="middle" fontSize={10.5} fill="var(--text-dim)"
              style={{ pointerEvents: "none" }}>{f.edges.ha_raw.label}</text>
      )}
      {!compact && (
        <text x={745} y={338} textAnchor="middle" fontSize={10.5} fill="var(--text-dim)"
              style={{ pointerEvents: "none" }}>your answers</text>
      )}

      {hover && !compact && (() => {
        const n = N[hover]; const nd = f.nodes[hover];
        const below = n.y < 200;
        const tx = Math.max(6, Math.min(980 - 210, cx(hover) - 105));
        const ty = below ? n.y + n.h + 8 : n.y - 78;
        return (
          <foreignObject x={tx} y={ty} width={210} height={72} style={{ pointerEvents: "none" }}>
            <div style={{ background: "var(--surface)", border: "1px solid var(--border)",
                          borderRadius: 10, padding: "8px 10px", boxShadow: "0 6px 20px rgba(0,0,0,0.25)" }}>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>{nd.label}</div>
              <div style={{ fontSize: 11.5, color: "var(--text-dim)", margin: "1px 0 3px", lineHeight: 1.3 }}>{n.desc}</div>
              <div style={{ fontSize: 11.5, color: "var(--accent)" }}>{nd.value} · open →</div>
            </div>
          </foreignObject>
        );
      })()}
    </svg>
  );

  if (compact) {
    return (
      <section className="card" style={{ padding: 14, cursor: "pointer", display: "flex",
                                         flexDirection: "column", gap: 8 }}
               onClick={() => navigate("/methodology")} title="Open the full data-flow map">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>Live data flow</h3>
          <span style={{ marginLeft: "auto", fontSize: 12.5, color: "var(--text-dim)" }}>How it works →</span>
        </div>
        {svg}
      </section>
    );
  }
  return svg;
}

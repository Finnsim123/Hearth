/**
 * FlowMap — the live data-flow map. Travelling dots flow node-to-node at the
 * real throughput (from GET /api/flow), each node shows this instance's number
 * and links to the relevant page. Full version is the hero of How-it-works
 * (nodes deep-link to steps); compact=true renders the dashboard mini.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { usePrefersReducedMotion } from "../useMedia";

type NodeData = { label: string; value: string; status: string; href: string; step: string;
                  source?: string | null };
type EdgeData = { rate: number; status: string; label?: string };
type Flow = { phase: string; tone: string; nodes: Record<string, NodeData>; edges: Record<string, EdgeData> };

// Compact layout (narrow canvas → text stays legible at half-card width).
const N: Record<string, { x: number; y: number; w: number; h: number; color: string; desc: string }> = {
  ha:          { x: 8,   y: 33,  w: 116, h: 54, color: "#34D399", desc: "Every sensor Hearth listens to." },
  raw:         { x: 150, y: 33,  w: 116, h: 54, color: "#60A5FA", desc: "Raw events kept in InfluxDB (180-day retention)." },
  features:    { x: 292, y: 33,  w: 116, h: 54, color: "#60A5FA", desc: "30-minute windows turned into model features." },
  model:       { x: 434, y: 33,  w: 116, h: 54, color: "#F59E0B", desc: "The Random Forest that predicts your activity." },
  predictions: { x: 576, y: 33,  w: 128, h: 54, color: "#34D399", desc: "Live states, pushed onto Home Assistant's bus." },
  you:         { x: 576, y: 173, w: 128, h: 54, color: "#F472B6", desc: "You confirm — the ground truth it learns from." },
  discovery:   { x: 292, y: 173, w: 116, h: 54, color: "#FB923C", desc: "Recurring routines it found for you to name." },
};
const E: { id: string; d: string; dot: string }[] = [
  { id: "ha_raw", d: "M124 60 L150 60", dot: "#F59E0B" },
  { id: "raw_features", d: "M266 60 L292 60", dot: "#F59E0B" },
  { id: "features_model", d: "M408 60 L434 60", dot: "#7F77DD" },
  { id: "model_predictions", d: "M550 60 L576 60", dot: "#34D399" },
  { id: "predictions_you", d: "M640 87 L640 173", dot: "#F472B6" },
  { id: "you_model", d: "M576 200 L492 200 L492 89", dot: "#F472B6" },
  { id: "features_discovery", d: "M350 87 L350 173", dot: "#FB923C" },
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
    <svg viewBox={compact ? "2 28 708 64" : "0 24 712 212"} role="img"
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
            <text x={cx(id)} y={n.y + 23} textAnchor="middle" fontSize={15.5} fontWeight={600}
                  fill="var(--text)" style={{ pointerEvents: "none" }}>{nd.label}</text>
            <text x={cx(id)} y={n.y + 41} textAnchor="middle" fontSize={13.5}
                  fill={nd.status === "ask" ? "var(--accent)" : "var(--text-dim)"}
                  style={{ pointerEvents: "none" }}>{nd.value}</text>
          </g>
        );
      })}

      {/* edge throughput label (ingest rate) */}
      {f.edges.ha_raw?.label && (
        <text x={137} y={106} textAnchor="middle" fontSize={11} fill="var(--text-dim)"
              style={{ pointerEvents: "none" }}>{f.edges.ha_raw.label}</text>
      )}
      {!compact && (
        <text x={534} y={216} textAnchor="middle" fontSize={12} fill="var(--text-dim)"
              style={{ pointerEvents: "none" }}>your answers</text>
      )}

      {hover && !compact && (() => {
        const n = N[hover]; const nd = f.nodes[hover];
        const below = n.y < 100;
        const HT = 98;   // fits title + 2-line desc + value (72 clipped the last line)
        const tx = Math.max(6, Math.min(712 - 210, cx(hover) - 105));
        const ty = below ? n.y + n.h + 8 : n.y - (HT + 6);
        // the source node names its actual platform on hover (Home Assistant today)
        const desc = nd.source ? `Reading from ${nd.source} — every sensor Hearth listens to.` : n.desc;
        return (
          <foreignObject x={tx} y={ty} width={210} height={HT} style={{ pointerEvents: "none" }}>
            <div style={{ background: "var(--surface)", border: "1px solid var(--border)",
                          borderRadius: 10, padding: "8px 10px", boxShadow: "0 6px 20px rgba(0,0,0,0.25)" }}>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>{nd.label}</div>
              <div style={{ fontSize: 11.5, color: "var(--text-dim)", margin: "1px 0 3px", lineHeight: 1.3 }}>{desc}</div>
              <div style={{ fontSize: 11.5, color: "var(--accent)" }}>{nd.value} · open →</div>
            </div>
          </foreignObject>
        );
      })()}
    </svg>
  );

  return svg;
}

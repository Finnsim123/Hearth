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
                  source?: string | null; desc?: string | null };
type EdgeData = { rate: number; status: string; label?: string };
type ModelInfo = { person_id: string; name: string; avatar?: string | null;
                   version: string | null; accuracy: number | null; nodes: number;
                   preds: number; status: string };
type Flow = { phase: string; tone: string; nodes: Record<string, NodeData>;
              edges: Record<string, EdgeData>; models?: ModelInfo[] };

// Per-person model status → dot/lane colour (trained vs still learning).
const MODEL_STATUS: Record<string, string> = {
  ok: "#34D399", work: "#F59E0B", idle: "var(--border)",
};
const mColor = (s: string) => MODEL_STATUS[s] ?? "var(--accent)";
const initial = (name: string) => (name.trim()[0] || "?").toUpperCase();

// Compact layout (narrow canvas → text stays legible at half-card width).
const N: Record<string, { x: number; y: number; w: number; h: number; color: string; desc: string }> = {
  ha:          { x: 8,   y: 33,  w: 116, h: 54, color: "#34D399", desc: "Every sensor Hearth listens to." },
  raw:         { x: 150, y: 33,  w: 116, h: 54, color: "#60A5FA", desc: "Raw events kept in InfluxDB." },
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

  const models = f.models ?? [];
  const multi = models.length > 1;
  // Full view with a handful of members: fork the model column into one box per
  // person, all feeding the shared predictions node. Beyond 4 that gets cramped,
  // so we fall back to the single aggregate "Models" node (with per-person dots).
  const fork = !compact && multi && models.length <= 4;

  // fork geometry: stack per-person model boxes centred on the pipeline row.
  const MB = N.model;
  const cyRow = MB.y + MB.h / 2;
  const GAP = 8;
  const hh = fork ? Math.min(48, (150 - (models.length - 1) * GAP) / models.length) : 0;
  const totalH = models.length * hh + (models.length - 1) * GAP;
  const stackTop = cyRow - totalH / 2;
  const laneY = (i: number) => stackTop + i * (hh + GAP);
  const laneMid = (i: number) => laneY(i) + hh / 2;

  let baseEdges = compact ? E.filter((e) => MAIN_E.includes(e.id)) : E;
  if (fork) {
    // the fixed model edges are replaced by per-lane ones; re-anchor the
    // feedback loop so it points into the bottom of the model stack.
    baseEdges = baseEdges
      .filter((e) => e.id !== "features_model" && e.id !== "model_predictions")
      .map((e) => e.id === "you_model"
        ? { ...e, d: `M576 200 L492 200 L492 ${Math.round(stackTop + totalH)}` } : e);
  }
  const nodeIds = Object.keys(N).filter((id) =>
    (!compact || MAIN_N.includes(id)) && !(fork && id === "model"));

  const renderEdge = (e: { d: string; dot: string }, ed: EdgeData, key: string) => {
    const dead = ed.rate === 0 || ed.status === "idle";
    const stroke = ed.status === "alert" ? "var(--danger)" : "var(--text-dim)";
    const dur = ed.rate >= 3 ? 1.2 : ed.rate === 2 ? 1.9 : 2.8;
    return (
      <g key={key}>
        <path d={e.d} fill="none" stroke={stroke} strokeWidth={1.6}
              strokeOpacity={dead ? 0.28 : 0.8} markerEnd="url(#fm-ar)" />
        {!reduce && ed.rate > 0 && Array.from({ length: ed.rate }).map((_, i) => (
          <circle key={i} r={compact ? 2.6 : 3.4} fill={e.dot}>
            <animateMotion dur={`${dur}s`} begin={`${((i * dur) / ed.rate).toFixed(2)}s`}
                           repeatCount="indefinite" path={e.d} />
          </circle>
        ))}
      </g>
    );
  };

  const svg = (
    <svg viewBox={compact ? "2 28 708 64" : "0 24 712 212"} role="img"
         style={{ width: "100%", height: "auto", display: "block" }}>
      <defs>
        <marker id="fm-ar" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0 0 L6 3 L0 6 Z" fill="var(--text-dim)" />
        </marker>
      </defs>

      {baseEdges.map((e) => renderEdge(e, f.edges[e.id] || { rate: 0, status: "ok" }, e.id))}

      {/* fork: one model box per person, each with its own feed + serve edges */}
      {fork && models.map((mi, i) => {
        const mid = laneMid(i);
        const rate = mi.preds >= 250 ? 3 : mi.preds >= 50 ? 2 : mi.preds > 0 ? 1 : 0;
        const dot = mColor(mi.status);
        const detail = mi.version
          ? (mi.accuracy ? `${Math.round(mi.accuracy * 100)}%` : "trained")
            + (mi.nodes > 1 ? ` · ${mi.nodes} nodes` : "")
          : "learning…";
        return (
          <g key={mi.person_id}>
            {renderEdge({ d: `M408 60 L${MB.x} ${mid}`, dot },
                        { rate: Math.max(1, rate), status: "ok" }, `fe${i}`)}
            {renderEdge({ d: `M${MB.x + MB.w} ${mid} L576 60`, dot },
                        { rate, status: mi.preds ? "ok" : "idle" }, `pe${i}`)}
            <g style={{ cursor: "pointer" }} onClick={() => navigate("/models")}>
              <rect x={MB.x} y={laneY(i)} width={MB.w} height={hh} rx={8}
                    fill="var(--surface-2)" stroke={dot} strokeWidth={mi.status === "ok" ? 1.6 : 1.4} />
              <circle cx={MB.x + 14} cy={mid} r={7.5}
                      fill={`color-mix(in srgb, ${dot} 30%, transparent)`} stroke={dot} strokeWidth={1} />
              <text x={MB.x + 14} y={mid + 3.5} textAnchor="middle" fontSize={9} fontWeight={700}
                    fill="var(--text)" style={{ pointerEvents: "none" }}>{initial(mi.name)}</text>
              <text x={MB.x + 28} y={mid - 3} fontSize={12} fontWeight={600} fill="var(--text)"
                    style={{ pointerEvents: "none" }}>{mi.name}</text>
              <text x={MB.x + 28} y={mid + 11} fontSize={10.5} fill="var(--text-dim)"
                    style={{ pointerEvents: "none" }}>{detail}</text>
            </g>
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
            {/* per-person model dots — one per member, colour = trained/learning.
                Shown on the aggregate node (compact, or full with 5+ members). */}
            {id === "model" && multi && (() => {
              const list = models.slice(0, 6);
              const extra = models.length - list.length;
              const sp = 11, slots = list.length + (extra > 0 ? 1 : 0);
              const x0 = cx(id) - ((slots - 1) * sp) / 2;
              const dy = n.y + n.h - 7;
              return (
                <g style={{ pointerEvents: "none" }}>
                  {list.map((mi, i) => (
                    <circle key={mi.person_id} cx={x0 + i * sp} cy={dy} r={3}
                            fill={mColor(mi.status)} stroke="var(--surface)" strokeWidth={0.6}>
                      <title>{mi.name}: {mi.version
                        ? (mi.accuracy ? `${Math.round(mi.accuracy * 100)}%` : "trained") : "learning"}</title>
                    </circle>
                  ))}
                  {extra > 0 && (
                    <text x={x0 + list.length * sp} y={dy + 3.5} textAnchor="middle"
                          fontSize={8.5} fill="var(--text-dim)">+{extra}</text>
                  )}
                </g>
              );
            })()}
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
        // the source node names its actual platform on hover (Home Assistant today);
        // other nodes may ship a live desc from the backend (e.g. raw retention)
        const desc = nd.source ? `Reading from ${nd.source} — every sensor Hearth listens to.`
                               : nd.desc || n.desc;
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

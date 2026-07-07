/**
 * HomeWiring — the home's temporal "wiring": which sensor reliably precedes
 * another and by how long (bathroom → bedroom light ~2 min). Backed by
 * /api/bindings/leadlag (lagged cross-correlation). Lazy, read-only; renders
 * nothing until there's signal.
 */
import { useEffect, useState } from "react";
import Card from "./Card";

type Node = { name: string; entity_id?: string; room?: string | null; device?: string | null };
type Edge = { from: string; to: string; lag_min: number; strength: number;
              from_label: Node; to_label: Node };

const label = (n: Node) => n.device || n.room || n.entity_id || n.name;

export default function HomeWiring() {
  const [edges, setEdges] = useState<Edge[] | null>(null);
  useEffect(() => {
    fetch("/api/bindings/leadlag")
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => setEdges(d.edges ?? []))
      .catch(() => setEdges([]));
  }, []);
  if (!edges || edges.length === 0) return null;

  return (
    <Card icon="flow" title="How your home flows"
      sub="Sensors that reliably fire in sequence — learned from the data, not configured. A hint at where automations (or transitions) could hang.">
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {edges.slice(0, 10).map((e, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13,
                                flexWrap: "wrap" }}>
            <span style={{ fontWeight: 500 }}>{label(e.from_label)}</span>
            <span style={{ color: "var(--text-dim)", fontVariantNumeric: "tabular-nums" }}>
              →&nbsp;~{e.lag_min} min&nbsp;→
            </span>
            <span style={{ fontWeight: 500 }}>{label(e.to_label)}</span>
            <span style={{ marginLeft: "auto", fontSize: 11.5, color: "var(--text-dim)",
                           fontVariantNumeric: "tabular-nums" }}>
              {Math.round(e.strength * 100)}%
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

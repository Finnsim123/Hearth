/**
 * HomeWiring — the home's temporal "wiring": which sensor reliably precedes
 * another and by how long (bathroom → bedroom light ~2 min). Backed by
 * /api/bindings/leadlag (lagged cross-correlation). Read-only, plus a light
 * "turn this into a transition marker" action for edges that lead into a state.
 */
import { useEffect, useState } from "react";
import Card from "./Card";

type Node = { name: string; entity_id?: string; room?: string | null; device?: string | null };
type Edge = { from: string; to: string; lag_min: number; strength: number;
              from_label: Node; to_label: Node };
type Suggestion = { binding_name: string; to_state: string; lead_min: number;
                    strength: number; reason: string; from_label: Node; to_label: Node };

const label = (n?: Node) => n?.device || n?.room || n?.entity_id || n?.name || "";

export default function HomeWiring() {
  const [edges, setEdges] = useState<Edge[] | null>(null);
  const [sugs, setSugs] = useState<Suggestion[]>([]);
  const [added, setAdded] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetch("/api/bindings/leadlag")
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => { setEdges(d.edges ?? []); setSugs(d.suggested_markers ?? []); })
      .catch(() => setEdges([]));
  }, []);

  const addMarker = async (s: Suggestion) => {
    const slug = `${s.binding_name}_to_${s.to_state}`.replace(/[^a-z0-9]+/gi, "_").toLowerCase();
    try {
      await fetch("/api/markers", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slug, name: `${label(s.from_label)} → ${s.to_state}`,
          binding_name: s.binding_name, to_state: s.to_state, from_state: null,
          lead_min: s.lead_min, source: "discovery" }) });
      setAdded((a) => new Set(a).add(slug));
    } catch { /* ignore */ }
  };

  if (!edges || (edges.length === 0 && sugs.length === 0)) return null;

  return (
    <Card icon="flow" title="How your home flows"
      sub="Sensors that reliably fire in sequence — learned from the data, not configured. A hint at where automations (or transitions) could hang.">
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {edges.slice(0, 10).map((e, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 500 }}>{label(e.from_label)}</span>
            <span style={{ color: "var(--text-dim)", fontVariantNumeric: "tabular-nums" }}>
              →&nbsp;~{e.lag_min} min&nbsp;→
            </span>
            <span style={{ fontWeight: 500 }}>{label(e.to_label)}</span>
            <span style={{ marginLeft: "auto", fontSize: 11.5, color: "var(--text-dim)",
                           fontVariantNumeric: "tabular-nums" }}>{Math.round(e.strength * 100)}%</span>
          </div>
        ))}
      </div>

      {sugs.length > 0 && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)",
                      display: "flex", flexDirection: "column", gap: 8 }}>
          <p className="label" style={{ margin: 0 }}>Looks like a lead-in to a state change</p>
          {sugs.map((s, i) => {
            const slug = `${s.binding_name}_to_${s.to_state}`.replace(/[^a-z0-9]+/gi, "_").toLowerCase();
            const done = added.has(slug);
            return (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, flexWrap: "wrap" }}>
                <span style={{ fontSize: 12.5, color: "var(--text-dim)", flex: 1, minWidth: 180 }}>
                  <strong style={{ color: "var(--text)" }}>{label(s.from_label)}</strong> {s.reason} —
                  mark it as the switch into <strong style={{ color: "var(--text)" }}>{s.to_state}</strong>?
                </span>
                <button className="btn btn-secondary" disabled={done} onClick={() => addMarker(s)}>
                  {done ? "Added ✓" : `Add marker (~${s.lead_min} min lead)`}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

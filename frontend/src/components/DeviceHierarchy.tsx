/**
 * DeviceHierarchy — the Sensors page's primary view: HA the way it's actually
 * structured, integration → device → entity, with a keep/skip relevance verdict at
 * every level (cascading) and new-device offers inline. Not a bolt-on: this is how
 * you reason about signals — the entity is the leaf, the device/integration are always
 * in view. Source: GET /api/hierarchy; POST /api/hierarchy/{relevance,decide}.
 */
import { useCallback, useEffect, useState } from "react";
import { Icon } from "../icons";

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };
const post = (u: string, b: unknown) =>
  fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });

type Ent = { entity_id: string; name: string; relevance: string; level: string;
             reason: string; bound: boolean; area: string | null };
type Dev = { id: string; name: string; manufacturer: string | null; model: string | null;
             area: string | null; relevance: string; keep_n: number; entities: Ent[] };
type Integ = { entry_id: string; domain: string | null; title: string; relevance: string;
               keep_n: number; devices: Dev[] };
type Pending = { kind: string; id: string; name: string; detail: string; entities: string[] };
type Tree = { integrations: Integ[]; orphans: Ent[]; pending: Pending[] };

const REL = {
  keep: { c: "var(--ok, #34D399)", label: "keep" },
  skip: { c: "var(--text-dim)", label: "skip" },
  unsure: { c: "#f59e0b", label: "unsure" },
} as const;

function Chip({ rel }: { rel: string }) {
  const r = REL[rel as keyof typeof REL] ?? REL.unsure;
  return <span style={{ fontSize: 10.5, fontWeight: 500, color: r.c, padding: "1px 7px",
    borderRadius: 999, border: `1px solid ${r.c}` }}>{r.label}</span>;
}

export default function DeviceHierarchy() {
  const [tree, setTree] = useState<Tree | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [show, setShow] = useState(false);
  const load = useCallback(() => {
    fetch("/api/hierarchy").then(j).then(setTree).catch(() => setTree(null));
  }, []);
  useEffect(() => { if (show && !tree) load(); }, [show, tree, load]);

  const toggle = (id: string) => setOpen((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n;
  });
  const setRel = (level: string, id: string, relevance: string) =>
    post("/api/hierarchy/relevance", { level, id, relevance }).then(load).catch(() => {});
  const decide = (id: string, decision: "integrate" | "skip") =>
    post("/api/hierarchy/decide", { id, decision }).then(load).catch(() => {});

  const relBtns = (level: string, id: string, cur: string) => (
    <span style={{ display: "inline-flex", gap: 4 }}>
      {(["keep", "skip"] as const).map((r) => (
        <button key={r} onClick={() => setRel(level, id, r)}
          style={{ fontSize: 10.5, padding: "1px 7px", borderRadius: 999, cursor: "pointer",
            border: `1px solid ${cur === r ? REL[r].c : "var(--border)"}`,
            background: cur === r ? `color-mix(in srgb, ${REL[r].c} 14%, transparent)` : "transparent",
            color: cur === r ? REL[r].c : "var(--text-dim)" }}>{r}</button>
      ))}
    </span>
  );

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden" }}>
      <button onClick={() => setShow((s) => !s)} style={{ width: "100%", display: "flex",
        alignItems: "center", gap: 10, padding: "12px 14px", background: "none", border: "none",
        cursor: "pointer", color: "var(--text)", fontSize: 14 }}>
        <Icon name="sensors" size={16} />
        <strong>By integration &amp; device</strong>
        {tree && <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
          {tree.integrations.length} integrations · {tree.integrations.reduce((n, i) => n + i.devices.length, 0)} devices</span>}
        <span style={{ marginLeft: "auto", color: "var(--text-dim)" }}>{show ? "▾" : "▸"}</span>
      </button>

      {show && !tree && <p style={{ padding: 14, color: "var(--text-dim)" }}>Reading your home…</p>}

      {show && tree && (
        <div style={{ padding: "0 14px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
          {tree.pending.length > 0 && (
            <div style={{ padding: "10px 12px", borderRadius: 10,
              background: "color-mix(in srgb, var(--accent) 10%, transparent)",
              border: "1px solid color-mix(in srgb, var(--accent) 40%, var(--border))" }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>New devices Hearth found</div>
              {tree.pending.map((p) => (
                <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
                  <span style={{ fontSize: 13, flex: 1 }}>{p.name}
                    <span style={{ color: "var(--text-dim)", fontSize: 12 }}> · {p.detail} · {p.entities.length} useful sensor(s)</span></span>
                  <button className="btn btn-primary" style={{ fontSize: 12.5, padding: "3px 10px" }}
                    onClick={() => decide(p.id, "integrate")}>Integrate</button>
                  <button className="btn btn-ghost" style={{ fontSize: 12.5, padding: "3px 10px" }}
                    onClick={() => decide(p.id, "skip")}>Not now</button>
                </div>
              ))}
            </div>
          )}

          {tree.integrations.map((ig) => (
            <div key={ig.entry_id} style={{ border: "1px solid var(--border)", borderRadius: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", cursor: "pointer" }}
                onClick={() => toggle(ig.entry_id)}>
                <span style={{ color: "var(--text-dim)" }}>{open.has(ig.entry_id) ? "▾" : "▸"}</span>
                <strong style={{ fontSize: 13 }}>{ig.title}</strong>
                <span style={{ fontSize: 11.5, color: "var(--text-dim)" }}>{ig.domain} · {ig.devices.length} devices</span>
                <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6, alignItems: "center" }}
                  onClick={(e) => e.stopPropagation()}>
                  <Chip rel={ig.relevance} />{relBtns("integration", ig.entry_id, ig.relevance)}
                </span>
              </div>
              {open.has(ig.entry_id) && (
                <div style={{ padding: "0 10px 8px 22px", display: "flex", flexDirection: "column", gap: 5 }}>
                  {ig.devices.map((d) => (
                    <div key={d.id} style={{ borderLeft: "2px solid var(--border)", paddingLeft: 8 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", cursor: "pointer" }}
                        onClick={() => toggle(d.id)}>
                        <span style={{ color: "var(--text-dim)", fontSize: 12 }}>{open.has(d.id) ? "▾" : "▸"}</span>
                        <span style={{ fontSize: 12.5, fontWeight: 500 }}>{d.name}</span>
                        <span style={{ fontSize: 11, color: "var(--text-dim)" }}>{d.model || d.manufacturer || ""}{d.area ? ` · ${d.area}` : ""}</span>
                        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6, alignItems: "center" }}
                          onClick={(e) => e.stopPropagation()}>
                          <Chip rel={d.relevance} />{relBtns("device", d.id, d.relevance)}
                        </span>
                      </div>
                      {open.has(d.id) && d.entities.map((e) => (
                        <div key={e.entity_id} style={{ display: "flex", alignItems: "center", gap: 8,
                          padding: "2px 0 2px 18px", fontSize: 12.5,
                          opacity: e.relevance === "skip" ? 0.55 : 1 }}>
                          <span style={{ flex: 1, minWidth: 0 }} title={`${e.entity_id} — ${e.reason}`}>
                            {e.name}{e.bound && <span style={{ color: "var(--ok, #34D399)", fontSize: 11 }}> · bound</span>}
                          </span>
                          <Chip rel={e.relevance} />
                          {relBtns("entity", e.entity_id, e.relevance)}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
          {tree.orphans.length > 0 && (
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
              + {tree.orphans.length} entities not attached to a device (helpers, integrations) — see the list below.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

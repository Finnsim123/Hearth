/**
 * TransitionMarkers — "Moments that mark a change".
 *
 * Events like an alarm or the coffee machine aren't activities you DO; they mark a
 * transition between states (asleep → home). A marker is never a prediction label —
 * when its sensor fires it nudges the model to switch state cleanly at that moment.
 * Most markers are created from the Patterns page; this card manages them and lets
 * you add one by hand from a sensor.
 *
 * Backend: GET/POST /api/markers, POST /api/markers/delete.
 */
import { useCallback, useEffect, useState } from "react";
import Card from "./Card";

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };
const post = (url: string, body?: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
               body: body === undefined ? undefined : JSON.stringify(body) });

type Timing = { lead_min: number; spread_min: number; precision: number; recall: number } | null;
type Marker = { slug: string; name: string; to_state: string; from_state: string | null;
                binding_name: string; source: string; enabled: boolean;
                lead_min: number; strength: number; timing: Timing };
type Bind = { name: string; entity_id: string; room?: string | null; device?: string | null };
type Act = { slug: string; name: string };
type Data = { markers: Marker[]; bindings: Bind[]; activities: Act[] };

export default function TransitionMarkers() {
  const [data, setData] = useState<Data | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ name: "", binding_name: "", from_state: "", to_state: "", lead_min: 0 });

  const load = useCallback(() => {
    fetch("/api/markers").then(j).then(setData)
      .catch(() => setData({ markers: [], bindings: [], activities: [] }));
  }, []);
  useEffect(load, [load]);

  const nameOf = (slug: string) => data?.activities.find((a) => a.slug === slug)?.name || slug;
  const save = async () => {
    const slug = draft.name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    if (!slug || !draft.binding_name || !draft.to_state) return;
    await post("/api/markers", { slug, name: draft.name, binding_name: draft.binding_name,
      from_state: draft.from_state || null, to_state: draft.to_state,
      lead_min: Number(draft.lead_min) || 0, source: "manual" });
    setAdding(false); setDraft({ name: "", binding_name: "", from_state: "", to_state: "", lead_min: 0 });
    load();
  };
  const remove = async (slug: string) => { await post("/api/markers/delete", { slug }); load(); };

  if (!data) return <Card title="Moments that mark a change"><p style={{ color: "var(--text-dim)" }}>Loading…</p></Card>;

  return (
    <Card title="Moments that mark a change"
      sub="Events like an alarm or the coffee machine mark a transition (asleep → home) rather than being an activity. They help Hearth switch state at the right moment — they're never guessed as an activity.">
      {data.markers.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {data.markers.map((m) => (
            <div key={m.slug} style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontWeight: 500 }}>{m.name}</span>
              <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
                {m.from_state ? nameOf(m.from_state) : "any"} → {nameOf(m.to_state)} · from <code>{m.binding_name}</code>
                {m.lead_min ? ` · fires ~${m.lead_min}m before` : ""}
                {m.timing ? ` · ${Math.round(m.timing.precision * 100)}% reliable` : ""}
                {m.strength < 0.5 ? " · used as a hint" : ""}
                {m.source === "discovery" ? " · discovered" : ""}
              </span>
              <button className="btn" style={{ marginLeft: "auto" }} onClick={() => remove(m.slug)}>Remove</button>
            </div>
          ))}
        </div>
      ) : (
        <p style={{ fontSize: 12.5, color: "var(--text-dim)", margin: 0 }}>
          No markers yet. When Hearth discovers a brief, time-locked pattern (like a morning
          alarm) you can mark it as a transition on the Patterns page — or add one here.
        </p>
      )}

      {adding ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12,
                      borderTop: "0.5px solid var(--border, #2a2f3a)", paddingTop: 12 }}>
          <input placeholder="Name (e.g. Morning coffee)" value={draft.name}
                 onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          <label style={{ fontSize: 12.5, color: "var(--text-dim)" }}>Sensor that fires</label>
          <select value={draft.binding_name} onChange={(e) => setDraft({ ...draft, binding_name: e.target.value })}>
            <option value="">Pick a sensor…</option>
            {data.bindings.map((b) => <option key={b.name} value={b.name}>{b.device ? `${b.device} · ` : ""}{b.entity_id}{b.room ? ` · ${b.room}` : ""}</option>)}
          </select>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <select value={draft.from_state} onChange={(e) => setDraft({ ...draft, from_state: e.target.value })} style={{ flex: 1 }}>
              <option value="">from: any state</option>
              {data.activities.map((a) => <option key={a.slug} value={a.slug}>{a.name}</option>)}
            </select>
            <span style={{ color: "var(--text-dim)" }}>→</span>
            <select value={draft.to_state} onChange={(e) => setDraft({ ...draft, to_state: e.target.value })} style={{ flex: 1 }}>
              <option value="">to: pick a state…</option>
              {data.activities.map((a) => <option key={a.slug} value={a.slug}>{a.name}</option>)}
            </select>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
            Fires
            <input type="number" step={5} value={draft.lead_min} style={{ width: 70 }}
                   onChange={(e) => setDraft({ ...draft, lead_min: Number(e.target.value) })} />
            min before the change (0 = at the moment)
          </label>
          <span style={{ fontSize: 11.5, color: "var(--text-dim)" }}>
            Leave at 0 — Hearth learns the real lead from your history and refines it automatically.
          </span>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-primary" onClick={save}>Add marker</button>
            <button className="btn btn-ghost" onClick={() => setAdding(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <button className="btn" style={{ marginTop: 12, alignSelf: "flex-start" }}
                onClick={() => setAdding(true)}>Add a marker by hand</button>
      )}
    </Card>
  );
}

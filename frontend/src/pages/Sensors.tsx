/**
 * Sensors — every entity Hearth listens to and what it thinks it means.
 * Search, filter by role, enable/disable, delete, junk cleanup, and the
 * LLM's stated reason whenever it overrode the physics gate.
 * Spec: docs/UI_SPEC.md §Sensors.
 */
import { useEffect, useMemo, useState } from "react";
import { Icon } from "../icons";

type Binding = {
  id: number; entity_id: string; role: string; name: string;
  room: string | null; person_id: string | null;
  options: Record<string, unknown>; enabled: boolean;
};

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };

const ROLE_HINTS: Record<string, string> = {
  bed: "occupancy / pressure — sleeping signal",
  presence: "room presence — where someone is",
  person: "home / away",
  power: "appliance wattage — activity proxy",
  light: "lights on/off — awake vs asleep boundary",
  media: "playback — movie / music signal",
  env: "CO₂, humidity, temperature, particulates",
  alarm_time: "next alarm — wakeup boundary",
  focus: "phone Focus/DND",
  steps: "phone step count",
  battery: "phone battery — charging patterns",
  door: "doors / windows",
  motion: "motion events",
};

function RoleBadge({ role }: { role: string }) {
  return (
    <span title={ROLE_HINTS[role] ?? role}
          style={{ fontSize: 11.5, padding: "2px 9px", borderRadius: 99, fontWeight: 600,
                   background: "color-mix(in srgb, var(--accent) 14%, transparent)",
                   color: "var(--accent)", whiteSpace: "nowrap" }}>
      {role}
    </span>
  );
}

function BindingRow({ b, persons, onChange }: {
  b: Binding; persons: Record<string, string>; onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const llmReason = (b.options?.llm_reason ?? b.options?.reason) as string | undefined;
  const overridden = Boolean(b.options?.llm_override);
  const toggle = async () => {
    setBusy(true);
    try {
      await fetch("/api/bindings", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...b, enabled: !b.enabled }) }).then(j);
      onChange();
    } catch { /* refresh shows reality */ }
    setBusy(false);
  };
  const remove = async () => {
    if (!window.confirm(`Remove ${b.entity_id}? Hearth stops recording it; features rebuild without it on the next training run.`)) return;
    setBusy(true);
    await fetch(`/api/bindings/${b.id}`, { method: "DELETE" });
    onChange();
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 14px",
                  border: "1px solid var(--border)", borderRadius: 10,
                  opacity: b.enabled ? 1 : 0.45 }}>
      <Icon name="sensors" size={15} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <strong style={{ fontSize: 13.5 }}>{b.name}</strong>
          <RoleBadge role={b.role} />
          {b.person_id && (
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
              · {persons[b.person_id] ?? b.person_id}
            </span>
          )}
          {b.room && <span style={{ fontSize: 12, color: "var(--text-dim)" }}>· {b.room}</span>}
          {overridden && (
            <span title={`The AI argued past the physics gate: “${llmReason ?? "no reason given"}”`}
                  style={{ fontSize: 11, padding: "1px 7px", borderRadius: 99,
                           border: "1px solid var(--border)", color: "var(--text-dim)" }}>
              AI override
            </span>
          )}
        </div>
        <code style={{ fontSize: 12, color: "var(--text-dim)", overflow: "hidden",
                       textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}>
          {b.entity_id}
        </code>
        {llmReason && !overridden && (
          <span style={{ fontSize: 12, color: "var(--text-dim)", fontStyle: "italic" }}>“{llmReason}”</span>
        )}
      </div>
      <button className="btn btn-ghost" disabled={busy} title={b.enabled ? "Disable (keep, ignore)" : "Enable"}
              style={{ minHeight: 30, padding: "3px 10px", fontSize: 12.5 }} onClick={toggle}>
        {b.enabled ? "Disable" : "Enable"}
      </button>
      <button className="btn btn-ghost" disabled={busy} title="Remove binding"
              style={{ minHeight: 30, padding: "3px 8px", color: "var(--danger)" }} onClick={remove}>
        <Icon name="trash" size={14} />
      </button>
    </div>
  );
}

export default function Sensors() {
  const [bindings, setBindings] = useState<Binding[] | null>(null);
  const [persons, setPersons] = useState<Record<string, string>>({});
  const [q, setQ] = useState("");
  const [role, setRole] = useState("all");
  const [cleanMsg, setCleanMsg] = useState("");
  const load = () => fetch("/api/bindings").then(j).then(setBindings).catch(() => setBindings([]));
  useEffect(() => {
    load();
    fetch("/api/persons").then(j)
      .then((ps: { id: string; name: string }[]) =>
        setPersons(Object.fromEntries(ps.map((p) => [p.id, p.name]))))
      .catch(() => {});
  }, []);
  const roles = useMemo(
    () => Array.from(new Set((bindings ?? []).map((b) => b.role))).sort(),
    [bindings]);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (bindings ?? [])
      .filter((b) => role === "all" || b.role === role)
      .filter((b) => !needle
        || b.entity_id.toLowerCase().includes(needle)
        || b.name.toLowerCase().includes(needle)
        || (b.room ?? "").toLowerCase().includes(needle))
      .sort((a, b) => a.role.localeCompare(b.role) || a.name.localeCompare(b.name));
  }, [bindings, q, role]);
  const cleanup = async () => {
    if (!window.confirm("Remove bindings that fail the physics check (buttons, scripts, configuration entities…)? Person bindings are always kept.")) return;
    const r = await fetch("/api/bindings/cleanup", { method: "POST" }).then(j);
    setCleanMsg(`Removed ${r.removed} junk binding${r.removed === 1 ? "" : "s"}.`);
    load();
  };
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 860 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="sensors" size={22} />
        <h2 style={{ margin: 0 }}>Sensors</h2>
        <span style={{ fontSize: 13.5, color: "var(--text-dim)" }}>
          {bindings ? `${bindings.length} bound · ${bindings.filter((b) => b.enabled).length} active` : ""}
        </span>
        <button className="btn btn-secondary" style={{ marginLeft: "auto" }} onClick={cleanup}>
          Clean up junk
        </button>
      </div>
      <p style={{ margin: 0, fontSize: 14, color: "var(--text-dim)", maxWidth: 640 }}>
        Every Home Assistant entity Hearth listens to, and the <em>role</em> it was given —
        roles are what make features household-independent. Disable anything that shouldn't
        influence predictions; changes apply at the next feature build.
      </p>
      {cleanMsg && <p style={{ margin: 0, fontSize: 13.5, color: "var(--accent)" }}>{cleanMsg}</p>}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <div style={{ position: "relative", flex: 1, minWidth: 220 }}>
          <input placeholder="Search entity, name, room…" value={q}
                 onChange={(e) => setQ(e.target.value)} style={{ width: "100%" }} />
        </div>
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="all">All roles</option>
          {roles.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </div>
      {bindings === null && <p style={{ color: "var(--text-dim)" }}>Loading…</p>}
      {bindings !== null && shown.length === 0 && (
        <p style={{ color: "var(--text-dim)" }}>Nothing matches.</p>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {shown.map((b) => <BindingRow key={b.id} b={b} persons={persons} onChange={load} />)}
      </div>
    </section>
  );
}

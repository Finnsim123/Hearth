/**
 * Sensors — every entity Hearth listens to and what it thinks it means.
 * Search, filter by role, enable/disable, delete, junk cleanup, and the
 * LLM's stated reason whenever it overrode the physics gate.
 * Spec: docs/UI_SPEC.md §Sensors.
 */
import { useEffect, useMemo, useState } from "react";
import { Icon } from "../icons";
import { useIsMobile } from "../useMedia";

type Binding = {
  id: number; entity_id: string; role: string; name: string;
  room: string | null; person_id: string | null;
  options: Record<string, unknown>; enabled: boolean;
};

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };
const postJSON = (url: string, body: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const ALL_ROLES = ["presence", "bed", "person", "power", "light", "media", "env",
                   "focus", "alarm_time", "door", "steps", "battery", "custom"];
// roles that belong to one household member — these get an inline "assign member"
const PERSONAL_ROLES = new Set(["person", "bed", "alarm_time", "focus", "steps", "battery"]);
type Member = { id: string; name: string; has_person: boolean; person_alive: boolean };
type Entity = { entity_id: string; domain: string | null; friendly_name: string | null;
                area: string | null; state: string | null; suggested_role: string | null;
                is_tracker: boolean; bound: boolean;
                bound_role: string | null; bound_person: string | null };
type EntityResp = { entities: Entity[]; total: number; bound: number;
                    available: number; disabled: number };

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

type Health = { name: string; status: string; spark: number[]; kind: string; obs: number; per_day: number; feature: string | null; model_use: number; room: string | null; tier: number; reliability?: string; reliability_reason?: string };

/** Sparkline of the raw signal over the selected window (1H/24H/7D), normalized
 *  0–1 — the per-window input the model aggregates. Binary roles render as a
 *  green barcode; numeric as a line; dead sensors as a dashed flat baseline. */
function Sparkline({ h }: { h?: Health }) {
  const W = 200, H = 22;
  if (!h || h.status !== "alive" || h.spark.length === 0) {
    return (
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none"
           style={{ flexShrink: 0 }} aria-label="no signal">
        <line x1="0" y1={H / 2} x2={W} y2={H / 2} stroke="var(--text-dim)"
              strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
      </svg>
    );
  }
  const n = h.spark.length;
  const x = (i: number) => (i / Math.max(n - 1, 1)) * W;
  if (h.kind === "binary") {
    const bw = W / n;
    return (
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none"
           style={{ flexShrink: 0 }} aria-label={`${h.name} signal`}>
        <rect x="0" y="4" width={W} height={H - 8} fill="var(--surface-2)" rx="2" />
        {h.spark.map((v, i) => v > 0.05 && (
          <rect key={i} x={i * bw} y="4" width={Math.max(bw - 0.3, 0.6)} height={H - 8}
                fill="var(--ok, #34D399)" opacity={0.35 + 0.65 * v} />
        ))}
      </svg>
    );
  }
  const pts = h.spark.map((v, i) => `${x(i).toFixed(1)},${(H - 3 - v * (H - 6)).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none"
         style={{ flexShrink: 0 }} aria-label={`${h.name} signal`}>
      <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth="1.5"
                vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

const HEALTH_BADGE: Record<string, [string, string]> = {
  alive: ["live", "var(--ok, #34D399)"],
  constant: ["no variation", "var(--danger)"],
  no_data: ["no data", "var(--danger)"],
};

function BindingRow({ b, persons, health, onChange }: {
  b: Binding; persons: Record<string, string>; health?: Health; onChange: () => void;
}) {
  const status = health?.status;
  const isMobile = useIsMobile();
  const [busy, setBusy] = useState(false);
  const [editRole, setEditRole] = useState(false);
  const [editPerson, setEditPerson] = useState(false);
  const changeRole = async (role: string) => {
    setEditRole(false);
    if (role === b.role) return;
    await postJSON("/api/bindings", { ...b, role }).catch(() => {});
    onChange();
  };
  const changePerson = async (pid: string) => {
    setEditPerson(false);
    const person_id = pid || null;
    if (person_id === b.person_id) return;
    await postJSON("/api/bindings", { ...b, person_id }).catch(() => {});
    onChange();
  };
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
    <div style={{ display: "flex", flexDirection: isMobile ? "column" : "row",
                  alignItems: isMobile ? "stretch" : "center", gap: isMobile ? 8 : 12,
                  padding: "10px 14px", border: "1px solid var(--border)", borderRadius: 10,
                  opacity: b.enabled ? 1 : 0.45 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start", minWidth: 0, flex: 1 }}>
      <Icon name="sensors" size={15} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <strong style={{ fontSize: 13.5 }}>{b.name}</strong>
          {editRole ? (
            <select autoFocus defaultValue={b.role} onBlur={() => setEditRole(false)}
                    onChange={(e) => changeRole(e.target.value)} style={{ fontSize: 12, padding: "1px 4px" }}>
              {ALL_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          ) : (
            <span onClick={() => setEditRole(true)} style={{ cursor: "pointer" }} title="Click to change role">
              <RoleBadge role={b.role} />
            </span>
          )}
          {health && health.model_use > 0.001 && (
            <span title={`This sensor accounts for ${(health.model_use * 100).toFixed(1)}% of the live model's total feature importance.`}
                  style={{ fontSize: 11, padding: "1px 7px", borderRadius: 99, fontWeight: 600,
                           color: "var(--accent)",
                           background: "color-mix(in srgb, var(--accent) 14%, transparent)" }}>
              model uses {(health.model_use * 100).toFixed(health.model_use >= 0.1 ? 0 : 1)}%
            </span>
          )}
          {status && HEALTH_BADGE[status] && (
            <span title={status === "alive" ? "Producing varying signal — a usable feature."
                       : status === "constant" ? "Bound, but the value never changes in recent data — the model can't learn from a constant."
                       : "No recent data — this sensor isn't reaching Hearth (check it's logged to InfluxDB / not disabled in HA)."}
                  style={{ fontSize: 11, padding: "1px 7px", borderRadius: 99, fontWeight: 600,
                           color: HEALTH_BADGE[status][1],
                           background: `color-mix(in srgb, ${HEALTH_BADGE[status][1]} 14%, transparent)` }}>
              {HEALTH_BADGE[status][0]}
            </span>
          )}
          {health?.reliability === "suspect" && (
            <span title={`Looks unreliable: ${health.reliability_reason}. Still used, but treat its signal with caution.`}
                  style={{ fontSize: 11, padding: "1px 7px", borderRadius: 99, fontWeight: 600,
                           color: "var(--accent)",
                           background: "color-mix(in srgb, var(--accent) 14%, transparent)" }}>
              suspect
            </span>
          )}
          {PERSONAL_ROLES.has(b.role) ? (
            editPerson ? (
              <select autoFocus defaultValue={b.person_id ?? ""} onBlur={() => setEditPerson(false)}
                      onChange={(e) => changePerson(e.target.value)} style={{ fontSize: 12, padding: "1px 4px" }}>
                <option value="">Shared / nobody</option>
                {Object.entries(persons).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
              </select>
            ) : (
              <span onClick={() => setEditPerson(true)} title="Click to assign this sensor to a household member"
                    style={{ fontSize: 12, cursor: "pointer",
                             color: b.person_id ? "var(--text-dim)" : "var(--accent)" }}>
                · {b.person_id ? (persons[b.person_id] ?? b.person_id) : "assign member"}
              </span>
            )
          ) : b.person_id && (
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
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12,
                    marginLeft: isMobile ? 0 : "auto",
                    justifyContent: isMobile ? "space-between" : "flex-end" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 2,
                    alignItems: isMobile ? "stretch" : "flex-end",
                    flex: isMobile ? 1 : "none", minWidth: 110, maxWidth: isMobile ? "none" : 220 }}>
        <Sparkline h={health} />
        {health?.feature && (
          <code style={{ fontSize: 10.5, color: "var(--text-dim)" }} title="The exact feature column this sparkline plots — the model's actual input, after 1-min normalization + 30-min windowing.">
            {health.feature}
          </code>
        )}
      </div>
      {health && (
        <span title={`${health.obs.toLocaleString()} observations in 7 days · ~${health.per_day.toLocaleString()}/day`}
              style={{ fontSize: 11.5, color: "var(--text-dim)", fontVariantNumeric: "tabular-nums",
                       minWidth: 52, textAlign: "right", whiteSpace: "nowrap" }}>
          {health.obs >= 1000 ? `${(health.obs / 1000).toFixed(1)}k` : health.obs}
          <span style={{ opacity: 0.6 }}> obs</span>
        </span>
      )}
      <button className="btn btn-ghost" disabled={busy} title={b.enabled ? "Disable (keep, ignore)" : "Enable"}
              style={{ minHeight: 30, padding: "3px 10px", fontSize: 12.5 }} onClick={toggle}>
        {b.enabled ? "Disable" : "Enable"}
      </button>
      <button className="btn btn-ghost" disabled={busy} title="Remove binding"
              style={{ minHeight: 30, padding: "3px 8px", color: "var(--danger)" }} onClick={remove}>
        <Icon name="trash" size={14} />
      </button>
      </div>
    </div>
  );
}

const CAP = 120;

function AddSensor({ members, initialPeople = false, onClose, onAdded }: {
  members: Member[]; initialPeople?: boolean; onClose: () => void; onAdded: () => void;
}) {
  const [resp, setResp] = useState<EntityResp | null>(null);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<Entity | null>(null);
  const [role, setRole] = useState("custom");
  const [person, setPerson] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [showBound, setShowBound] = useState(initialPeople);   // reveal bound when linking people
  const [peopleOnly, setPeopleOnly] = useState(initialPeople);
  useEffect(() => {
    fetch("/api/ha/entities").then(j).then(setResp)
      .catch(() => setResp({ entities: [], total: 0, bound: 0, available: 0, disabled: 0 }));
  }, []);
  const entities = resp?.entities ?? null;
  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (entities ?? [])
      .filter((e) => showBound || !e.bound)
      .filter((e) => !peopleOnly || e.domain === "person" || e.domain === "device_tracker")
      .filter((e) => !needle || e.entity_id.toLowerCase().includes(needle)
        || (e.friendly_name ?? "").toLowerCase().includes(needle))
      // genuine trackers first, then anything with a suggested role, then the rest
      .sort((a, b) => Number(b.is_tracker) - Number(a.is_tracker)
        || Number(Boolean(b.suggested_role)) - Number(Boolean(a.suggested_role))
        || Number(a.bound) - Number(b.bound)
        || a.entity_id.localeCompare(b.entity_id));
  }, [entities, q, showBound, peopleOnly]);
  const shown = matches.slice(0, CAP);
  const pick = (e: Entity) => {
    if (e.bound) return;                       // bound entities are info-only here
    setSel(e); setRole(e.suggested_role ?? "custom"); setErr("");
  };
  const add = async () => {
    if (!sel) return;
    setBusy(true);
    const name = (sel.entity_id.split(".").pop() ?? "sensor")
      .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "sensor";
    try {
      await postJSON("/api/bindings", {
        entity_id: sel.entity_id, role, name, room: sel.area ?? null,
        person_id: person || null, options: {}, enabled: true,
      }).then(j);
      onAdded(); onClose();
    } catch { setErr("Couldn't add — the name may clash; try a different role."); setBusy(false); }
  };
  return (
    <div onClick={onClose}
         style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 40,
                  display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "8vh 16px" }}>
      <div className="card" onClick={(e) => e.stopPropagation()}
           style={{ width: 520, maxWidth: "100%", maxHeight: "80vh", display: "flex",
                    flexDirection: "column", gap: 12, padding: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <h3 style={{ margin: 0, fontSize: 16 }}>Add a sensor</h3>
          <button className="btn btn-ghost" style={{ marginLeft: "auto" }} onClick={onClose}>Close</button>
        </div>
        {!sel ? (
          <>
            {resp && (
              <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
                Home Assistant returned <strong style={{ color: "var(--text)" }}>{resp.total}</strong> entities ·{" "}
                {resp.bound} already bound · <strong style={{ color: "var(--text)" }}>{resp.available}</strong> available to add
                {resp.disabled > 0 && ` · ${resp.disabled} disabled in HA (hidden)`}
              </p>
            )}
            <input autoFocus placeholder="Search all Home Assistant entities…" value={q}
                   onChange={(e) => setQ(e.target.value)} style={{ width: "100%", boxSizing: "border-box" }} />
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12.5, color: "var(--text-dim)" }}>
              <label style={{ display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}>
                <input type="checkbox" checked={peopleOnly} onChange={(e) => setPeopleOnly(e.target.checked)} />
                People only (person / device_tracker)
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}>
                <input type="checkbox" checked={showBound} onChange={(e) => setShowBound(e.target.checked)} />
                Show already-bound too
              </label>
            </div>
            <div style={{ overflowY: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
              {entities === null && <p style={{ color: "var(--text-dim)", fontSize: 13 }}>Loading…</p>}
              {entities && shown.length === 0 && (
                <p style={{ color: "var(--text-dim)", fontSize: 13 }}>
                  Nothing matches{!showBound && " — tick “Show already-bound too” to see entities Hearth already tracks"}.
                </p>
              )}
              {shown.map((e) => (
                <button key={e.entity_id} onClick={() => pick(e)} disabled={e.bound}
                        title={e.bound ? `Already bound as ${e.bound_role}${e.bound_person ? ` · ${e.bound_person}` : ""} — change it in the list below` : undefined}
                        style={{ display: "flex", alignItems: "center", gap: 8, textAlign: "left",
                                 padding: "8px 10px", borderRadius: 8, border: "1px solid var(--border)",
                                 background: "var(--surface-2)", cursor: e.bound ? "default" : "pointer",
                                 opacity: e.bound ? 0.6 : 1, color: "var(--text)" }}>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, display: "flex", gap: 6, alignItems: "center" }}>
                      {e.friendly_name || e.entity_id}
                      {e.is_tracker && <span style={{ fontSize: 10.5, color: "var(--text-dim)" }}>· home/away · {e.state ?? "?"}</span>}
                    </div>
                    <code style={{ fontSize: 11.5, color: "var(--text-dim)" }}>{e.entity_id}</code>
                  </div>
                  {e.bound
                    ? <span style={{ fontSize: 11, color: "var(--text-dim)" }}>bound · {e.bound_role}{e.bound_person ? ` · ${e.bound_person}` : ""}</span>
                    : e.suggested_role && <RoleBadge role={e.suggested_role} />}
                </button>
              ))}
              {matches.length > CAP && (
                <p style={{ color: "var(--text-dim)", fontSize: 12.5, margin: "4px 0 0" }}>
                  +{matches.length - CAP} more — refine your search to narrow it down.
                </p>
              )}
            </div>
          </>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ fontSize: 13 }}>
              <div style={{ fontWeight: 600 }}>{sel.friendly_name || sel.entity_id}</div>
              <code style={{ fontSize: 12, color: "var(--text-dim)" }}>{sel.entity_id}</code>
            </div>
            <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 13.5 }}>
              Role {sel.suggested_role && <span style={{ color: "var(--text-dim)", fontSize: 12 }}>· AI suggested “{sel.suggested_role}”</span>}
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                {ALL_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </label>
            {(role === "person" || role === "bed" || role === "alarm_time" || role === "focus"
              || role === "steps" || role === "battery") && members.length > 0 && (
              <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 13.5 }}>
                Belongs to <span style={{ color: "var(--text-dim)", fontSize: 12 }}>· links a personal sensor to one member</span>
                <select value={person} onChange={(e) => setPerson(e.target.value)}>
                  <option value="">Shared / nobody</option>
                  {members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
              </label>
            )}
            {err && <span style={{ fontSize: 12.5, color: "var(--danger)" }}>{err}</span>}
            <div style={{ display: "flex", gap: 10 }}>
              <button className="btn btn-primary" disabled={busy} onClick={add}>
                {busy ? "Adding…" : "Add sensor"}
              </button>
              <button className="btn btn-ghost" disabled={busy} onClick={() => setSel(null)}>Back</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

type Pending = { entity_id: string; suggested_role: string; suggested_name: string;
                 friendly_name: string | null; area: string | null };

/** Newly-discovered sensors awaiting approval (detect-then-ask). Approving runs
 *  a scoped AI re-analysis + background retrain; nothing enters the model until
 *  the user says so. */
function PendingSensors({ nonce, onChange }: { nonce: number; onChange: () => void }) {
  const [pending, setPending] = useState<Pending[] | null>(null);
  const [busy, setBusy] = useState("");
  const load = () => fetch("/api/sensors/pending").then(j)
    .then((r) => setPending(r.pending ?? [])).catch(() => setPending([]));
  useEffect(() => { load(); }, [nonce]);
  if (!pending || pending.length === 0) return null;
  const act = async (path: string, ids: string[] | undefined, key: string) => {
    setBusy(key);
    try { await postJSON(path, ids ? { entity_ids: ids } : {}).then(j); } catch { /* refresh */ }
    await load(); onChange(); setBusy("");
  };
  const allIds = pending.map((p) => p.entity_id);
  return (
    <div style={{ padding: "12px 14px", borderRadius: 10,
                  background: "color-mix(in srgb, var(--accent) 10%, transparent)",
                  border: "1px solid var(--accent)", display: "flex",
                  flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <Icon name="sensors" size={16} />
        <strong style={{ fontSize: 14 }}>
          {pending.length} new sensor{pending.length === 1 ? "" : "s"} found
        </strong>
        <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
          Approving runs a quick AI analysis and retrains in the background — nothing
          enters the model until you approve.
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button className="btn btn-primary" disabled={!!busy}
                  onClick={() => act("/api/sensors/pending/approve", undefined, "all")}>
            {busy === "all" ? "Approving…" : "Approve all"}
          </button>
          <button className="btn btn-ghost" disabled={!!busy}
                  onClick={() => act("/api/sensors/pending/dismiss", undefined, "dismiss")}>
            Dismiss all
          </button>
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {pending.map((p) => (
          <div key={p.entity_id} style={{ display: "flex", alignItems: "center", gap: 8,
                       padding: "6px 10px", borderRadius: 8, border: "1px solid var(--border)",
                       background: "var(--surface)" }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>
                {p.friendly_name || p.suggested_name}
                {p.area && <span style={{ fontSize: 12, color: "var(--text-dim)" }}> · {p.area}</span>}
              </div>
              <code style={{ fontSize: 11.5, color: "var(--text-dim)" }}>{p.entity_id}</code>
            </div>
            <RoleBadge role={p.suggested_role} />
            <button className="btn btn-ghost" disabled={!!busy}
                    style={{ minHeight: 28, padding: "2px 9px", fontSize: 12.5 }}
                    onClick={() => act("/api/sensors/pending/approve", [p.entity_id], p.entity_id)}>
              {busy === p.entity_id ? "…" : "Approve"}
            </button>
            <button className="btn btn-ghost" disabled={!!busy} title="Dismiss"
                    style={{ minHeight: 28, padding: "2px 8px", color: "var(--text-dim)" }}
                    onClick={() => act("/api/sensors/pending/dismiss", [p.entity_id], p.entity_id)}>
              ✕
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

type Selection = { entity_id: string; keep: boolean; role: string | null;
                   info_tier: string | null; reliability: string; reason: string };
type FeatureDef = { name: string; transform: string; inputs: string[];
                    info_tier: string | null; rationale: string; expected_separates: string[] };
type SpecResp = { active: boolean; created_by?: string | null; llm_model?: string | null;
                  spec_version?: string; selections?: Selection[]; features?: FeatureDef[] };

const TIER_LABEL: Record<string, string> = {
  T0: "low info", T1: "event gate", T2: "state", T3: "measurement",
  T4: "counter", T5: "slow state",
};
const RELIABILITY_COLOR: Record<string, string> = {
  suspect: "var(--accent)", unusable: "var(--danger)",
};

function TierBadge({ tier }: { tier: string | null }) {
  if (!tier) return null;
  return (
    <span style={{ fontSize: 11, padding: "1px 7px", borderRadius: 99, fontWeight: 600,
                   border: "1px solid var(--border)", color: "var(--text-dim)" }}>
      {TIER_LABEL[tier] ?? tier}
    </span>
  );
}

/** The AI feature architect's design work — what it kept, the information tier it
 *  assigned, any reliability flags, and the executable features with rationales.
 *  Read-only transparency; absent (= default recipes) renders nothing. */
function FeatureSpecPanel({ nonce }: { nonce: number }) {
  const [spec, setSpec] = useState<SpecResp | null>(null);
  const [open, setOpen] = useState(false);
  const [showSel, setShowSel] = useState(false);
  useEffect(() => {
    fetch("/api/feature-spec").then(j).then(setSpec).catch(() => setSpec({ active: false }));
  }, [nonce]);
  if (!spec || !spec.active) return null;
  const feats = spec.features ?? [];
  const sels = spec.selections ?? [];
  const flagged = sels.filter((s) => s.reliability && s.reliability !== "ok");
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden" }}>
      <button onClick={() => setOpen(!open)}
              style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", border: "none",
                       background: open ? "var(--surface-2)" : "transparent", cursor: "pointer",
                       color: "var(--text)", padding: "12px 14px", textAlign: "left" }}>
        <Icon name="activities" size={16} />
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <strong style={{ fontSize: 14 }}>AI feature design</strong>
          <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
            {feats.length} feature{feats.length === 1 ? "" : "s"} · {sels.length} sensor decision{sels.length === 1 ? "" : "s"}
            {flagged.length > 0 && ` · ${flagged.length} flagged`}
            {spec.llm_model ? ` · ${spec.llm_model}` : ""}
          </span>
        </div>
        <span style={{ marginLeft: "auto", fontSize: 12.5, color: "var(--text-dim)" }}>
          {open ? "Hide" : "Show"}
        </span>
      </button>
      {open && (
        <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14,
                      borderTop: "1px solid var(--border)" }}>
          {flagged.length > 0 && (
            <div style={{ fontSize: 13 }}>
              <h4 style={{ margin: "0 0 6px", fontSize: 13.5 }}>Reliability flags</h4>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {flagged.map((s) => (
                  <div key={s.entity_id} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <span style={{ fontSize: 11, padding: "1px 7px", borderRadius: 99, fontWeight: 600,
                                   color: RELIABILITY_COLOR[s.reliability] ?? "var(--text-dim)",
                                   background: `color-mix(in srgb, ${RELIABILITY_COLOR[s.reliability] ?? "var(--text-dim)"} 14%, transparent)` }}>
                      {s.reliability}
                    </span>
                    <code style={{ fontSize: 12 }}>{s.entity_id}</code>
                    {s.reason && <span style={{ fontSize: 12, color: "var(--text-dim)" }}>— {s.reason}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div>
            <h4 style={{ margin: "0 0 6px", fontSize: 13.5 }}>Features the AI designed</h4>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {feats.map((f) => (
                <div key={f.name} style={{ display: "flex", flexDirection: "column", gap: 2,
                             padding: "6px 10px", border: "1px solid var(--border)", borderRadius: 8 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <code style={{ fontSize: 12.5, fontWeight: 600 }}>{f.name}</code>
                    <TierBadge tier={f.info_tier} />
                    <span style={{ fontSize: 11.5, color: "var(--text-dim)" }}>{f.transform}</span>
                    {f.expected_separates?.length > 0 && (
                      <span style={{ fontSize: 11.5, color: "var(--text-dim)" }}>
                        → {f.expected_separates.join(", ")}
                      </span>
                    )}
                  </div>
                  {f.rationale && (
                    <span style={{ fontSize: 12, color: "var(--text-dim)", fontStyle: "italic" }}>
                      “{f.rationale}”
                    </span>
                  )}
                </div>
              ))}
              {feats.length === 0 && (
                <p style={{ fontSize: 12.5, color: "var(--text-dim)", margin: 0 }}>
                  No custom features yet — using the default sensor recipes.
                </p>
              )}
            </div>
          </div>
          <button className="btn btn-ghost" style={{ alignSelf: "flex-start", fontSize: 12.5 }}
                  onClick={() => setShowSel(!showSel)}>
            {showSel ? "Hide" : "Show"} all {sels.length} sensor decisions
          </button>
          {showSel && (
            <div style={{ overflowX: "auto" }}>
              <table style={{ borderCollapse: "collapse", fontSize: 12.5, width: "100%" }}>
                <thead><tr>
                  {["sensor", "role", "tier", "reliability", "why"].map((h) => (
                    <th key={h} style={{ textAlign: "left", padding: 6, color: "var(--text-dim)",
                                         fontWeight: 500 }}>{h}</th>))}
                </tr></thead>
                <tbody>
                  {sels.map((s) => (
                    <tr key={s.entity_id} style={{ borderTop: "1px solid var(--border)",
                                                   opacity: s.keep ? 1 : 0.5 }}>
                      <td style={{ padding: 6 }}><code style={{ fontSize: 11.5 }}>{s.entity_id}</code></td>
                      <td style={{ padding: 6 }}>{s.role ?? "—"}</td>
                      <td style={{ padding: 6 }}>{s.info_tier ? (TIER_LABEL[s.info_tier] ?? s.info_tier) : "—"}</td>
                      <td style={{ padding: 6, color: RELIABILITY_COLOR[s.reliability] ?? "var(--text-dim)" }}>
                        {s.reliability}
                      </td>
                      <td style={{ padding: 6, color: "var(--text-dim)" }}>{s.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Sensors() {
  const [bindings, setBindings] = useState<Binding[] | null>(null);
  const [health, setHealth] = useState<Record<string, Health>>({});
  const [classes, setClasses] = useState<Record<string, number>>({});
  const [persons, setPersons] = useState<Record<string, string>>({});
  const [q, setQ] = useState("");
  const [role, setRole] = useState("all");
  const [statusF, setStatusF] = useState("all");
  const [roomF, setRoomF] = useState("all");
  const [sparkHours, setSparkHours] = useState(168);   // 1h / 24h / 7d sparkline zoom
  const [cleanMsg, setCleanMsg] = useState("");
  const [members, setMembers] = useState<Member[]>([]);
  const [adding, setAdding] = useState<false | "all" | "people">(false);
  const [pendingNonce, setPendingNonce] = useState(0);
  const load = () => fetch("/api/bindings").then(j).then(setBindings).catch(() => setBindings([]));
  const loadHealth = (hours: number) =>
    fetch(`/api/bindings/health?hours=${hours}`).then(j).then((h) => {
      setHealth(Object.fromEntries((h.bindings ?? []).map((b: Health) => [b.name, b])));
      setClasses(h.classes ?? {});
      setMembers(h.members ?? []);
    }).catch(() => {});
  const reload = () => { load(); loadHealth(sparkHours); };
  const relink = async () => {
    setCleanMsg("Re-linking household to home/away sensors…");
    try {
      const r = await postJSON("/api/household/relink", {}).then(j);
      if (r.linked) {
        setCleanMsg(`Linked ${r.linked} member${r.linked === 1 ? "" : "s"} to a home/away sensor.`);
      } else if (!r.candidates) {
        setCleanMsg("No person.* or device_tracker home/away entity exists in Home Assistant. "
          + "Add HA's ‘Person’ integration (Settings → People), then Rescan HA.");
      } else {
        setCleanMsg(`Couldn't auto-match ${(r.unlinked ?? []).join(", ") || "everyone"} — `
          + "opening the picker so you can link them by hand.");
        setAdding("people");
      }
      reload();
    } catch { setCleanMsg("Re-link failed — is Home Assistant connected?"); }
  };
  useEffect(() => {
    load();
    fetch("/api/persons").then(j)
      .then((ps: { id: string; name: string }[]) =>
        setPersons(Object.fromEntries(ps.map((p) => [p.id, p.name]))))
      .catch(() => {});
  }, []);
  useEffect(() => { loadHealth(sparkHours); }, [sparkHours]);
  const roles = useMemo(
    () => Array.from(new Set((bindings ?? []).map((b) => b.role))).sort(),
    [bindings]);
  const rooms = useMemo(
    () => Array.from(new Set((bindings ?? []).map((b) => b.room).filter(Boolean) as string[])).sort(),
    [bindings]);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (bindings ?? [])
      .filter((b) => role === "all" || b.role === role)
      .filter((b) => roomF === "all" || b.room === roomF)
      .filter((b) => statusF === "all" || health[b.name]?.status === statusF)
      .filter((b) => !needle
        || b.entity_id.toLowerCase().includes(needle)
        || b.name.toLowerCase().includes(needle)
        || (b.room ?? "").toLowerCase().includes(needle))
      .sort((a, b) => a.role.localeCompare(b.role) || a.name.localeCompare(b.name));
  }, [bindings, q, role, roomF, statusF, health]);
  const emptyCount = useMemo(
    () => (bindings ?? []).filter((b) => b.enabled && b.role !== "person"
      && health[b.name]?.status === "no_data").length,
    [bindings, health]);
  const rescan = async () => {
    setCleanMsg("Rescanning Home Assistant…");
    try {
      const r = await fetch("/api/ha/sync", { method: "POST" }).then(j);
      setCleanMsg(r.pending || r.rooms_updated
        ? `${r.pending || 0} new sensor${r.pending === 1 ? "" : "s"} to review, updated ${r.rooms_updated} room${r.rooms_updated === 1 ? "" : "s"}.`
        : "Up to date — no new sensors or room changes.");
      setPendingNonce((n) => n + 1);
      load();
    } catch { setCleanMsg("Rescan failed — is Home Assistant connected?"); }
  };
  const pruneEmpty = async () => {
    if (!window.confirm("Disable every bound sensor with no data in the last 7 days? They add empty columns to the model. You can re-enable any of them here once they start reporting.")) return;
    const r = await fetch("/api/bindings/prune-empty", { method: "POST" }).then(j);
    setCleanMsg(`Disabled ${r.disabled} empty sensor${r.disabled === 1 ? "" : "s"}.`);
    load();
  };
  const cleanup = async () => {
    if (!window.confirm("Remove bindings that fail the physics check (buttons, scripts, configuration entities…)? Person bindings are always kept.")) return;
    const r = await fetch("/api/bindings/cleanup", { method: "POST" }).then(j);
    setCleanMsg(`Removed ${r.removed} junk binding${r.removed === 1 ? "" : "s"}.`);
    load();
  };
  const tidyRooms = async () => {
    setCleanMsg("Tidying room names…");
    try {
      const r = await fetch("/api/rooms/tidy", { method: "POST" }).then(j);
      setCleanMsg(r.changed
        ? `Merged duplicate rooms — ${r.changed} sensor${r.changed === 1 ? "" : "s"} reassigned. Now: ${r.rooms.join(", ")}.`
        : "Rooms already tidy — no duplicates found.");
      load();
    } catch { setCleanMsg("Tidy failed — check logs."); }
  };
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 860 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="sensors" size={22} />
        <h2 style={{ margin: 0 }}>Sensors</h2>
        <span style={{ fontSize: 13.5, color: "var(--text-dim)" }}>
          {bindings ? `${bindings.length} bound · ${bindings.filter((b) => b.enabled).length} active` : ""}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          {emptyCount > 0 && (
            <button className="btn btn-secondary" onClick={pruneEmpty}>
              Disable empty ({emptyCount})
            </button>
          )}
          <button className="btn btn-primary" onClick={() => setAdding("all")}>+ Add sensor</button>
          <button className="btn btn-secondary" onClick={rescan}>Rescan HA</button>
          <button className="btn btn-secondary" onClick={tidyRooms}>Tidy rooms</button>
          <button className="btn btn-secondary" onClick={cleanup}>Clean up junk</button>
        </div>
      </div>

      {members.some((m) => !m.person_alive) && (() => {
        const unlinked = members.filter((m) => !m.has_person);
        const stale = members.filter((m) => m.has_person && !m.person_alive);
        return (
          <div style={{ padding: "10px 14px", borderRadius: 10, fontSize: 13.5,
                        display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
                        background: "color-mix(in srgb, var(--danger) 12%, transparent)",
                        border: "1px solid var(--danger)" }}>
            <span style={{ flex: 1, minWidth: 220 }}>
              {unlinked.length > 0 && (
                <><strong>{unlinked.map((m) => m.name).join(", ")}</strong>{" "}
                {unlinked.length === 1 ? "has" : "have"} no <code>person.*</code> home/away sensor
                linked — Hearth can't predict <em>away</em> for {unlinked.length === 1 ? "them" : "them"} yet.{" "}</>
              )}
              {stale.length > 0 && (
                <><strong>{stale.map((m) => m.name).join(", ")}</strong>{" "}
                {stale.length === 1 ? "is" : "are"} linked, but no recent data is arriving — check the
                {" "}<code>person.*</code> entity is logging to InfluxDB (HA InfluxDB integration include list).</>
              )}
            </span>
            {unlinked.length > 0 && (
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn btn-secondary" onClick={relink}>Auto-link with AI</button>
                <button className="btn btn-ghost" onClick={() => setAdding("people")}>Link manually</button>
              </div>
            )}
          </div>
        );
      })()}
      <PendingSensors nonce={pendingNonce} onChange={reload} />
      <FeatureSpecPanel nonce={pendingNonce} />
      {adding && <AddSensor members={members} initialPeople={adding === "people"}
                            onClose={() => setAdding(false)} onAdded={reload} />}
      <p style={{ margin: 0, fontSize: 14, color: "var(--text-dim)", maxWidth: 640 }}>
        Every Home Assistant entity Hearth listens to, and the <em>role</em> it was given —
        roles are what make features household-independent. Disable anything that shouldn't
        influence predictions; changes apply at the next feature build.
      </p>
      {cleanMsg && <p style={{ margin: 0, fontSize: 13.5, color: "var(--accent)" }}>{cleanMsg}</p>}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <input placeholder="Search entity, name, room…" value={q}
               onChange={(e) => setQ(e.target.value)} style={{ width: "100%", boxSizing: "border-box" }} />
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="all">All roles</option>
            {roles.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          {rooms.length > 0 && (
            <select value={roomF} onChange={(e) => setRoomF(e.target.value)}>
              <option value="all">All rooms</option>
              {rooms.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          )}
          <select value={statusF} onChange={(e) => setStatusF(e.target.value)}>
            <option value="all">Any signal</option>
            <option value="alive">Live</option>
            <option value="constant">No variation</option>
            <option value="no_data">No data</option>
          </select>
          <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-dim)" }}>Sparkline:</span>
          <div style={{ display: "inline-flex", height: 34, border: "1px solid var(--border)",
                        borderRadius: 8, overflow: "hidden" }} title="Sparkline time window">
            {[[1, "1H"], [24, "24H"], [168, "7D"]].map(([h, lbl]) => (
              <button key={h as number} onClick={() => setSparkHours(h as number)}
                      style={{ display: "flex", alignItems: "center", justifyContent: "center",
                               border: "none", padding: "0 12px", cursor: "pointer", lineHeight: 1,
                               fontSize: 12.5, fontWeight: sparkHours === h ? 600 : 400,
                               background: sparkHours === h ? "var(--accent)" : "transparent",
                               color: sparkHours === h ? "#fff" : "var(--text-dim)" }}>
                {lbl}
              </button>
            ))}
          </div>
        </div>
      </div>
      {bindings === null && <p style={{ color: "var(--text-dim)" }}>Loading…</p>}
      {bindings !== null && shown.length === 0 && (
        <p style={{ color: "var(--text-dim)" }}>Nothing matches.</p>
      )}
      {Object.keys(classes).length > 0 && !classes.away && (
        <div style={{ padding: "10px 14px", borderRadius: 10, fontSize: 13,
                      background: "color-mix(in srgb, var(--danger) 12%, transparent)",
                      border: "1px solid var(--danger)" }}>
          No <strong>away</strong> windows in the training data — the model can't predict a
          state it has never seen. Check that a presence sensor below is <em>alive</em> while
          you're out, or tap “away” on the dashboard a few times to teach it.
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {shown.map((b) => <BindingRow key={b.id} b={b} persons={persons}
                                      health={health[b.name]} onChange={load} />)}
      </div>
    </section>
  );
}

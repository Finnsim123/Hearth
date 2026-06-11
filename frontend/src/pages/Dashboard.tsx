/**
 * Dashboard — two lives (docs/UI_SPEC.md §2):
 *   cold start: "Hearth is learning your home" journey card + milestones
 *   steady:     avatar hero cards · today ribbon · needs-you · trust · pulse
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import Avatar from "../components/Avatar";
import { Icon, type IconName } from "../icons";
import { packSiblings, enclose, type C } from "../bubbles";

type Pred = { time: string; predicted: string; smoothed: string; confidence: number;
              model_version: string; probs: Record<string, number>
  evidence?: number | null; parent?: string | null;
  explanation?: [string, number][];
};
type Person = { id: string; name: string; avatar?: string | null; enabled: boolean };
type Journey = { recording_since: string | null; days: number; events_24h: number;
                 sensors_bound: number; milestones: { recording: boolean; patterns: boolean; model: boolean } };
type Question = { id: number; person_id: string; window_ts: string; predicted: string; confidence: number };

const ACT: Record<string, string> = {
  sleeping: "var(--act-sleeping)", away: "var(--act-away)", home: "var(--act-home)",
  cooking: "var(--act-cooking)", eating: "var(--act-eating)", movie: "var(--act-media)",
  working: "var(--act-working)",
};
const color = (a: string) => ACT[a] ?? "var(--text-dim)";
const icon = (a: string): IconName =>
  (["sleeping", "away", "home", "cooking", "eating", "movie", "working"].includes(a) ? (a as IconName) : "activities");
const t = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** Hero: the member's avatar "inside" the activity scene — face on the bed. */
function Scene({ activity, person }: { activity: string; person: Person }) {
  return (
    <div style={{ position: "relative", width: 72, height: 72, flexShrink: 0,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  background: `color-mix(in srgb, ${color(activity)} 12%, transparent)`,
                  border: `1px solid color-mix(in srgb, ${color(activity)} 40%, transparent)`,
                  borderRadius: "var(--radius-card)", color: color(activity) }}>
      <Icon name={icon(activity)} size={36} />
      <span style={{ position: "absolute", right: -8, bottom: -8 }}>
        <Avatar name={person.name} value={person.avatar} size={32} />
      </span>
    </div>
  );
}

function Conf({ v }: { v: number }) {
  return (
    <span className={`conf${v < 0.75 ? " low" : ""}`} style={{ display: "inline-block", width: 72 }}
          title={`${Math.round(v * 100)}% confident`}>
      <span style={{ width: `${Math.round(v * 100)}%` }} />
    </span>
  );
}

const CORRECTION_SLUGS = ["sleeping", "away", "home", "cooking", "eating", "movie", "working"];

function Ribbon({ preds, ruleBased, personId }: { preds: Pred[]; ruleBased: boolean; personId: string }) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Pred | null>(null);
  if (!preds.length) return null;
  // fixed 48-slot 24h grid: both cards always time-aligned; gaps stay dim
  const slotMs = 30 * 60_000;
  const nowSlot = Math.floor(Date.now() / slotMs) * slotMs;
  const byTs = new Map(preds.map((p) => [Math.floor(new Date(p.time).getTime() / slotMs) * slotMs, p]));
  const ordered: (Pred | null)[] = Array.from({ length: 48 }, (_, i) =>
    byTs.get(nowSlot - (47 - i) * slotMs) ?? null);
  const correct = async (slug: string) => {
    if (!selected) return;
    const start = new Date(selected.time);
    const end = new Date(start.getTime() + 30 * 60_000);
    await fetch("/api/labels/bulk", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person_id: personId, activity: slug, source: "ribbon",
                             start: start.toISOString(), end: end.toISOString() }) });
    setSelected(null);
    qc.invalidateQueries({ queryKey: ["predictions"] });
  };
  return (
    <div>
      <div style={{ display: "flex", gap: 1, height: 16, borderRadius: 4, overflow: "hidden" }}>
        {ordered.map((p, i) => p === null ? (
          <span key={i} style={{ flex: 1, background: "var(--surface-2)" }} />
        ) : (
          <span key={i} role="button"
                title={`${t(p.time)} · ${p.smoothed} (${Math.round(p.confidence * 100)}%) — tap to correct`}
                onClick={() => setSelected(selected?.time === p.time ? null : p)}
                style={{ flex: 1, background: color(p.smoothed), cursor: "pointer",
                         opacity: 0.35 + 0.65 * p.confidence,
                         outline: selected?.time === p.time ? "2px solid var(--accent)" : "none" }} />
        ))}
      </div>
      {selected && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginTop: 8 }}>
          <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
            {t(selected.time)} was actually:
          </span>
          {CORRECTION_SLUGS.map((slug) => (
            <button key={slug} className="btn btn-secondary"
                    style={{ minHeight: 30, padding: "4px 10px", fontSize: 12.5 }}
                    onClick={() => correct(slug)}>
              {slug.replace("_", " ")}
            </button>
          ))}
        </div>
      )}
      {ruleBased && (
        <p style={{ margin: "6px 0 0", fontSize: 12, color: "var(--text-dim)" }}>
          <Icon name="info" size={12} /> rule-based until the first model is trained
        </p>
      )}
    </div>
  );
}

/** What the current prediction rests on — SHAP signals for models, the
 *  fired rule for the rules fallback, plus the evidence-strength chip. */
function BasedOn({ latest }: { latest: Pred }) {
  const ex = latest.explanation ?? [];
  const ev = latest.evidence;
  const ruleBased = latest.model_version?.startsWith("rules");
  if (ex.length === 0 && (ev === null || ev === undefined)) return null;
  const evLabel = ev === null || ev === undefined ? null
    : ev >= 0.5 ? ["strong", "var(--ok, #34D399)"]
    : ev >= 0.25 ? ["mixed", "var(--accent)"]
    : ["weak", "var(--danger)"];
  const nice = (f: string) => f.replace(/_/g, " ");
  return (
    <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10,
                  display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ fontSize: 11.5, color: "var(--text-dim)", fontWeight: 600,
                     letterSpacing: "0.04em", textTransform: "uppercase" }}>
        Based on
      </span>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        {evLabel && (
          <span title={`${Math.round((ev as number) * 100)}% of this prediction rests on direct sensors (bed, presence, doors…) — weak predictions are held below the ask threshold`}
                style={{ fontSize: 11.5, padding: "2px 9px", borderRadius: 99, fontWeight: 600,
                         color: evLabel[1],
                         background: `color-mix(in srgb, ${evLabel[1]} 14%, transparent)` }}>
            {evLabel[0]} evidence
          </span>
        )}
        {ruleBased && ex[0] ? (
          <code style={{ fontSize: 11.5, color: "var(--text-dim)", overflowWrap: "anywhere" }}>
            {ex[0][0]}
          </code>
        ) : (
          ex.slice(0, 3).map(([feat, v]) => (
            <span key={feat}
                  title={`SHAP ${v >= 0 ? "+" : ""}${v.toFixed(3)} toward “${latest.predicted}”`}
                  style={{ fontSize: 11.5, padding: "2px 9px", borderRadius: 99,
                           background: "var(--surface-2)", border: "1px solid var(--border)",
                           color: "var(--text-dim)" }}>
              {nice(feat)} {v >= 0 ? "↑" : "↓"}
            </span>
          ))
        )}
        {latest.parent && (
          <span style={{ fontSize: 11.5, color: "var(--text-dim)" }}>
            · within {latest.parent}
          </span>
        )}
      </div>
    </div>
  );
}

function PersonCard({ person, preds }: { person: Person; preds: Pred[] }) {
  const latest = preds[0];
  const ruleBased = latest?.model_version?.startsWith("rules");
  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 14, flex: 1, minWidth: 280 }}>
      {latest ? (
        <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
          <Scene activity={latest.smoothed} person={person} />
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontWeight: 500 }}>{person.name}</span>
            <span style={{ color: color(latest.smoothed), fontWeight: 500, textTransform: "capitalize" }}>
              {latest.smoothed.replace("_", " ")}
            </span>
            <span style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, color: "var(--text-dim)" }}>
              <Conf v={latest.confidence} /> {Math.round(latest.confidence * 100)}% · since {t(latest.time)}
            </span>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <Avatar name={person.name} value={person.avatar} size={40} />
          <span style={{ fontWeight: 500 }}>{person.name}</span>
          <span style={{ fontSize: 13, color: "var(--text-dim)" }}>no predictions yet</span>
        </div>
      )}
      <Ribbon preds={preds} ruleBased={!!ruleBased} personId={person.id} />
      {latest && <BasedOn latest={latest} />}
    </div>
  );
}

function JourneyCard({ j }: { j: Journey }) {
  const milestones = [
    { done: j.milestones.recording || j.events_24h > 0, icon: "sensors" as IconName,
      label: "Recording", detail: `${j.events_24h.toLocaleString()} events in 24 h from ${j.sensors_bound} sensors` },
    { done: j.milestones.patterns, icon: "patterns" as IconName,
      label: "First patterns", detail: j.milestones.patterns ? "Found — go name them!" : "expected around day 3" },
    { done: j.milestones.model, icon: "models" as IconName,
      label: "First model", detail: j.milestones.model ? "Live!" : "possible from day 7 — or import history to skip ahead" },
  ];
  const pct = Math.min(100, Math.round((j.days / 7) * 100));
  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 16, padding: 24 }}>
      <div>
        <h2 style={{ margin: "0 0 4px" }}>Hearth is learning your home</h2>
        <p style={{ margin: 0, color: "var(--text-dim)", fontSize: 14.5 }}>
          All set up — now go live your normal life. Day {Math.floor(j.days)} of recording;
          we'll send a notification to your phone the moment predictions go live.
        </p>
      </div>
      <div className="conf" style={{ height: 6 }}><span style={{ width: `${pct}%` }} /></div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
        {milestones.map((m) => (
          <div key={m.label} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
            <span style={{ color: m.done ? "var(--ok)" : "var(--text-dim)", marginTop: 2 }}>
              <Icon name={m.done ? "check" : m.icon} size={18} />
            </span>
            <span>
              <span style={{ display: "block", fontWeight: 500, fontSize: 14 }}>{m.label}</span>
              <span style={{ display: "block", fontSize: 13, color: "var(--text-dim)" }}>{m.detail}</span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function NeedsYou({ questions }: { questions: Question[] }) {
  if (!questions.length) return null;
  return (
    <section>
      <p className="label" style={{ margin: "0 0 8px" }}>Needs you</p>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {questions.slice(0, 3).map((q) => (
          <div key={q.id} className="card" style={{ display: "flex", gap: 12, alignItems: "center", padding: "12px 16px" }}>
            <Icon name="question" size={18} />
            <span style={{ fontSize: 14 }}>
              {t(q.window_ts)} — was <strong style={{ fontWeight: 500 }}>{q.person_id}</strong>{" "}
              {q.predicted.replace("_", " ")}? ({Math.round(q.confidence * 100)}% sure)
            </span>
            <a href="/inbox" className="btn btn-secondary" style={{ marginLeft: "auto", textDecoration: "none", fontSize: 13 }}>
              Answer
            </a>
          </div>
        ))}
      </div>
    </section>
  );
}

function Pulse({ j, hasTsdb }: { j?: Journey; hasTsdb: boolean }) {
  const dot = (ok: boolean) => (
    <span style={{ width: 8, height: 8, borderRadius: "50%", display: "inline-block",
                   background: ok ? "var(--ok)" : "var(--danger)" }} />
  );
  return (
    <footer style={{ display: "flex", gap: 24, alignItems: "center", fontSize: 12.5,
                     color: "var(--text-dim)", borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      <span style={{ display: "flex", gap: 6, alignItems: "center" }}>{dot(hasTsdb)} database</span>
      <span style={{ display: "flex", gap: 6, alignItems: "center" }}>{dot((j?.events_24h ?? 0) > 0)} ingest · {j?.events_24h?.toLocaleString() ?? 0} events/24 h</span>
      <span style={{ display: "flex", gap: 6, alignItems: "center" }}>{dot((j?.sensors_bound ?? 0) > 0)} {j?.sensors_bound ?? 0} sensors bound</span>
    </footer>
  );
}

type Health = { name: string; status: string; role: string; room: string | null;
                tier: number; per_day?: number };

const TIER_META: Record<number, [string, string]> = {
  1: ["direct", "#34D399"],      // bed, presence, person, media, door
  2: ["behavioral", "#F59E0B"],  // power, lights, steps  (ember/amber)
  3: ["ambient", "#F472B6"],     // temp, CO2, humidity, battery
};
const tierColor = (t: number) => TIER_META[t]?.[1] ?? TIER_META[2][1];

type Leaf = C & { tier: number; name: string; role: string; per_day: number };
type Room = { x: number; y: number; r: number; key: string; label: string;
              leaves: Leaf[]; total: number; sparse: boolean; tiers: Record<number, number> };

// Merge key for case / separator variants ("Living_room" == "livingroom").
const roomKey = (room: string | null) =>
  (room || "").toLowerCase().replace(/[^a-z0-9]/g, "") || "unassigned";
// Prettiest display from a set of original spellings: prefer the one that
// splits into the most words, then title-case it ("Living_room" → "Living Room").
const prettyRoom = (originals: string[]) => {
  const rep = originals.reduce((best, o) =>
    o.split(/[^a-zA-Z0-9]+/).filter(Boolean).length >
    best.split(/[^a-zA-Z0-9]+/).filter(Boolean).length ? o : best, originals[0]);
  return rep.split(/[^a-zA-Z0-9]+/).filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1)).join(" ") || "Unassigned";
};

const W = 1040;            // layout width in svg units (scales to card width)
const GAP_X = 20, GAP_Y = 16, LABEL_H = 26;

/** Pack each room's sensors into a disc, then lay the room-discs out in rows
 *  that fill the available width (no central clump). Mutates positions. */
function layoutRooms(rows: Health[]): { rooms: Room[]; height: number } {
  const groups: Record<string, { label: string; list: Health[]; originals: Set<string> }> = {};
  for (const b of rows) {
    const k = roomKey(b.room);
    (groups[k] ??= { label: "", list: [], originals: new Set() });
    groups[k].list.push(b);
    groups[k].originals.add(b.room || "Unassigned");
  }
  const maxPD = Math.max(1, ...rows.map((b) => b.per_day ?? 0));
  const leafR = (b: Health) => 5 + Math.sqrt((b.per_day ?? 0) / maxPD) * 9;   // 5–14px

  const rooms: Room[] = Object.entries(groups).map(([key, g]) => {
    const leaves: Leaf[] = g.list.map((b) => ({
      x: 0, y: 0, r: leafR(b), tier: b.tier || 2, name: b.name,
      role: b.role, per_day: b.per_day ?? 0 }));
    packSiblings(leaves);
    const e = enclose(leaves);
    for (const l of leaves) { l.x -= e.x; l.y -= e.y; }
    const tiers: Record<number, number> = { 1: 0, 2: 0, 3: 0 };
    for (const l of leaves) tiers[l.tier] = (tiers[l.tier] || 0) + 1;
    return { x: 0, y: 0, r: e.r + 7, key, label: prettyRoom([...g.originals]),
             leaves, total: leaves.length, sparse: leaves.length <= 1, tiers };
  }).sort((a, b) => b.r - a.r);

  // shelf-pack rooms into centered rows that fill the width
  const rowsOf: Room[][] = [];
  let row: Room[] = [], x = 0;
  for (const rm of rooms) {
    if (row.length && x + 2 * rm.r > W) { rowsOf.push(row); row = []; x = 0; }
    rm.x = x + rm.r; x += 2 * rm.r + GAP_X; row.push(rm);
  }
  if (row.length) rowsOf.push(row);

  let y = GAP_Y;
  for (const r of rowsOf) {
    const rowR = Math.max(...r.map((rm) => rm.r));
    const rowW = r.reduce((s, rm) => s + 2 * rm.r, 0) + GAP_X * (r.length - 1);
    const off = (W - rowW) / 2;
    for (const rm of r) { rm.x += off; rm.y = y + rowR; }
    y += 2 * rowR + LABEL_H + GAP_Y;
  }
  return { rooms, height: y };
}

/** Sensor coverage bubble chart: rooms laid out across the width, each holding
 *  one dot per live sensor (size = how often it fires, colour = evidence tier).
 *  Click a room to list its sensors. Small/all-pink rooms = barely sensed. */
function SensorCoverage() {
  const [rows, setRows] = useState<Health[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  useEffect(() => {
    fetch("/api/bindings/health").then((r) => r.json())
      .then((h) => setRows((h.bindings ?? []).filter((b: Health) => b.status === "alive")))
      .catch(() => setRows([]));
  }, []);
  if (!rows || rows.length === 0) return null;

  const { rooms, height } = layoutRooms(rows);
  const selected = rooms.find((r) => r.key === sel) || null;

  return (
    <section className="card" style={{ padding: 20 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <Icon name="sensors" size={18} />
        <h3 style={{ margin: 0, fontSize: 16 }}>Sensor coverage</h3>
      </div>
      <p style={{ margin: "0 0 12px", fontSize: 13, color: "var(--text-dim)" }}>
        Each cluster is a room; each dot is a live sensor — bigger dots fire more often,
        colour is how directly it senses people. Click a room to see its sensors.
      </p>
      <svg viewBox={`0 0 ${W} ${height}`} role="img"
           style={{ width: "100%", display: "block" }}>
        {rooms.map((rm) => {
          const active = rm.key === sel || rm.key === hover;
          const dim = (sel || hover) && !active;
          return (
            <g key={rm.key} onClick={() => setSel(sel === rm.key ? null : rm.key)}
               onMouseEnter={() => setHover(rm.key)} onMouseLeave={() => setHover(null)}
               style={{ cursor: "pointer", opacity: dim ? 0.45 : 1, transition: "opacity .12s" }}>
              <title>{rm.label} — {rm.total} sensor{rm.total === 1 ? "" : "s"}</title>
              <circle cx={rm.x} cy={rm.y} r={rm.r}
                      fill="var(--surface-2)" fillOpacity={active ? 0.55 : 0.32}
                      stroke={rm.sparse ? "var(--danger)" : active ? "var(--accent)" : "var(--border)"}
                      strokeWidth={active ? 2 : rm.sparse ? 1.8 : 1} />
              {rm.leaves.map((l, i) => (
                <circle key={i} cx={rm.x + l.x} cy={rm.y + l.y} r={l.r}
                        fill={tierColor(l.tier)} fillOpacity={0.92}
                        stroke="var(--surface)" strokeWidth={0.6} />
              ))}
              <text x={rm.x} y={rm.y + rm.r + 16} textAnchor="middle"
                    fontSize={12.5} fill={rm.sparse ? "var(--danger)" : "var(--text-dim)"}
                    style={{ pointerEvents: "none" }}>
                {rm.label}
                <tspan fill={rm.sparse ? "var(--danger)" : "var(--text)"} fontWeight={600}> {rm.total}</tspan>
              </text>
            </g>
          );
        })}
      </svg>
      {selected && (
        <div style={{ marginTop: 4, padding: "12px 14px", borderRadius: 10,
                      background: "var(--surface-2)", border: "1px solid var(--border)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <strong style={{ fontSize: 14 }}>{selected.label}</strong>
            <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
              {selected.total} live sensor{selected.total === 1 ? "" : "s"}
            </span>
            <button className="btn btn-ghost" onClick={() => setSel(null)}
                    style={{ marginLeft: "auto", fontSize: 12 }}>Close</button>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {[...selected.leaves].sort((a, b) => b.per_day - a.per_day).map((l) => (
              <span key={l.name} title={`${l.role} · ~${l.per_day.toLocaleString()}/day`}
                    style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12,
                             padding: "3px 9px", borderRadius: 99, background: "var(--surface)",
                             border: "1px solid var(--border)" }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: tierColor(l.tier) }} />
                {l.name}
                <span style={{ color: "var(--text-dim)" }}>{l.role}</span>
              </span>
            ))}
          </div>
        </div>
      )}
      <div style={{ display: "flex", gap: 16, marginTop: 10, fontSize: 12, color: "var(--text-dim)",
                    justifyContent: "center" }}>
        {[1, 2, 3].map((tier) => (
          <span key={tier} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: tierColor(tier) }} />
            {TIER_META[tier][0]}
          </span>
        ))}
      </div>
    </section>
  );
}

export default function Dashboard() {
  const preds = useQuery<{ persons: Record<string, Pred[]>; note?: string }>({
    queryKey: ["predictions"],
    queryFn: () => fetch("/api/predictions?hours=24").then((r) => r.json()),
    refetchInterval: 60_000,
  });
  const persons = useQuery<Person[]>({
    queryKey: ["persons"], queryFn: () => fetch("/api/persons").then((r) => r.json()),
  });
  const journey = useQuery<Journey>({
    queryKey: ["journey"], queryFn: () => fetch("/api/journey").then((r) => r.json()),
    refetchInterval: 300_000,
  });
  const inbox = useQuery<Question[]>({
    queryKey: ["inbox"], queryFn: () => fetch("/api/inbox").then((r) => r.json()),
    refetchInterval: 120_000,
  });

  const byPerson = preds.data?.persons ?? {};
  const members = (persons.data ?? []).filter((p) => p.enabled);
  const anyPredictions = Object.values(byPerson).some((l) => l.length > 0);
  const coldStart = !anyPredictions;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {coldStart && journey.data ? (
        <JourneyCard j={journey.data} />
      ) : (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          {members.map((p) => <PersonCard key={p.id} person={p} preds={byPerson[p.id] ?? []} />)}
        </div>
      )}
      <NeedsYou questions={inbox.data ?? []} />
      {!coldStart && <SensorCoverage />}
      <Pulse j={journey.data} hasTsdb={!preds.data?.note} />
    </div>
  );
}

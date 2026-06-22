/**
 * Dashboard — two lives (docs/UI_SPEC.md §2):
 *   cold start: "Hearth is learning your home" journey card + milestones
 *   steady:     avatar hero cards · today ribbon · needs-you · trust · pulse
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Avatar from "../components/Avatar";
import Card from "../components/Card";
import { Icon, type IconName } from "../icons";
import { packSiblings, enclose, type C } from "../bubbles";
import { useIsMobile } from "../useMedia";
import FlowMap from "../components/FlowMap";

type Pred = { time: string; predicted: string; smoothed: string; confidence: number;
              model_version: string; probs: Record<string, number>
  evidence?: number | null; parent?: string | null;
  explanation?: [string, number][];
};
type Person = { id: string; name: string; avatar?: string | null; enabled: boolean };
type Journey = { recording_since: string | null; days: number; events_24h: number;
                 sensors_bound: number; milestones: { recording: boolean; patterns: boolean; model: boolean } };
type Question = { id: number; person_id: string; window_ts: string; predicted: string;
                  confidence: number; alternatives: string[] };
type Activity = { slug: string; name: string; enabled: boolean };

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

const WK = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

/** A week of activity: 7 day-rows × 24 hour-columns, each cell the dominant
 *  predicted activity that hour. Compact + tappable — tap a cell to correct
 *  that hour (replaces the old 24h ribbon). Fills in as the days roll by. */
function WeekHeatmap({ preds, personId, ruleBased, unreliable }:
                     { preds: Pred[]; personId: string; ruleBased: boolean; unreliable?: Set<string> }) {
  const qc = useQueryClient();
  // selection is a SET of cell start-times (epoch ms) so a drag can mark many
  // hours at once; click = one cell, click-drag = a streak.
  const [sel, setSel] = useState<Set<number>>(new Set());
  const painting = useRef(false);
  if (!preds.length) return null;
  const now = new Date();
  const days: { key: string; label: string; date: Date }[] = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date(now); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - i);
    days.push({ key: d.toDateString(), label: WK[d.getDay()], date: new Date(d) });
  }
  const grid: Record<string, Record<number, Record<string, number>>> = {};
  for (const p of preds) {
    const d = new Date(p.time);
    const key = d.toDateString(), h = d.getHours();
    const s = p.smoothed || p.predicted;
    ((grid[key] ??= {})[h] ??= {});
    grid[key][h][s] = (grid[key][h][s] || 0) + 1;
  }
  const dom = (key: string, h: number): string | null => {
    const c = grid[key]?.[h];
    return c ? Object.entries(c).sort((a, b) => b[1] - a[1])[0][0] : null;
  };
  const addCell = (t: number) => setSel((s) => { const n = new Set(s); n.add(t); return n; });
  const correct = async (slug: string) => {
    if (!sel.size) return;
    // collapse the selected hours into contiguous runs → one bulk call each
    // (usually just one). Each cell is a 1h window; the API labels every 30-min
    // window in [start, end).
    const times = [...sel].sort((a, b) => a - b);
    const runs: [number, number][] = [];
    for (const t of times) {
      const last = runs[runs.length - 1];
      if (last && t === last[1] + 3600_000) last[1] = t;
      else runs.push([t, t]);
    }
    await Promise.all(runs.map(([s0, e0]) =>
      fetch("/api/labels/bulk", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ person_id: personId, activity: slug, source: "heatmap",
          start: new Date(s0).toISOString(), end: new Date(e0 + 3600_000).toISOString() }) })));
    setSel(new Set());
    qc.invalidateQueries({ queryKey: ["predictions"] });
    qc.invalidateQueries({ queryKey: ["predictions_week"] });
  };
  const cells: JSX.Element[] = [<div key="corner" />];
  HOURS.forEach((h) => cells.push(
    <div key={"hh" + h} style={{ fontSize: 8, color: "var(--text-dim)", textAlign: "center", lineHeight: "9px" }}>
      {h % 6 === 0 ? String(h).padStart(2, "0") : ""}
    </div>));
  days.forEach((d) => {
    cells.push(<div key={"dl" + d.key} style={{ fontSize: 10, color: "var(--text-dim)",
                                                lineHeight: "14px", paddingRight: 4 }}>{d.label}</div>);
    HOURS.forEach((h) => {
      const st = dom(d.key, h);
      const cellTime = new Date(d.date); cellTime.setHours(h);
      const future = cellTime > now;
      const t = +cellTime;
      const on = sel.has(t);
      cells.push(
        <div key={`c${d.key}-${h}`}
             onPointerDown={future ? undefined : (e) => {
               (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
               painting.current = true; setSel(new Set([t])); }}
             onPointerEnter={future ? undefined : () => { if (painting.current) addCell(t); }}
             title={`${d.label} ${String(h).padStart(2, "0")}:00 — ${st ? st.replace("_", " ") : "no data"}`}
             style={{ height: 14, borderRadius: 2, cursor: future ? "default" : "pointer",
                      background: st ? color(st) : "var(--surface-2)",
                      opacity: future ? 0.3 : (st && unreliable?.has(st) ? 0.4 : 1),
                      touchAction: "none",
                      outline: on ? "2px solid var(--accent)" : "none", outlineOffset: -1 }} />);
    });
  });
  const present = Array.from(new Set(preds.map((p) => p.smoothed || p.predicted)));
  return (
    <div>
      <p className="label" style={{ margin: "0 0 6px" }}>This week · tap or drag across cells, then label</p>
      <div style={{ display: "grid", gridTemplateColumns: "32px repeat(24, 1fr)", gap: 2,
                    touchAction: "none" }}
           onPointerUp={() => { painting.current = false; }}
           onPointerLeave={() => { painting.current = false; }}>
        {cells}
      </div>
      {sel.size > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginTop: 8 }}>
          <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
            {sel.size === 1 ? "1 hour" : `${sel.size} hours`} selected — mark as:
          </span>
          {CORRECTION_SLUGS.map((slug) => (
            <button key={slug} className="btn btn-secondary"
                    style={{ minHeight: 30, padding: "4px 10px", fontSize: 12.5 }}
                    onClick={() => correct(slug)}>{slug.replace("_", " ")}</button>
          ))}
          <button className="btn btn-ghost" style={{ minHeight: 30, padding: "4px 10px", fontSize: 12.5 }}
                  onClick={() => setSel(new Set())}>Clear</button>
        </div>
      )}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 8, alignItems: "center" }}>
        {present.map((s) => (
          <span key={s} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11,
                                 color: "var(--text-dim)", textTransform: "capitalize" }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: color(s) }} /> {s.replace("_", " ")}
          </span>
        ))}
        {ruleBased && (
          <span style={{ marginLeft: "auto", fontSize: 11.5, color: "var(--text-dim)" }}>
            <Icon name="info" size={11} /> rule-based until the first model
          </span>
        )}
      </div>
    </div>
  );
}

type CapReport = { has_model: boolean; reliable: string[];
                   activities: { slug: string; tier: string }[] };

function PersonCard({ person, preds, weekPreds }: { person: Person; preds: Pred[]; weekPreds: Pred[] }) {
  const latest = preds[0];
  const ruleBased = latest?.model_version?.startsWith("rules");
  // honesty: don't present an activity the model can't actually do reliably as if
  // it's certain. Facts (away/asleep) are always trustworthy regardless.
  const cap = useQuery<CapReport>({
    queryKey: ["capability", person.id],
    queryFn: () => fetch(`/api/capability?person=${encodeURIComponent(person.id)}`).then((r) => r.json()),
    staleTime: 300_000,
  });
  const tierOf = (slug?: string) => cap.data?.activities.find((a) => a.slug === slug)?.tier;
  const isFact = !!latest?.model_version?.startsWith("fact");
  const tier = latest ? (isFact ? "reliable" : tierOf(latest.smoothed)) : undefined;
  const hedge = (!isFact && (tier === "unreliable" || tier === "blind")) ? "not reliable yet"
              : (!isFact && tier === "learning") ? "still learning — not sure" : null;
  const noReliable = !!cap.data?.has_model && (cap.data.reliable?.length ?? 0) === 0;
  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 14, flex: 1, minWidth: 280 }}>
      {latest ? (
        <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
          <Scene activity={latest.smoothed} person={person} />
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontWeight: 500 }}>{person.name}</span>
            <span style={{ color: color(latest.smoothed), fontWeight: 500, textTransform: "capitalize",
                           opacity: hedge ? 0.6 : 1 }}>
              {hedge ? "possibly " : ""}{latest.smoothed.replace("_", " ")}
            </span>
            <span style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, color: "var(--text-dim)" }}>
              {hedge
                ? <><Icon name="info" size={12} /> {hedge} · since {t(latest.time)}</>
                : <><Conf v={latest.confidence} /> {Math.round(latest.confidence * 100)}% · since {t(latest.time)}</>}
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
      {noReliable && (
        <div style={{ fontSize: 12.5, display: "flex", gap: 8, alignItems: "flex-start",
                      padding: "8px 10px", borderRadius: 8,
                      background: "color-mix(in srgb, var(--danger) 9%, transparent)",
                      border: "1px solid color-mix(in srgb, var(--danger) 35%, var(--border))" }}>
          <Icon name="info" size={14} />
          <span>I can't reliably predict {person.name}'s activities yet with these sensors —
            facts like home/away still hold. <a href="/models" style={{ color: "var(--accent)" }}>See what would help.</a></span>
        </div>
      )}
      <WeekHeatmap preds={weekPreds} personId={person.id} ruleBased={!!ruleBased} unreliable={
        new Set((cap.data?.activities ?? []).filter((a) => a.tier === "unreliable" || a.tier === "blind").map((a) => a.slug))} />
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

function QuestionRow({ q, activities }: { q: Question; activities: Activity[] }) {
  const qc = useQueryClient();
  const [choosing, setChoosing] = useState(false);
  const [busy, setBusy] = useState(false);
  const send = async (slug: string) => {
    setBusy(true);
    try {
      await fetch(`/api/inbox/${q.id}/answer`, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ answer: slug }) });
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["predictions"] });
      qc.invalidateQueries({ queryKey: ["predictions_week"] });
    } catch { setBusy(false); }
  };
  const skip = async () => {
    setBusy(true);
    try { await fetch(`/api/inbox/${q.id}/skip`, { method: "POST" }); qc.invalidateQueries({ queryKey: ["inbox"] }); }
    catch { setBusy(false); }
  };
  // "No, it was…" options: model's alternatives first, then the rest of the
  // taxonomy, minus the activity we just rejected.
  const drop = new Set([q.predicted]);
  const options = [...q.alternatives, ...activities.filter((a) => a.enabled).map((a) => a.slug)]
    .filter((s, i, arr) => !drop.has(s) && arr.indexOf(s) === i).slice(0, 7);
  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 16px" }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <Icon name="question" size={18} />
        <span style={{ fontSize: 14, flex: 1, minWidth: 180 }}>
          {t(q.window_ts)} — was <strong style={{ fontWeight: 500, textTransform: "capitalize" }}>{q.person_id}</strong>{" "}
          {q.predicted.replace("_", " ")}? <span style={{ color: "var(--text-dim)" }}>({Math.round(q.confidence * 100)}% sure)</span>
        </span>
        {!choosing ? (
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-primary" disabled={busy} onClick={() => send(q.predicted)}
                    style={{ minWidth: 64 }}>Yes</button>
            <button className="btn btn-secondary" disabled={busy} onClick={() => setChoosing(true)}
                    style={{ minWidth: 64 }}>No</button>
          </div>
        ) : (
          <button className="btn btn-ghost" disabled={busy} onClick={() => setChoosing(false)}
                  style={{ fontSize: 13 }}>Cancel</button>
        )}
      </div>
      {choosing && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span style={{ fontSize: 13, color: "var(--text-dim)" }}>What was {q.person_id} actually doing?</span>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {options.map((slug) => (
              <button key={slug} className="btn btn-secondary" disabled={busy} onClick={() => send(slug)}
                      style={{ display: "inline-flex", gap: 6, alignItems: "center", textTransform: "capitalize" }}>
                <Icon name={icon(slug)} size={15} />{slug.replace("_", " ")}
              </button>
            ))}
            <button className="btn btn-ghost" disabled={busy} onClick={skip}
                    style={{ fontSize: 13 }} title="Not sure — ask me later">Skip</button>
          </div>
        </div>
      )}
    </div>
  );
}

function NeedsYou({ questions, activities }: { questions: Question[]; activities: Activity[] }) {
  if (!questions.length) return null;
  return (
    <section>
      <p className="label" style={{ margin: "0 0 8px" }}>Needs you</p>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {questions.slice(0, 3).map((q) => <QuestionRow key={q.id} q={q} activities={activities} />)}
        {questions.length > 3 && (
          <a href="/inbox" style={{ fontSize: 13, color: "var(--text-dim)", textDecoration: "none" }}>
            +{questions.length - 3} more in your inbox →
          </a>
        )}
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
                tier: number; per_day?: number; recent?: boolean };

const TIER_META: Record<number, [string, string]> = {
  1: ["Senses people", "#34D399"],   // bed, presence, person, media, door
  2: ["Behavioural", "#F59E0B"],     // power, lights, steps  (ember/amber)
  3: ["Ambient", "#F472B6"],         // temp, CO2, humidity, battery
};
const tierColor = (t: number) => TIER_META[t]?.[1] ?? TIER_META[2][1];

// optional second lens: colour dots by role instead of evidence tier
const ROLE_COLORS: Record<string, string> = {
  presence: "#34D399", person: "#22D3EE", bed: "#A78BFA", media: "#2DD4BF",
  power: "#F59E0B", light: "#FBBF24", door: "#60A5FA", env: "#F472B6",
  focus: "#C084FC", steps: "#FB923C", battery: "#94A3B8", alarm_time: "#818CF8",
  custom: "#64748B",
};
const roleColor = (r: string) => ROLE_COLORS[r] ?? ROLE_COLORS.custom;

type Leaf = C & { tier: number; name: string; role: string; per_day: number; recent: boolean };
type Room = { x: number; y: number; r: number; key: string; label: string;
              leaves: Leaf[]; total: number; sparse: boolean; tiers: Record<number, number>;
              live: number; blind?: boolean; seed?: { x: number; y: number } };

// Co-activation grouping from /api/bindings/coactivation — sensors that fire
// together, with an MDS layout seed (0..1) per cluster so the map's position
// carries meaning. Empty `clusters` → we keep the room grouping.
type CoData = { clusters: { id: number; x: number; y: number; n: number }[];
                assign: Record<string, number> };

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

const LABEL_H = 24;

const leafOf = (b: Health, maxPD: number): Leaf => ({
  x: 0, y: 0, r: 5 + Math.sqrt((b.per_day ?? 0) / maxPD) * 9,   // 5–14px
  tier: b.tier || 2, name: b.name, role: b.role, per_day: b.per_day ?? 0,
  recent: !!b.recent });

/** Pack one group of sensors into a disc (leaves centred on the disc origin).
 *  `seed` (0..1) optionally fixes where the disc starts in the cloud. */
function packRoom(key: string, label: string, list: Health[], maxPD: number,
                  seed?: { x: number; y: number }): Room {
  const leaves = list.map((b) => leafOf(b, maxPD));
  packSiblings(leaves);
  const e = enclose(leaves);
  for (const l of leaves) { l.x -= e.x; l.y -= e.y; }
  const tiers: Record<number, number> = { 1: 0, 2: 0, 3: 0 };
  for (const l of leaves) tiers[l.tier] = (tiers[l.tier] || 0) + 1;
  return { x: 0, y: 0, r: e.r + 7, key, label, leaves, total: leaves.length,
           sparse: leaves.length <= 1, tiers,
           live: leaves.filter((l) => l.recent).length, seed };
}

/** Flow discs into an organic, non-overlapping cloud filling width `W`. Discs
 *  with a `seed` (0..1 coords from the co-activation MDS embedding) start there,
 *  so position carries meaning; the rest fall on a phyllotaxis spiral. Collisions
 *  ONLY remove overlap — no centre-of-mass gravity (that collapses the cloud
 *  onto a line or into a central clump). */
function spread(rooms: Room[], W: number): { rooms: Room[]; width: number; height: number } {
  rooms.sort((a, b) => b.r - a.r);
  const maxR = Math.max(...rooms.map((rm) => rm.r), 1);
  const H = Math.max(2 * maxR + 50, 320);
  const cx = W / 2, cy = H / 2, PAD = 9;
  const GA = Math.PI * (3 - Math.sqrt(5));   // golden angle → even 2D spread
  const n = rooms.length;
  rooms.forEach((rm, i) => {
    if (rm.seed) {                           // embedding coords → meaningful xy
      rm.x = rm.r + rm.seed.x * Math.max(W - 2 * rm.r, 1);
      rm.y = rm.r + rm.seed.y * Math.max(H - 2 * rm.r, 1);
    } else {
      const rad = Math.sqrt(i / Math.max(n - 1, 1));   // 0 (centre) … 1 (edge)
      rm.x = cx + Math.cos(i * GA) * rad * (W * 0.44);
      rm.y = cy + Math.sin(i * GA) * rad * (H * 0.42);
    }
  });
  for (let it = 0; it < 500; it++) {
    let moved = false;
    for (let a = 0; a < n; a++) {
      for (let b = a + 1; b < n; b++) {
        const A = rooms[a], Bb = rooms[b];
        let dx = Bb.x - A.x, dy = Bb.y - A.y;
        const d = Math.hypot(dx, dy) || 0.01, min = A.r + Bb.r + PAD;
        if (d < min) {
          const push = (min - d) / 2; dx /= d; dy /= d;
          A.x -= dx * push; A.y -= dy * push;
          Bb.x += dx * push; Bb.y += dy * push;
          moved = true;
        }
      }
    }
    for (const rm of rooms) {              // keep inside the band only
      rm.x = Math.max(rm.r, Math.min(W - rm.r, rm.x));
      rm.y = Math.max(rm.r, Math.min(H - rm.r, rm.y));
    }
    if (!moved) break;                     // settled — stop early
  }
  return { rooms, width: W, height: H + LABEL_H };
}

/** Default lens: group sensors by their room label. */
function layoutRooms(rows: Health[], W: number, blind: string[] = []) {
  const groups: Record<string, { list: Health[]; originals: Set<string> }> = {};
  for (const b of rows) {
    const k = roomKey(b.room);
    (groups[k] ??= { list: [], originals: new Set() });
    groups[k].list.push(b);
    groups[k].originals.add(b.room || "Unassigned");
  }
  const maxPD = Math.max(1, ...rows.map((b) => b.per_day ?? 0));
  const rooms = Object.entries(groups).map(([key, g]) =>
    packRoom(key, prettyRoom([...g.originals]), g.list, maxPD));
  // blind spots: HA areas with no usable sensor — small dashed ghost discs
  for (const area of blind) {
    rooms.push({ x: 0, y: 0, r: 20, key: `blind:${roomKey(area)}`, label: prettyRoom([area]),
                 leaves: [], total: 0, sparse: true, tiers: { 1: 0, 2: 0, 3: 0 },
                 live: 0, blind: true });
  }
  return spread(rooms, W);
}

// Name a behaviour cluster from its members: the room a clear majority share,
// else the dominant role ("Lighting", "Presence"). Keeps the short human
// captions of the room lens instead of bare "Cluster 3".
function clusterLabel(list: Health[]): string {
  const tally = (key: (b: Health) => string | null) => {
    const m: Record<string, number> = {};
    for (const b of list) { const k = key(b); if (k) m[k] = (m[k] || 0) + 1; }
    const top = Object.entries(m).sort((a, b) => b[1] - a[1])[0];
    return top ? { name: top[0], frac: top[1] / list.length } : null;
  };
  const room = tally((b) => b.room || null);
  if (room && room.frac >= 0.6) return prettyRoom([room.name]);
  const role = tally((b) => b.role);
  const r = role ? role.name : "mixed";
  return r[0].toUpperCase() + r.slice(1);
}

/** 'By behaviour' lens: group sensors by co-activation cluster instead of room.
 *  Each disc holds sensors that fire together; its seed comes from the MDS
 *  embedding so similar clusters sit near each other. Sensors with no
 *  co-activation signal are dropped (they'd be singletons either way). */
function layoutClusters(rows: Health[], co: CoData, W: number) {
  const maxPD = Math.max(1, ...rows.map((b) => b.per_day ?? 0));
  const seedOf: Record<number, { x: number; y: number }> = {};
  for (const c of co.clusters) seedOf[c.id] = { x: c.x, y: c.y };
  const groups: Record<number, Health[]> = {};
  for (const b of rows) {
    const id = co.assign[b.name];
    if (id == null) continue;
    (groups[id] ??= []).push(b);
  }
  const rooms = Object.entries(groups).map(([id, list]) =>
    packRoom(`cluster:${id}`, clusterLabel(list), list, maxPD, seedOf[Number(id)]));
  return spread(rooms, W);
}

/** Sensor coverage bubble chart: rooms laid out across the width, each holding
 *  one dot per live sensor (size = how often it fires, colour = evidence tier).
 *  Click a room to list its sensors. Small/all-pink rooms = barely sensed. */
function SensorCoverage() {
  const [rows, setRows] = useState<Health[] | null>(null);
  const [known, setKnown] = useState<string[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [colorBy, setColorBy] = useState<"value" | "role">("value");
  const [groupBy, setGroupBy] = useState<"room" | "behavior">("room");
  const [co, setCo] = useState<CoData | null>(null);
  const isMobile = useIsMobile();
  useEffect(() => {
    const load = () => fetch("/api/bindings/health").then((r) => r.json())
      .then((h) => {
        setRows((h.bindings ?? []).filter((b: Health) => b.status === "alive"));
        setKnown(h.rooms_known ?? []);
      })
      .catch(() => setRows([]));
    load();
    const id = setInterval(load, 30_000);   // live heartbeat: refresh recent-activity
    return () => clearInterval(id);
  }, []);
  // co-activation is expensive (reads weeks of raw data) and slow-changing —
  // fetch once, lazily, only when the user first opens the behaviour lens
  useEffect(() => {
    if (groupBy !== "behavior" || co) return;
    fetch("/api/bindings/coactivation").then((r) => r.json())
      .then((d) => setCo({ clusters: d.clusters ?? [], assign: d.assign ?? {} }))
      .catch(() => setCo({ clusters: [], assign: {} }));
  }, [groupBy, co]);
  if (!rows || rows.length === 0) return null;

  // HA areas with no usable sensor → blind-spot ghost bubbles
  const covered = new Set(rows.map((b) => roomKey(b.room)));
  const blind = known.filter((a) => !covered.has(roomKey(a)));
  // behaviour lens only when clustering actually returned groups, else fall back
  const byBehavior = groupBy === "behavior" && !!co && co.clusters.length > 0;
  const W = isMobile ? 600 : 1040;
  const { rooms } = byBehavior ? layoutClusters(rows, co!, W) : layoutRooms(rows, W, blind);
  const selected = rooms.find((r) => r.key === sel) || null;

  // Fit the viewBox to the actual bubble cloud (incl. labels) so the chart fills
  // its card instead of floating in dead space — this is what scales the bubbles up.
  const M = 6;
  const minX = Math.min(...rooms.map((rm) => rm.x - rm.r)) - M;
  const minY = Math.min(...rooms.map((rm) => rm.y - rm.r)) - M;
  const maxX = Math.max(...rooms.map((rm) => rm.x + rm.r)) + M;
  const maxY = Math.max(...rooms.map((rm) => rm.y + rm.r + 22)) + M;   // +label line
  const vbW = maxX - minX, vbH = maxY - minY;

  return (
    <Card icon="sensors" title="Sensor coverage"
          sub={byBehavior
            ? "Each bubble is a group of sensors that FIRE TOGETHER — a behavioural zone learned from the data, not the room labels. Nearby bubbles behave alike. Each dot is a sensor; bigger fires more often, colour is how directly it senses people. Click a bubble to see its sensors."
            : "Each cluster is a room; each dot is a live sensor — bigger dots fire more often, colour is how directly it senses people. Dots pulsing green fired in the last few minutes; dashed rooms have no sensor Hearth can use. Click a room to see its sensors."}>
      <svg viewBox={`${minX} ${minY} ${vbW} ${vbH}`} role="img"
           style={{ width: "100%", display: "block" }}>
        <style>{`
          @keyframes hearth-pulse { 0%,100%{opacity:.5} 50%{opacity:1} }
          @keyframes hearth-in { from { transform: scale(.9) } to { transform: scale(1) } }
          .hearth-live { animation: hearth-pulse 1.8s ease-in-out infinite; }
          .hearth-room { transform-box: fill-box; transform-origin: center;
                         animation: hearth-in .45s cubic-bezier(.2,.8,.2,1) both; }
        `}</style>
        {rooms.map((rm, i) => {
          const active = rm.key === sel || rm.key === hover;
          const dim = (sel || hover) && !active;
          const inDelay = `${Math.min(i * 25, 350)}ms`;
          if (rm.blind) {
            // a room HA knows about but Hearth has no usable sensor in
            return (
              <g key={rm.key} className="hearth-room" style={{ opacity: dim ? 0.4 : 0.8,
                                                               animationDelay: inDelay }}>
                <title>{rm.label} — no sensors Hearth can use here. Add one in Home Assistant, then Rescan.</title>
                <circle cx={rm.x} cy={rm.y} r={rm.r} fill="none"
                        stroke="var(--text-dim)" strokeWidth={1.2} strokeDasharray="3 3" opacity={0.6} />
                <text x={rm.x} y={rm.y + 4} textAnchor="middle" fontSize={16}
                      fill="var(--text-dim)" style={{ pointerEvents: "none" }}>∅</text>
                <text x={rm.x} y={rm.y + rm.r + 16} textAnchor="middle" fontSize={11.5}
                      fill="var(--text-dim)" style={{ pointerEvents: "none" }}>{rm.label}</text>
              </g>
            );
          }
          return (
            <g key={rm.key} className="hearth-room"
               onClick={() => setSel(sel === rm.key ? null : rm.key)}
               onMouseEnter={() => setHover(rm.key)} onMouseLeave={() => setHover(null)}
               style={{ cursor: "pointer", opacity: dim ? 0.45 : 1, transition: "opacity .12s",
                        animationDelay: inDelay }}>
              <title>{rm.label} — {rm.total} sensor{rm.total === 1 ? "" : "s"}
                {rm.live ? ` · ${rm.live} active now` : ""}</title>
              {/* a soft halo around rooms with live activity right now */}
              {rm.live > 0 && (
                <circle cx={rm.x} cy={rm.y} r={rm.r + 3} fill="none"
                        stroke="var(--ok, #34D399)" strokeWidth={1.5} opacity={0.5}
                        className="hearth-live" />
              )}
              <circle cx={rm.x} cy={rm.y} r={rm.r}
                      fill="var(--surface-2)" fillOpacity={active ? 0.55 : 0.32}
                      stroke={rm.sparse ? "var(--danger)" : active ? "var(--accent)"
                              : rm.live > 0 ? "var(--ok, #34D399)" : "var(--border)"}
                      strokeWidth={active ? 2 : rm.sparse ? 1.8 : 1} />
              {rm.leaves.map((l, i) => (
                <circle key={i} cx={rm.x + l.x} cy={rm.y + l.y} r={l.r}
                        className={l.recent ? "hearth-live" : undefined}
                        fill={colorBy === "role" ? roleColor(l.role) : tierColor(l.tier)}
                        fillOpacity={l.recent ? 1 : 0.92}
                        stroke={l.recent ? "var(--ok, #34D399)" : "var(--surface)"}
                        strokeWidth={l.recent ? 1.2 : 0.6} />
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
                <span style={{ width: 8, height: 8, borderRadius: "50%",
                      background: colorBy === "role" ? roleColor(l.role) : tierColor(l.tier) }} />
                {l.name}
                <span style={{ color: "var(--text-dim)" }}>{l.role}</span>
              </span>
            ))}
          </div>
        </div>
      )}
      <div style={{ display: "flex", gap: 12, marginTop: 10, fontSize: 12, color: "var(--text-dim)",
                    justifyContent: "center", alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ display: "inline-flex", border: "1px solid var(--border)",
                       borderRadius: 999, overflow: "hidden" }}>
          {(["room", "behavior"] as const).map((m) => (
            <button key={m} onClick={() => { setGroupBy(m); setSel(null); }}
              title={m === "behavior" ? "Group sensors that fire together (learned from data)"
                                      : "Group sensors by their room"}
              style={{ border: "none", cursor: "pointer", padding: "3px 10px", fontSize: 11.5,
                       fontWeight: 600, background: groupBy === m ? "var(--accent)" : "transparent",
                       color: groupBy === m ? "#fff" : "var(--text-dim)" }}>
              {m === "room" ? "By room" : "By behaviour"}
            </button>
          ))}
        </span>
        {groupBy === "behavior" && !co && (
          <span style={{ color: "var(--text-dim)" }}>clustering…</span>
        )}
        {groupBy === "behavior" && co && co.clusters.length === 0 && (
          <span style={{ color: "var(--text-dim)" }}>not enough data to cluster yet — showing rooms</span>
        )}
        <span style={{ display: "inline-flex", border: "1px solid var(--border)",
                       borderRadius: 999, overflow: "hidden" }}>
          {(["value", "role"] as const).map((m) => (
            <button key={m} onClick={() => setColorBy(m)}
              style={{ border: "none", cursor: "pointer", padding: "3px 10px", fontSize: 11.5,
                       fontWeight: 600, background: colorBy === m ? "var(--accent)" : "transparent",
                       color: colorBy === m ? "#fff" : "var(--text-dim)" }}>
              {m === "value" ? "By value" : "By role"}
            </button>
          ))}
        </span>
        {colorBy === "value"
          ? [1, 2, 3].map((tier) => (
              <span key={tier} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                <span style={{ width: 9, height: 9, borderRadius: "50%", background: tierColor(tier) }} />
                {TIER_META[tier][0]}
              </span>))
          : [...new Set(rows.map((r) => r.role))].sort().map((role) => (
              <span key={role} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                <span style={{ width: 9, height: 9, borderRadius: "50%", background: roleColor(role) }} />
                {role}
              </span>))}
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          <span className="hearth-live" style={{ width: 9, height: 9, borderRadius: "50%",
                background: "var(--ok, #34D399)" }} />
          active now
        </span>
      </div>
    </Card>
  );
}

export default function Dashboard() {
  const preds = useQuery<{ persons: Record<string, Pred[]>; note?: string }>({
    queryKey: ["predictions"],
    queryFn: () => fetch("/api/predictions?hours=24").then((r) => r.json()),
    refetchInterval: 60_000,
  });
  const predsWeek = useQuery<{ persons: Record<string, Pred[]> }>({
    queryKey: ["predictions_week"],
    queryFn: () => fetch("/api/predictions?hours=168").then((r) => r.json()),
    refetchInterval: 300_000,
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
  const activities = useQuery<Activity[]>({
    queryKey: ["activities"], queryFn: () => fetch("/api/activities").then((r) => r.json()),
  });

  const byPerson = preds.data?.persons ?? {};
  const byPersonWeek = predsWeek.data?.persons ?? {};
  const members = (persons.data ?? []).filter((p) => p.enabled);
  const anyPredictions = Object.values(byPerson).some((l) => l.length > 0);
  const coldStart = !anyPredictions;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {coldStart && journey.data ? (
        <JourneyCard j={journey.data} />
      ) : (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          {members.map((p) => <PersonCard key={p.id} person={p} preds={byPerson[p.id] ?? []}
                                          weekPreds={byPersonWeek[p.id] ?? []} />)}
        </div>
      )}
      <NeedsYou questions={inbox.data ?? []} activities={activities.data ?? []} />
      <div style={{ display: "grid", gap: 16, alignItems: "start",
                    gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))" }}>
        <Card icon="flow" title="Live data flow"
              action={<Link to="/methodology" style={{ fontSize: 12.5, color: "var(--text-dim)",
                            textDecoration: "none" }}>How it works →</Link>}>
          <FlowMap />
        </Card>
        {!coldStart && <SensorCoverage />}
      </div>
      <Pulse j={journey.data} hasTsdb={!preds.data?.note} />
    </div>
  );
}

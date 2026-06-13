/**
 * Welcome — the live hand-off between the wizard and the dashboard.
 *
 * Introduces the buddy (Ember) and then *shows* the pipeline running on this
 * home's real data: scanning entities → (reading with AI, if a key is set) →
 * building features → training → finding patterns. Every number is live,
 * pulled from GET /api/buddy (phase) and GET /api/flow (this instance's counts);
 * nothing here is faked. The entity strip animates over the sensors Hearth
 * actually bound (GET /api/bindings).
 *
 * Warm start runs for everyone now (flag `fastTrack` in localStorage
 * 'hearth.welcome', with `source`: "bucket" = an external HA→Influx bucket with
 * the longest history, "recorder" = ~10 days from HA's own recorder). The whole
 * pipeline completes in minutes and is watched live. The legacy "fresh / wait a
 * week" arc (`fastTrack` false) remains only as a fallback.
 *
 * The wizard renders it inline the instant setup finishes (it doubles as the
 * loading screen while Hearth restarts); "Go to dashboard" reloads into the app.
 */
import { useEffect, useMemo, useRef, useState } from "react";

type Buddy = { phase: string; tone: string; title: string; detail: string;
               progress: number | null };
type FlowNode = { label: string; value: string; status: string };
type Flow = { phase: string; nodes: Record<string, FlowNode> };
type LlmActivity = { phase: "sending" | "received" | "error"; task: string;
                     model?: string; sent?: string; items?: number; at?: string;
                     prompt?: string; reply?: string };
type Feat = { name: string; transform: string };
type Cluster = { label: string; relevant: boolean; why: string; count: number; kept: number };
type Triage = { by: string | null; total: number; kept_count: number; clusters: Cluster[];
                awaiting?: boolean; has_llm?: boolean };
type Llm = { configured: boolean; model?: string; activity?: LlmActivity | null };
type Binding = { id: number; name: string; role: string; room: string | null;
                 person_id: string | null; enabled: boolean };
type Ent = { entity_id: string; friendly_name: string | null; domain: string | null };

// the ordered fast-track phases, used to place the current phase on the arc
const fmtUsd = (u: number) => (u < 0.01 ? "<$0.01" : `$${u.toFixed(2)}`);

const ORDER = [
  "setup:importing", "setup:imported", "setup:pruned_empty",
  "setup:building_features", "setup:features_built",
  "setup:training", "setup:trained",
  "setup:discovering", "setup:discovered",
];

type StageStatus = "pending" | "active" | "done" | "later";

const CSS = `
@keyframes ember-flicker { 0%,100%{transform:scale(1);opacity:.95} 50%{transform:scale(1.06);opacity:1} }
@keyframes ember-ring { 0%{transform:scale(.8);opacity:.55} 70%{opacity:0} 100%{transform:scale(1.7);opacity:0} }
@keyframes wlc-rise { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:none} }
@keyframes wlc-pulse { 0%,100%{opacity:1} 50%{opacity:.45} }
.wlc-row{animation:wlc-rise .4s ease both}
.wlc-active .wlc-dot{animation:wlc-pulse 1.3s ease-in-out infinite}
.wlc-scan-on{animation:wlc-pulse 1s ease-in-out infinite}
`;

function Ember({ size = 96, busy }: { size?: number; busy: boolean }) {
  return (
    <div style={{ position: "relative", width: size, height: size }}>
      {busy && (
        <span style={{ position: "absolute", inset: 0, borderRadius: "50%",
                       border: "2px solid var(--accent)",
                       animation: "ember-ring 2.2s ease-out infinite" }} />
      )}
      <div style={{ width: size, height: size, borderRadius: "50%",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    background: "radial-gradient(circle at 50% 35%, color-mix(in srgb, var(--accent) 30%, var(--surface)), var(--surface))",
                    border: "2px solid var(--accent)",
                    boxShadow: "0 0 0 8px color-mix(in srgb, var(--accent) 10%, transparent)",
                    animation: busy ? "ember-flicker 2.2s ease-in-out infinite" : "none" }}>
        <svg width={size * 0.46} height={size * 0.46} viewBox="0 0 24 24" aria-hidden>
          <path d="M12 2.2c2.1 4 5.6 5.6 4 10.1A4.2 4.2 0 0 1 7.7 13C7 9.6 9.6 8 12 2.2z"
                style={{ fill: "var(--accent)" }} />
          <path d="M12 12.2c1 1.6 1.7 2.5 1.1 4.2a2.1 2.1 0 0 1-4.1-.6c0-1.6 1.6-2.2 3-3.6z"
                style={{ fill: "rgba(255,255,255,0.7)" }} />
        </svg>
      </div>
    </div>
  );
}

const Check = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff"
       strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M5 13l4 4L19 7" />
  </svg>
);

function StageRow({ title, detail, status, children }: {
  title: string; detail: string; status: StageStatus; children?: React.ReactNode;
}) {
  const done = status === "done";
  const active = status === "active";
  const color = done ? "var(--ok, #34D399)" : active ? "var(--accent)" : "var(--border)";
  return (
    <div className={`wlc-row${active ? " wlc-active" : ""}`}
         style={{ display: "flex", gap: 14, opacity: status === "pending" || status === "later" ? 0.55 : 1 }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
        <span className="wlc-dot" style={{ width: 26, height: 26, borderRadius: "50%", flexShrink: 0,
                     display: "flex", alignItems: "center", justifyContent: "center",
                     background: done || active ? color : "var(--surface-2)",
                     border: `2px solid ${color}` }}>
          {done ? <Check /> : <span style={{ width: 7, height: 7, borderRadius: "50%",
                     background: active ? "#fff" : "var(--text-dim)" }} />}
        </span>
        <span style={{ flex: 1, width: 2, marginTop: 4,
                       background: done ? "var(--ok, #34D399)" : "var(--border)" }} />
      </div>
      <div style={{ paddingBottom: 18, flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: 15 }}>{title}</div>
        <div style={{ fontSize: 13.5, color: "var(--text-dim)", marginTop: 2 }}>
          {status === "later" ? "Starts as your data arrives" : detail}
        </div>
        {children}
      </div>
    </div>
  );
}

const PersonGlyph = ({ color }: { color: string }) => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={color}
       strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <circle cx="12" cy="8" r="3.4" />
    <path d="M5.5 20a6.5 6.5 0 0 1 13 0" />
  </svg>
);

/** Household members recognised from their person.* tracker — surfaced by name
 *  ("Found Alex from your household") as the scan reaches them, rather than as
 *  a cryptic binding chip. */
function PersonFinds({ people, active, done }: {
  people: { key: number; name: string }[]; active: boolean; done: boolean;
}) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (done) { setShown(people.length); return; }
    if (!active || people.length === 0) return;
    setShown(0);
    const id = setInterval(() => setShown((n) => Math.min(n + 1, people.length)), 450);
    return () => clearInterval(id);
  }, [active, done, people.length]);
  if (people.length === 0) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}>
      {people.map((p, i) => {
        const seen = done || i < shown;
        return (
          <div key={p.key} className={seen ? "wlc-row" : ""}
            style={{ display: seen ? "flex" : "none", alignItems: "center", gap: 8,
                     fontSize: 13.5, color: "var(--text)" }}>
            <span style={{ width: 22, height: 22, borderRadius: "50%", flexShrink: 0,
                           display: "flex", alignItems: "center", justifyContent: "center",
                           background: "color-mix(in srgb, var(--ok, #34D399) 14%, transparent)",
                           border: "1.5px solid var(--ok, #34D399)" }}>
              <PersonGlyph color="var(--ok, #34D399)" />
            </span>
            Found <strong style={{ fontWeight: 600 }}>{p.name}</strong> from your household
          </div>
        );
      })}
    </div>
  );
}

/** A live "reading your home" feed — the actual HA entities scroll past one by
 *  one while scanning, so the user recognises their own things and feels the
 *  real connection. Auto-scrolls to keep the newest rows in view; faded top and
 *  bottom. Honest theatre over the genuine entity list. */
function ScanFeed({ entities, active, done }: {
  entities: Ent[]; active: boolean; done: boolean;
}) {
  const [cursor, setCursor] = useState(0);
  useEffect(() => {
    if (done) { setCursor(entities.length); return; }
    if (!active || entities.length === 0) return;
    setCursor(0);
    // one entity every 600ms — slow enough to read your own things scroll past
    // (75ms was a blur). The scan stage outlasts this, so showing fewer is fine.
    const id = setInterval(() => setCursor((c) => Math.min(c + 1, entities.length)), 600);
    return () => clearInterval(id);
  }, [active, done, entities.length]);
  if (entities.length === 0) return null;
  const ROW = 30, VISIBLE = 4;
  const shown = entities.slice(0, Math.max(cursor, done ? entities.length : 0));
  const offset = Math.max(0, shown.length - VISIBLE) * ROW;
  const fade = "linear-gradient(to bottom, transparent, #000 22%, #000 78%, transparent)";
  return (
    <div style={{ height: ROW * VISIBLE, overflow: "hidden", position: "relative", marginTop: 10,
                  borderRadius: 10, border: "1px solid var(--border)", background: "var(--surface)",
                  maskImage: fade, WebkitMaskImage: fade }}>
      <div style={{ transform: `translateY(-${offset}px)`, transition: "transform .35s ease" }}>
        {shown.map((e, i) => {
          const scanning = active && !done && i === shown.length - 1;
          return (
            <div key={e.entity_id} style={{ height: ROW, display: "flex", alignItems: "center",
                                            gap: 9, padding: "0 12px", minWidth: 0 }}>
              <span className={scanning ? "wlc-scan-on" : ""}
                style={{ width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                         background: scanning ? "var(--accent)" : "var(--ok, #34D399)" }} />
              <span style={{ fontSize: 13, whiteSpace: "nowrap", overflow: "hidden",
                             textOverflow: "ellipsis", flexShrink: 1 }}>
                {e.friendly_name || e.entity_id}
              </span>
              {e.friendly_name && (
                <span style={{ fontSize: 11.5, color: "var(--text-dim)", whiteSpace: "nowrap",
                               overflow: "hidden", textOverflow: "ellipsis",
                               fontFamily: "ui-monospace, Menlo, monospace" }}>
                  {e.entity_id}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Live "building features" feed — the real feature columns Hearth is creating
 *  scroll past with the transform each uses, so you see what it's computing from
 *  your sensors (e.g. sofa_occupancy_fraction · rolling_mean). */
function FeatureFeed({ features, active, done }: {
  features: Feat[]; active: boolean; done: boolean;
}) {
  const [cursor, setCursor] = useState(0);
  useEffect(() => {
    if (done) { setCursor(features.length); return; }
    if (!active || features.length === 0) return;
    setCursor(0);
    const id = setInterval(() => setCursor((c) => Math.min(c + 1, features.length)), 90);
    return () => clearInterval(id);
  }, [active, done, features.length]);
  if (features.length === 0) return null;
  const ROW = 30, VISIBLE = 4;
  const shown = features.slice(0, Math.max(cursor, done ? features.length : 0));
  const offset = Math.max(0, shown.length - VISIBLE) * ROW;
  const fade = "linear-gradient(to bottom, transparent, #000 22%, #000 78%, transparent)";
  return (
    <div style={{ height: ROW * VISIBLE, overflow: "hidden", position: "relative", marginTop: 10,
                  borderRadius: 10, border: "1px solid var(--border)", background: "var(--surface)",
                  maskImage: fade, WebkitMaskImage: fade }}>
      <div style={{ transform: `translateY(-${offset}px)`, transition: "transform .16s linear" }}>
        {shown.map((f, i) => {
          const building = active && !done && i === shown.length - 1;
          return (
            <div key={f.name} style={{ height: ROW, display: "flex", alignItems: "center",
                                       gap: 9, padding: "0 12px", minWidth: 0 }}>
              <span className={building ? "wlc-scan-on" : ""}
                style={{ width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                         background: building ? "var(--accent)" : "var(--ok, #34D399)" }} />
              <span style={{ fontSize: 13, whiteSpace: "nowrap", overflow: "hidden",
                             textOverflow: "ellipsis", flexShrink: 1,
                             fontFamily: "ui-monospace, Menlo, monospace" }}>
                {f.name}
              </span>
              {f.transform && (
                <span style={{ fontSize: 11.5, color: "var(--text-dim)", whiteSpace: "nowrap" }}>
                  · {f.transform}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Bubble cloud of the coarse triage: each cluster a bubble sized by how many
 *  entities it holds, tinted by whether it's relevant to activity prediction —
 *  so the user sees "lots of lights, a 3D printer, some server stuff" and which
 *  of it Hearth is keeping. Real data from GET /api/entity-triage. */
function BubbleCloud({ triage, kept, onToggle }: {
  triage: Triage; kept?: Record<string, boolean>; onToggle?: (label: string) => void;
}) {
  const clusters = triage.clusters.slice(0, 18);
  if (!clusters.length) return null;
  const max = Math.max(...clusters.map((c) => c.count), 1);
  const size = (n: number) => Math.round(46 + 52 * Math.sqrt(n / max));   // 46–98px
  const interactive = !!onToggle;
  const isOn = (c: Cluster) => (kept ? !!kept[c.label] : c.relevant);
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center",
                    justifyContent: "center" }}>
        {clusters.map((c) => {
          const d = size(c.count);
          const on = isOn(c);
          return (
            <button key={c.label} disabled={!interactive}
              onClick={() => onToggle?.(c.label)}
              title={`${c.label} · ${c.count} entities${c.why ? ` · ${c.why}` : ""}`
                     + (interactive ? (on ? " · click to skip" : " · click to keep") : "")}
              style={{ width: d, height: d, borderRadius: "50%", flexShrink: 0,
                       display: "flex", flexDirection: "column", alignItems: "center",
                       justifyContent: "center", textAlign: "center", padding: 4, lineHeight: 1.15,
                       cursor: interactive ? "pointer" : "default",
                       border: `1.5px solid ${on ? "var(--accent)" : "var(--border)"}`,
                       background: on ? "color-mix(in srgb, var(--accent) 16%, transparent)"
                                      : "var(--surface)",
                       color: on ? "var(--text)" : "var(--text-dim)",
                       opacity: on ? 1 : 0.6 }}>
              <span style={{ fontSize: Math.max(9, Math.min(12, d / 7)), fontWeight: 600,
                             overflow: "hidden", textOverflow: "ellipsis",
                             maxWidth: d - 8, whiteSpace: "nowrap" }}>{c.label}</span>
              <span style={{ fontSize: 10, opacity: 0.7 }}>{c.count}</span>
            </button>
          );
        })}
      </div>
      {!interactive && (
        <div style={{ fontSize: 12, color: "var(--text-dim)", textAlign: "center", marginTop: 8 }}>
          Keeping <strong style={{ color: "var(--accent)" }}>{triage.kept_count}</strong> of {triage.total}{" "}
          entities for your model{triage.by === "llm" ? " — chosen by AI" : ""}.
        </div>
      )}
    </div>
  );
}

/** A tiny live transcript of the AI exchange — what Hearth just asked and the
 *  reply typing back in — so the LLM step shows real insight, not just a label. */
function AITranscript({ act }: { act: LlmActivity }) {
  const [typed, setTyped] = useState(0);
  const reply = act.phase === "received" ? (act.reply ?? "") : "";
  useEffect(() => {
    setTyped(0);
    if (!reply) return;
    const id = setInterval(() => setTyped((n) => {
      if (n >= reply.length) { clearInterval(id); return n; }
      return Math.min(n + 3, reply.length);
    }), 18);
    return () => clearInterval(id);
  }, [reply, act.at]);
  if (!act.prompt && !reply) return null;
  const box: React.CSSProperties = {
    fontFamily: "ui-monospace, Menlo, monospace", fontSize: 11.5, lineHeight: 1.5,
    whiteSpace: "pre-wrap", wordBreak: "break-word", color: "var(--text-dim)",
    display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden",
  };
  return (
    <div style={{ marginTop: 10, borderRadius: 10, border: "1px solid var(--border)",
                  background: "var(--surface)", padding: "9px 11px",
                  display: "flex", flexDirection: "column", gap: 6 }}>
      {act.prompt && (
        <div style={box}>
          <span style={{ color: "var(--accent)" }}>↑ </span>{act.prompt}
        </div>
      )}
      {act.phase !== "sending" && reply && (
        <div style={{ ...box, color: "var(--text)" }}>
          <span style={{ color: "var(--ok, #34D399)" }}>↓ </span>
          {reply.slice(0, typed)}{typed < reply.length ? "▌" : ""}
        </div>
      )}
      {act.phase === "sending" && (
        <div style={{ ...box }}>
          <span style={{ color: "var(--ok, #34D399)" }}>↓ </span>waiting for the reply…
        </div>
      )}
    </div>
  );
}

/** The scanning entity strip — chips for the sensors Hearth bound, ticked off
 *  one by one while the scan stage is active, all checked once it's done. */
function EntityStrip({ bindings, active, done }: {
  bindings: Binding[]; active: boolean; done: boolean;
}) {
  const [cursor, setCursor] = useState(0);
  useEffect(() => {
    if (done) { setCursor(bindings.length); return; }
    if (!active || bindings.length === 0) return;
    const id = setInterval(() => setCursor((c) => (c + 1) % (bindings.length + 1)), 260);
    return () => clearInterval(id);
  }, [active, done, bindings.length]);
  if (bindings.length === 0) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
      {bindings.map((b, i) => {
        const seen = done || i < cursor;
        const scanning = active && !done && i === cursor;
        return (
          <span key={b.id} className={scanning ? "wlc-scan-on" : ""}
            style={{ fontSize: 11.5, padding: "3px 9px", borderRadius: 99,
                     display: "inline-flex", alignItems: "center", gap: 5,
                     border: `1px solid ${seen ? "var(--ok, #34D399)" : scanning ? "var(--accent)" : "var(--border)"}`,
                     background: seen ? "color-mix(in srgb, var(--ok, #34D399) 12%, transparent)"
                               : scanning ? "color-mix(in srgb, var(--accent) 12%, transparent)" : "var(--surface)",
                     color: seen ? "var(--text)" : "var(--text-dim)" }}>
            {seen && <span style={{ color: "var(--ok, #34D399)", display: "flex" }}>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M5 13l4 4L19 7" /></svg></span>}
            {b.name}
            <span style={{ opacity: 0.6 }}>· {b.role}</span>
          </span>
        );
      })}
    </div>
  );
}

export default function Welcome() {
  const flag = useMemo(() => {
    try { return JSON.parse(localStorage.getItem("hearth.welcome") || "{}"); }
    catch { return {}; }
  }, []);
  const fastTrack: boolean = !!flag.fastTrack;
  const source: string = flag.source ?? "recorder";   // "bucket" | "recorder"

  const [buddy, setBuddy] = useState<Buddy | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [bindings, setBindings] = useState<Binding[]>([]);
  const [entities, setEntities] = useState<Ent[]>([]);
  const [features, setFeatures] = useState<Feat[]>([]);
  const [triage, setTriage] = useState<Triage | null>(null);
  const [kept, setKept] = useState<Record<string, boolean>>({});
  const [approving, setApproving] = useState(false);
  const [approved, setApproved] = useState(false);
  const [triageCost, setTriageCost] = useState<number | null>(null);
  const keptInit = useRef(false);
  const [llm, setLlm] = useState<Llm | null>(null);
  const [names, setNames] = useState<string[]>(flag.members ?? []);
  const [personMap, setPersonMap] = useState<Record<string, string>>({});
  const sawSetup = useRef(false);

  useEffect(() => {
    let stop = false;
    // Retry until the backend is up — we arrive here while Hearth is still
    // restarting from setup, so these will 401/fail for the first few seconds.
    const loadOnce = (url: string, onData: (d: unknown) => void) => {
      const attempt = () => fetch(url)
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((d) => { if (!stop) onData(d); })
        .catch(() => { if (!stop) setTimeout(attempt, 2500); });
      attempt();
    };
    loadOnce("/api/bindings", (b) =>
      setBindings(((b as Binding[]) || []).filter((x) => x.enabled).slice(0, 50)));
    // the live entity list — what Hearth is actually reading from your home; the
    // scan feed rolls through these so you recognise your own stuff.
    loadOnce("/api/ha/entities", (res) =>
      setEntities(((res as { entities?: Ent[] })?.entities ?? []).map((e) => ({
        entity_id: e.entity_id, friendly_name: e.friendly_name, domain: e.domain }))));
    loadOnce("/api/persons", (ps) => {
      const list = (ps as { id: string; name: string; notify_system?: boolean }[]) || [];
      setPersonMap(Object.fromEntries(list.map((p) => [p.id, p.name])));
      // greet only whoever actually receives Hearth's messages (the operator),
      // not every household member — others aren't the one setting this up.
      if (!flag.members) setNames(list.filter((p) => p.notify_system).map((p) => p.name));
    });
    return () => { stop = true; };
  }, []);

  useEffect(() => {
    let alive = true;
    // only accept genuinely OK responses — during the post-setup restart these
    // 401/503 for a few seconds, and we must NOT treat that as real state.
    const ok = (url: string) => fetch(url).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    const tick = () => {
      Promise.all([ok("/api/buddy"), ok("/api/flow"), ok("/api/connections/llm"),
                   ok("/api/feature-spec"), ok("/api/entity-triage")]).then(([b, f, c, fs, tr]) => {
        if (!alive) return;
        if (b?.phase) { setBuddy(b); if (String(b.phase).startsWith("setup:")) sawSetup.current = true; }
        if (f) setFlow(f);
        if (c) setLlm({ configured: !!c.configured, model: c.options?.model, activity: c.activity });
        if (fs?.active && Array.isArray(fs.features))
          setFeatures(fs.features.map((x: Feat) => ({ name: x.name, transform: x.transform })));
        if (tr?.clusters?.length) setTriage(tr);
        const busy = b && String(b.phase).startsWith("setup:");
        // poll fast while the LLM is mid-call so "sending → received" is visible
        const llmBusy = c && c.activity && c.activity.phase === "sending";
        setTimeout(tick, busy || llmBusy ? 1500 : 5000);
      });
    };
    tick();
    return () => { alive = false; };
  }, []);

  // seed the per-cluster keep toggles once, from the AI's relevance verdict
  useEffect(() => {
    if (triage && !keptInit.current && triage.clusters.length) {
      setKept(Object.fromEntries(triage.clusters.map((c) => [c.label, c.relevant])));
      keptInit.current = true;
    }
  }, [triage]);

  const awaiting = !!triage?.awaiting && !!triage?.has_llm && !approved;
  const keptEstimate = triage
    ? triage.clusters.reduce((n, c) => n + (kept[c.label] ? c.count : 0), 0)
    : 0;

  // live cost estimate for the gated AI pass, recomputed as clusters toggle
  useEffect(() => {
    if (!awaiting) return;
    let live = true;
    fetch("/api/feature-spec/estimate", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entity_count: keptEstimate, model: llm?.model }) })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (live && j) setTriageCost(j.est_usd); }).catch(() => {});
    return () => { live = false; };
  }, [awaiting, keptEstimate, llm?.model]);

  const approveTriage = async () => {
    if (!triage) return;
    setApproving(true);
    const excluded = triage.clusters.filter((c) => c.relevant && !kept[c.label]).map((c) => c.label);
    const included = triage.clusters.filter((c) => !c.relevant && kept[c.label]).map((c) => c.label);
    try {
      await fetch("/api/entity-triage/approve", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ excluded_labels: excluded, included_labels: included }) });
      setApproved(true);
    } catch { /* the poll will reflect state either way */ }
    setApproving(false);
  };

  const phase = buddy?.phase ?? "";
  const pos = ORDER.indexOf(phase);
  const inSetup = phase.startsWith("setup:");
  const started = !!buddy;                 // false while Hearth is still restarting
  const node = (k: string) => flow?.nodes?.[k];
  const hasModel = node("model")?.status === "ok";   // survives a later reload
  const finished = !!buddy && !inSetup && (sawSetup.current || !fastTrack || hasModel);

  const statusFor = (firstIdx: number, lastIdx: number): StageStatus => {
    if (inSetup) {
      if (pos > lastIdx) return "done";
      if (pos >= firstIdx) return "active";
      return "pending";
    }
    if (finished) return "done";
    return "pending";
  };
  // on the fresh arc, only scanning happens now; the rest waits for data
  const laterIfFresh = (s: StageStatus): StageStatus => (fastTrack ? s : "later");

  const greeting = names.length
    ? `Hi ${names.slice(0, 3).join(", ").replace(/, ([^,]*)$/, " and $1")} — I'm Ember.`
    : "Hi — I'm Ember.";
  const sub = !fastTrack
    ? "I'll live on every page, quietly narrating what I'm up to. Here's what just happened."
    : source === "bucket"
      ? "You brought history, so I'm not waiting — watch me learn from it now."
      : "Home Assistant already remembers the last several days — I'm learning from that now, so you don't have to wait a week.";

  const patterns = node("discovery");
  const nPatterns = parseInt(patterns?.value ?? "", 10);
  const youNode = node("you");

  // person.* trackers become "Found <name> from your household"; everything
  // else shows in the sensor chip strip.
  const prettify = (s: string) => s.replace(/_loc$/, "").replace(/_/g, " ");
  const people = bindings.filter((b) => b.role === "person").map((b) => ({
    key: b.id, name: (b.person_id && personMap[b.person_id]) || prettify(b.name) }));
  const sensorChips = bindings.filter((b) => b.role !== "person");
  const scanStatus: StageStatus = inSetup ? statusFor(0, 2) : (started ? "done" : "pending");
  const scanActive = scanStatus === "active";
  const scanDone = scanStatus === "done";
  // bound-sensor count from the flow map (e.g. "96 sensors"); 0/absent early on
  const scanCount = parseInt(node("ha")?.value ?? "", 10) || 0;
  const featStatus = laterIfFresh(statusFor(3, 4));
  const featActive = featStatus === "active";
  const featDone = featStatus === "done";
  const hasTriage = !!(triage && triage.clusters.length);
  const triageStatus: StageStatus = awaiting ? "active"
    : inSetup ? (pos >= 3 ? "done" : "active") : (started ? "done" : "pending");

  // live narration of the AI calls: "sending ... now" → "receiving ... now",
  // but only while the call is actually fresh (so a finished call doesn't keep
  // claiming it's happening "now").
  const act = llm?.activity;
  const model = llm?.model && llm.model !== "auto" ? llm.model : "the LLM";
  const actFresh = !!act?.at && Date.now() - Date.parse(act.at) < 12000;
  const aiDetail = (() => {
    if (awaiting) return "Waiting for you to approve the groups above";
    if (act && actFresh && act.phase === "sending")
      return `Sending to ${model} now — ${act.task.toLowerCase()}${act.sent ? ` (${act.sent})` : ""}`;
    if (act && actFresh && act.phase === "received")
      return `Receiving ${act.task.toLowerCase()} now${act.items != null ? ` — ${act.items} back` : ""}`;
    if (act?.phase === "error")
      return "AI call hit a snag — continuing with the built-in fallback";
    if (inSetup && pos >= 3) return "Your AI suggestions are in";
    return `${model} is reading your sensors`;
  })();
  // AI is "done" only once it's genuinely idle (no fresh call) AND the pipeline
  // has moved past scanning — and always by the time training starts.
  const aiStatus: StageStatus = awaiting ? "pending"
    : !inSetup ? (started ? "done" : "pending")
    : (pos >= 5 || (pos >= 3 && !actFresh)) ? "done" : "active";

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column",
                  alignItems: "center", padding: "48px 18px 80px",
                  background: "var(--bg)" }}>
      <style>{CSS}</style>
      <div style={{ width: "100%", maxWidth: 560, display: "flex", flexDirection: "column",
                    alignItems: "center", textAlign: "center", gap: 14 }}>
        <Ember busy={inSetup || !started} />
        <h1 style={{ margin: 0, fontSize: 26, letterSpacing: "-0.02em" }}>{greeting}</h1>
        <p style={{ margin: 0, color: "var(--text-dim)", fontSize: 15, maxWidth: 460 }}>{sub}</p>
        {!started && (
          <div style={{ fontSize: 13.5, color: "var(--accent)", fontWeight: 600 }}>
            Settling in — getting Hearth up and running
          </div>
        )}
        {inSetup && buddy && (
          <div style={{ fontSize: 13.5, color: "var(--accent)", fontWeight: 600 }}>
            {buddy.title}{buddy.detail ? ` — ${buddy.detail}` : ""}
          </div>
        )}
      </div>

      <div style={{ width: "100%", maxWidth: 560, marginTop: 34 }}>
        <StageRow title="Scanning your home"
          detail={scanCount > 0 ? `${scanCount} sensor${scanCount !== 1 ? "s" : ""} found`
                                : "Reading what you've connected"}
          status={scanStatus}>
          <PersonFinds people={people} active={scanActive} done={scanDone} />
          {scanDone
            ? <EntityStrip bindings={sensorChips} active={false} done={true} />
            : <ScanFeed entities={entities} active={scanActive} done={false} />}
        </StageRow>

        <StageRow title="Sorting your home into groups"
          detail={awaiting ? "Tap a group to keep or skip it, then let AI dig in"
            : hasTriage
              ? `${triage!.clusters.length} groups found${triage!.by === "llm" ? "" : " (by type)"}`
              : "Grouping what I found, keeping what matters"}
          status={triageStatus}>
          {hasTriage && (
            <BubbleCloud triage={triage!}
              kept={awaiting ? kept : undefined}
              onToggle={awaiting ? (l) => setKept((k) => ({ ...k, [l]: !k[l] })) : undefined} />
          )}
          {awaiting && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center",
                          justifyContent: "center", marginTop: 12 }}>
              <button className="btn btn-primary" disabled={approving || keptEstimate === 0}
                      onClick={approveTriage}>
                {approving ? "Sending to AI…"
                  : `Analyse these ${keptEstimate} entities${triageCost != null ? ` · ~${fmtUsd(triageCost)}` : ""}`}
              </button>
              <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
                or keep the free baseline — you can refine later on the Sensors page.
              </span>
            </div>
          )}
        </StageRow>

        {llm?.configured && (
          <StageRow title="Reading your sensors with AI" detail={aiDetail} status={aiStatus}>
            {act && (actFresh || aiStatus === "active") && <AITranscript act={act} />}
          </StageRow>
        )}

        <StageRow title="Building features"
          detail={node("features")?.value ? `${node("features")!.value}` : "Turning raw readings into signals"}
          status={featStatus}>
          {(featActive || featDone) && (
            <FeatureFeed features={features} active={featActive} done={featDone} />
          )}
        </StageRow>

        <StageRow title="Learning your routines"
          detail={node("model")?.value ? `Model ${node("model")!.value}` : "Training a model for each of you"}
          status={laterIfFresh(statusFor(5, 6))} />

        <StageRow title="Finding patterns"
          detail={!isNaN(nPatterns) && nPatterns > 0 ? `${nPatterns} routine${nPatterns !== 1 ? "s" : ""} to name`
                                                     : "Spotting routines worth naming"}
          status={laterIfFresh(statusFor(7, 8))} />
      </div>

      <div style={{ width: "100%", maxWidth: 560, marginTop: 10,
                    display: "flex", flexDirection: "column", gap: 12, alignItems: "center" }}>
        {!fastTrack && (
          <p style={{ color: "var(--text-dim)", fontSize: 14, textAlign: "center", margin: 0, maxWidth: 460 }}>
            I'm recording now. Go live your normal life around the house — that's the training data.
            I'll send your phone a note when the first patterns are ready, in a few days.
          </p>
        )}
        {finished && fastTrack && hasModel && (
          <p style={{ color: "var(--ok, #34D399)", fontWeight: 600, fontSize: 14.5, margin: 0 }}>
            All set — your first model is live. It starts “provisional” and sharpens as you confirm.
          </p>
        )}
        {finished && fastTrack && !hasModel && (
          <p style={{ color: "var(--text-dim)", fontSize: 14, textAlign: "center", margin: 0, maxWidth: 460 }}>
            All set — I've started learning from what Home Assistant remembers. Predictions appear
            as soon as there's enough signal; I'll keep building in the background.
          </p>
        )}

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "center" }}>
          {finished && !isNaN(nPatterns) && nPatterns > 0 && (
            <button className="btn btn-secondary" onClick={() => go("/patterns")}>
              Name {nPatterns} pattern{nPatterns !== 1 ? "s" : ""}
            </button>
          )}
          {finished && youNode?.status === "ask" && (
            <button className="btn btn-secondary" onClick={() => go("/inbox")}>
              {youNode.value}
            </button>
          )}
          <button className="btn btn-primary" disabled={!started} onClick={() => go("/")}>
            {inSetup || !started ? "Skip ahead to my dashboard" : "Go to my dashboard"}
          </button>
        </div>
        {inSetup && (
          <p style={{ color: "var(--text-dim)", fontSize: 12.5, margin: 0 }}>
            You don't have to wait here — this keeps running in the background.
          </p>
        )}
      </div>
    </div>
  );
}

function go(href: string) {
  localStorage.removeItem("hearth.welcome");
  window.location.href = href;
}

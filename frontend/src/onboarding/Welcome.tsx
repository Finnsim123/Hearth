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
 * Two arcs, chosen by whether the wizard imported history (flag in
 * localStorage 'hearth.welcome'):
 *   • fast-track  → the whole pipeline completes in minutes, watched live.
 *   • fresh       → scanning completes, then "recording started, come back".
 *
 * Reached via window.location → /welcome after setup; App renders it full-screen.
 */
import { useEffect, useMemo, useRef, useState } from "react";

type Buddy = { phase: string; tone: string; title: string; detail: string;
               progress: number | null };
type FlowNode = { label: string; value: string; status: string };
type Flow = { phase: string; nodes: Record<string, FlowNode> };
type Binding = { id: number; name: string; role: string; room: string | null;
                 person_id: string | null; enabled: boolean };

// the ordered fast-track phases, used to place the current phase on the arc
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

  const [buddy, setBuddy] = useState<Buddy | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [bindings, setBindings] = useState<Binding[]>([]);
  const [llm, setLlm] = useState<{ configured: boolean; model?: string } | null>(null);
  const [names, setNames] = useState<string[]>(flag.members ?? []);
  const sawSetup = useRef(false);

  useEffect(() => {
    fetch("/api/bindings").then((r) => r.json())
      .then((b: Binding[]) => setBindings((b || []).filter((x) => x.enabled).slice(0, 40)))
      .catch(() => {});
    fetch("/api/connections/llm").then((r) => r.json())
      .then((c) => setLlm({ configured: !!c.configured, model: c.options?.model })).catch(() => {});
    if (!flag.members) {
      fetch("/api/persons").then((r) => r.json())
        .then((ps) => setNames((ps || []).map((p: { name: string }) => p.name))).catch(() => {});
    }
  }, []);

  useEffect(() => {
    let alive = true;
    const tick = () => {
      Promise.all([
        fetch("/api/buddy").then((r) => r.json()).catch(() => null),
        fetch("/api/flow").then((r) => r.json()).catch(() => null),
      ]).then(([b, f]) => {
        if (!alive) return;
        if (b) { setBuddy(b); if (String(b.phase).startsWith("setup:")) sawSetup.current = true; }
        if (f) setFlow(f);
        const busy = b && String(b.phase).startsWith("setup:");
        setTimeout(tick, busy ? 2000 : 5000);
      });
    };
    tick();
    return () => { alive = false; };
  }, []);

  const phase = buddy?.phase ?? "";
  const pos = ORDER.indexOf(phase);
  const inSetup = phase.startsWith("setup:");
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
  const sub = fastTrack
    ? "Your friendly ember. You brought history, so I'm not waiting a week — watch me work through it now."
    : "Your friendly ember. I'll live on every page, quietly narrating what I'm up to. Here's what just happened.";

  const patterns = node("discovery");
  const nPatterns = parseInt(patterns?.value ?? "", 10);
  const youNode = node("you");

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column",
                  alignItems: "center", padding: "48px 18px 80px",
                  background: "var(--bg)" }}>
      <style>{CSS}</style>
      <div style={{ width: "100%", maxWidth: 560, display: "flex", flexDirection: "column",
                    alignItems: "center", textAlign: "center", gap: 14 }}>
        <Ember busy={inSetup} />
        <h1 style={{ margin: 0, fontSize: 26, letterSpacing: "-0.02em" }}>{greeting}</h1>
        <p style={{ margin: 0, color: "var(--text-dim)", fontSize: 15, maxWidth: 460 }}>{sub}</p>
        {inSetup && buddy && (
          <div style={{ fontSize: 13.5, color: "var(--accent)", fontWeight: 600 }}>
            {buddy.title}{buddy.detail ? ` — ${buddy.detail}` : ""}
          </div>
        )}
      </div>

      <div style={{ width: "100%", maxWidth: 560, marginTop: 34 }}>
        <StageRow title="Scanning your home"
          detail={node("ha")?.value ? `${node("ha")!.value} found` : "Reading what you've connected"}
          status={inSetup ? statusFor(0, 2) : "done"}>
          <EntityStrip bindings={bindings}
            active={inSetup ? statusFor(0, 2) === "active" : false}
            done={!inSetup || statusFor(0, 2) === "done"} />
        </StageRow>

        {llm?.configured && (
          <StageRow title="Reading your sensors with AI"
            detail={llm.model ? `${llm.model} is naming roles and proposing features` : "Proposing smarter sensor mappings"}
            status={inSetup ? (pos >= 3 ? "done" : "active") : "done"} />
        )}

        <StageRow title="Building features"
          detail={node("features")?.value ? `${node("features")!.value}` : "Turning raw readings into signals"}
          status={laterIfFresh(statusFor(3, 4))} />

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
        {finished && fastTrack && (
          <p style={{ color: "var(--ok, #34D399)", fontWeight: 600, fontSize: 14.5, margin: 0 }}>
            All set — your first model is live. It starts “provisional” and sharpens as you confirm.
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
          <button className="btn btn-primary" onClick={() => go("/")}>
            {inSetup ? "Skip ahead to my dashboard" : "Go to my dashboard"}
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

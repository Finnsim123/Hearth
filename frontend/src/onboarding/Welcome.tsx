/**
 * Welcome — the live hand-off between the wizard and the dashboard.
 *
 * Introduces the buddy (Ember) and then *shows* the rest of the pipeline running
 * on this home's real data. Scanning entities + sorting them into groups already
 * happened IN THE WIZARD (the bubble-cloud step) before setup completed, so this
 * screen no longer repeats them — it picks up at reading sensors with AI →
 * building features → training → finding patterns. Every number is live, pulled
 * from GET /api/buddy (phase) and GET /api/flow (this instance's counts);
 * nothing here is faked.
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
type Llm = { configured: boolean; model?: string; activity?: LlmActivity | null };

// the full pipeline in order — seed (scan → sort → map) THEN fast-track
// (import → features → train → discover). One monotonic position so each UI
// stage owns a contiguous slice and only ONE is ever active at a time.
const ORDER = [
  "setup:scanning", "setup:triaging", "setup:mapping",                       // 0,1,2 (seed)
  "setup:importing", "setup:imported", "setup:pruned_empty",                 // 3,4,5
  "setup:building_features", "setup:features_built",                         // 6,7
  "setup:training", "setup:trained",                                         // 8,9
  "setup:discovering", "setup:discovered",                                   // 10,11
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

export default function Welcome() {
  const flag = useMemo(() => {
    try { return JSON.parse(localStorage.getItem("hearth.welcome") || "{}"); }
    catch { return {}; }
  }, []);
  const fastTrack: boolean = !!flag.fastTrack;
  const source: string = flag.source ?? "recorder";   // "bucket" | "recorder"

  const [buddy, setBuddy] = useState<Buddy | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [features, setFeatures] = useState<Feat[]>([]);
  const [llm, setLlm] = useState<Llm | null>(null);
  const [names, setNames] = useState<string[]>(flag.members ?? []);
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
    loadOnce("/api/persons", (ps) => {
      const list = (ps as { id: string; name: string; notify_system?: boolean }[]) || [];
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
                   ok("/api/feature-spec")]).then(([b, f, c, fs]) => {
        if (!alive) return;
        if (b?.phase) { setBuddy(b); if (String(b.phase).startsWith("setup:")) sawSetup.current = true; }
        if (f) setFlow(f);
        if (c) setLlm({ configured: !!c.configured, model: c.options?.model, activity: c.activity });
        if (fs?.active && Array.isArray(fs.features))
          setFeatures(fs.features.map((x: Feat) => ({ name: x.name, transform: x.transform })));
        const busy = b && String(b.phase).startsWith("setup:");
        // poll fast while the LLM is mid-call so "sending → received" is visible
        const llmBusy = c && c.activity && c.activity.phase === "sending";
        setTimeout(tick, busy || llmBusy ? 1500 : 5000);
      });
    };
    tick();
    return () => { alive = false; };
  }, []);

  const phase = buddy?.phase ?? "";
  const pos = ORDER.indexOf(phase);
  const inSetup = phase.startsWith("setup:");
  const started = !!buddy;                 // false while Hearth is still restarting
  const node = (k: string) => flow?.nodes?.[k];
  const hasModel = node("model")?.status === "ok";   // survives a later reload
  const finished = !!buddy && !inSetup && (sawSetup.current || !fastTrack || hasModel);

  // Strict, single-active gating: a stage is active only while the live phase
  // sits inside its slice of ORDER, done once the phase has moved past it, and
  // pending before. Exactly one stage is ever active — the pipeline waits.
  const statusFor = (firstIdx: number, lastIdx: number): StageStatus => {
    if (!started) return "pending";
    if (!inSetup) return "done";            // setup finished → everything done
    if (pos < 0) return "pending";
    if (pos > lastIdx) return "done";
    if (pos >= firstIdx) return "active";
    return "pending";
  };
  // on the fresh arc (no warm-start data), the post-sorting stages never run
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

  // stage slices of ORDER (scan/sort happened in the wizard; we start at map):
  //   2 map(AI) · 3-7 build features · 8-9 train · 10-11 patterns
  const aiStatus = statusFor(2, 2);
  const featStatus = laterIfFresh(statusFor(3, 7));
  const featActive = featStatus === "active";
  const featDone = featStatus === "done";

  // live narration of the AI calls — only while a call is genuinely fresh.
  const act = llm?.activity;
  const model = llm?.model && llm.model !== "auto" ? llm.model : "the LLM";
  const actFresh = !!act?.at && Date.now() - Date.parse(act.at) < 12000;
  const aiDetail = (() => {
    if (aiStatus === "pending") return "Up next";
    if (aiStatus === "done") return "Your AI suggestions are in";
    if (act && actFresh && act.phase === "sending")
      return `Sending to ${model} now — ${act.task.toLowerCase()}${act.sent ? ` (${act.sent})` : ""}`;
    if (act && actFresh && act.phase === "received")
      return `Receiving ${act.task.toLowerCase()} now${act.items != null ? ` — ${act.items} back` : ""}`;
    if (act?.phase === "error")
      return "AI call hit a snag — continuing with the built-in fallback";
    return `${model} is reading your sensors`;
  })();

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
          status={laterIfFresh(statusFor(8, 9))} />

        <StageRow title="Finding patterns"
          detail={!isNaN(nPatterns) && nPatterns > 0 ? `${nPatterns} routine${nPatterns !== 1 ? "s" : ""} to name`
                                                     : "Spotting routines worth naming"}
          status={laterIfFresh(statusFor(10, 11))} />
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

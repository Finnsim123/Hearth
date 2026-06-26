/**
 * Landing — the first thing anyone sees, both before first-run setup and before
 * login. Sells what Hearth is and why, then a single big call to action that
 * adapts: "Let's get started" → the setup wizard (fresh install), or "Log in" →
 * the sign-in screen (already set up). Pure presentation; App decides which.
 */
import { useState, type ReactNode } from "react";

const ACCENT = "var(--accent)";

function HaMark() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden>
      <path d="M12 3 2.5 11.2V21h19v-9.8L12 3z" fill="#41BDF5" />
      <path d="M8 20v-5l2 2 2-3 2 3 2-2v5" fill="none" stroke="#fff"
            strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
function InfluxMark() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden>
      <rect x="3" y="3" width="18" height="18" rx="5" fill="#6A35FF" />
      <path d="M6.5 15.5l3.5-4.5 3 2.2 4.5-6" fill="none" stroke="#fff"
            strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Official logo from /public/logos (tries .svg then .png), else the built-in mark. */
function Logo({ base, alt, fallback }: { base: string; alt: string; fallback: ReactNode }) {
  const exts = ["svg", "png"];
  const [i, setI] = useState(0);
  if (i >= exts.length) return <>{fallback}</>;
  return <img src={`/logos/${base}.${exts[i]}`} alt={alt} width={22} height={22}
              style={{ display: "block", objectFit: "contain" }} onError={() => setI((n) => n + 1)} />;
}

const Arrow = () => (
  <span aria-hidden style={{ alignSelf: "center", color: "var(--text-dim)", fontSize: 20, flex: "0 0 auto" }}>→</span>
);

function Pill({ children }: { children: ReactNode }) {
  return (
    <span style={{ fontSize: 11, padding: "3px 8px", borderRadius: 999, whiteSpace: "nowrap",
                   background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-dim)" }}>
      {children}
    </span>
  );
}

function Flow() {
  const signals = ["motion", "stove power", "fridge door", "presence", "lights",
                   "TV", "humidity", "phone"];
  return (
    <section style={{ width: "100%", marginTop: 30, display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ fontSize: 13.5, color: "var(--text-dim)", textAlign: "center" }}>
        It answers one question: <strong style={{ color: "var(--text)" }}>"What are you doing right now?"</strong>
      </div>

      <div style={{ display: "flex", gap: 10, justifyContent: "center", flexWrap: "wrap", alignItems: "stretch" }}>
        {/* MANY raw signals in */}
        <div style={{ flex: "1 1 230px", maxWidth: 300, background: "var(--surface)",
                      border: "1px solid var(--border)", borderRadius: 14, padding: "13px 14px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 8 }}>
            <Logo base="home-assistant" alt="Home Assistant" fallback={<HaMark />} />
            <strong style={{ fontSize: 13.5 }}>Your sensors</strong>
            <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-dim)" }}>dozens of raw signals</span>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
            {signals.map((s) => <Pill key={s}>{s}</Pill>)}
          </div>
        </div>

        <Arrow />

        {/* the brain */}
        <div style={{ flex: "0 1 140px", alignSelf: "center", textAlign: "center",
                      background: "var(--surface)", border: `1px solid ${ACCENT}`, borderRadius: 14,
                      padding: "14px 12px", boxShadow: `0 0 0 3px color-mix(in srgb, ${ACCENT} 14%, transparent)` }}>
          <div style={{ display: "flex", justifyContent: "center", marginBottom: 6 }}><Ember size={24} /></div>
          <div style={{ fontSize: 14, fontWeight: 600, color: ACCENT }}>Hearth</div>
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>makes sense of it all</div>
        </div>

        <Arrow />

        {/* ONE clear answer out */}
        <div style={{ flex: "1 1 220px", maxWidth: 280, display: "flex", flexDirection: "column",
                      justifyContent: "center", borderRadius: 14, padding: "14px",
                      background: `color-mix(in srgb, ${ACCENT} 9%, var(--surface))`, border: `1px solid ${ACCENT}` }}>
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 5 }}>Home Assistant now knows:</div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <span style={{ fontSize: 15, fontWeight: 600 }}>You're</span>
            <span style={{ fontSize: 24, fontWeight: 700, color: ACCENT }}>cooking</span>
          </div>
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 9,
                    background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 12,
                    padding: "9px 14px", fontSize: 12.5, color: "var(--text-dim)" }}>
        <Logo base="influxdb" alt="InfluxDB" fallback={<InfluxMark />} />
        <span><strong style={{ color: "var(--text)" }}>InfluxDB</strong> keeps your history — locally, never leaving the house.</span>
      </div>
      <div style={{ fontSize: 12.5, color: "var(--text-dim)", textAlign: "center" }}>
        ↻ You confirm when Hearth asks — it gets sharper every week.
      </div>
    </section>
  );
}

function Ember({ size = 40 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <path d="M16 4 L28 14 V26 a2 2 0 0 1 -2 2 H6 a2 2 0 0 1 -2 -2 V14 Z"
            stroke="currentColor" strokeWidth="2.4" strokeLinejoin="round" />
      <circle cx="16" cy="20" r="3.6" fill={ACCENT} />
    </svg>
  );
}

const VALUES: { title: string; body: string }[] = [
  { title: "Runs on your own hardware",
    body: "No cloud, no account but yours. A Pi, NAS or mini-PC is plenty — your data never leaves the house." },
  { title: "Your activities, your words",
    body: "Cooking, gaming, winding down — you define them. Hearth spots the patterns; you name them in a tap." },
  { title: "A glass box, not a black box",
    body: "See the accuracy, the signals behind each call — and an honest 'I can't do this yet' when it can't." },
  { title: "A fact beats a guess",
    body: "When your sensors know you're out or asleep, Hearth knows it — no model, no mistakes. Predictions fill in the rest." },
];

export default function Landing({ setup, onEnter }: { setup: boolean; onEnter: () => void }) {
  return (
    <main style={{ minHeight: "100vh", display: "flex", flexDirection: "column",
                   alignItems: "center", padding: "8vh 20px 40px" }}>
      <div style={{ width: "100%", maxWidth: 760, display: "flex", flexDirection: "column",
                    alignItems: "center", textAlign: "center", gap: 22 }}>

        <div style={{ display: "flex", alignItems: "center", gap: 12, color: "var(--text)" }}>
          <Ember size={44} />
          <span style={{ fontSize: 34, fontWeight: 600, letterSpacing: "-0.5px" }}>hearth</span>
        </div>

        <h1 style={{ margin: 0, fontSize: 30, lineHeight: 1.2, fontWeight: 600, maxWidth: 620 }}>
          Your home, truly understood.
        </h1>
        <p style={{ margin: 0, fontSize: 17, lineHeight: 1.6, color: "var(--text-dim)", maxWidth: 600 }}>
          Most home automations fire on a switch or a schedule. Hearth makes yours respond to
          what you're actually doing — it learns activities like sleeping, cooking and working
          from the sensors you already have, and feeds them to Home Assistant as entities you
          can automate on.
        </p>

        <button onClick={onEnter} className="btn btn-primary"
          style={{ fontSize: 16, padding: "12px 26px", marginTop: 4, minWidth: 200 }}>
          {setup ? "Let's get started →" : "Log in"}
        </button>
        {setup && (
          <span style={{ fontSize: 13, color: "var(--text-dim)" }}>
            Takes a few minutes · you approve every step
          </span>
        )}

        <Flow />

        <div style={{ display: "grid", gap: 14, marginTop: 26, width: "100%",
                      gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", textAlign: "left" }}>
          {VALUES.map((v) => (
            <div key={v.title} style={{ background: "var(--surface)", border: "1px solid var(--border)",
                                        borderRadius: 14, padding: "16px 18px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 6 }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: ACCENT, flex: "none" }} />
                <strong style={{ fontSize: 15 }}>{v.title}</strong>
              </div>
              <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.55, color: "var(--text-dim)" }}>{v.body}</p>
            </div>
          ))}
        </div>

        <div style={{ marginTop: 22, fontSize: 13.5, color: "var(--text-dim)", lineHeight: 1.7 }}>
          <strong style={{ color: "var(--text)" }}>How it works:</strong> connect Home Assistant →
          Hearth reads your sensors once and learns for a few days → it asks "was that right?" when
          unsure → labelled activities flow back to HA. An optional AI key just speeds up setup;
          after the first model trains, everything is 100% local.
        </div>

        <div style={{ marginTop: 18, fontSize: 13, color: "var(--text-dim)" }}>
          Open source ·{" "}
          <a href="https://github.com/Finnsim123/Hearth" target="_blank" rel="noreferrer"
             style={{ color: ACCENT }}>GitHub</a>
          {" "}· local-first · MIT
        </div>
      </div>
    </main>
  );
}

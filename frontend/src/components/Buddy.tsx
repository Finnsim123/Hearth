/**
 * The ember buddy — a small character, top-right on every page, that narrates
 * what Hearth is doing: importing history, building features, finding patterns,
 * training, then resting as "watching & predicting". Driven by GET /api/buddy
 * (one source of truth, shared with the dashboard). Collapses to just the orb
 * in steady state; expands during setup or when it needs you.
 *
 * Responsiveness: besides polling, it listens on buddyBus — when you complete an
 * action (answer a question, approve sensors, name a pattern), the page fires a
 * cheer and the buddy immediately acknowledges it ("Thanks — now I know more")
 * and re-polls, instead of sitting on the stale prompt until the next tick.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useIsMobile } from "../useMedia";
import { onBuddyCheer, type BuddyCheer } from "./buddyBus";

type State = {
  phase: string; tone: string; title: string; detail: string;
  progress: number | null; cta: { label: string; href: string } | null;
  ack?: string | null;
};

const TONE: Record<string, string> = {
  work: "var(--accent)", ask: "var(--accent)", news: "var(--accent)",
  live: "var(--ok, #34D399)", alert: "var(--danger)", error: "var(--danger)",
};

function Flame({ color }: { color: string }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden>
      <path d="M12 2.2c2.1 4 5.6 5.6 4 10.1A4.2 4.2 0 0 1 7.7 13C7 9.6 9.6 8 12 2.2z"
            style={{ fill: color }} />
      <path d="M12 12.2c1 1.6 1.7 2.5 1.1 4.2a2.1 2.1 0 0 1-4.1-.6c0-1.6 1.6-2.2 3-3.6z"
            style={{ fill: "rgba(255,255,255,0.6)" }} />
    </svg>
  );
}

export default function Buddy() {
  const [s, setS] = useState<State | null>(null);
  const [cheer, setCheer] = useState<BuddyCheer | null>(null);
  const [override, setOverride] = useState<boolean | null>(() => {
    const v = localStorage.getItem("hearth.buddy.collapsed");
    return v === null ? null : v === "1";
  });
  const isMobile = useIsMobile();
  const navigate = useNavigate();
  const timer = useRef<number>();
  const cheerTimer = useRef<number>();
  const tickRef = useRef<() => void>();

  useEffect(() => {
    let alive = true;
    const tick = () => {
      fetch("/api/buddy").then((r) => r.json()).then((d: State) => {
        if (!alive) return;
        setS(d);
        const fast = d.tone === "work" || d.phase.startsWith("setup:");
        const next = fast ? 4000 : d.tone === "live" ? 60000 : 15000;
        timer.current = window.setTimeout(tick, next);
      }).catch(() => { timer.current = window.setTimeout(tick, 20000); });
    };
    tickRef.current = tick;
    tick();
    return () => { alive = false; window.clearTimeout(timer.current); };
  }, []);

  // React immediately to user actions (see buddyBus): show a warm ack, then
  // re-poll so the real state is fresh by the time the ack fades.
  useEffect(() => onBuddyCheer((c) => {
    setCheer(c);
    window.clearTimeout(cheerTimer.current);
    window.setTimeout(() => {        // let the backend settle, then refresh
      window.clearTimeout(timer.current);
      tickRef.current?.();
    }, 1200);
    cheerTimer.current = window.setTimeout(() => setCheer(null), 4200);
  }), []);

  if (!s && !cheer) return null;
  // a cheer takes over the bubble briefly, forcing it open
  const d: State = cheer
    ? { phase: "cheer", tone: "live", title: cheer.title, detail: cheer.detail ?? "",
        progress: null, cta: null, ack: null }
    : (s as State);

  // acknowledge a "what's new" announcement: mark the build seen, then re-poll.
  const ackNews = () => {
    if (!d.ack) return;
    fetch(d.ack, { method: "POST" }).catch(() => {}).finally(() => {
      window.clearTimeout(timer.current);
      tickRef.current?.();
    });
  };
  const color = TONE[d.tone] ?? "var(--accent)";
  const working = d.tone === "work" || d.tone === "ask";
  const collapsed = cheer ? false : (override ?? (d.tone === "live"));   // rest = just the orb
  const toggle = () => {
    const next = !collapsed;
    setOverride(next);
    localStorage.setItem("hearth.buddy.collapsed", next ? "1" : "0");
  };
  const attention = d.tone === "ask" || d.tone === "alert" || d.tone === "error"
    || d.tone === "news";

  const orb = (
    <span className={`buddy-orb${working ? " anim" : ""}`}
          style={{ width: 40, height: 40, borderRadius: "50%", flexShrink: 0,
                   display: "inline-flex", alignItems: "center", justifyContent: "center",
                   background: `color-mix(in srgb, ${color} 18%, var(--surface))`,
                   border: `1.5px solid ${color}`,
                   boxShadow: `0 0 0 4px color-mix(in srgb, ${color} 12%, transparent)` }}>
      <Flame color={color} />
    </span>
  );

  const wrap: React.CSSProperties = {
    position: "fixed", top: isMobile ? 60 : 16, right: isMobile ? 12 : 18, zIndex: 25,
    maxWidth: isMobile ? "calc(100vw - 24px)" : 320,
  };

  if (collapsed) {
    return (
      <div style={wrap}>
        <button onClick={toggle} aria-label={`Hearth: ${d.title}`} title={d.title}
                style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
                         position: "relative" }}>
          {orb}
          {attention && (
            <span style={{ position: "absolute", top: -1, right: -1, width: 11, height: 11,
                           borderRadius: "50%", background: color,
                           border: "2px solid var(--bg)" }} />
          )}
        </button>
      </div>
    );
  }

  return (
    <div style={wrap}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 11, padding: "11px 12px",
                    background: "var(--surface)", border: "1px solid var(--border)",
                    borderRadius: 14, boxShadow: "0 6px 24px rgba(0,0,0,0.22)" }}>
        {orb}
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600 }}>{d.title}</div>
          {d.detail && (
            <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginTop: 1 }}>{d.detail}</div>
          )}
          {typeof d.progress === "number" && (
            <div style={{ height: 5, borderRadius: 99, background: "var(--surface-2)",
                          marginTop: 7, overflow: "hidden" }}>
              <div style={{ height: "100%", borderRadius: 99, background: color,
                            width: `${Math.round(d.progress * 100)}%`, transition: "width .5s ease" }} />
            </div>
          )}
          {d.cta && (
            <button className="btn btn-secondary"
                    style={{ marginTop: 9, fontSize: 12.5, minHeight: 30, padding: "4px 10px" }}
                    onClick={() => {
                      const h = d.cta!.href;
                      if (/^https?:\/\//.test(h)) window.open(h, "_blank", "noopener");
                      else navigate(h);
                    }}>
              {d.cta.label}
            </button>
          )}
          {d.ack && (
            <button className="btn btn-secondary"
                    style={{ marginTop: 9, fontSize: 12.5, minHeight: 30, padding: "4px 10px" }}
                    onClick={ackNews}>
              Got it
            </button>
          )}
        </div>
        <button onClick={toggle} aria-label="Collapse" title="Collapse"
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-dim)",
                         padding: 2, lineHeight: 1, fontSize: 16, flexShrink: 0 }}>
          ×
        </button>
      </div>
    </div>
  );
}

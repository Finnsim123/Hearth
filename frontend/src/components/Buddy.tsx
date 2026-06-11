/**
 * The ember buddy — a small character, top-right on every page, that narrates
 * what Hearth is doing: importing history, building features, finding patterns,
 * training, then resting as "watching & predicting". Driven by GET /api/buddy
 * (one source of truth, shared with the dashboard). Collapses to just the orb
 * in steady state; expands during setup or when it needs you.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useIsMobile } from "../useMedia";

type State = {
  phase: string; tone: string; title: string; detail: string;
  progress: number | null; cta: { label: string; href: string } | null;
};

const TONE: Record<string, string> = {
  work: "var(--accent)", ask: "var(--accent)",
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
  const [override, setOverride] = useState<boolean | null>(() => {
    const v = localStorage.getItem("hearth.buddy.collapsed");
    return v === null ? null : v === "1";
  });
  const isMobile = useIsMobile();
  const navigate = useNavigate();
  const timer = useRef<number>();

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
    tick();
    return () => { alive = false; window.clearTimeout(timer.current); };
  }, []);

  if (!s) return null;
  const color = TONE[s.tone] ?? "var(--accent)";
  const working = s.tone === "work" || s.tone === "ask";
  const collapsed = override ?? (s.tone === "live");      // rest = just the orb
  const toggle = () => {
    const next = !collapsed;
    setOverride(next);
    localStorage.setItem("hearth.buddy.collapsed", next ? "1" : "0");
  };
  const attention = s.tone === "ask" || s.tone === "alert" || s.tone === "error";

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
        <button onClick={toggle} aria-label={`Hearth: ${s.title}`} title={s.title}
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
          <div style={{ fontSize: 13.5, fontWeight: 600 }}>{s.title}</div>
          {s.detail && (
            <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginTop: 1 }}>{s.detail}</div>
          )}
          {typeof s.progress === "number" && (
            <div style={{ height: 5, borderRadius: 99, background: "var(--surface-2)",
                          marginTop: 7, overflow: "hidden" }}>
              <div style={{ height: "100%", borderRadius: 99, background: color,
                            width: `${Math.round(s.progress * 100)}%`, transition: "width .5s ease" }} />
            </div>
          )}
          {s.cta && (
            <button className="btn btn-secondary"
                    style={{ marginTop: 9, fontSize: 12.5, minHeight: 30, padding: "4px 10px" }}
                    onClick={() => navigate(s.cta!.href)}>
              {s.cta.label}
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

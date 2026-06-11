/**
 * Shell: nav + routes, styled with the Hearth design tokens (docs/DESIGN.md).
 * Each page is a stub matching docs/UI_SPEC.md; implement in roadmap order.
 */
import { useEffect, useState } from "react";
import { Routes, Route, NavLink, Link, useLocation, useNavigate } from "react-router-dom";
import "./theme.css";
import { applyTheme, getTheme, initTheme, type ThemeMode } from "./theme";

initTheme();
import Dashboard from "./pages/Dashboard";
import Inbox from "./pages/Inbox";
import Activities from "./pages/Activities";
import Patterns from "./pages/Patterns";
import Models from "./pages/Models";
import Sensors from "./pages/Sensors";
import Methodology from "./pages/Methodology";
import Settings from "./pages/Settings";
import Onboarding from "./pages/Onboarding";
import Login from "./components/Login";
import ProgressWait from "./components/ProgressWait";

// The three pipeline stages — collapsible groups (Data → Model → Output).
const PIPELINE: { label: string; items: readonly (readonly [string, string])[] }[] = [
  { label: "Inputs", items: [["/sensors", "Sensors"]] },
  { label: "The model", items: [["/activities", "Activities"], ["/patterns", "Patterns"], ["/models", "Models"]] },
  { label: "Predictions", items: [["/inbox", "Inbox"]] },
];

const navLinkStyle = ({ isActive }: { isActive: boolean }) => ({
  display: "block", padding: "7px 10px", borderRadius: 8, fontSize: 14,
  textDecoration: "none", fontWeight: isActive ? 600 : 500,
  color: isActive ? "var(--text)" : "var(--text-dim)",
  background: isActive ? "var(--surface-2)" : "transparent",
});

const Chevron = ({ open }: { open: boolean }) => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden
       style={{ transition: "transform .18s", transform: open ? "none" : "rotate(-90deg)" }}>
    <path d="M6 9l6 6 6-6" />
  </svg>
);

const ICON = { width: 17, height: 17, viewBox: "0 0 24 24", fill: "none",
  stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const, "aria-hidden": true };
const SunIcon = () => (
  <svg {...ICON}><circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </svg>
);
const MoonIcon = () => (
  <svg {...ICON}><path d="M21 12.8A8.5 8.5 0 1 1 11.2 3a6.5 6.5 0 0 0 9.8 9.8z" /></svg>
);
const AutoIcon = () => (   // "match system": a half-filled disc
  <svg {...ICON}><circle cx="12" cy="12" r="9" />
    <path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor" stroke="none" />
  </svg>
);
const THEMES: [ThemeMode, () => JSX.Element, string][] = [
  ["light", SunIcon, "Light"], ["system", AutoIcon, "Match system"], ["dark", MoonIcon, "Dark"],
];

function Mark() {
  // logo-ember mark (brand/logo-ember.svg), inline for the nav
  return (
    <svg width="22" height="22" viewBox="0 0 32 32" fill="none" aria-hidden>
      <path
        d="M16 4 L28 14 V26 a2 2 0 0 1 -2 2 H6 a2 2 0 0 1 -2 -2 V14 Z"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="20" r="3.5" fill="var(--accent)" />
    </svg>
  );
}

function UpdatingScreen() {
  return (
    <ProgressWait
      title="Updating Hearth…"
      sub="This page reloads automatically when the new version is up — usually about two minutes."
      estimateS={120}
      stages={[
        [0, "Asking the host to pull the latest version…"],
        [15, "Pulling and rebuilding the container…"],
        [75, "Rebuilding — almost there…"],
        [150, "Taking longer than usual — bigger updates rebuild more layers. Still going."],
      ]}
    />
  );
}

type AuthState = "loading" | "setup" | "login" | "ready";
type UpdateInfo = { build: string; behind: number; latest_subject?: string; pending?: boolean };

export default function App() {
  const [mode, setMode] = useState<ThemeMode>(getTheme());
  const [auth, setAuth] = useState<AuthState>("loading");
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const [updating, setUpdating] = useState(false);
  const [navCollapsed, setNavCollapsed] = useState<Set<string>>(
    () => new Set(JSON.parse(localStorage.getItem("hearth.nav.collapsed") || "[]")));
  const navigate = useNavigate();
  const location = useLocation();

  const toggleGroup = (label: string) => setNavCollapsed((prev) => {
    const next = new Set(prev);
    next.has(label) ? next.delete(label) : next.add(label);
    localStorage.setItem("hearth.nav.collapsed", JSON.stringify([...next]));
    return next;
  });
  // the group holding the current page is always shown (never hide the active item)
  const currentGroup = PIPELINE.find((g) => g.items.some(([to]) => to === location.pathname))?.label;
  const setTheme = (m: ThemeMode) => { applyTheme(m); setMode(m); };

  const checkAuth = async () => {
    try {
      const h = await fetch("/api/health").then((r) => r.json());
      if (h.needs_setup) {
        setAuth("setup");
        if (location.pathname !== "/onboarding") navigate("/onboarding");
        return;
      }
      const me = await fetch("/api/auth/me");
      setAuth(me.ok ? "ready" : "login");
    } catch { setAuth("login"); }
  };
  useEffect(() => { checkAuth(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, []);

  useEffect(() => {
    if (auth !== "ready") return;
    const poll = () => fetch("/api/system/update").then((r) => r.json())
      .then(setUpdate).catch(() => {});
    poll();
    const id = setInterval(poll, 5 * 60_000);
    return () => clearInterval(id);
  }, [auth]);

  const runUpdate = async () => {
    if (!update) return;
    if (!window.confirm(`Update Hearth to the latest version?\n\n${update.behind} commit(s) behind — latest: "${update.latest_subject ?? ""}"\n\nHearth rebuilds and restarts (~2 min).`)) return;
    await fetch("/api/system/update", { method: "POST" });
    setUpdating(true);
    const startBuild = update.build;
    const id = setInterval(async () => {
      try {
        const h = await fetch("/api/health").then((r) => r.json());
        if (h.build && h.build !== startBuild) {
          clearInterval(id);
          window.location.reload();
        }
      } catch { /* rebuilding */ }
    }, 5000);
  };

  if (updating) return <UpdatingScreen />;

  if (auth === "loading") return null;
  if (auth === "setup") {
    // setup mode: wizard only — no nav, no other routes
    return (
      <main style={{ maxWidth: "var(--content-max)", margin: "0 auto", padding: 24 }}>
        <Onboarding />
      </main>
    );
  }
  if (auth === "login") return <Login onSuccess={() => { setAuth("ready"); navigate("/"); }} />;

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <aside
        style={{
          width: 214, flexShrink: 0, position: "sticky", top: 0, height: "100vh",
          display: "flex", flexDirection: "column", gap: 3, padding: "16px 12px",
          background: "var(--surface)", borderRight: "1px solid var(--border)",
          overflowY: "auto",
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600,
                       letterSpacing: "-0.02em", padding: "4px 8px 14px" }}>
          <Mark />
          hearth
        </span>

        <NavLink to="/" end style={navLinkStyle}>Dashboard</NavLink>

        {PIPELINE.map((g) => {
          const open = !navCollapsed.has(g.label) || g.label === currentGroup;
          return (
            <div key={g.label} style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 12 }}>
              <button onClick={() => toggleGroup(g.label)}
                style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                         background: "none", border: "none", cursor: "pointer", width: "100%",
                         padding: "4px 10px 4px", color: "var(--text-dim)", fontSize: 10.5,
                         textTransform: "uppercase", letterSpacing: "0.07em" }}>
                {g.label}
                <Chevron open={open} />
              </button>
              {open && g.items.map(([to, label]) => (
                <NavLink key={to} to={to} style={navLinkStyle}>{label}</NavLink>
              ))}
            </div>
          );
        })}

        <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 2, paddingTop: 14 }}>
          {update && update.behind > 0 && (
            <button className="btn btn-primary" style={{ width: "100%", fontSize: 13, marginBottom: 6 }}
                    onClick={runUpdate} title={update.latest_subject}>
              Update ({update.behind})
            </button>
          )}
          <NavLink to="/methodology" style={navLinkStyle}>How it works</NavLink>
          <NavLink to="/settings" end style={navLinkStyle}>Settings</NavLink>
          <Link to="/settings#account" style={{ ...navLinkStyle({ isActive: false }) }}>Account</Link>

          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                        marginTop: 10, padding: "0 8px" }}>
            <div style={{ display: "inline-flex", border: "1px solid var(--border)",
                          borderRadius: 999, overflow: "hidden" }}>
              {THEMES.map(([m, Glyph, title]) => (
                <button key={m} onClick={() => setTheme(m)} title={title} aria-label={title}
                  style={{ display: "flex", alignItems: "center", justifyContent: "center",
                           border: "none", cursor: "pointer", padding: "5px 9px", lineHeight: 1,
                           background: mode === m ? "var(--accent)" : "transparent",
                           color: mode === m ? "#fff" : "var(--text-dim)" }}>
                  <Glyph />
                </button>
              ))}
            </div>
            <button className="btn btn-ghost" style={{ fontSize: 12.5, padding: "6px 8px" }}
                    onClick={async () => { await fetch("/api/auth/logout", { method: "POST" }); setAuth("login"); }}>
              Sign out
            </button>
          </div>
        </div>
      </aside>
      <main style={{ flex: 1, minWidth: 0 }}>
        <div style={{ maxWidth: "var(--content-max)", margin: "0 auto", padding: 24 }}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/inbox" element={<Inbox />} />
            <Route path="/activities" element={<Activities />} />
            <Route path="/patterns" element={<Patterns />} />
            <Route path="/models" element={<Models />} />
            <Route path="/sensors" element={<Sensors />} />
            <Route path="/methodology" element={<Methodology />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/onboarding" element={<Onboarding />} />
          </Routes>
        </div>
      </main>
    </div>
  );
}

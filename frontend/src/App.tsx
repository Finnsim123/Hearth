/**
 * Shell: nav + routes, styled with the Hearth design tokens (docs/DESIGN.md).
 * Each page is a stub matching docs/UI_SPEC.md; implement in roadmap order.
 */
import { useEffect, useState } from "react";
import { Routes, Route, NavLink, useLocation, useNavigate } from "react-router-dom";
import "./theme.css";
import { cycleTheme, getTheme, initTheme, type ThemeMode } from "./theme";

initTheme();
import Dashboard from "./pages/Dashboard";
import Inbox from "./pages/Inbox";
import Activities from "./pages/Activities";
import Patterns from "./pages/Patterns";
import Models from "./pages/Models";
import Sensors from "./pages/Sensors";
import Settings from "./pages/Settings";
import Onboarding from "./pages/Onboarding";
import Login from "./components/Login";

const tabs = [
  ["/", "Dashboard"],
  ["/inbox", "Inbox"],
  ["/activities", "Activities"],
  ["/patterns", "Patterns"],
  ["/models", "Models"],
  ["/sensors", "Sensors"],
  ["/settings", "Settings"],
] as const;

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

const themeLabel: Record<ThemeMode, string> = {
  system: "Theme: system",
  light: "Theme: light",
  dark: "Theme: dark",
};

/** Full-screen update progress. The real duration depends on the host (git
 *  pull + docker build), so the bar eases toward an ~2 min estimate and never
 *  pretends to be done — App reloads the page the moment the new build is up. */
const UPDATE_ESTIMATE_S = 120;

function UpdatingScreen() {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(id);
  }, []);
  // asymptotic fill: hits ~63% at the estimate, ~86% at 2× — never 100
  const pct = Math.min(97, (1 - Math.exp(-elapsed / UPDATE_ESTIMATE_S)) * 100);
  const stage =
    elapsed < 15 ? "Asking the host to pull the latest version…"
    : elapsed < 75 ? "Pulling and rebuilding the container…"
    : elapsed < 150 ? "Rebuilding — almost there…"
    : "Taking longer than usual — bigger updates rebuild more layers. Still going.";
  const mm = String(Math.floor(elapsed / 60));
  const ss = String(elapsed % 60).padStart(2, "0");
  return (
    <div style={{ padding: "120px 16px", maxWidth: 560, margin: "0 auto", textAlign: "center" }}>
      <h2>Updating Hearth…</h2>
      <p style={{ color: "var(--text-dim)", fontSize: 14.5 }}>
        This page reloads automatically when the new version is up — usually about two minutes.
      </p>
      <div aria-label="update progress" style={{
        height: 6, borderRadius: 3, background: "var(--surface-2)",
        margin: "28px 0 10px", overflow: "hidden",
      }}>
        <div style={{
          height: "100%", width: `${pct}%`, borderRadius: 3,
          background: "var(--accent)", transition: "width 1s linear",
        }} />
      </div>
      <p style={{ color: "var(--text-dim)", fontSize: 13, fontVariantNumeric: "tabular-nums" }}>
        {mm}:{ss} elapsed · {stage}
      </p>
    </div>
  );
}

type AuthState = "loading" | "setup" | "login" | "ready";
type UpdateInfo = { build: string; behind: number; latest_subject?: string; pending?: boolean };

export default function App() {
  const [mode, setMode] = useState<ThemeMode>(getTheme());
  const [auth, setAuth] = useState<AuthState>("loading");
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const [updating, setUpdating] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

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
    <div>
      <nav
        style={{
          display: "flex",
          alignItems: "center",
          gap: 20,
          padding: "12px 24px",
          background: "var(--surface)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, letterSpacing: "-0.02em" }}>
          <Mark />
          hearth
        </span>
        {tabs.map(([to, label]) => (
          <NavLink
            key={to}
            to={to}
            style={({ isActive }) => ({
              color: isActive ? "var(--text)" : "var(--text-dim)",
              textDecoration: "none",
              fontSize: 14,
              fontWeight: 500,
            })}
          >
            {label}
          </NavLink>
        ))}
        {update && update.behind > 0 && (
          <button
            className="btn btn-primary"
            style={{ marginLeft: "auto", minHeight: 32, padding: "4px 12px", fontSize: 13 }}
            onClick={runUpdate}
            title={update.latest_subject}
          >
            Update available ({update.behind})
          </button>
        )}
        <button
          className="btn btn-ghost"
          style={{ marginLeft: update && update.behind > 0 ? 0 : "auto", minHeight: 36, padding: "6px 10px" }}
          onClick={() => setMode(cycleTheme())}
          title="Cycle theme: system → light → dark"
        >
          {themeLabel[mode]}
        </button>
        <button
          className="btn btn-ghost"
          style={{ minHeight: 36, padding: "6px 10px" }}
          onClick={async () => { await fetch("/api/auth/logout", { method: "POST" }); setAuth("login"); }}
        >
          Sign out
        </button>
      </nav>
      <main style={{ maxWidth: "var(--content-max)", margin: "0 auto", padding: 24 }}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/inbox" element={<Inbox />} />
          <Route path="/activities" element={<Activities />} />
          <Route path="/patterns" element={<Patterns />} />
          <Route path="/models" element={<Models />} />
          <Route path="/sensors" element={<Sensors />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/onboarding" element={<Onboarding />} />
        </Routes>
      </main>
    </div>
  );
}

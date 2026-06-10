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

type AuthState = "loading" | "setup" | "login" | "ready";

export default function App() {
  const [mode, setMode] = useState<ThemeMode>(getTheme());
  const [auth, setAuth] = useState<AuthState>("loading");
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
        <button
          className="btn btn-ghost"
          style={{ marginLeft: "auto", minHeight: 36, padding: "6px 10px" }}
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

/**
 * Shell: nav + routes, styled with the Hearth design tokens (docs/DESIGN.md).
 * Each page is a stub matching docs/UI_SPEC.md; implement in roadmap order.
 */
import { useEffect, useState } from "react";
import { Routes, Route, NavLink, useLocation, useNavigate } from "react-router-dom";
import "./theme.css";
import { applyTheme, getTheme, initTheme, type ThemeMode } from "./theme";
import { useIsMobile } from "./useMedia";

initTheme();
import Dashboard from "./pages/Dashboard";
import Behaviour from "./pages/Behaviour";
import Activity from "./pages/Activity";
import Inbox from "./pages/Inbox";
import Activities from "./pages/Activities";
import Patterns from "./pages/Patterns";
import Models from "./pages/Models";
import Sensors from "./pages/Sensors";
import Methodology from "./pages/Methodology";
import Logs from "./pages/Logs";
import Settings from "./pages/Settings";
import Onboarding from "./pages/Onboarding";
import Welcome from "./onboarding/Welcome";
import Login from "./components/Login";
import ProgressWait from "./components/ProgressWait";
import Buddy from "./components/Buddy";

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

const MenuIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="2" strokeLinecap="round" aria-hidden>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </svg>
);

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

function HaLogo() {   // Home Assistant — house mark in HA blue
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden>
      <path d="M12 3 2.5 11.2V21h19v-9.8L12 3z" fill="#41BDF5" />
      <path d="M8 20v-5l2 2 2-3 2 3 2-2v5" fill="none" stroke="#fff"
            strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function InfluxLogo() {   // InfluxDB — rising data line on a rounded tile
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden>
      <rect x="3" y="3" width="18" height="18" rx="5" fill="#6A35FF" />
      <path d="M6.5 15.5l3.5-4.5 3 2.2 4.5-6" fill="none" stroke="#fff"
            strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Official logo if present in /public/logos (see logos/README.md), otherwise
 *  the built-in mark — so it works out of the box and upgrades to the real
 *  brand asset once you drop the file in. */
function LogoImg({ src, alt, fallback }: {
  src: string; alt: string; fallback: React.ReactNode;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) return <>{fallback}</>;
  return (
    <img src={src} alt={alt} width={20} height={20}
         style={{ display: "block", objectFit: "contain" }}
         onError={() => setFailed(true)} />
  );
}

/** Clickable logos for the two systems Hearth connects to (data provider +
 *  InfluxDB) — open each in a new tab. The bundled InfluxDB's internal URL is
 *  rewritten to this host's address so the browser can actually reach it. */
function ConnectionLinks({ onClick }: { onClick: () => void }) {
  const [ha, setHa] = useState<string | null>(null);
  const [influx, setInflux] = useState<string | null>(null);
  useEffect(() => {
    const json = (r: Response) => (r.ok ? r.json() : Promise.reject());
    fetch("/api/connections/ha").then(json)
      .then((c) => { if (c.configured && c.url) setHa(c.url); }).catch(() => {});
    fetch("/api/connections/influx").then(json).then((c) => {
      if (!c.configured) return;
      const bundled = c.options?.mode === "bundled" || (c.url || "").includes("influxdb:8086");
      setInflux(bundled ? `${window.location.protocol}//${window.location.hostname}:8086`
                        : c.url || null);
    }).catch(() => {});
  }, []);
  if (!ha && !influx) return null;
  const tile: React.CSSProperties = {
    display: "inline-flex", alignItems: "center", justifyContent: "center",
    width: 34, height: 34, borderRadius: 8, textDecoration: "none",
    border: "1px solid var(--border)", background: "var(--surface-2)",
  };
  return (
    <div style={{ display: "flex", gap: 8, padding: "2px 2px 4px" }}>
      {ha && (
        <a href={ha} target="_blank" rel="noreferrer" style={tile} onClick={onClick}
           title={`Open Home Assistant — ${ha}`} aria-label="Open Home Assistant">
          <LogoImg src="/logos/home-assistant.svg" alt="Home Assistant" fallback={<HaLogo />} />
        </a>
      )}
      {influx && (
        <a href={influx} target="_blank" rel="noreferrer" style={tile} onClick={onClick}
           title={`Open InfluxDB — ${influx}`} aria-label="Open InfluxDB">
          <LogoImg src="/logos/influxdb.svg" alt="InfluxDB" fallback={<InfluxLogo />} />
        </a>
      )}
    </div>
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
  const [drawerOpen, setDrawerOpen] = useState(false);
  const isMobile = useIsMobile();
  const navigate = useNavigate();
  const location = useLocation();

  // close the mobile drawer whenever the route changes (covers every nav link)
  useEffect(() => { setDrawerOpen(false); }, [location.pathname]);

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

  // Live hand-off after setup: full-screen, no nav/buddy chrome (it has its own).
  if (location.pathname === "/welcome") return <Welcome />;

  const closeNav = () => setDrawerOpen(false);

  const navBody = (
    <>
      <span style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600,
                     letterSpacing: "-0.02em", padding: "4px 8px 14px" }}>
        <Mark />
        hearth
      </span>

      <NavLink to="/" end style={navLinkStyle} onClick={closeNav}>Dashboard</NavLink>
      <NavLink to="/behaviour" style={navLinkStyle} onClick={closeNav}>Behaviour</NavLink>
      <NavLink to="/activity" style={navLinkStyle} onClick={closeNav}>Activity</NavLink>

      {PIPELINE.map((g) => {
        const open = !navCollapsed.has(g.label) || g.label === currentGroup;
        return (
          <div key={g.label} style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 12 }}>
            <button onClick={() => toggleGroup(g.label)}
              style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                       background: "none", border: "none", cursor: "pointer", width: "100%",
                       padding: "5px 10px", color: "var(--text)", fontSize: 14, fontWeight: 600 }}>
              {g.label}
              <Chevron open={open} />
            </button>
            {open && g.items.map(([to, label]) => (
              <NavLink key={to} to={to} onClick={closeNav}
                       style={(p) => ({ ...navLinkStyle(p), paddingLeft: 22, fontSize: 13.5,
                                        fontWeight: p.isActive ? 600 : 400 })}>
                {label}
              </NavLink>
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
        <ConnectionLinks onClick={closeNav} />
        <NavLink to="/settings" end style={navLinkStyle} onClick={closeNav}>Settings</NavLink>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                      gap: 8, marginTop: 8, paddingTop: 10, paddingLeft: 2,
                      borderTop: "1px solid var(--border)" }}>
          <div style={{ display: "inline-flex", border: "1px solid var(--border)",
                        borderRadius: 999, overflow: "hidden" }}>
            {THEMES.map(([m, Glyph, title]) => (
              <button key={m} onClick={() => setTheme(m)} title={title} aria-label={title}
                style={{ display: "flex", alignItems: "center", justifyContent: "center",
                         border: "none", cursor: "pointer", padding: "5px 8px", lineHeight: 1,
                         background: mode === m ? "var(--accent)" : "transparent",
                         color: mode === m ? "#fff" : "var(--text-dim)" }}>
                <Glyph />
              </button>
            ))}
          </div>
          <button className="btn btn-ghost" style={{ fontSize: 12.5, padding: "6px 8px", whiteSpace: "nowrap" }}
                  onClick={async () => { await fetch("/api/auth/logout", { method: "POST" }); setAuth("login"); }}>
            Sign out
          </button>
        </div>
      </div>
    </>
  );

  const mainContent = (
    <main style={{ flex: 1, minWidth: 0 }}>
      <div style={{ maxWidth: "var(--content-max)", margin: "0 auto",
                    padding: isMobile ? "16px 14px 40px" : 24 }}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/behaviour" element={<Behaviour />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/inbox" element={<Inbox />} />
          <Route path="/activities" element={<Activities />} />
          <Route path="/patterns" element={<Patterns />} />
          <Route path="/models" element={<Models />} />
          <Route path="/sensors" element={<Sensors />} />
          <Route path="/methodology" element={<Methodology />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/onboarding" element={<Onboarding />} />
        </Routes>
      </div>
    </main>
  );

  const asideStyle: React.CSSProperties = {
    boxSizing: "border-box", display: "flex", flexDirection: "column", gap: 3,
    padding: "16px 12px", background: "var(--surface)", borderRight: "1px solid var(--border)",
    overflowY: "auto",
  };

  if (isMobile) {
    return (
      <div style={{ minHeight: "100vh" }}>
        <header style={{ position: "sticky", top: 0, zIndex: 20, display: "flex", alignItems: "center",
                         gap: 10, padding: "10px 14px", background: "var(--surface)",
                         borderBottom: "1px solid var(--border)" }}>
          <button onClick={() => setDrawerOpen(true)} aria-label="Open menu"
            style={{ background: "none", border: "none", color: "var(--text)", cursor: "pointer",
                     padding: 4, display: "flex" }}>
            <MenuIcon />
          </button>
          <span style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600,
                         letterSpacing: "-0.02em" }}>
            <Mark />
            hearth
          </span>
          {update && update.behind > 0 && (
            <button className="btn btn-primary" style={{ marginLeft: "auto", fontSize: 12.5, padding: "4px 10px" }}
                    onClick={runUpdate}>Update ({update.behind})</button>
          )}
        </header>
        <Buddy />
        {drawerOpen && (
          <>
            <div onClick={closeNav}
                 style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 30 }} />
            <aside style={{ ...asideStyle, position: "fixed", top: 0, left: 0, height: "100vh",
                            width: 264, maxWidth: "82vw", zIndex: 31 }}>
              {navBody}
            </aside>
          </>
        )}
        {mainContent}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <aside style={{ ...asideStyle, width: 214, flexShrink: 0, position: "sticky",
                      top: 0, height: "100vh" }}>
        {navBody}
      </aside>
      {mainContent}
      <Buddy />
    </div>
  );
}

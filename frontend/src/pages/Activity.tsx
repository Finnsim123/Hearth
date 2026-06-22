/**
 * Activity — the system's memory and standing advice. Two parts:
 *  - Advisories: standing, dismissible recommendations (sensor demoted, blind spot,
 *    model health) the buddy may also surface. Dismiss snoozes them.
 *  - Timeline: an append-only log of notable things Hearth did/decided.
 * Source: GET /api/advisories ({advisories, events}); POST /api/advisories/dismiss.
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Card from "../components/Card";

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };

type Advisory = { kind: string; severity: string; title: string; detail: string;
                  cta: { label: string; href: string } | null; at: string };
type Event = { at: string; kind: string; title: string; detail: string };

const SEV_COLOR: Record<string, string> = {
  critical: "var(--danger)", warn: "var(--accent)", info: "var(--text-dim)",
};
const fmtWhen = (iso: string) => { try { return new Date(iso).toLocaleString(); } catch { return iso; } };

export default function ActivityPage() {
  const [advisories, setAdvisories] = useState<Advisory[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [loaded, setLoaded] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(() => {
    fetch("/api/advisories").then(j).then((d) => {
      setAdvisories(d.advisories || []); setEvents(d.events || []);
    }).catch(() => {}).finally(() => setLoaded(true));
  }, []);
  useEffect(load, [load]);

  const dismiss = (kind: string) => {
    setAdvisories((a) => a.filter((x) => x.kind !== kind));
    fetch(`/api/advisories/dismiss?kind=${encodeURIComponent(kind)}`, { method: "POST" })
      .catch(() => {}).finally(load);
  };

  if (!loaded) return <p style={{ color: "var(--text-dim)" }}>Loading…</p>;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 18, maxWidth: 760 }}>
      <h2 style={{ margin: 0 }}>Activity</h2>

      <Card title="Advisories" sub="Standing suggestions from what Hearth has noticed. Dismiss to snooze.">
        {advisories.length === 0 ? (
          <p style={{ color: "var(--text-dim)", margin: 0, fontSize: 13 }}>
            Nothing needs your attention — sensors trusted, no blind spots flagged.
          </p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {advisories.map((a) => (
              <div key={a.kind} style={{ display: "flex", alignItems: "flex-start", gap: 10,
                borderLeft: `3px solid ${SEV_COLOR[a.severity] || "var(--border)"}`, paddingLeft: 10 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13.5, fontWeight: 600 }}>{a.title}</div>
                  <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginTop: 1 }}>{a.detail}</div>
                </div>
                {a.cta && (
                  <button className="btn btn-secondary" style={{ fontSize: 12.5, padding: "3px 9px" }}
                    onClick={() => { const h = a.cta!.href;
                      /^https?:\/\//.test(h) ? window.open(h, "_blank", "noopener") : navigate(h); }}>
                    {a.cta.label}
                  </button>
                )}
                <button className="btn btn-ghost" style={{ fontSize: 12.5, padding: "3px 9px" }}
                  onClick={() => dismiss(a.kind)}>Dismiss</button>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Timeline" sub="What Hearth has done and decided, most recent first.">
        {events.length === 0 ? (
          <p style={{ color: "var(--text-dim)", margin: 0, fontSize: 13 }}>No events recorded yet.</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {events.map((e, i) => (
              <div key={i} style={{ display: "flex", gap: 10, fontSize: 13 }}>
                <span style={{ width: 132, flex: "none", color: "var(--text-dim)", fontSize: 12 }}>{fmtWhen(e.at)}</span>
                <span style={{ flex: 1 }}>
                  <span style={{ fontWeight: 500 }}>{e.title}</span>
                  {e.detail && <span style={{ color: "var(--text-dim)" }}> — {e.detail}</span>}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </section>
  );
}

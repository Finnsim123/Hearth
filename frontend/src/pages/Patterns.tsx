/**
 * Patterns — discovered, unnamed clusters of behavior. Hearth found these in
 * your sensor data; you name them (one click labels weeks of history) or
 * dismiss them. Spec: docs/UI_SPEC.md §Patterns, ARCHITECTURE.md §6.
 */
import { useEffect, useState } from "react";
import { Icon } from "../icons";
import { cheerBuddy } from "../components/buddyBus";

type Cluster = {
  id: number; person_id: string; run_at: string | null; algo: string;
  n_windows: number; signature: [string, number][]; hour_histogram: number[];
  example_windows: string[]; status: string;
  named_activity_slug: string | null; suggested_slug: string | null;
};
type Activity = { slug: string; name: string };
type PlainFeat = { raw: string; label: string; room: string | null; dir: "up" | "down" };
type Evidence = {
  plain: PlainFeat[];
  when: { span: string; peak_hour: number; daypart: string } | null;
  where: string[];
  cadence: { weekday_frac: number; phrase: string } | null;
  adjacency: { before?: string; after?: string } | null;
  contrast: { slug: string; name: string; shared: number } | null;
  summary: string;
};

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };
const post = (url: string, body: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** Plain-English signal chips — "Bed empty ↓ · Bedroom warmer ↑". Raw feature
 * name is kept as a tooltip for the curious. Falls back to raw codes pre-load. */
function SignatureLine({ plain, raw }: { plain?: PlainFeat[]; raw: [string, number][] }) {
  const chips = plain
    ? plain.map((p) => ({ key: p.raw, text: p.label, up: p.dir === "up", title: p.raw }))
    : raw.map(([f, z]) => ({ key: f, text: f, up: z > 0, title: `z-score ${z}` }));
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
      {chips.map((c) => (
        <span key={c.key} title={c.title}
              style={{ fontSize: 12.5, padding: "3px 10px", borderRadius: 99,
                       background: "var(--surface-2)", border: "1px solid var(--border)" }}>
          {c.text}{" "}
          <span style={{ color: c.up ? "var(--accent)" : "var(--text-dim)", fontWeight: 600 }}>
            {c.up ? "↑" : "↓"}
          </span>
        </span>
      ))}
    </div>
  );
}

/** The deterministic "what is this" context: when, where, what's around it. */
function EvidenceBlock({ ev }: { ev: Evidence }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {ev.summary && (
        <p style={{ margin: 0, fontSize: 13.5, color: "var(--text)" }}>{ev.summary}</p>
      )}
      {(ev.adjacency?.before || ev.adjacency?.after || ev.contrast) && (
        <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
          {ev.adjacency?.before && <>Usually after <strong>{ev.adjacency.before}</strong>. </>}
          {ev.adjacency?.after && <>Tends to lead into <strong>{ev.adjacency.after}</strong>. </>}
          {ev.contrast && <>Looks a lot like <strong>{ev.contrast.name}</strong> — maybe the same thing.</>}
        </p>
      )}
    </div>
  );
}

function HourHistogram({ hist }: { hist: number[] }) {
  const max = Math.max(...hist, 1);
  return (
    <div>
      <div style={{ display: "flex", gap: 2, alignItems: "flex-end", height: 44 }}>
        {hist.map((v, h) => (
          <div key={h} title={`${String(h).padStart(2, "0")}:00 — ${v} windows`}
               style={{ flex: 1, borderRadius: 2, minHeight: v > 0 ? 3 : 1,
                        height: `${(v / max) * 100}%`,
                        background: v > 0 ? "var(--accent)" : "var(--surface-2)",
                        opacity: v > 0 ? 0.4 + 0.6 * (v / max) : 1 }} />
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5,
                    color: "var(--text-dim)", marginTop: 2 }}>
        <span>00</span><span>06</span><span>12</span><span>18</span><span>23</span>
      </div>
    </div>
  );
}

function PatternCard({ c, activities, personName, siblings, onChange }: {
  c: Cluster; activities: Activity[]; personName: string;
  siblings: Cluster[]; onChange: () => void;
}) {
  const [choice, setChoice] = useState(c.suggested_slug ?? "");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [ev, setEv] = useState<Evidence | null>(null);
  useEffect(() => {
    fetch(`/api/clusters/${c.id}/evidence`).then(j).then(setEv).catch(() => {});
  }, [c.id]);
  const name = async () => {
    setBusy(true); setMsg("");
    const body = choice === "__new__" ? { name: newName.trim() } : { activity_slug: choice };
    try {
      const r = await post(`/api/clusters/${c.id}/name`, body).then(j);
      setMsg(`Labeled ${r.labeled_windows} windows as “${r.activity}” — next training run learns from them.`);
      cheerBuddy({ title: `“${r.activity}” — that has a name now`, detail: `${r.labeled_windows} windows labeled for the next run.` });
      setTimeout(onChange, 1600);
    } catch { setMsg("Couldn't name this pattern — check logs."); }
    setBusy(false);
  };
  const dismiss = async () => {
    setBusy(true);
    await post(`/api/clusters/${c.id}/dismiss`, {});
    onChange();
  };
  const hoursOfLife = Math.round(c.n_windows / 2);
  const ready = choice && (choice !== "__new__" || newName.trim().length > 1);
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 16,
                  display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <Icon name="patterns" size={16} />
        <strong style={{ fontSize: 14.5 }}>Unnamed pattern · {personName}</strong>
        <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
          {c.n_windows} windows ≈ {hoursOfLife}h of the last month
        </span>
        {c.suggested_slug && (
          <span style={{ fontSize: 11.5, padding: "2px 8px", borderRadius: 99,
                         background: "color-mix(in srgb, var(--accent) 14%, transparent)",
                         color: "var(--accent)" }}>
            AI thinks: {c.suggested_slug}
          </span>
        )}
      </div>
      {ev && <EvidenceBlock ev={ev} />}
      <SignatureLine plain={ev?.plain} raw={c.signature} />
      <HourHistogram hist={c.hour_histogram} />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <select value={choice} onChange={(e) => setChoice(e.target.value)}>
          <option value="">What is this?</option>
          {activities.map((a) => <option key={a.slug} value={a.slug}>{a.name}</option>)}
          <option value="__new__">+ New activity…</option>
        </select>
        {choice === "__new__" && (
          <input placeholder="e.g. Gaming" value={newName}
                 onChange={(e) => setNewName(e.target.value)} style={{ maxWidth: 180 }} />
        )}
        <button className="btn btn-primary" disabled={!ready || busy} onClick={name}>
          {busy ? "Labeling…" : "Name it"}
        </button>
        <button className="btn btn-ghost" disabled={busy} onClick={dismiss}
                style={{ color: "var(--text-dim)" }}>
          Not a thing — dismiss
        </button>
        {siblings.length > 0 && (
          <select defaultValue="" disabled={busy} title="Fold this pattern into another one"
                  onChange={async (e) => {
                    if (!e.target.value) return;
                    setBusy(true);
                    await post(`/api/clusters/${c.id}/merge`, { into: Number(e.target.value) });
                    onChange();
                  }}>
            <option value="">Same as…</option>
            {siblings.map((s2) => (
              <option key={s2.id} value={s2.id}>
                #{s2.id} — {s2.signature.slice(0, 2).map(([f]) => f).join(" + ")}
              </option>
            ))}
          </select>
        )}
      </div>
      {msg && <p style={{ margin: 0, fontSize: 13, color: "var(--accent)" }}>{msg}</p>}
    </div>
  );
}

export default function Patterns() {
  const [clusters, setClusters] = useState<Cluster[] | null>(null);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [persons, setPersons] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState("");
  const load = () => fetch("/api/clusters?status=new").then(j).then(setClusters).catch(() => setClusters([]));
  useEffect(() => {
    load();
    fetch("/api/activities").then(j).then(setActivities).catch(() => {});
    fetch("/api/persons").then(j)
      .then((ps: { id: string; name: string }[]) =>
        setPersons(Object.fromEntries(ps.map((p) => [p.id, p.name]))))
      .catch(() => {});
  }, []);
  const run = async () => {
    setRunning(true); setRunMsg("Clustering the last 30 days — this takes a minute…");
    try {
      const r = await post("/api/discovery/run", {}).then(j);
      setRunMsg(r.found === 0
        ? "No new patterns — everything recent already looks explained, or there isn't enough data yet."
        : `Found ${r.found} pattern candidate${r.found > 1 ? "s" : ""}.`);
      load();
    } catch { setRunMsg("Discovery failed — check logs."); }
    setRunning(false);
  };
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="patterns" size={22} />
        <h2 style={{ margin: 0 }}>Patterns</h2>
        <button className="btn btn-secondary" style={{ marginLeft: "auto" }}
                disabled={running} onClick={run}>
          {running ? "Discovering…" : "Discover now"}
        </button>
      </div>
      <p style={{ margin: 0, fontSize: 14, color: "var(--text-dim)", maxWidth: 640 }}>
        Hearth clusters recent sensor windows it can't explain and shows you the recurring shapes
        it found — when they happen and which sensors define them. Naming one labels all its
        windows at once (your confirmed answers still outrank these). Runs automatically every
        Saturday night, right before Sunday's retrain.
      </p>
      {runMsg && <p style={{ margin: 0, fontSize: 13.5, color: "var(--accent)" }}>{runMsg}</p>}
      {clusters === null && <p style={{ color: "var(--text-dim)" }}>Loading…</p>}
      {clusters !== null && clusters.length === 0 && !runMsg && (
        <div style={{ border: "1px dashed var(--border)", borderRadius: 12, padding: 24,
                      textAlign: "center", color: "var(--text-dim)", fontSize: 14 }}>
          No unnamed patterns right now. Click <strong>Discover now</strong> to scan the last
          30 days, or wait for Saturday's automatic run.
        </div>
      )}
      {clusters?.map((c) => (
        <PatternCard key={c.id} c={c} activities={activities}
                     personName={persons[c.person_id] ?? c.person_id}
                     siblings={clusters.filter((s2) => s2.id !== c.id && s2.person_id === c.person_id)}
                     onChange={load} />
      ))}
    </section>
  );
}

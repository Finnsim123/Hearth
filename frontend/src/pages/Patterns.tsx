/**
 * Patterns — discovered, unnamed clusters of behavior. Hearth found these in
 * your sensor data; you name them (one click labels weeks of history) or
 * dismiss them. Spec: docs/UI_SPEC.md §Patterns, ARCHITECTURE.md §6.
 */
import { useEffect, useState } from "react";
import { Icon } from "../icons";
import { cheerBuddy } from "../components/buddyBus";

type Suggestion = {
  name: string; slug: string | null; rationale: string;
  confidence: number; kind: "existing" | "new" | "merge";
};
type Cluster = {
  id: number; person_id: string; run_at: string | null; algo: string;
  n_windows: number; signature: [string, number][]; hour_histogram: number[];
  example_windows: string[]; status: string;
  named_activity_slug: string | null; suggested_slug: string | null;
  suggestions: Suggestion[];
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
  examples: { ts: string; when: string }[];
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

/** Concrete moments this pattern happened — recognition beats abstraction.
 * "What were YOU doing on Tue at 15:10?" is far easier to answer than "name
 * this statistical cluster". */
function ExampleMoments({ examples }: { examples: { ts: string; when: string }[] }) {
  if (!examples.length) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ fontSize: 13, color: "var(--text)" }}>
        A few times this happened — what were you up to?
      </span>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {examples.map((e) => (
          <span key={e.ts} style={{ fontSize: 12.5, padding: "3px 10px", borderRadius: 8,
                       background: "var(--surface-2)", border: "1px solid var(--border)",
                       color: "var(--text-dim)", fontVariantNumeric: "tabular-nums" }}>
            {e.when}
          </span>
        ))}
      </div>
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
  const [suggestions, setSuggestions] = useState<Suggestion[]>(c.suggestions ?? []);
  const [suggesting, setSuggesting] = useState(false);
  // a marker-like pattern is brief and time-concentrated (mirrors markers.looks_like_marker)
  const total = c.hour_histogram.reduce((a, b) => a + b, 0);
  const peak = Math.max(...c.hour_histogram, 0);
  const top2 = [...c.hour_histogram].sort((a, b) => b - a).slice(0, 2).reduce((a, b) => a + b, 0);
  const markerish = total >= 3 && (peak / total >= 0.4 || top2 / total >= 0.6) && c.n_windows <= 8;
  const [mode, setMode] = useState<"activity" | "marker">(markerish ? "marker" : "activity");
  const [mName, setMName] = useState("");
  const [mFrom, setMFrom] = useState("");
  const [mTo, setMTo] = useState("");
  const createMarker = async () => {
    if (!mTo) return;
    setBusy(true); setMsg("");
    try {
      const r = await post(`/api/clusters/${c.id}/marker`, {
        name: mName.trim() || "Transition", from_state: mFrom, to_state: mTo }).then(j);
      cheerBuddy({ title: `Learned a transition: ${r.marker.name}`,
                   detail: `marks ${r.marker.from_state || "any"} → ${r.marker.to_state}` });
      setTimeout(onChange, 1600);
    } catch { setMsg("Couldn't save the marker — check logs."); }
    setBusy(false);
  };
  useEffect(() => {
    fetch(`/api/clusters/${c.id}/evidence`).then(j).then(setEv).catch(() => {});
  }, [c.id]);
  const nameWith = async (body: Record<string, string>, label: string) => {
    setBusy(true); setMsg("");
    try {
      const r = await post(`/api/clusters/${c.id}/name`, body).then(j);
      setMsg(r.merged_into
        ? `That's the same as “${r.activity}” — merged into it instead of creating a duplicate. Labeled ${r.labeled_windows} windows.`
        : `Labeled ${r.labeled_windows} windows as “${r.activity}” — next training run learns from them.`);
      cheerBuddy({ title: r.merged_into ? `Merged into “${r.activity}”` : `“${r.activity}” — that has a name now`,
                   detail: `${r.labeled_windows} windows labeled for the next run.` });
      setTimeout(onChange, 1600);
    } catch { setMsg(`Couldn't save “${label}” — check logs.`); }
    setBusy(false);
  };
  const name = () => nameWith(
    choice === "__new__" ? { name: newName.trim() } : { activity_slug: choice },
    choice === "__new__" ? newName.trim() : choice);
  const acceptSuggestion = (s: Suggestion) =>
    nameWith(s.slug ? { activity_slug: s.slug } : { name: s.name }, s.name);
  const askForSuggestions = async () => {
    setSuggesting(true);
    try {
      const r = await post(`/api/clusters/${c.id}/suggest`, {}).then(j);
      setSuggestions(r.suggestions ?? []);
      if (!r.has_llm) setMsg("Add an AI key in Settings → Connections to get name suggestions.");
      else if (!(r.suggestions ?? []).length) setMsg("The assistant couldn't pin this one down — name it from what you see.");
    } catch { setMsg("Couldn't get suggestions — check logs."); }
    setSuggesting(false);
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
      </div>
      {ev && <EvidenceBlock ev={ev} />}
      {ev && ev.examples.length > 0 && <ExampleMoments examples={ev.examples} />}
      <SignatureLine plain={ev?.plain} raw={c.signature} />
      <HourHistogram hist={c.hour_histogram} />

      {/* Is this an activity, or a moment that marks a change? */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button className="btn" onClick={() => setMode("activity")}
          style={{ borderColor: mode === "activity" ? "var(--accent)" : undefined }}>Something I do</button>
        <button className="btn" onClick={() => setMode("marker")}
          style={{ borderColor: mode === "marker" ? "var(--accent)" : undefined }}>A moment of change</button>
        {markerish && <span style={{ fontSize: 12, color: "var(--text-dim)" }}>brief &amp; time-locked — looks like a transition</span>}
      </div>

      {mode === "marker" ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <input placeholder="Name (e.g. Wake up, Morning coffee)" value={mName}
                 onChange={(e) => setMName(e.target.value)} />
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select value={mFrom} onChange={(e) => setMFrom(e.target.value)}>
              <option value="">from: any state</option>
              {activities.map((a) => <option key={a.slug} value={a.slug}>{a.name}</option>)}
            </select>
            <span style={{ color: "var(--text-dim)" }}>→</span>
            <select value={mTo} onChange={(e) => setMTo(e.target.value)}>
              <option value="">to: pick a state…</option>
              {activities.map((a) => <option key={a.slug} value={a.slug}>{a.name}</option>)}
            </select>
            <button className="btn btn-primary" disabled={!mTo || busy} onClick={createMarker}>
              {busy ? "Saving…" : "Mark as transition"}
            </button>
          </div>
          <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
            Bound to its main signal (<code>{c.signature?.[0]?.[0] ?? "—"}</code>). It won't be guessed as an activity — it nudges the switch into the new state.
          </span>
        </div>
      ) : (
      <>
      {/* Suggestions first — one tap to accept the AI's read of the moments above */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {suggestions.length > 0 ? (
          <>
            <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>Was it…</span>
            {suggestions.map((s, i) => (
              <button key={i} disabled={busy} onClick={() => acceptSuggestion(s)}
                      title={`${s.rationale}${s.slug ? "" : " (new activity)"} · ${Math.round(s.confidence * 100)}% sure`}
                      style={{ fontSize: 13, padding: "5px 12px", borderRadius: 99,
                               cursor: "pointer", color: "var(--accent)",
                               background: "color-mix(in srgb, var(--accent) 12%, transparent)",
                               border: "1px solid color-mix(in srgb, var(--accent) 35%, transparent)" }}>
                {s.name}{s.kind === "new" ? " +" : ""}
              </button>
            ))}
          </>
        ) : (
          <button className="btn btn-ghost" disabled={suggesting} onClick={askForSuggestions}
                  style={{ fontSize: 13, color: "var(--accent)" }}>
            {suggesting ? "Thinking…" : "✨ Suggest names"}
          </button>
        )}
      </div>

      {/* …or say it yourself */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <select value={choice} onChange={(e) => setChoice(e.target.value)} aria-label="Activity">
          <option value="">Pick an activity…</option>
          {activities.map((a) => <option key={a.slug} value={a.slug}>{a.name}</option>)}
          <option value="__new__">+ New activity…</option>
        </select>
        {choice === "__new__" && (
          <input placeholder="e.g. Gaming" value={newName}
                 onChange={(e) => setNewName(e.target.value)} style={{ maxWidth: 180 }} />
        )}
        <button className="btn btn-primary" disabled={!ready || busy} onClick={name}>
          {busy ? "Labeling…" : "That's it — name it"}
        </button>
      </div>
      </>
      )}

      {/* Not-an-activity and merge are first-class outcomes, not afterthoughts:
          many clusters are everyday downtime, or a variant of something named. */}
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center",
                    paddingTop: 4, borderTop: "1px solid var(--border)" }}>
        {siblings.length > 0 && (
          <label style={{ display: "flex", alignItems: "center", gap: 6,
                          fontSize: 12.5, color: "var(--text-dim)" }}>
            Part of:
            <select defaultValue="" disabled={busy} title="Fold this pattern into another one"
                    onChange={async (e) => {
                      if (!e.target.value) return;
                      setBusy(true);
                      await post(`/api/clusters/${c.id}/merge`, { into: Number(e.target.value) });
                      onChange();
                    }}>
              <option value="">another pattern…</option>
              {siblings.map((s2) => (
                <option key={s2.id} value={s2.id}>
                  {s2.suggestions?.[0]?.name
                    ?? s2.signature.slice(0, 2).map(([f]) => f).join(" + ")}
                </option>
              ))}
            </select>
          </label>
        )}
        <button disabled={busy} onClick={dismiss}
                style={{ fontSize: 12.5, color: "var(--text-dim)", background: "none",
                         border: "none", cursor: "pointer", padding: 0,
                         textDecoration: "underline", marginLeft: siblings.length ? 0 : "auto" }}>
          Not an activity — dismiss
        </button>
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
        Hearth clusters recent sensor windows it can't explain into recurring shapes. For each, it
        shows when and where it happened and a few concrete moments — so you can recognise what you
        were doing and name it (or pick one of the AI's guesses). Naming one labels all its windows
        at once; not everything is an activity, so dismissing is fine too. Confirmed answers always
        outrank these. Runs automatically every Saturday night, right before Sunday's retrain.
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

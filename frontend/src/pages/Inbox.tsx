/**
 * Inbox — the feedback loop's primary surface (docs/UI_SPEC.md §3).
 * Open questions with one-tap activity answers + the bulk labeler.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Icon, type IconName } from "../icons";

type Question = { id: number; person_id: string; window_ts: string; predicted: string;
                  confidence: number; alternatives: string[] };
type Activity = { slug: string; name: string; enabled: boolean };

const KNOWN: IconName[] = ["sleeping", "away", "home", "cooking", "eating", "movie", "working"];
const icon = (s: string): IconName => (KNOWN.includes(s as IconName) ? (s as IconName) : "activities");
const fmt = (iso: string) =>
  new Date(iso).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });

function QuestionCard({ q, activities, onDone }: {
  q: Question; activities: Activity[]; onDone: () => void;
}) {
  const qc = useQueryClient();
  const answer = useMutation({
    mutationFn: (slug: string) =>
      fetch(`/api/inbox/${q.id}/answer`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer: slug }) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["inbox"] }); onDone(); },
  });
  const skip = useMutation({
    mutationFn: () => fetch(`/api/inbox/${q.id}/skip`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["inbox"] }),
  });
  // alternatives first, then the rest of the taxonomy
  const altSet = new Set(q.alternatives);
  const options = [
    ...q.alternatives,
    ...activities.filter((a) => a.enabled && !altSet.has(a.slug)).map((a) => a.slug),
  ].slice(0, 8);
  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <Icon name="question" size={18} />
        <span style={{ fontSize: 14.5 }}>
          <strong style={{ fontWeight: 500, textTransform: "capitalize" }}>{q.person_id}</strong>,{" "}
          {fmt(q.window_ts)} — I guessed{" "}
          <em style={{ fontStyle: "normal", color: "var(--accent)" }}>{q.predicted.replace("_", " ")}</em>{" "}
          ({Math.round(q.confidence * 100)}% sure). What was it?
        </span>
        <button className="btn btn-ghost" style={{ marginLeft: "auto" }} onClick={() => skip.mutate()}>
          Skip
        </button>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {options.map((slug, i) => (
          <button key={slug} className={i === 0 ? "btn btn-primary" : "btn btn-secondary"}
                  disabled={answer.isPending} onClick={() => answer.mutate(slug)}
                  style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
            <Icon name={icon(slug)} size={15} />
            {(i === 0 ? "✓ " : "") + slug.replace("_", " ")}
          </button>
        ))}
      </div>
    </div>
  );
}

function BulkLabeler({ activities, persons }: { activities: Activity[]; persons: string[] }) {
  const [person, setPerson] = useState(persons[0] ?? "");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [from, setFrom] = useState("19:00");
  const [to, setTo] = useState("21:00");
  const [activity, setActivity] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const submit = async () => {
    const r = await fetch("/api/labels/bulk", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person_id: person, activity,
        start: `${date}T${from}:00`, end: `${date}T${to}:00` }) });
    const j = await r.json();
    setResult(r.ok ? `Labeled ${j.labeled_windows} windows as ${activity}.` : "Failed — is InfluxDB connected?");
  };
  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <p className="label" style={{ margin: 0 }}>Bulk label a time range</p>
      <p style={{ margin: 0, fontSize: 13.5, color: "var(--text-dim)" }}>
        The fastest way to teach Hearth: "that whole evening was a movie." One action, many labels.
      </p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <select value={person} onChange={(e) => setPerson(e.target.value)}>
          {persons.map((p) => <option key={p}>{p}</option>)}
        </select>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        <input type="time" value={from} onChange={(e) => setFrom(e.target.value)} />
        <span style={{ color: "var(--text-dim)" }}>→</span>
        <input type="time" value={to} onChange={(e) => setTo(e.target.value)} />
        <select value={activity} onChange={(e) => setActivity(e.target.value)}>
          <option value="" disabled>activity…</option>
          {activities.filter((a) => a.enabled).map((a) => (
            <option key={a.slug} value={a.slug}>{a.name}</option>
          ))}
        </select>
        <button className="btn btn-primary" disabled={!person || !activity} onClick={submit}>
          Label range
        </button>
      </div>
      {result && <p style={{ margin: 0, fontSize: 13.5, color: "var(--ok)" }}>{result}</p>}
    </div>
  );
}

export default function Inbox() {
  const qc = useQueryClient();
  const inbox = useQuery<Question[]>({
    queryKey: ["inbox"], queryFn: () => fetch("/api/inbox").then((r) => r.json()),
    refetchInterval: 60_000,
  });
  const acts = useQuery<Activity[]>({
    queryKey: ["activities"], queryFn: () => fetch("/api/activities").then((r) => r.json()),
  });
  const persons = useQuery<{ id: string; enabled: boolean }[]>({
    queryKey: ["persons"], queryFn: () => fetch("/api/persons").then((r) => r.json()),
  });
  const questions = inbox.data ?? [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 760 }}>
      <h1 style={{ margin: 0 }}>Inbox</h1>
      {questions.length === 0 && (
        <p style={{ color: "var(--text-dim)", margin: 0 }}>
          Nothing needs you right now. Questions appear here when Hearth isn't sure —
          and every answer makes next week's model better.
        </p>
      )}
      {questions.map((q) => (
        <QuestionCard key={q.id} q={q} activities={acts.data ?? []}
                      onDone={() => qc.invalidateQueries({ queryKey: ["predictions"] })} />
      ))}
      <BulkLabeler activities={acts.data ?? []}
                   persons={(persons.data ?? []).filter((p) => p.enabled).map((p) => p.id)} />
    </div>
  );
}

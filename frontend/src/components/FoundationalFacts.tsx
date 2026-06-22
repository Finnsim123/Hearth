/**
 * FoundationalFacts — "What I can know for sure".
 *
 * Lets the user bind ground-truth sensors (home/away, asleep) that BYPASS the
 * model: when a fact applies, Hearth knows instead of guessing, and skips the
 * model (saves compute, can't be wrong). A sensor must EARN it — the reliability
 * verdict (fact | feature | suspect) decides whether it bypasses or is just a hint.
 *
 * Drop into Settings  : <FoundationalFacts />
 * Drop into the wizard : <FoundationalFacts wizard />  (same component, tighter copy)
 *
 * Backend: GET/POST /api/foundational, POST /api/foundational/run.
 */
import { useCallback, useEffect, useState } from "react";
import Card from "./Card";

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };
const post = (url: string, body?: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
               body: body === undefined ? undefined : JSON.stringify(body) });

type Verdict = { role_decision: "fact" | "feature" | "suspect"; score: number;
                 eligible: boolean; reason: string } | null;
type Fact = { id: string; gate: string; binding_name: string; person_id?: string | null;
              enabled: boolean; verdict: Verdict };
type Candidate = { binding_name: string; entity_id: string; room?: string | null;
                   person_id?: string | null };
type Data = { facts: Fact[]; candidates: Record<string, Candidate[]> };

const PILL: Record<string, { bg: string; fg: string; label: string }> = {
  fact:    { bg: "var(--ok-bg, #103b2c)",   fg: "var(--ok, #34D399)",     label: "trusted as a fact" },
  feature: { bg: "var(--warn-bg, #3a2f12)", fg: "var(--warn, #F59E0B)",   label: "used as a hint" },
  suspect: { bg: "var(--danger-bg, #3a1717)", fg: "var(--danger, #F87171)", label: "unreliable" },
};

function VerdictPill({ v }: { v: Verdict }) {
  if (!v) return <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>not scored yet</span>;
  const p = PILL[v.role_decision] ?? PILL.feature;
  return (
    <span title={v.reason}
      style={{ fontSize: 12, fontWeight: 500, color: p.fg, background: p.bg,
               padding: "2px 10px", borderRadius: 8 }}>
      {p.label} · {Math.round(v.score * 100)}%
    </span>
  );
}

export default function FoundationalFacts({ wizard = false }: { wizard?: boolean }) {
  const [data, setData] = useState<Data | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    fetch("/api/foundational").then(j).then(setData).catch(() => setData({ facts: [], candidates: {} }));
  }, []);
  useEffect(load, [load]);

  const bind = async (gate: string, c: Candidate) => {
    await post("/api/foundational", { gate, binding_name: c.binding_name, person_id: c.person_id ?? null });
    load();
  };
  const toggle = async (id: string) => { await post(`/api/foundational/${id}/toggle`); load(); };
  const remove = async (id: string) => { await fetch(`/api/foundational/${id}`, { method: "DELETE" }); load(); };
  const testNow = async () => {
    setBusy(true);
    try { await post("/api/foundational/run"); load(); } finally { setBusy(false); }
  };

  if (!data) return <Card title="What I can know for sure"><p style={{ color: "var(--text-dim)" }}>Loading…</p></Card>;

  const sleepFact = data.facts.find((f) => f.gate === "asleep");
  const sleepCands = data.candidates.asleep ?? [];

  return (
    <Card title="What I can know for sure"
      sub="Some things Hearth doesn't have to guess. When a fact applies it skips the model — faster, and a fact beats a prediction. A sensor only becomes a fact once it's proven reliable.">

      {/* Away — automatic from the person tracker */}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 500 }}>Home / away</div>
          <div style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
            Read straight from your presence. When you're out, Hearth marks you away and
            doesn't run the model. (Link a person to their tracker on the Sensors page.)
          </div>
        </div>
        <VerdictPill v={{ role_decision: "fact", score: 1, eligible: true, reason: "presence is ground truth" }} />
      </div>

      <div style={{ borderTop: "0.5px solid var(--border, #2a2f3a)", paddingTop: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500 }}>Asleep</div>
            <div style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
              Bind a sleep or bed sensor and I'll mark you asleep directly — once it
              proves reliable. Until then I treat it as a hint, not a fact.
            </div>
          </div>
          {sleepFact && <VerdictPill v={sleepFact.verdict} />}
        </div>

        {sleepFact ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 13 }}>Using <code>{sleepFact.binding_name}</code>
              {sleepFact.person_id ? ` for ${sleepFact.person_id}` : ""}</span>
            {sleepFact.verdict?.reason && (
              <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>— {sleepFact.verdict.reason}</span>
            )}
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <button className="btn" onClick={() => toggle(sleepFact.id)}>
                {sleepFact.enabled ? "Disable" : "Enable"}
              </button>
              <button className="btn" onClick={() => remove(sleepFact.id)}>Remove</button>
            </div>
          </div>
        ) : sleepCands.length ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {sleepCands.map((c) => (
              <div key={c.binding_name} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 13, flex: 1 }}>{c.entity_id}
                  {c.room ? <span style={{ color: "var(--text-dim)" }}> · {c.room}</span> : null}</span>
                <button className="btn" onClick={() => bind("asleep", c)}>Use this</button>
              </div>
            ))}
          </div>
        ) : (
          <p style={{ fontSize: 12.5, color: "var(--text-dim)", margin: 0 }}>
            No bed/sleep sensor found. Add one in Home Assistant (a bed pressure pad,
            sleep tracker, or wearable) and it'll appear here.
          </p>
        )}
      </div>

      {!wizard && (data.facts.length > 0) && (
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button className="btn btn-primary" disabled={busy} onClick={testNow}>
            {busy ? "Scoring…" : "Test reliability now"}
          </button>
          <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
            Re-scores each sensor against recent history.
          </span>
        </div>
      )}
    </Card>
  );
}

/**
 * Models — the glass box. Per-person model registry with honest metrics:
 * confirmed-label accuracy (with CI) vs bootstrap agreement, per-class
 * precision/recall/F1, confusion matrix, feature importances, and
 * train / promote actions. Spec: docs/UI_SPEC.md §Models.
 */
import { useEffect, useState } from "react";
import { Icon } from "../icons";

type PerClass = { precision: number; recall: number; f1: number; support: number };
type Metrics = {
  accuracy_confirmed?: number | null;
  accuracy_confirmed_ci?: [number, number];
  accuracy_bootstrap?: number | null;
  accuracy_train?: number;
  auc_macro?: number;
  n_confirmed?: number;
  n_train?: number;
  feature_count?: number;
  per_class?: Record<string, PerClass>;
  confusion?: { labels: string[]; matrix: number[][] };
  feature_importances?: Record<string, number>;
  evidence_profile?: Record<string, number>;
  hyperparams?: Record<string, unknown>;
  validation_status?: "validated" | "provisional";
};
type Model = {
  id: number; person_id: string; version: string; algo: string;
  trained_at: string | null; promoted: boolean;
  label_counts: Record<string, number>; metrics: Metrics;
};

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };
const pct = (v: number | null | undefined) => (v === null || v === undefined) ? "—" : `${(v * 100).toFixed(1)}%`;

function MetricChip({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div title={hint} style={{ padding: "8px 14px", border: "1px solid var(--border)",
                               borderRadius: 10, textAlign: "center", minWidth: 92 }}>
      <div style={{ fontSize: 18, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      <div style={{ fontSize: 12, color: "var(--text-dim)" }}>{label}</div>
    </div>
  );
}

function StatusBadge({ status, nConfirmed }: { status?: string; nConfirmed: number }) {
  // Honest cold-start signal: a model is only "validated" once enough human-
  // confirmed labels back its accuracy. A fresh / fast-track model is promoted
  // on bootstrap AGREEMENT (agreement with the rules that made its own labels),
  // so it serves predictions but is not yet validated.
  if (status !== "provisional" && status !== "validated") return null;
  const provisional = status === "provisional";
  return (
    <span
      title={provisional
        ? `Provisional: only ${nConfirmed} human-confirmed labels (need 30). Its accuracy so far leans on rule-generated labels, which can be wrong. Confirm a few predictions to validate it.`
        : `Validated on ${nConfirmed} human-confirmed labels.`}
      style={{
        fontSize: 11.5, padding: "2px 8px", borderRadius: 99, fontWeight: 600,
        display: "inline-flex", alignItems: "center", gap: 4,
        background: provisional
          ? "color-mix(in srgb, var(--danger) 14%, transparent)"
          : "color-mix(in srgb, var(--ok, #34D399) 16%, transparent)",
        color: provisional ? "var(--danger)" : "var(--ok, #34D399)",
      }}>
      <Icon name={provisional ? "warning" : "check"} size={12} />
      {provisional ? "Provisional" : "Validated"}
    </span>
  );
}

function Confusion({ c }: { c: { labels: string[]; matrix: number[][] } }) {
  const max = Math.max(1, ...c.matrix.flat());
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", fontSize: 12.5 }}>
        <thead>
          <tr>
            <th style={{ padding: 6, textAlign: "left", color: "var(--text-dim)", fontWeight: 500 }}>actual ↓ pred →</th>
            {c.labels.map((l) => <th key={l} style={{ padding: 6, color: "var(--text-dim)", fontWeight: 500 }}>{l}</th>)}
          </tr>
        </thead>
        <tbody>
          {c.matrix.map((row, i) => (
            <tr key={i}>
              <td style={{ padding: 6, fontWeight: 500 }}>{c.labels[i]}</td>
              {row.map((v, k) => (
                <td key={k} style={{
                  padding: "6px 12px", textAlign: "center", fontVariantNumeric: "tabular-nums",
                  background: v === 0 ? "transparent"
                    : i === k ? `color-mix(in srgb, var(--ok, #34D399) ${20 + 50 * (v / max)}%, transparent)`
                              : `color-mix(in srgb, var(--danger) ${15 + 45 * (v / max)}%, transparent)`,
                  borderRadius: 6,
                }}>{v}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p style={{ fontSize: 12, color: "var(--text-dim)", margin: "6px 0 0" }}>
        Green diagonal = correct. Off-diagonal red = confusion between two activities.
      </p>
    </div>
  );
}

const TIER_COLORS: Record<string, string> = {
  direct: "var(--ok, #34D399)", behavioral: "var(--accent)",
  ambient: "#F472B6", prior: "var(--text-dim)",
};
const TIER_HINTS: Record<string, string> = {
  direct: "bed, presence, person, media, door, own phone — a human did something",
  behavioral: "power, lights, steps — usually human-caused, automations too",
  ambient: "temperature, CO2, humidity, battery — drifts and correlates with everything",
  prior: "time-of-day & composites — 'usually X at 23:00' is a prior, not evidence",
};

function EvidenceBar({ profile }: { profile: Record<string, number> }) {
  const order = ["direct", "behavioral", "ambient", "prior"];
  return (
    <div>
      <h4 style={{ margin: "0 0 6px", fontSize: 13.5 }}>What the trust rests on</h4>
      <div style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden" }}>
        {order.map((t) => (profile[t] ?? 0) > 0.001 && (
          <div key={t} title={`${t} ${(profile[t] * 100).toFixed(0)}% — ${TIER_HINTS[t]}`}
               style={{ width: `${profile[t] * 100}%`, background: TIER_COLORS[t] }} />
        ))}
      </div>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 6 }}>
        {order.map((t) => (profile[t] ?? 0) > 0.001 && (
          <span key={t} title={TIER_HINTS[t]}
                style={{ fontSize: 12, color: "var(--text-dim)", display: "inline-flex",
                         alignItems: "center", gap: 5 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: TIER_COLORS[t] }} />
            {t} {(profile[t] * 100).toFixed(0)}%
          </span>
        ))}
      </div>
      {(profile.direct ?? 0) < 0.4 && (
        <p style={{ margin: "6px 0 0", fontSize: 12, color: "var(--text-dim)" }}>
          Low direct share — this model leans on indirect signals. Predictions with weak
          per-window evidence are automatically held below the ask threshold.
        </p>
      )}
    </div>
  );
}

function Importances({ imp }: { imp: Record<string, number> }) {
  const entries = Object.entries(imp);
  const max = Math.max(...entries.map(([, v]) => v), 0.0001);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {entries.map(([feat, v]) => (
        <div key={feat} style={{ display: "grid", gridTemplateColumns: "minmax(96px, 200px) 1fr 44px", gap: 10, alignItems: "center", fontSize: 12.5 }}>
          <code style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{feat}</code>
          <div style={{ height: 8, background: "var(--surface-2)", borderRadius: 4 }}>
            <div style={{ height: "100%", width: `${(v / max) * 100}%`, background: "var(--accent)", borderRadius: 4 }} />
          </div>
          <span style={{ color: "var(--text-dim)", fontVariantNumeric: "tabular-nums" }}>{(v * 100).toFixed(1)}%</span>
        </div>
      ))}
    </div>
  );
}

function ModelCard({ m, onAction }: { m: Model; onAction: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const mt = m.metrics ?? {};
  const act = async (label: string, fn: () => Promise<Response>) => {
    setBusy(label);
    try { await fn().then(j); onAction(); } catch { /* refresh shows reality */ }
    setBusy("");
  };
  const confirmedLabels = m.label_counts?.confirmed ?? 0;
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 16,
                  display: "flex", flexDirection: "column", gap: 12,
                  ...(m.promoted ? { borderColor: "var(--accent)" } : {}) }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <Icon name="models" size={16} />
        <strong>{m.version}</strong>
        {m.promoted && (
          <span style={{ fontSize: 11.5, padding: "2px 8px", borderRadius: 99,
                         background: "color-mix(in srgb, var(--accent) 18%, transparent)",
                         color: "var(--accent)", fontWeight: 600 }}>LIVE</span>
        )}
        <StatusBadge status={mt.validation_status} nConfirmed={mt.n_confirmed ?? 0} />
        <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
          {m.trained_at ? new Date(m.trained_at).toLocaleString() : ""} · {m.algo}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          {!m.promoted && (
            <button className="btn btn-secondary" disabled={!!busy}
                    onClick={() => act("promote", () => fetch(`/api/models/${m.id}/promote`, { method: "POST" }))}>
              {busy === "promote" ? "…" : "Promote"}
            </button>
          )}
          <button className="btn btn-ghost" onClick={() => setOpen(!open)}>
            {open ? "Hide details" : "Details"}
          </button>
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <MetricChip label="confirmed acc" value={pct(mt.accuracy_confirmed)}
          hint={mt.accuracy_confirmed_ci ? `95% CI: ${pct(mt.accuracy_confirmed_ci[0])}–${pct(mt.accuracy_confirmed_ci[1])} — accuracy on labels a human confirmed` : "no confirmed labels in validation yet"} />
        <MetricChip label="bootstrap agr" value={pct(mt.accuracy_bootstrap)}
          hint="Agreement with rule-generated labels — NOT real accuracy, the rules can be wrong" />
        <MetricChip label="AUC (macro)" value={mt.auc_macro ? mt.auc_macro.toFixed(3) : "—"}
          hint="Ranking quality across all classes; 1.0 = perfect, 0.5 = coin flip" />
        <MetricChip label="train windows" value={String(mt.n_train ?? "—")} />
        <MetricChip label="features" value={String(mt.feature_count ?? "—")} />
        <MetricChip label="human labels" value={String(confirmedLabels)}
          hint="Confirmed answers from notifications, the Inbox and ribbon corrections" />
      </div>

      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {mt.per_class && (
            <div style={{ overflowX: "auto" }}>
              <table style={{ borderCollapse: "collapse", fontSize: 12.5, minWidth: 380 }}>
                <thead><tr>
                  {["activity", "precision", "recall", "F1", "support"].map((h) => (
                    <th key={h} style={{ padding: 6, textAlign: h === "activity" ? "left" : "right",
                                         color: "var(--text-dim)", fontWeight: 500 }}>{h}</th>))}
                </tr></thead>
                <tbody>
                  {Object.entries(mt.per_class).map(([cls, v]) => (
                    <tr key={cls} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={{ padding: 6, fontWeight: 500 }}>{cls}</td>
                      <td style={{ padding: 6, textAlign: "right" }}>{pct(v.precision)}</td>
                      <td style={{ padding: 6, textAlign: "right" }}>{pct(v.recall)}</td>
                      <td style={{ padding: 6, textAlign: "right" }}>{pct(v.f1)}</td>
                      <td style={{ padding: 6, textAlign: "right", color: "var(--text-dim)" }}>{v.support}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {mt.confusion && <Confusion c={mt.confusion} />}
          {mt.evidence_profile && Object.keys(mt.evidence_profile).length > 0 && (
            <EvidenceBar profile={mt.evidence_profile} />
          )}
          {mt.feature_importances ? (
            <div>
              <h4 style={{ margin: "0 0 8px", fontSize: 13.5 }}>What the model looks at</h4>
              <Importances imp={mt.feature_importances} />
            </div>
          ) : (
            <p style={{ fontSize: 12.5, color: "var(--text-dim)", margin: 0 }}>
              Feature importances appear for models trained from now on.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function Models() {
  const [models, setModels] = useState<Model[] | null>(null);
  const [persons, setPersons] = useState<{ id: string; name: string }[]>([]);
  const [training, setTraining] = useState("");
  const [trainMsg, setTrainMsg] = useState("");
  const load = () => fetch("/api/models").then(j).then(setModels).catch(() => setModels([]));
  useEffect(() => {
    load();
    fetch("/api/persons").then(j).then(setPersons).catch(() => {});
  }, []);
  const train = async (pid: string) => {
    setTraining(pid); setTrainMsg("");
    try {
      const r = await fetch("/api/models/train", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ person_id: pid }) }).then(j);
      setTrainMsg(r.trained ? `Trained ${r.version}${r.promoted ? " — promoted ✓" : " — kept previous (promotion gate)"}` : `Not trained: ${r.reason}`);
      load();
    } catch { setTrainMsg("Training failed — check logs"); }
    setTraining("");
  };
  const byPerson = (models ?? []).reduce<Record<string, Model[]>>((acc, m) => {
    (acc[m.person_id] = acc[m.person_id] ?? []).push(m); return acc;
  }, {});
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 860 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="models" size={22} />
        <h2 style={{ margin: 0 }}>Models</h2>
      </div>
      <p style={{ margin: 0, fontSize: 14, color: "var(--text-dim)", maxWidth: 640 }}>
        Every model Hearth has trained, with honest numbers: <strong>confirmed acc</strong> is
        measured only on labels a human gave; bootstrap agreement just says how much the model
        echoes the starter rules. New models go live only when the promotion gate says they're
        not credibly worse than the current one.
      </p>
      {trainMsg && <p style={{ margin: 0, fontSize: 13.5, color: "var(--accent)" }}>{trainMsg}</p>}
      {models === null && <p style={{ color: "var(--text-dim)" }}>Loading…</p>}
      {models !== null && Object.keys(byPerson).length === 0 && (
        <p style={{ color: "var(--text-dim)" }}>No models yet — they appear after the first training run.</p>
      )}
      {Object.entries(byPerson).map(([pid, list]) => {
        const name = persons.find((p) => p.id === pid)?.name ?? pid;
        const sorted = [...list].sort((a, b) => (b.trained_at ?? "").localeCompare(a.trained_at ?? ""));
        return (
          <div key={pid} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <h3 style={{ margin: 0, fontSize: 16 }}>{name}</h3>
              <button className="btn btn-secondary" style={{ marginLeft: "auto" }}
                      disabled={training === pid}
                      onClick={() => train(pid)}>
                {training === pid ? "Training…" : "Train now"}
              </button>
            </div>
            {sorted.slice(0, 6).map((m) => <ModelCard key={m.id} m={m} onAction={load} />)}
            {sorted.length > 6 && (
              <p style={{ fontSize: 12.5, color: "var(--text-dim)", margin: 0 }}>
                + {sorted.length - 6} older versions kept for rollback.
              </p>
            )}
          </div>
        );
      })}
    </section>
  );
}

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
  accuracy_gold?: number | null;
  accuracy_gold_ci?: [number, number];
  n_gold?: number;
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
  calibration?: {
    brier: number; ece: number; n_check: number;
    reliability?: { conf: number; acc: number; n: number }[];
  };
};

const GOLD_MIN = 30;  // matches MIN_CONFIRMED_FOR_VALIDATED — below this the
                      // unbiased spot-check estimate is too thin to headline
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

function HeroAccuracy({ mt }: { mt: Metrics }) {
  // The honest headline (audit F1): accuracy measured on RANDOM spot-checks
  // (ε-explore asks) is an unbiased sample of the home's life. accuracy_confirmed
  // pools those with the hard, uncertainty-sampled windows we deliberately asked
  // about, so it reads pessimistically — shown beside, not as, the headline.
  const nGold = mt.n_gold ?? 0;
  const ready = nGold >= GOLD_MIN && mt.accuracy_gold != null;
  return (
    <div style={{ padding: "10px 16px", border: "1px solid var(--accent)", borderRadius: 10,
                  minWidth: 150, background: "color-mix(in srgb, var(--accent) 7%, transparent)" }}
         title={ready && mt.accuracy_gold_ci
           ? `95% CI: ${pct(mt.accuracy_gold_ci[0])}–${pct(mt.accuracy_gold_ci[1])}. Measured on ${nGold} random spot-checks — an unbiased sample, the fair estimate of real-world accuracy.`
           : `Gathering random spot-checks (${nGold}/${GOLD_MIN}). Until then the fair headline can't be measured; the numbers beside are on harder, hand-picked moments.`}>
      <div style={{ fontSize: 24, fontWeight: 700, fontVariantNumeric: "tabular-nums",
                    color: ready ? "var(--text)" : "var(--text-dim)" }}>
        {ready ? pct(mt.accuracy_gold) : `${nGold}/${GOLD_MIN}`}
      </div>
      <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
        {ready ? "real-world accuracy" : "gathering spot-checks"}
      </div>
      {ready && mt.accuracy_gold_ci && (
        <div style={{ fontSize: 11, color: "var(--text-dim)", fontVariantNumeric: "tabular-nums" }}>
          ±{pct((mt.accuracy_gold_ci[1] - mt.accuracy_gold_ci[0]) / 2)}
        </div>
      )}
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

function Calibration({ cal }: { cal: NonNullable<Metrics["calibration"]> }) {
  // Reliability diagram (UX2): predicted confidence (x) vs observed accuracy (y).
  // The diagonal is perfect calibration; points below = overconfident, above =
  // underconfident. ECE/Brier measured out-of-sample (audit F4).
  const S = 150, pad = 22;
  const pts = cal.reliability ?? [];
  const x = (v: number) => pad + v * (S - 2 * pad);
  const y = (v: number) => S - pad - v * (S - 2 * pad);
  const example = pts.find((p) => p.conf >= 0.55) ?? pts[pts.length - 1];
  return (
    <div>
      <h4 style={{ margin: "0 0 6px", fontSize: 13.5 }}>Is its confidence honest?</h4>
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center" }}>
        <svg viewBox={`0 0 ${S} ${S}`} width={S} height={S} aria-label="reliability diagram"
             style={{ border: "1px solid var(--border)", borderRadius: 8 }}>
          <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="var(--text-dim)"
                strokeWidth="1" strokeDasharray="3 3" />
          <polyline points={pts.map((p) => `${x(p.conf).toFixed(1)},${y(p.acc).toFixed(1)}`).join(" ")}
                    fill="none" stroke="var(--accent)" strokeWidth="2" />
          {pts.map((p, i) => (
            <circle key={i} cx={x(p.conf)} cy={y(p.acc)} r={2.5 + Math.min(3, p.n / 20)}
                    fill="var(--accent)" opacity={0.8}><title>{`says ${(p.conf * 100).toFixed(0)}% · right ${(p.acc * 100).toFixed(0)}% (n=${p.n})`}</title></circle>
          ))}
          <text x={S / 2} y={S - 4} fontSize="8" textAnchor="middle" fill="var(--text-dim)">says →</text>
          <text x={6} y={S / 2} fontSize="8" textAnchor="middle" fill="var(--text-dim)"
                transform={`rotate(-90 6 ${S / 2})`}>is right →</text>
        </svg>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", gap: 10 }}>
            <MetricChip label="ECE" value={cal.ece.toFixed(3)}
              hint="Expected Calibration Error — mean gap between stated confidence and actual accuracy. 0 = perfect." />
            <MetricChip label="Brier" value={cal.brier.toFixed(3)}
              hint="Brier score — overall probability error (lower is sharper & truer)." />
          </div>
          {example && (
            <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)", maxWidth: 260 }}>
              When Hearth says <strong>{(example.conf * 100).toFixed(0)}%</strong> it's right about{" "}
              <strong>{(example.acc * 100).toFixed(0)}%</strong> of the time. Measured on a held-out
              slice it never calibrated on, so it's a fair check.
            </p>
          )}
        </div>
      </div>
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

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "stretch" }}>
        <HeroAccuracy mt={mt} />
        <MetricChip label="on tricky moments" value={pct(mt.accuracy_confirmed)}
          hint={mt.accuracy_confirmed_ci ? `95% CI: ${pct(mt.accuracy_confirmed_ci[0])}–${pct(mt.accuracy_confirmed_ci[1])}. Accuracy on the uncertain windows Hearth deliberately asked about — these are the HARD cases, so this reads lower than real-world accuracy. Great for training, not the fair headline.` : "no confirmed labels in validation yet"} />
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
          {mt.calibration && (mt.calibration.reliability?.length ?? 0) > 0 && (
            <Calibration cal={mt.calibration} />
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

function MiniTrend({ accs, aucs }: { accs: (number | null)[]; aucs: (number | null)[] }) {
  const W = 260, H = 60, pad = 4;
  const n = accs.length;
  if (n < 2) return null;
  const x = (i: number) => pad + (i / (n - 1)) * (W - 2 * pad);
  const y = (v: number) => H - pad - v * (H - 2 * pad);          // 0..1 -> bottom..top
  const line = (vals: (number | null)[]) => vals
    .map((v, i) => (v === null || v === undefined) ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`)
    .filter(Boolean).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none"
         style={{ maxWidth: W }} aria-label="metrics across versions">
      {[0.25, 0.5, 0.75].map((g) => (
        <line key={g} x1={pad} x2={W - pad} y1={y(g)} y2={y(g)}
              stroke="var(--border)" strokeWidth="0.5" vectorEffect="non-scaling-stroke" />
      ))}
      <polyline points={line(aucs)} fill="none" stroke="var(--text-dim)" strokeWidth="1.5"
                strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
      <polyline points={line(accs)} fill="none" stroke="var(--accent)" strokeWidth="2"
                vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Per-person version history: a trend of confirmed-accuracy + AUC across model
 *  versions (the honest "drift over time" view) plus a compact compare table. */
function PersonHistory({ models }: { models: Model[] }) {
  const [open, setOpen] = useState(false);
  if (models.length < 2) return null;
  const series = [...models].reverse();             // oldest -> newest
  const accs = series.map((m) => m.metrics?.accuracy_gold
    ?? m.metrics?.accuracy_confirmed ?? m.metrics?.accuracy_bootstrap ?? null);
  const aucs = series.map((m) => m.metrics?.auc_macro ?? null);
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden" }}>
      <button onClick={() => setOpen(!open)}
              style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", border: "none",
                       background: open ? "var(--surface-2)" : "transparent", cursor: "pointer",
                       color: "var(--text)", padding: "10px 14px", textAlign: "left" }}>
        <Icon name="models" size={15} />
        <strong style={{ fontSize: 13.5 }}>History &amp; trend</strong>
        <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
          {models.length} versions
        </span>
        <span style={{ marginLeft: "auto", fontSize: 12.5, color: "var(--text-dim)" }}>
          {open ? "Hide" : "Compare"}
        </span>
      </button>
      {open && (
        <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12,
                      borderTop: "1px solid var(--border)" }}>
          <div>
            <MiniTrend accs={accs} aucs={aucs} />
            <div style={{ display: "flex", gap: 16, fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                <span style={{ width: 14, height: 2, background: "var(--accent)" }} /> confirmed accuracy
              </span>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                <span style={{ width: 14, height: 0, borderTop: "2px dashed var(--text-dim)" }} /> AUC
              </span>
              <span>oldest → newest · watch for a downward drift</span>
            </div>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ borderCollapse: "collapse", fontSize: 12.5, width: "100%" }}>
              <thead><tr>
                {["version", "trained", "confirmed", "bootstrap", "AUC", "windows", "status"].map((h) => (
                  <th key={h} style={{ textAlign: h === "version" || h === "trained" ? "left" : "right",
                                       padding: 6, color: "var(--text-dim)", fontWeight: 500 }}>{h}</th>))}
              </tr></thead>
              <tbody>
                {models.map((m) => {
                  const mt = m.metrics ?? {};
                  const status = m.promoted ? "live" : (mt.validation_status ?? "");
                  return (
                    <tr key={m.id} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={{ padding: 6 }}><code style={{ fontSize: 11.5 }}>{m.version}</code></td>
                      <td style={{ padding: 6, color: "var(--text-dim)" }}>
                        {m.trained_at ? new Date(m.trained_at).toLocaleDateString() : "—"}</td>
                      <td style={{ padding: 6, textAlign: "right" }}>{pct(mt.accuracy_confirmed)}</td>
                      <td style={{ padding: 6, textAlign: "right", color: "var(--text-dim)" }}>{pct(mt.accuracy_bootstrap)}</td>
                      <td style={{ padding: 6, textAlign: "right" }}>{mt.auc_macro ? mt.auc_macro.toFixed(3) : "—"}</td>
                      <td style={{ padding: 6, textAlign: "right", color: "var(--text-dim)" }}>{mt.n_train ?? "—"}</td>
                      <td style={{ padding: 6, textAlign: "right",
                                   color: status === "live" ? "var(--accent)"
                                     : status === "provisional" ? "var(--danger)" : "var(--text-dim)" }}>
                        {status}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

type DriftReport = {
  person_id: string; model_version: string; computed_at: string;
  recent_days: number; n_expected: number; n_actual: number;
  psi: Record<string, number>; drifted: string[]; max_psi: number;
  severe: boolean; trend?: number[];
};

function PsiTrend({ trend }: { trend: number[] }) {
  const W = 180, H = 40, pad = 3;
  if (trend.length < 2) return null;
  const top = Math.max(0.4, ...trend);
  const x = (i: number) => pad + (i / (trend.length - 1)) * (W - 2 * pad);
  const y = (v: number) => H - pad - (v / top) * (H - 2 * pad);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} preserveAspectRatio="none"
         aria-label="drift trend">
      <line x1={pad} x2={W - pad} y1={y(0.2)} y2={y(0.2)} stroke="var(--danger)"
            strokeWidth="0.5" strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
      <polyline points={trend.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}
                fill="none" stroke="var(--accent)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Drift & Health (UX3 / audit F5): the Evidently-style "what to track / when to
 *  act" surface. Per-person PSI trend, drifted features, and the opt-in retrain. */
function DriftHealth({ personName }: { personName: (id: string) => string }) {
  const [reports, setReports] = useState<Record<string, DriftReport> | null>(null);
  const [auto, setAuto] = useState(false);
  const [busy, setBusy] = useState("");
  const load = () => fetch("/api/drift").then(j).then(setReports).catch(() => setReports({}));
  useEffect(() => {
    load();
    fetch("/api/drift/auto-retrain").then(j).then((d) => setAuto(!!d.enabled)).catch(() => {});
  }, []);
  const recompute = async () => {
    setBusy("run");
    try { await fetch("/api/drift/run", { method: "POST" }).then(j); load(); } catch { /* */ }
    setBusy("");
  };
  const toggleAuto = async () => {
    const next = !auto; setAuto(next);
    try { await fetch("/api/drift/auto-retrain", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: next }) }).then(j); } catch { setAuto(!next); }
  };
  if (reports === null) return null;
  const list = Object.values(reports);
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 16,
                  display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <Icon name="sensors" size={16} />
        <strong style={{ fontSize: 14 }}>Drift &amp; health</strong>
        <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
          has the home changed since the model was trained?
        </span>
        <label style={{ marginLeft: "auto", fontSize: 12.5, color: "var(--text-dim)",
                        display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}
               title="When a feature drifts severely, retrain automatically instead of just flagging it.">
          <input type="checkbox" checked={auto} onChange={toggleAuto} /> auto-retrain on severe drift
        </label>
        <button className="btn btn-ghost" disabled={!!busy} onClick={recompute}>
          {busy === "run" ? "Checking…" : "Recompute"}
        </button>
      </div>
      {list.length === 0 && (
        <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
          No drift checks yet — they run daily once a model is live, or hit Recompute.
        </p>
      )}
      {list.map((r) => {
        const drifted = r.drifted ?? [];
        return (
          <div key={r.person_id} style={{ display: "flex", flexDirection: "column", gap: 8,
                                          borderTop: "1px solid var(--border)", paddingTop: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <strong style={{ fontSize: 13 }}>{personName(r.person_id)}</strong>
              <span style={{ fontSize: 11.5, padding: "2px 8px", borderRadius: 99, fontWeight: 600,
                background: drifted.length === 0
                  ? "color-mix(in srgb, var(--ok, #34D399) 16%, transparent)"
                  : r.severe ? "color-mix(in srgb, var(--danger) 16%, transparent)"
                             : "color-mix(in srgb, var(--accent) 16%, transparent)",
                color: drifted.length === 0 ? "var(--ok, #34D399)"
                  : r.severe ? "var(--danger)" : "var(--accent)" }}>
                {drifted.length === 0 ? "stable" : r.severe ? "severe drift" : `${drifted.length} drifted`}
              </span>
              <span style={{ fontSize: 11.5, color: "var(--text-dim)", marginLeft: "auto" }}>
                {r.computed_at ? new Date(r.computed_at).toLocaleDateString() : ""} · vs {r.model_version}
              </span>
            </div>
            {r.trend && r.trend.length > 1 && (
              <div>
                <PsiTrend trend={r.trend} />
                <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
                  worst-feature drift over recent checks · dashed line = investigate (0.2)
                </div>
              </div>
            )}
            {drifted.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                {drifted.slice(0, 6).map((f) => {
                  const v = r.psi[f] ?? 0;
                  return (
                    <div key={f} style={{ display: "grid", gridTemplateColumns: "minmax(96px,200px) 1fr 44px",
                                          gap: 10, alignItems: "center", fontSize: 12 }}>
                      <code style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f}</code>
                      <div style={{ height: 7, background: "var(--surface-2)", borderRadius: 4 }}>
                        <div style={{ height: "100%", width: `${Math.min(100, (v / 0.5) * 100)}%`,
                                      background: v > 0.5 ? "var(--danger)" : "var(--accent)", borderRadius: 4 }} />
                      </div>
                      <span style={{ color: "var(--text-dim)", fontVariantNumeric: "tabular-nums" }}>{v.toFixed(2)}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
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
        Every model Hearth has trained, with honest numbers. <strong>Real-world accuracy</strong> is
        measured on random spot-checks — an unbiased sample, so it's the fair headline; the
        "tricky moments" number is on the hard windows Hearth asked about and reads lower by design.
        Bootstrap agreement just says how much the model echoes the starter rules. New models go
        live only when the promotion gate says they're not credibly worse than the current one.
      </p>
      {trainMsg && <p style={{ margin: 0, fontSize: 13.5, color: "var(--accent)" }}>{trainMsg}</p>}
      <DriftHealth personName={(id) => persons.find((p) => p.id === id)?.name ?? id} />
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
            <PersonHistory models={sorted} />
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

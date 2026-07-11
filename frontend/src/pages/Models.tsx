/**
 * Models — the glass box. Per-person model registry with honest metrics:
 * confirmed-label accuracy (with CI) vs bootstrap agreement, per-class
 * precision/recall/F1, confusion matrix, feature importances, and
 * train / promote actions. Spec: docs/UI_SPEC.md §Models.
 */
import { useEffect, useState, type ReactNode } from "react";
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
  flat_baseline?: {
    accuracy_gold?: number; accuracy_confirmed?: number;
    accuracy_bootstrap?: number; n_gold?: number; n_confirmed?: number;
  };
  excluded_features?: string[];
  slices?: {
    dayparts: string[];
    by_activity_daypart: { activity: string; cells: { acc: number | null; n: number }[] }[];
  };
};

const GOLD_MIN = 30;  // matches MIN_CONFIRMED_FOR_VALIDATED — below this the
                      // unbiased spot-check estimate is too thin to headline
type Model = {
  id: number; person_id: string; version: string; algo: string;
  trained_at: string | null; promoted: boolean;
  label_counts: Record<string, number>; metrics: Metrics;
};

type Cadence = { due: boolean; streak: number; interval_days: number;
                 last_trained: string | null; phase: "first" | "learning" | "plateauing" | "stable" };

/** Adaptive-cadence status in plain words — daily while improving, easing off
 *  once the promotion gate keeps saying "no better". */
function CadenceLine({ c }: { c?: Cadence }) {
  if (!c) return null;
  const text =
    c.phase === "first" ? "First model pending — it trains the moment there's enough data." :
    c.phase === "learning" ? "Training daily while it's still improving." :
    c.phase === "plateauing" ? "Improvement is slowing — training every 3 days now." :
    "Stable — training weekly. A real improvement or a batch of new answers snaps it back to daily.";
  return (
    <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)", display: "flex",
                alignItems: "center", gap: 6 }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
                     background: c.phase === "stable" ? "var(--ok, #34D399)"
                       : c.phase === "first" ? "var(--text-dim)" : "var(--accent)" }} />
      {text}
      {c.due && c.phase !== "first" && <span>· next run tonight</span>}
    </p>
  );
}

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

function SliceHeatmap({ slices }: { slices: NonNullable<Metrics["slices"]> }) {
  // Per-slice accuracy (UX5): rows = activities, cols = daypart. Red cells are
  // exactly where the model fails — the aggregate can't show this.
  const cell = (acc: number | null) => {
    if (acc === null) return "transparent";
    return acc >= 0.5
      ? `color-mix(in srgb, var(--ok, #34D399) ${20 + 60 * ((acc - 0.5) / 0.5)}%, transparent)`
      : `color-mix(in srgb, var(--danger) ${20 + 60 * ((0.5 - acc) / 0.5)}%, transparent)`;
  };
  return (
    <div>
      <h4 style={{ margin: "0 0 6px", fontSize: 13.5 }}>Where it does well — and doesn't</h4>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", fontSize: 12.5 }}>
          <thead><tr>
            <th style={{ padding: 6, textAlign: "left", color: "var(--text-dim)", fontWeight: 500 }}>activity</th>
            {slices.dayparts.map((d) => (
              <th key={d} style={{ padding: 6, color: "var(--text-dim)", fontWeight: 500 }}>{d}</th>))}
          </tr></thead>
          <tbody>
            {slices.by_activity_daypart.map((r) => (
              <tr key={r.activity}>
                <td style={{ padding: 6, fontWeight: 500 }}>{r.activity}</td>
                {r.cells.map((c, i) => (
                  <td key={i} title={c.n ? `${(c.acc! * 100).toFixed(0)}% right · ${c.n} windows` : "no windows"}
                      style={{ padding: "6px 12px", textAlign: "center", borderRadius: 6,
                               fontVariantNumeric: "tabular-nums", background: cell(c.acc),
                               color: c.n ? "var(--text)" : "var(--text-dim)" }}>
                    {c.n ? `${(c.acc! * 100).toFixed(0)}%` : "·"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: 12, color: "var(--text-dim)", margin: "6px 0 0" }}>
        Red = struggling in that part of day. A dot means too few windows to judge.
      </p>
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

function Row({ k, v, hint }: { k: string; v: ReactNode; hint?: string }) {
  return (
    <div title={hint} style={{ display: "flex", justifyContent: "space-between", gap: 16,
                               fontSize: 12.5, padding: "3px 0", borderBottom: "1px solid var(--border)" }}>
      <span style={{ color: "var(--text-dim)" }}>{k}</span>
      <span style={{ fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{v}</span>
    </div>
  );
}

/** Model Card (UX4): a consolidated, printable "nutrition label" per model in the
 *  Mitchell-2019 / IBM-FactSheets idiom — what it is, on what data, how well, with
 *  what limits. Every field already exists in the registry; this just gathers the
 *  scattered numbers into one honest, shareable artifact. */
function ModelCardSheet({ m, personName }: { m: Model; personName: string }) {
  const mt = m.metrics ?? {};
  const fb = mt.flat_baseline;
  const mine = mt.accuracy_gold ?? mt.accuracy_confirmed;
  const flat = fb?.accuracy_gold ?? fb?.accuracy_confirmed;
  const flatVerdict = mine == null || flat == null || (mine === 0 && flat === 0) ? null
    : mine > flat + 0.005 ? "the hierarchy is pulling its weight."
    : mine < flat - 0.005 ? "the flat model does better; the hierarchy isn't helping here."
    : "the flat model does just as well; the hierarchy isn't adding anything yet.";
  return (
    <div style={{ border: "1px solid var(--accent)", borderRadius: 12, padding: 18,
                  display: "flex", flexDirection: "column", gap: 14,
                  background: "var(--surface)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="models" size={18} />
        <div>
          <strong style={{ fontSize: 15 }}>Model card · {personName}</strong>
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
            {m.version} · {m.algo} · {m.trained_at ? new Date(m.trained_at).toLocaleString() : ""}
          </div>
        </div>
        <button className="btn btn-ghost" style={{ marginLeft: "auto" }}
                onClick={() => window.open(`/models/report?person=${encodeURIComponent(m.person_id)}&print=1`, "_blank")}>
          Print / Save PDF</button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 24px" }}>
        <div>
          <h4 style={{ margin: "0 0 4px", fontSize: 13 }}>Trained on</h4>
          <Row k="training windows" v={mt.n_train ?? "—"} />
          <Row k="features" v={mt.feature_count ?? "—"} />
          <Row k="human labels" v={m.label_counts?.confirmed ?? 0} />
          <Row k="rule labels" v={m.label_counts?.bootstrap ?? 0}
               hint="Windows labelled by the starter rules — used to bootstrap, not ground truth." />
        </div>
        <div>
          <h4 style={{ margin: "0 0 4px", fontSize: 13 }}>How well</h4>
          <Row k="real-world accuracy" hint="On random spot-checks — the fair headline."
               v={mt.n_gold && mt.n_gold >= GOLD_MIN ? pct(mt.accuracy_gold)
                  : `gathering (${mt.n_gold ?? 0}/${GOLD_MIN})`} />
          <Row k="on tricky moments" v={pct(mt.accuracy_confirmed)}
               hint="On the hard windows Hearth asked about — reads lower by design." />
          <Row k="AUC (macro)" v={mt.auc_macro ? mt.auc_macro.toFixed(3) : "—"} />
          <Row k="calibration (ECE)" v={mt.calibration ? mt.calibration.ece.toFixed(3) : "—"}
               hint="Gap between stated confidence and real accuracy; 0 = perfect." />
        </div>
      </div>

      {flatVerdict && (
        <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
          <strong>Earns its complexity?</strong> This model scores {pct(mine)} vs a plain
          flat model's {pct(flat)} on the same split — {flatVerdict}
        </p>
      )}

      {mt.excluded_features && mt.excluded_features.length > 0 && (
        <p style={{ margin: 0, fontSize: 12, color: "var(--text-dim)" }}>
          <strong>Excluded:</strong> {mt.excluded_features.slice(0, 8).join(", ")}
          {mt.excluded_features.length > 8 ? ` +${mt.excluded_features.length - 8} more` : ""} — dropped
          as low-variance or another member's personal sensors.
        </p>
      )}

      <p style={{ margin: 0, fontSize: 11.5, color: "var(--text-dim)", lineHeight: 1.5 }}>
        <strong>Intended use:</strong> recognise this household member's everyday activities from
        their own sensors, for automations and insight. <strong>Limits:</strong> trained only on
        this home; accuracy is honest only once enough spot-checks back it (see status badge); rare
        activities have few examples; predictions below the confidence threshold abstain rather than
        guess.
      </p>
    </div>
  );
}

/** Collapsible section. Children are mounted only while open, so any data fetch
 *  inside runs lazily on first open (used for What-if / Drift / History). */
function Fold({ title, hint, badge, defaultOpen = false, children }: {
  title: string; hint?: string; badge?: ReactNode; defaultOpen?: boolean; children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 10, overflow: "hidden" }}>
      <button onClick={() => setOpen(!open)}
        style={{ display: "flex", alignItems: "center", gap: 9, width: "100%", border: "none",
                 background: open ? "var(--surface-2)" : "transparent", cursor: "pointer",
                 color: "var(--text)", padding: "9px 13px", textAlign: "left" }}>
        <span style={{ color: "var(--text-dim)", display: "inline-flex", transition: "transform .15s",
                       transform: open ? "rotate(90deg)" : "none", fontSize: 11 }}>▶</span>
        <strong style={{ fontSize: 13 }}>{title}</strong>
        {hint && <span style={{ fontSize: 12, color: "var(--text-dim)" }}>{hint}</span>}
        {badge && <span style={{ marginLeft: "auto" }}>{badge}</span>}
      </button>
      {open && <div style={{ padding: 13, borderTop: "1px solid var(--border)" }}>{children}</div>}
    </div>
  );
}

/** The live model's headline chip row. */
function LiveChips({ m }: { m: Model }) {
  const mt = m.metrics ?? {};
  const confirmedLabels = m.label_counts?.confirmed ?? 0;
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "stretch" }}>
      <HeroAccuracy mt={mt} />
      <MetricChip label="on tricky moments" value={pct(mt.accuracy_confirmed)}
        hint={mt.accuracy_confirmed_ci ? `95% CI: ${pct(mt.accuracy_confirmed_ci[0])}–${pct(mt.accuracy_confirmed_ci[1])}. Accuracy on the uncertain windows Hearth deliberately asked about — the HARD cases, so it reads lower than real-world accuracy.` : "no confirmed labels in validation yet"} />
      <MetricChip label="bootstrap agr" value={pct(mt.accuracy_bootstrap)}
        hint="Agreement with rule-generated labels — NOT real accuracy, the rules can be wrong" />
      <MetricChip label="AUC (macro)" value={mt.auc_macro ? mt.auc_macro.toFixed(3) : "—"}
        hint="Ranking quality across all classes; 1.0 = perfect, 0.5 = coin flip" />
      <MetricChip label="train windows" value={String(mt.n_train ?? "—")} />
      <MetricChip label="features" value={String(mt.feature_count ?? "—")} />
      <MetricChip label="human labels" value={String(confirmedLabels)}
        hint="Confirmed answers from notifications, the Inbox and ribbon corrections" />
    </div>
  );
}

/** Per-class precision/recall/F1 table for the live model. */
function PerClassTable({ per }: { per: NonNullable<Metrics["per_class"]> }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", fontSize: 12.5, minWidth: 380 }}>
        <thead><tr>
          {["activity", "precision", "recall", "F1", "support"].map((h) => (
            <th key={h} style={{ padding: 6, textAlign: h === "activity" ? "left" : "right",
                                 color: "var(--text-dim)", fontWeight: 500 }}>{h}</th>))}
        </tr></thead>
        <tbody>
          {Object.entries(per).map(([cls, v]) => (
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
type Gate = {
  live?: string; candidate?: string;
  deltas?: Record<string, { candidate: number | null; live: number | null }>;
  gate?: { promoted: boolean; basis: string; reason: string };
};

/** History & versions body (rendered inside a Fold): promotion-gate verdict,
 *  accuracy trend, and every version as a row with Promote / Roll back. */
function HistoryBody({ models, onAction }: { models: Model[]; onAction: () => void }) {
  const [gate, setGate] = useState<Gate | null>(null);
  const [busy, setBusy] = useState("");
  const pid = models[0]?.person_id;
  useEffect(() => {
    if (pid) fetch(`/api/models/gate?person=${encodeURIComponent(pid)}`).then(j).then(setGate).catch(() => {});
  }, [pid]);
  const act = async (label: string, fn: () => Promise<Response>) => {
    setBusy(label);
    try { await fn().then(j); onAction(); } catch { /* refresh shows reality */ }
    setBusy("");
  };
  const series = [...models].reverse();             // oldest -> newest
  const accs = series.map((m) => m.metrics?.accuracy_gold
    ?? m.metrics?.accuracy_confirmed ?? m.metrics?.accuracy_bootstrap ?? null);
  const aucs = series.map((m) => m.metrics?.auc_macro ?? null);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {gate?.gate && gate.candidate && (
        <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "10px 12px",
                      display: "flex", flexDirection: "column", gap: 6, background: "var(--surface-2)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <Icon name={gate.gate.promoted ? "check" : "warning"} size={14} />
            <strong style={{ fontSize: 13 }}>{gate.candidate} vs live {gate.live}</strong>
            <span style={{ fontSize: 11.5, padding: "2px 8px", borderRadius: 99, fontWeight: 600,
              background: gate.gate.promoted
                ? "color-mix(in srgb, var(--ok, #34D399) 16%, transparent)"
                : "color-mix(in srgb, var(--danger) 16%, transparent)",
              color: gate.gate.promoted ? "var(--ok, #34D399)" : "var(--danger)" }}>
              {gate.gate.promoted ? "would promote" : "held back"}
            </span>
            <button className="btn btn-ghost" style={{ marginLeft: "auto" }} disabled={!!busy}
                    onClick={() => act("rollback", () => fetch("/api/models/rollback", { method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ person_id: pid }) }))}>
              {busy === "rollback" ? "…" : "Roll back"}
            </button>
          </div>
          <p style={{ margin: 0, fontSize: 12, color: "var(--text-dim)" }}>{gate.gate.reason}</p>
        </div>
      )}
      {models.length >= 2 && (
        <div>
          <MiniTrend accs={accs} aucs={aucs} />
          <div style={{ display: "flex", gap: 16, fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
              <span style={{ width: 14, height: 2, background: "var(--accent)" }} /> accuracy
            </span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
              <span style={{ width: 14, height: 0, borderTop: "2px dashed var(--text-dim)" }} /> AUC
            </span>
            <span>oldest → newest · watch for a downward drift</span>
          </div>
        </div>
      )}
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", fontSize: 12.5, width: "100%" }}>
          <thead><tr>
            {["version", "trained", "real-world", "AUC", "windows", "status", ""].map((h, i) => (
              <th key={i} style={{ textAlign: i <= 1 ? "left" : i === 6 ? "right" : "right",
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
                  <td style={{ padding: 6, textAlign: "right" }}>
                    {pct(mt.accuracy_gold ?? mt.accuracy_confirmed)}</td>
                  <td style={{ padding: 6, textAlign: "right" }}>{mt.auc_macro ? mt.auc_macro.toFixed(3) : "—"}</td>
                  <td style={{ padding: 6, textAlign: "right", color: "var(--text-dim)" }}>{mt.n_train ?? "—"}</td>
                  <td style={{ padding: 6, textAlign: "right",
                               color: status === "live" ? "var(--accent)"
                                 : status === "provisional" ? "var(--danger)" : "var(--text-dim)" }}>{status}</td>
                  <td style={{ padding: 6, textAlign: "right" }}>
                    {!m.promoted && (
                      <button className="btn btn-ghost" style={{ fontSize: 12, padding: "2px 8px" }}
                              disabled={!!busy}
                              onClick={() => act(`promote-${m.id}`,
                                () => fetch(`/api/models/${m.id}/promote`, { method: "POST" }))}>
                        {busy === `promote-${m.id}` ? "…" : "Promote"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
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

/** Drift & health body for ONE person (rendered inside a Fold): PSI trend,
 *  drifted features, the opt-in auto-retrain (global setting), and Recompute. */
function PersonDrift({ report, auto, onToggleAuto, onRecompute, busy }: {
  report: DriftReport | null | undefined; auto: boolean;
  onToggleAuto: () => void; onRecompute: () => void; busy: boolean;
}) {
  const drifted = report?.drifted ?? [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <label style={{ fontSize: 12.5, color: "var(--text-dim)", display: "inline-flex",
                        alignItems: "center", gap: 6, cursor: "pointer" }}
               title="When a feature drifts severely, retrain automatically instead of just flagging it.">
          <input type="checkbox" checked={auto} onChange={onToggleAuto} /> auto-retrain on severe drift
        </label>
        <button className="btn btn-ghost" style={{ marginLeft: "auto" }} disabled={busy} onClick={onRecompute}>
          {busy ? "Checking…" : "Recompute"}
        </button>
      </div>
      {!report ? (
        <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
          No drift check yet — runs daily once a model is live, or hit Recompute.
        </p>
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontSize: 11.5, padding: "2px 8px", borderRadius: 99, fontWeight: 600,
              background: drifted.length === 0 ? "color-mix(in srgb, var(--ok, #34D399) 16%, transparent)"
                : report.severe ? "color-mix(in srgb, var(--danger) 16%, transparent)"
                                : "color-mix(in srgb, var(--accent) 16%, transparent)",
              color: drifted.length === 0 ? "var(--ok, #34D399)"
                : report.severe ? "var(--danger)" : "var(--accent)" }}>
              {drifted.length === 0 ? "stable" : report.severe ? "severe drift" : `${drifted.length} drifted`}
            </span>
            <span style={{ fontSize: 11.5, color: "var(--text-dim)", marginLeft: "auto" }}>
              {report.computed_at ? new Date(report.computed_at).toLocaleDateString() : ""} · vs {report.model_version}
            </span>
          </div>
          {report.trend && report.trend.length > 1 && (
            <div>
              <PsiTrend trend={report.trend} />
              <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
                worst-feature drift over recent checks · dashed line = investigate (0.2)
              </div>
            </div>
          )}
          {drifted.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
              {drifted.slice(0, 6).map((f) => {
                const v = report.psi[f] ?? 0;
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
        </>
      )}
    </div>
  );
}

type Probe = {
  error?: string;
  window_ts?: string; predicted?: string; confidence?: number;
  probabilities?: Record<string, number>; features?: Record<string, number>;
  explanation?: [string, number][]; edited?: string[];
};

/** What-If probe (UX8): perturb a feature, watch the live model's prediction
 *  move. The Google What-If Tool / LIT idiom — the standout trust-builder. */
function WhatIfBody({ personId }: { personId: string }) {
  const [p, setP] = useState<Probe | null>(null);
  const [ov, setOv] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const probe = async (overrides: Record<string, number>) => {
    setBusy(true);
    try {
      const r = await fetch("/api/predict/probe", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ person_id: personId, overrides }) }).then(j);
      setP(r);
    } catch { setP({ error: "probe_failed" }); }
    setBusy(false);
  };
  useEffect(() => { probe({}); /* lazy: runs when the fold first mounts */ // eslint-disable-next-line
  }, [personId]);
  const reset = () => { setOv({}); probe({}); };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {!p && <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>Loading…</p>}
          {p?.error && <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
            {p.error === "no_model" ? "No live model yet." : p.error === "no_features"
              ? "No recent feature windows to probe." : "Probe failed."}</p>}
          {p && !p.error && (
            <>
              <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
                <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>predicts</span>
                <strong style={{ fontSize: 18 }}>{p.predicted}</strong>
                <span style={{ fontSize: 13, color: "var(--accent)", fontVariantNumeric: "tabular-nums" }}>
                  {pct(p.confidence)}</span>
                {(p.edited?.length ?? 0) > 0 && (
                  <button className="btn btn-ghost" style={{ marginLeft: "auto" }} onClick={reset}>
                    Reset</button>
                )}
              </div>
              {p.probabilities && (
                <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                  {Object.entries(p.probabilities).map(([cls, v]) => (
                    <div key={cls} style={{ display: "grid", gridTemplateColumns: "minmax(80px,140px) 1fr 44px",
                                            gap: 10, alignItems: "center", fontSize: 12.5 }}>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{cls}</span>
                      <div style={{ height: 7, background: "var(--surface-2)", borderRadius: 4 }}>
                        <div style={{ height: "100%", width: `${v * 100}%`,
                                      background: cls === p.predicted ? "var(--accent)" : "var(--text-dim)",
                                      borderRadius: 4 }} />
                      </div>
                      <span style={{ color: "var(--text-dim)", fontVariantNumeric: "tabular-nums" }}>{pct(v)}</span>
                    </div>
                  ))}
                </div>
              )}
              {p.features && Object.keys(p.features).length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <h4 style={{ margin: 0, fontSize: 13 }}>Nudge a signal{busy ? " …" : ""}</h4>
                  {Object.entries(p.features).map(([f, cur]) => {
                    const val = ov[f] ?? cur;
                    const max = Math.max(1, Math.abs(cur) * 2);
                    return (
                      <div key={f} style={{ display: "grid", gridTemplateColumns: "minmax(96px,200px) 1fr 56px",
                                            gap: 10, alignItems: "center", fontSize: 12.5 }}>
                        <code style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f}</code>
                        <input type="range" min={0} max={max} step={max / 100} value={val}
                               onChange={(e) => setOv({ ...ov, [f]: Number(e.target.value) })}
                               onMouseUp={() => probe({ ...ov, [f]: val })}
                               onTouchEnd={() => probe({ ...ov, [f]: val })} />
                        <span style={{ color: ov[f] != null ? "var(--accent)" : "var(--text-dim)",
                                       fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
                          {val.toFixed(2)}</span>
                      </div>
                    );
                  })}
                </div>
              )}
              {p.window_ts && (
                <p style={{ margin: 0, fontSize: 11.5, color: "var(--text-dim)" }}>
                  Based on the window at {new Date(p.window_ts).toLocaleString()}. Changes here are
                  hypothetical — they don't alter stored data.
                </p>
              )}
            </>
          )}
    </div>
  );
}

/** Plain-language model-health line (UX10), from /buddy/insight. */
function InsightLine({ personId }: { personId: string }) {
  const [summary, setSummary] = useState("");
  useEffect(() => {
    fetch(`/api/buddy/insight?person=${encodeURIComponent(personId)}`).then(j)
      .then((r) => setSummary(r.summary || "")).catch(() => {});
  }, [personId]);
  if (!summary) return null;
  return (
    <p style={{ margin: 0, fontSize: 13, color: "var(--text-dim)", display: "flex",
                alignItems: "flex-start", gap: 8 }}>
      <Icon name="models" size={14} /> <span>{summary}</span>
    </p>
  );
}

/** One household member = one collapsible panel. Collapsed: a clean status row
 *  (name + live accuracy + badge + Train). Expanded: the live model's chips and
 *  every deep view as a named fold — all content preserved, progressively
 *  disclosed. */
function PersonPanel({ person, models, drift, auto, onToggleAuto, onRecompute, driftBusy,
                       onAction, onTrain, training, cadence }: {
  person: { id: string; name: string };
  models: Model[]; drift: DriftReport | null | undefined; auto: boolean;
  onToggleAuto: () => void; onRecompute: () => void; driftBusy: boolean;
  onAction: () => void; onTrain: (pid: string) => void; training: string;
  cadence?: Cadence;
}) {
  const [open, setOpen] = useState(false);
  const sorted = [...models].sort((a, b) => (b.trained_at ?? "").localeCompare(a.trained_at ?? ""));
  const live = sorted.find((m) => m.promoted) ?? sorted[0];
  const mt = live?.metrics ?? {};
  const nGold = mt.n_gold ?? 0;
  const goldReady = nGold >= GOLD_MIN && mt.accuracy_gold != null;
  const headline = goldReady ? `${pct(mt.accuracy_gold)} real-world`
    : mt.accuracy_confirmed != null ? `${pct(mt.accuracy_confirmed)} so far`
    : `gathering ${nGold}/${GOLD_MIN}`;
  const hasPerf = mt.per_class || mt.confusion || (mt.slices?.by_activity_daypart.length ?? 0) > 0;
  const hasEvidence = (mt.evidence_profile && Object.keys(mt.evidence_profile).length > 0)
    || mt.feature_importances;
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden",
                  ...(live?.promoted ? { borderColor: "color-mix(in srgb, var(--accent) 55%, var(--border))" } : {}) }}>
      <div onClick={() => setOpen(!open)}
        style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 14px",
                 cursor: "pointer", background: open ? "var(--surface-2)" : "transparent" }}>
        <span style={{ color: "var(--text-dim)", display: "inline-flex", transition: "transform .15s",
                       transform: open ? "rotate(90deg)" : "none", fontSize: 12 }}>▶</span>
        <strong style={{ fontSize: 16 }}>{person.name}</strong>
        <span style={{ fontSize: 14, fontWeight: 600,
                       color: goldReady ? "var(--accent)" : "var(--text-dim)",
                       fontVariantNumeric: "tabular-nums" }}>{headline}</span>
        <StatusBadge status={mt.validation_status} nConfirmed={mt.n_confirmed ?? 0} />
        <button className="btn btn-secondary" style={{ marginLeft: "auto" }}
                disabled={training === person.id}
                onClick={(e) => { e.stopPropagation(); onTrain(person.id); }}>
          {training === person.id ? "Training…" : "Train now"}
        </button>
      </div>
      {open && (
        <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12,
                      borderTop: "1px solid var(--border)" }}>
          {live ? <LiveChips m={live} />
                : <p style={{ margin: 0, color: "var(--text-dim)" }}>No model yet.</p>}
          <CadenceLine c={cadence} />
          <InsightLine personId={person.id} />
          {hasPerf && (
            <Fold title="Performance detail" hint="per-class · confusion · where it does well & badly">
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                {mt.per_class && <PerClassTable per={mt.per_class} />}
                {mt.confusion && <Confusion c={mt.confusion} />}
                {mt.slices && mt.slices.by_activity_daypart.length > 0 && <SliceHeatmap slices={mt.slices} />}
              </div>
            </Fold>
          )}
          {mt.calibration && (mt.calibration.reliability?.length ?? 0) > 0 && (
            <Fold title="Confidence" hint="is its confidence honest?">
              <Calibration cal={mt.calibration} />
            </Fold>
          )}
          {hasEvidence && (
            <Fold title="What it looks at" hint="evidence tiers · feature importances">
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                {mt.evidence_profile && Object.keys(mt.evidence_profile).length > 0 &&
                  <EvidenceBar profile={mt.evidence_profile} />}
                {mt.feature_importances && (
                  <div>
                    <h4 style={{ margin: "0 0 8px", fontSize: 13.5 }}>What the model looks at</h4>
                    <Importances imp={mt.feature_importances} />
                  </div>
                )}
              </div>
            </Fold>
          )}
          {live && (
            <Fold title="Model card" hint="printable summary">
              <ModelCardSheet m={live} personName={person.name} />
            </Fold>
          )}
          {live?.promoted && (
            <Fold title="What-if" hint="change a signal, watch the guess move">
              <WhatIfBody personId={person.id} />
            </Fold>
          )}
          <Fold title="Drift & health" hint="has the home changed since training?">
            <PersonDrift report={drift} auto={auto} onToggleAuto={onToggleAuto}
                         onRecompute={onRecompute} busy={driftBusy} />
          </Fold>
          <Fold title="History & versions" hint={`${models.length} version${models.length === 1 ? "" : "s"}`}>
            <HistoryBody models={sorted} onAction={onAction} />
          </Fold>
        </div>
      )}
    </div>
  );
}

type Cap = { slug: string; name: string; tier: string; reason: string;
             remedy: string | null; f1: number | null; support: number; confused_with: string | null };
type CapReport = { person_id: string | null; has_model: boolean; validation_status: string;
                   overall: string; reliable: string[]; needs_help: string[]; activities: Cap[] };

const TIER = {
  reliable: { c: "var(--ok, #34D399)", label: "reliable" },
  learning: { c: "var(--text-dim)", label: "learning" },
  unreliable: { c: "#f59e0b", label: "not working" },
  blind: { c: "var(--danger)", label: "blind" },
} as const;

/** Honest "what I can and can't do" — pairs every activity with its real quality
 *  and, when it won't work, what would actually help. */
function CapabilityPanel({ persons }: { persons: { id: string; name: string }[] }) {
  const [pid, setPid] = useState("");
  const [rep, setRep] = useState<CapReport | null>(null);
  useEffect(() => {
    const who = pid || persons[0]?.id || "";
    const q = who ? `?person=${encodeURIComponent(who)}` : "";
    fetch(`/api/capability${q}`).then(j).then(setRep).catch(() => setRep(null));
  }, [pid, persons]);
  if (!rep) return null;
  const order = ["unreliable", "blind", "learning", "reliable"];
  const acts = [...rep.activities].sort((a, b) => order.indexOf(a.tier) - order.indexOf(b.tier));
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 16,
                  display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <Icon name="check" size={16} />
        <strong style={{ fontSize: 14.5 }}>What I can and can't do</strong>
        {persons.length > 1 && (
          <select value={pid || persons[0]?.id} onChange={(e) => setPid(e.target.value)}
                  style={{ marginLeft: "auto", fontSize: 12.5 }}>
            {persons.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        )}
      </div>
      <p style={{ margin: 0, fontSize: 13.5 }}>{rep.overall}</p>
      {acts.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          {acts.map((a) => {
            const t = TIER[a.tier as keyof typeof TIER] ?? TIER.learning;
            return (
              <div key={a.slug} style={{ display: "flex", gap: 9, alignItems: "baseline" }}>
                <span style={{ width: 78, flex: "none", fontSize: 11, fontWeight: 500, color: t.c }}>{t.label}</span>
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>{a.name}</span>
                  <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}> — {a.reason}</span>
                  {a.remedy && (
                    <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginTop: 1 }}>
                      <Icon name="plus" size={12} /> {a.remedy}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
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
  const [drift, setDrift] = useState<Record<string, DriftReport>>({});
  const [auto, setAuto] = useState(false);
  const [driftBusy, setDriftBusy] = useState(false);
  const [cadence, setCadence] = useState<Record<string, Cadence>>({});
  const load = () => {
    fetch("/api/models").then(j).then(setModels).catch(() => setModels([]));
    fetch("/api/models/cadence").then(j).then(setCadence).catch(() => setCadence({}));
  };
  const loadDrift = () => fetch("/api/drift").then(j).then(setDrift).catch(() => setDrift({}));
  useEffect(() => {
    load();
    fetch("/api/persons").then(j).then(setPersons).catch(() => {});
    loadDrift();
    fetch("/api/drift/auto-retrain").then(j).then((d) => setAuto(!!d.enabled)).catch(() => {});
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
  const recompute = async () => {
    setDriftBusy(true);
    try { await fetch("/api/drift/run", { method: "POST" }).then(j); loadDrift(); } catch { /* */ }
    setDriftBusy(false);
  };
  const toggleAuto = async () => {
    const next = !auto; setAuto(next);
    try { await fetch("/api/drift/auto-retrain", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: next }) }).then(j); } catch { setAuto(!next); }
  };
  const byPerson = (models ?? []).reduce<Record<string, Model[]>>((acc, m) => {
    (acc[m.person_id] = acc[m.person_id] ?? []).push(m); return acc;
  }, {});
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 860 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="models" size={22} />
        <h2 style={{ margin: 0 }}>Models</h2>
        <button className="btn btn-ghost" style={{ marginLeft: "auto", fontSize: 13 }}
                onClick={() => window.open("/models/report", "_blank")}
                title="A printable, comprehensive report of every model and its stats">
          Download report
        </button>
      </div>
      <p style={{ margin: 0, fontSize: 14, color: "var(--text-dim)", maxWidth: 640 }}>
        One panel per person — open it for the live model and every detail.
        <strong> Real-world accuracy</strong> is measured on random spot-checks (the fair headline);
        "tricky moments" is the harder windows Hearth asked about. New models go live only when the
        promotion gate says they're not credibly worse than the current one.
      </p>
      {persons.length > 0 && <CapabilityPanel persons={persons} />}
      {trainMsg && <p style={{ margin: 0, fontSize: 13.5, color: "var(--accent)" }}>{trainMsg}</p>}
      {models === null && <p style={{ color: "var(--text-dim)" }}>Loading…</p>}
      {models !== null && Object.keys(byPerson).length === 0 && (
        <p style={{ color: "var(--text-dim)" }}>No models yet — they appear after the first training run.</p>
      )}
      {Object.entries(byPerson).map(([pid, list]) => {
        const name = persons.find((p) => p.id === pid)?.name ?? pid;
        return (
          <PersonPanel key={pid} person={{ id: pid, name }} models={list}
                       drift={drift[pid]} auto={auto} onToggleAuto={toggleAuto}
                       onRecompute={recompute} driftBusy={driftBusy} cadence={cadence[pid]}
                       onAction={() => { load(); loadDrift(); }} onTrain={train} training={training} />
        );
      })}
    </section>
  );
}

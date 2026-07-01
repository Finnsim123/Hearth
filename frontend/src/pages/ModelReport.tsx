/**
 * ModelReport — the downloadable, print-first "everything" document.
 *
 * The Models page is a live glass box; its old "Save PDF" just window.print()'d
 * the whole SPA, so the export looked like a screenshot (nav, collapsed folds).
 * This is a STANDALONE route (no app chrome) that gathers every stat the
 * registry holds and lays it out as a proper paginated report: a household
 * summary, then one comprehensive section per person — headline accuracy, the
 * honest capability verdict, per-class performance, confusion, calibration,
 * evidence tiers, drift/health, and version history.
 *
 * Reached at /models/report (App.tsx short-circuits the shell for it). Add
 * ?print=1 to auto-open the print dialog, ?person=<id> to jump to one member.
 * Colours are hard-coded for paper (white bg, dark ink) — theme-independent.
 */
import { useEffect, useMemo, useState } from "react";

// ── palette (paper, not theme) ───────────────────────────────────────────────
const INK = "#1c1c26";
const DIM = "#5c6270";
const FAINT = "#8a90a0";
const LINE = "#e4e5ec";
const ACCENT = "#d9682e";        // Hearth ember
const OK = "#1f9d63";
const WARN = "#c8791b";
const DANGER = "#d0402e";

// ── shared shapes (kept minimal + local to the report) ───────────────────────
type PerClass = { precision: number; recall: number; f1: number; support: number };
type Metrics = {
  accuracy_gold?: number | null; accuracy_gold_ci?: [number, number]; n_gold?: number;
  accuracy_confirmed?: number | null; accuracy_confirmed_ci?: [number, number];
  accuracy_bootstrap?: number | null; auc_macro?: number; n_confirmed?: number;
  n_train?: number; feature_count?: number;
  per_class?: Record<string, PerClass>;
  confusion?: { labels: string[]; matrix: number[][] };
  feature_importances?: Record<string, number>;
  evidence_profile?: Record<string, number>;
  validation_status?: "validated" | "provisional";
  calibration?: { brier: number; ece: number; n_check: number;
                  reliability?: { conf: number; acc: number; n: number }[] };
  flat_baseline?: { accuracy_gold?: number; accuracy_confirmed?: number };
  excluded_features?: string[];
};
type Model = {
  id: number; person_id: string; version: string; algo: string;
  trained_at: string | null; promoted: boolean;
  label_counts: Record<string, number>; metrics: Metrics;
};
type Person = { id: string; name: string };
type Cap = { slug: string; name: string; tier: string; reason: string;
             remedy: string | null; f1: number | null; support: number; confused_with: string | null };
type CapReport = { overall: string; reliable: string[]; needs_help: string[]; activities: Cap[] };
type DriftReport = { drifted?: string[]; severe?: boolean; computed_at?: string | null;
                     model_version?: string; psi?: Record<string, number> };

const GOLD_MIN = 30;
const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };
const pct = (v: number | null | undefined, dp = 1) =>
  (v === null || v === undefined) ? "—" : `${(v * 100).toFixed(dp)}%`;
const fmtDate = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";

// ── tiny presentational atoms ────────────────────────────────────────────────
function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div style={{ border: `1px solid ${LINE}`, borderRadius: 8, padding: "9px 12px",
                  minWidth: 96, breakInside: "avoid" }}>
      <div style={{ fontSize: 19, fontWeight: 700, color: INK, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      <div style={{ fontSize: 10.5, color: DIM, marginTop: 1 }}>{label}</div>
      {sub && <div style={{ fontSize: 10, color: FAINT, fontVariantNumeric: "tabular-nums" }}>{sub}</div>}
    </div>
  );
}
function H({ children }: { children: React.ReactNode }) {
  return <h3 style={{ margin: "0 0 8px", fontSize: 13, color: INK, letterSpacing: "-0.01em",
                      textTransform: "uppercase", fontWeight: 700 }}>{children}</h3>;
}
function Section({ children }: { children: React.ReactNode }) {
  return <div style={{ breakInside: "avoid", marginTop: 16 }}>{children}</div>;
}
function StatusPill({ status, nConfirmed }: { status?: string; nConfirmed: number }) {
  if (status !== "provisional" && status !== "validated") return null;
  const prov = status === "provisional";
  return (
    <span style={{ fontSize: 10.5, padding: "2px 9px", borderRadius: 99, fontWeight: 700,
                   color: prov ? WARN : OK, background: prov ? "#fbf1e2" : "#e7f6ee",
                   border: `1px solid ${prov ? "#f0dcc0" : "#c9ecd8"}` }}>
      {prov ? `Provisional · ${nConfirmed}/30 confirmed` : "Validated"}
    </span>
  );
}

const TIER: Record<string, { c: string; label: string }> = {
  reliable: { c: OK, label: "reliable" }, learning: { c: DIM, label: "learning" },
  unreliable: { c: WARN, label: "not working" }, blind: { c: DANGER, label: "blind" },
};

// top off-diagonal confusions: "actual → predicted (n)"
function topConfusions(c: { labels: string[]; matrix: number[][] }, k = 6) {
  const out: { a: string; b: string; n: number }[] = [];
  c.matrix.forEach((row, i) => row.forEach((n, jx) => {
    if (i !== jx && n > 0) out.push({ a: c.labels[i], b: c.labels[jx], n });
  }));
  return out.sort((x, y) => y.n - x.n).slice(0, k);
}

// ── per-person section ───────────────────────────────────────────────────────
function PersonReport({ person, models, cap, insight, drift, first }: {
  person: Person; models: Model[]; cap: CapReport | null; insight: string;
  drift: DriftReport | null; first: boolean;
}) {
  const sorted = [...models].sort((a, b) => (b.trained_at ?? "").localeCompare(a.trained_at ?? ""));
  const live = sorted.find((m) => m.promoted) ?? sorted[0];
  const mt = live?.metrics ?? {};
  const nGold = mt.n_gold ?? 0;
  const goldReady = nGold >= GOLD_MIN && mt.accuracy_gold != null;
  const ci = mt.accuracy_gold_ci;
  const mine = mt.accuracy_gold ?? mt.accuracy_confirmed;
  const flat = mt.flat_baseline?.accuracy_gold ?? mt.flat_baseline?.accuracy_confirmed;
  const perClass = mt.per_class ? Object.entries(mt.per_class).sort((a, b) => b[1].support - a[1].support) : [];
  const importances = mt.feature_importances
    ? Object.entries(mt.feature_importances).sort((a, b) => b[1] - a[1]).slice(0, 10) : [];
  const impMax = importances.length ? importances[0][1] : 1;
  const ev = mt.evidence_profile ?? {};
  const evOrder = ["direct", "behavioral", "ambient", "prior"];
  const acts = cap ? [...cap.activities].sort((a, b) =>
    ["unreliable", "blind", "learning", "reliable"].indexOf(a.tier)
    - ["unreliable", "blind", "learning", "reliable"].indexOf(b.tier)) : [];

  return (
    <section style={{ breakBefore: first ? "auto" : "page", paddingTop: first ? 0 : 4 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap",
                    borderBottom: `2px solid ${INK}`, paddingBottom: 6 }}>
        <h2 style={{ margin: 0, fontSize: 22, color: INK }}>{person.name}</h2>
        <StatusPill status={mt.validation_status} nConfirmed={mt.n_confirmed ?? 0} />
        <span style={{ marginLeft: "auto", fontSize: 11, color: DIM }}>
          {live ? `${live.version} · ${live.algo} · trained ${fmtDate(live.trained_at)}` : "no model yet"}
        </span>
      </div>

      {!live ? (
        <p style={{ color: DIM, fontSize: 13 }}>No model has been trained for this person yet.</p>
      ) : (
        <>
          {/* headline metrics */}
          <Section>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Stat label={goldReady ? "real-world accuracy" : "gathering spot-checks"}
                    value={goldReady ? pct(mt.accuracy_gold) : `${nGold}/${GOLD_MIN}`}
                    sub={goldReady && ci ? `95% CI ${pct(ci[0])}–${pct(ci[1])}` : undefined} />
              <Stat label="on tricky moments" value={pct(mt.accuracy_confirmed)} />
              <Stat label="bootstrap agr" value={pct(mt.accuracy_bootstrap)} />
              <Stat label="AUC (macro)" value={mt.auc_macro ? mt.auc_macro.toFixed(3) : "—"} />
              <Stat label="calibration (ECE)" value={mt.calibration ? mt.calibration.ece.toFixed(3) : "—"} />
              <Stat label="train windows" value={String(mt.n_train ?? "—")} />
              <Stat label="features" value={String(mt.feature_count ?? "—")} />
              <Stat label="human labels" value={String(live.label_counts?.confirmed ?? 0)} />
            </div>
          </Section>

          {insight && (
            <p style={{ marginTop: 12, marginBottom: 0, fontSize: 12.5, color: DIM,
                        lineHeight: 1.5, borderLeft: `3px solid ${ACCENT}`, paddingLeft: 10 }}>
              {insight}
            </p>
          )}

          {mine != null && flat != null && (
            <p style={{ marginTop: 10, marginBottom: 0, fontSize: 12, color: DIM }}>
              <b style={{ color: INK }}>Earns its complexity?</b> {pct(mine)} here vs a plain flat
              model's {pct(flat)} on the same split — {mine >= flat
                ? "the hierarchy is pulling its weight." : "the flat model is as good; the hierarchy isn't adding much."}
            </p>
          )}

          {/* capability */}
          {cap && (
            <Section>
              <H>What it can and can't do</H>
              <p style={{ margin: "0 0 8px", fontSize: 12.5, color: INK }}>{cap.overall}</p>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {acts.map((a) => {
                  const t = TIER[a.tier] ?? TIER.learning;
                  return (
                    <div key={a.slug} style={{ display: "flex", gap: 8, alignItems: "baseline",
                                               fontSize: 12, breakInside: "avoid" }}>
                      <span style={{ width: 74, flex: "none", fontSize: 10, fontWeight: 700,
                                     color: t.c, textTransform: "uppercase" }}>{t.label}</span>
                      <div style={{ flex: 1 }}>
                        <b style={{ color: INK }}>{a.name}</b>
                        <span style={{ color: DIM }}> — {a.reason}</span>
                        {a.remedy && <span style={{ color: ACCENT }}> &nbsp;+ {a.remedy}</span>}
                      </div>
                      {a.f1 != null && <span style={{ color: FAINT, fontVariantNumeric: "tabular-nums" }}>F1 {a.f1.toFixed(2)}</span>}
                    </div>
                  );
                })}
              </div>
            </Section>
          )}

          {/* per-class table */}
          {perClass.length > 0 && (
            <Section>
              <H>Per-activity performance</H>
              <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11.5 }}>
                <thead>
                  <tr>
                    {["Activity", "Precision", "Recall", "F1", "Support"].map((h, i) => (
                      <th key={h} style={{ textAlign: i === 0 ? "left" : "right", padding: "4px 6px",
                                           borderBottom: `1px solid ${LINE}`, color: DIM, fontWeight: 600 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {perClass.map(([name, p]) => (
                    <tr key={name}>
                      <td style={{ padding: "4px 6px", borderBottom: `1px solid ${LINE}`, color: INK }}>{name}</td>
                      {[p.precision, p.recall, p.f1].map((v, i) => (
                        <td key={i} style={{ padding: "4px 6px", textAlign: "right", borderBottom: `1px solid ${LINE}`,
                                             fontVariantNumeric: "tabular-nums",
                                             color: v >= 0.7 ? OK : v >= 0.5 ? INK : DANGER }}>{pct(v, 0)}</td>
                      ))}
                      <td style={{ padding: "4px 6px", textAlign: "right", borderBottom: `1px solid ${LINE}`,
                                   color: DIM, fontVariantNumeric: "tabular-nums" }}>{p.support}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Section>
          )}

          {/* confusion highlights */}
          {mt.confusion && topConfusions(mt.confusion).length > 0 && (
            <Section>
              <H>Most common mix-ups</H>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", fontSize: 12 }}>
                {topConfusions(mt.confusion).map((c, i) => (
                  <span key={i} style={{ color: DIM }}>
                    <b style={{ color: INK }}>{c.a}</b> → {c.b} <span style={{ color: FAINT }}>×{c.n}</span>
                  </span>
                ))}
              </div>
            </Section>
          )}

          {/* calibration */}
          {mt.calibration && (mt.calibration.reliability?.length ?? 0) > 0 && (
            <Section>
              <H>Is its confidence honest?</H>
              <p style={{ margin: "0 0 6px", fontSize: 11.5, color: DIM }}>
                ECE {mt.calibration.ece.toFixed(3)} · Brier {mt.calibration.brier.toFixed(3)}
                {" "}(0 = stated confidence perfectly matches real accuracy). Bins:
              </p>
              <table style={{ borderCollapse: "collapse", fontSize: 11, minWidth: 320 }}>
                <thead><tr>{["stated confidence", "actual accuracy", "n"].map((h, i) => (
                  <th key={h} style={{ textAlign: i === 0 ? "left" : "right", padding: "3px 8px",
                                       color: DIM, fontWeight: 600, borderBottom: `1px solid ${LINE}` }}>{h}</th>))}
                </tr></thead>
                <tbody>
                  {mt.calibration.reliability!.map((b, i) => (
                    <tr key={i}>
                      <td style={{ padding: "3px 8px", color: INK, fontVariantNumeric: "tabular-nums" }}>~{pct(b.conf, 0)}</td>
                      <td style={{ padding: "3px 8px", textAlign: "right", fontVariantNumeric: "tabular-nums",
                                   color: Math.abs(b.acc - b.conf) <= 0.1 ? OK : WARN }}>{pct(b.acc, 0)}</td>
                      <td style={{ padding: "3px 8px", textAlign: "right", color: FAINT }}>{b.n}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Section>
          )}

          {/* evidence tiers + importances */}
          {(Object.keys(ev).length > 0 || importances.length > 0) && (
            <Section>
              <H>What it looks at</H>
              {Object.keys(ev).length > 0 && (
                <div style={{ fontSize: 12, color: DIM, marginBottom: importances.length ? 10 : 0 }}>
                  {evOrder.filter((k) => ev[k] != null).map((k, i) => (
                    <span key={k}>{i > 0 ? " · " : ""}<b style={{ color: INK, textTransform: "capitalize" }}>{k}</b> {pct(ev[k], 0)}</span>
                  ))}
                  <span style={{ color: FAINT }}> — how much of the decision rests on direct human signals vs ambient drift.</span>
                </div>
              )}
              {importances.map(([f, v]) => (
                <div key={f} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 90px",
                                      gap: 8, alignItems: "center", fontSize: 11, marginBottom: 3 }}>
                  <div style={{ position: "relative", background: "#f2f2f6", borderRadius: 3, height: 13 }}>
                    <div style={{ position: "absolute", inset: 0, width: `${(v / impMax) * 100}%`,
                                  background: "#f0d8c6", borderRadius: 3 }} />
                    <code style={{ position: "relative", paddingLeft: 6, fontSize: 10, color: INK,
                                   lineHeight: "13px", whiteSpace: "nowrap" }}>{f}</code>
                  </div>
                  <span style={{ textAlign: "right", color: DIM, fontVariantNumeric: "tabular-nums" }}>{(v * 100).toFixed(1)}%</span>
                </div>
              ))}
            </Section>
          )}

          {/* drift & health */}
          {drift && (
            <Section>
              <H>Drift &amp; health</H>
              <p style={{ margin: 0, fontSize: 12, color: DIM }}>
                <b style={{ color: (drift.drifted?.length ?? 0) === 0 ? OK : drift.severe ? DANGER : WARN }}>
                  {(drift.drifted?.length ?? 0) === 0 ? "Stable" : drift.severe
                    ? `Severe drift — ${drift.drifted!.length} signals` : `${drift.drifted!.length} signals drifted`}
                </b>
                {drift.computed_at ? ` · checked ${fmtDate(drift.computed_at)}` : ""}
                {drift.model_version ? ` · vs ${drift.model_version}` : ""}
                {(drift.drifted?.length ?? 0) > 0 && (
                  <> — e.g. {drift.drifted!.slice(0, 5).join(", ")}
                    {drift.drifted!.length > 5 ? `, +${drift.drifted!.length - 5} more` : ""}.</>
                )}
              </p>
            </Section>
          )}

          {/* history */}
          {sorted.length > 1 && (
            <Section>
              <H>Version history</H>
              <div style={{ fontSize: 11.5, color: DIM }}>
                {sorted.length} versions. Recent:{" "}
                {sorted.slice(0, 6).map((m, i) => (
                  <span key={m.id}>
                    {i > 0 ? " · " : ""}
                    <b style={{ color: m.promoted ? ACCENT : INK }}>{m.version}</b>
                    {" "}{m.metrics?.accuracy_gold != null ? pct(m.metrics.accuracy_gold, 0)
                          : m.metrics?.accuracy_confirmed != null ? pct(m.metrics.accuracy_confirmed, 0) : "—"}
                    {m.promoted ? " (live)" : ""}
                  </span>
                ))}
              </div>
            </Section>
          )}

          {mt.excluded_features && mt.excluded_features.length > 0 && (
            <p style={{ marginTop: 12, marginBottom: 0, fontSize: 10.5, color: FAINT }}>
              <b>Excluded features:</b> {mt.excluded_features.slice(0, 12).join(", ")}
              {mt.excluded_features.length > 12 ? ` +${mt.excluded_features.length - 12} more` : ""} —
              dropped as low-variance or another member's personal sensors.
            </p>
          )}
        </>
      )}
    </section>
  );
}

// ── the document ─────────────────────────────────────────────────────────────
export default function ModelReport() {
  const [models, setModels] = useState<Model[] | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [caps, setCaps] = useState<Record<string, CapReport>>({});
  const [insights, setInsights] = useState<Record<string, string>>({});
  const [drift, setDrift] = useState<Record<string, DriftReport>>({});
  const params = new URLSearchParams(window.location.search);
  const onlyPerson = params.get("person");
  const autoPrint = params.get("print") === "1";

  useEffect(() => { document.title = "Hearth — Model Report"; }, []);

  useEffect(() => {
    (async () => {
      const [ms, ps, dr] = await Promise.all([
        fetch("/api/models").then(j).catch(() => []),
        fetch("/api/persons").then(j).catch(() => []),
        fetch("/api/drift").then(j).catch(() => ({})),
      ]);
      setModels(ms); setPersons(ps); setDrift(dr || {});
      const people: Person[] = ps || [];
      const capEntries = await Promise.all(people.map(async (p) => {
        const c = await fetch(`/api/capability?person=${encodeURIComponent(p.id)}`).then(j).catch(() => null);
        return [p.id, c] as const;
      }));
      const insEntries = await Promise.all(people.map(async (p) => {
        const r = await fetch(`/api/buddy/insight?person=${encodeURIComponent(p.id)}`).then(j).catch(() => null);
        return [p.id, r?.summary || ""] as const;
      }));
      setCaps(Object.fromEntries(capEntries.filter(([, c]) => c)));
      setInsights(Object.fromEntries(insEntries));
    })();
  }, []);

  const byPerson = useMemo(() => (models ?? []).reduce<Record<string, Model[]>>((acc, m) => {
    (acc[m.person_id] = acc[m.person_id] ?? []).push(m); return acc;
  }, {}), [models]);

  const ready = models !== null;
  useEffect(() => {
    if (ready && autoPrint) { const t = setTimeout(() => window.print(), 600); return () => clearTimeout(t); }
  }, [ready, autoPrint]);

  const shown = persons.filter((p) => (!onlyPerson || p.id === onlyPerson) && byPerson[p.id]);
  const generated = new Date().toLocaleString(undefined, { dateStyle: "long", timeStyle: "short" });

  return (
    <div style={{ background: "#f3f3f6", minHeight: "100vh", color: INK }}>
      <style>{`
        @media print {
          @page { margin: 15mm; }
          .no-print { display: none !important; }
          .sheet { box-shadow: none !important; margin: 0 !important; width: auto !important; }
          html, body { background: #fff !important; }
        }
        .report a { color: ${ACCENT}; }
        .report table { page-break-inside: auto; }
        .report tr { page-break-inside: avoid; }
      `}</style>

      <div className="no-print" style={{ position: "sticky", top: 0, zIndex: 5,
        display: "flex", alignItems: "center", gap: 12, padding: "10px 16px",
        background: "#fff", borderBottom: `1px solid ${LINE}` }}>
        <strong style={{ color: INK }}>Model Report</strong>
        <span style={{ fontSize: 12.5, color: DIM }}>Save as PDF to keep or share.</span>
        <button onClick={() => window.print()} style={{ marginLeft: "auto", cursor: "pointer",
          background: ACCENT, color: "#fff", border: "none", borderRadius: 8, padding: "7px 16px",
          fontSize: 13, fontWeight: 600 }}>Print / Save PDF</button>
        <button onClick={() => window.close()} style={{ cursor: "pointer", background: "transparent",
          color: DIM, border: `1px solid ${LINE}`, borderRadius: 8, padding: "7px 12px", fontSize: 13 }}>Close</button>
      </div>

      <div className="report sheet" style={{ maxWidth: 820, margin: "18px auto", background: "#fff",
        padding: "36px 40px 48px", boxShadow: "0 1px 8px rgba(0,0,0,0.08)", borderRadius: 4,
        fontFamily: "Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif" }}>

        {/* masthead */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, borderBottom: `2px solid ${ACCENT}`,
                      paddingBottom: 14 }}>
          <svg width="30" height="30" viewBox="0 0 32 32" fill="none" aria-hidden>
            <path d="M16 4 L28 14 V26 a2 2 0 0 1 -2 2 H6 a2 2 0 0 1 -2 -2 V14 Z"
                  stroke={INK} strokeWidth="2.5" strokeLinejoin="round" />
            <circle cx="16" cy="20" r="3.5" fill={ACCENT} />
          </svg>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.02em", color: INK }}>
              Hearth — Model Report
            </div>
            <div style={{ fontSize: 12, color: DIM }}>
              Per-person activity models · generated {generated}
            </div>
          </div>
        </div>

        <p style={{ fontSize: 12.5, color: DIM, lineHeight: 1.55, marginTop: 14 }}>
          Every model is trained only on this home's own sensors. <b style={{ color: INK }}>Real-world
          accuracy</b> is measured on random spot-checks — the fair, unbiased headline; <b style={{ color: INK }}>tricky
          moments</b> is accuracy on the harder windows Hearth deliberately asked about, so it reads lower
          by design. A new model only goes live when the promotion gate says it isn't credibly worse than
          the one it replaces.
        </p>

        {!ready && <p style={{ color: DIM }}>Loading the registry…</p>}
        {ready && shown.length === 0 && (
          <p style={{ color: DIM }}>No trained models yet — this report fills in after the first training run.</p>
        )}

        {/* household summary */}
        {shown.length > 1 && (
          <Section>
            <H>Household summary</H>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11.5 }}>
              <thead><tr>
                {["Person", "Real-world", "Tricky", "AUC", "ECE", "Train", "Feat.", "Status"].map((h, i) => (
                  <th key={h} style={{ textAlign: i === 0 ? "left" : "right", padding: "4px 6px",
                                       borderBottom: `1px solid ${INK}`, color: DIM, fontWeight: 600 }}>{h}</th>))}
              </tr></thead>
              <tbody>
                {shown.map((p) => {
                  const list = byPerson[p.id];
                  const live = [...list].sort((a, b) => (b.trained_at ?? "").localeCompare(a.trained_at ?? ""))
                    .find((m) => m.promoted) ?? list[0];
                  const m = live?.metrics ?? {};
                  const gold = (m.n_gold ?? 0) >= GOLD_MIN ? pct(m.accuracy_gold) : `${m.n_gold ?? 0}/${GOLD_MIN}`;
                  return (
                    <tr key={p.id}>
                      <td style={{ padding: "4px 6px", borderBottom: `1px solid ${LINE}`, color: INK, fontWeight: 600 }}>{p.name}</td>
                      <td style={cell}>{gold}</td>
                      <td style={cell}>{pct(m.accuracy_confirmed)}</td>
                      <td style={cell}>{m.auc_macro ? m.auc_macro.toFixed(2) : "—"}</td>
                      <td style={cell}>{m.calibration ? m.calibration.ece.toFixed(2) : "—"}</td>
                      <td style={cell}>{m.n_train ?? "—"}</td>
                      <td style={cell}>{m.feature_count ?? "—"}</td>
                      <td style={{ ...cell, color: m.validation_status === "validated" ? OK : WARN }}>
                        {m.validation_status === "validated" ? "Validated" : "Provisional"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Section>
        )}

        {/* per-person */}
        {shown.map((p, i) => (
          <PersonReport key={p.id} person={p} models={byPerson[p.id]} cap={caps[p.id] ?? null}
                        insight={insights[p.id] ?? ""} drift={drift[p.id] ?? null} first={i === 0} />
        ))}

        {ready && shown.length > 0 && (
          <p style={{ marginTop: 26, paddingTop: 12, borderTop: `1px solid ${LINE}`,
                      fontSize: 10.5, color: FAINT, lineHeight: 1.5 }}>
            <b>Intended use:</b> recognise each household member's everyday activities from their own
            sensors, for automations and insight. <b>Limits:</b> trained only on this home; accuracy is
            honest only once enough spot-checks back it; rare activities have few examples; predictions
            below the confidence threshold abstain rather than guess. Generated by Hearth · {generated}.
          </p>
        )}
      </div>
    </div>
  );
}

const cell: React.CSSProperties = {
  padding: "4px 6px", textAlign: "right", borderBottom: `1px solid ${LINE}`,
  color: INK, fontVariantNumeric: "tabular-nums",
};

/**
 * Behaviour — habits & routines over the published prediction timeline.
 * Descriptive, not judgmental; honest about KNOWN (facts) vs INFERRED (model).
 * v1: today ribbon · time budget (stacked bars + totals) · sleep/away facts.
 * Source: GET /api/behaviour?person=&days=.  Dependency-free (CSS bars).
 */
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import Card from "../components/Card";

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };

type Day = { date: string; totals: Record<string, number>; unknown_min: number;
             fact_min: number; inferred_min: number };
type Seg = { start: string; end: string; activity: string; basis: string };
type Cell = { dow: number; hour: number; totals: Record<string, number> };
type Trans = { src: string; dst: string; count: number; prob: number };
type Sess = { activity: string; count: number; mean_min: number; median_min: number;
              longest_min: number; last_ts: string | null; last_basis: string | null };
type Consistency = { nights: number; wake_avg_min: number | null; wake_spread_min: number | null;
                     wake_band: string | null; bed_avg_min: number | null;
                     bed_spread_min: number | null; bed_band: string | null };
type Trend = { activity: string; recent_avg_min: number; prior_avg_min: number;
               delta_min: number; pct: number; direction: string; basis: string };
type BodyDay = { date: string; totals: Record<string, number>; covered_min: number;
                active_min: number; sedentary_min: number };
type Body = { person_id: string; signals: string[]; primary: string | null;
              units: Record<string, string>; total: Record<string, number>;
              coverage: number; worn_min: number; charging_min: number; absent_min: number;
              active_min: number; sedentary_min: number;
              per_day: BodyDay[]; rhythm: Cell[]; by_activity: Record<string, number>;
              trends: Trend[] };
type Summary = {
  person_id: string; start: string; end: string; window_min: number;
  totals: Record<string, number>; total_min: number; classified_min: number;
  coverage: number; fact_min: number; inferred_min: number; known_fraction: number;
  per_day: Day[]; today: Seg[];
  sleep_per_day_min: Record<string, number>; away_per_day_min: Record<string, number>;
  rhythm: Cell[]; sequences: Trans[];
  sessions: Sess[]; consistency: Consistency;
};
type MarkerFlag = { time: string; name: string; to: string };
type Data = { summary: Summary | null; trends: Trend[]; body: Body | null;
              marker_flags?: MarkerFlag[];
              persons: { id: string; name: string }[];
              activities: { slug: string; name: string; color: string }[] };

type Cooc = { a: string; b: string; minutes: number; frac: number };
type HhPair = { other_id: string; other_name: string; items: Cooc[] };
type Household = { enabled: boolean; shared: string[]; self_shared: boolean;
                  self_name?: string; pairs: HhPair[] };

const UNKNOWN_COLOR = "var(--surface-2, #2a2f3a)";
const fmtH = (min: number) => {
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return h ? `${h}h${m ? ` ${m}m` : ""}` : `${m}m`;
};
const fmtClock = (minOfDay: number | null) => {
  if (minOfDay == null) return "—";
  const h = Math.floor(minOfDay / 60) % 24, m = Math.round(minOfDay % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
};
const dayLabel = (iso: string) =>
  new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric" });

export default function Behaviour() {
  const [data, setData] = useState<Data | null>(null);
  const [person, setPerson] = useState<string>("");
  const [days, setDays] = useState(7);
  const [digest, setDigest] = useState<boolean | null>(null);

  useEffect(() => {
    fetch("/api/behaviour/digest").then(j).then((d) => setDigest(!!d.enabled)).catch(() => {});
  }, []);
  const toggleDigest = () => {
    const next = !digest;
    setDigest(next);
    fetch("/api/behaviour/digest", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: next }) }).catch(() => setDigest(!next));
  };

  const [household, setHousehold] = useState<Household | null>(null);
  const [why, setWhy] = useState<{ ts: string; activity: string; basis: string } | null>(null);
  const [unreliable, setUnreliable] = useState<Set<string>>(new Set());

  const load = useCallback(() => {
    const q = new URLSearchParams({ days: String(days) });
    if (person) q.set("person", person);
    // Personal summary is the critical path; the household co-occurrence view
    // does one heavy prediction read PER member, so defer it until after the
    // page has painted so it doesn't compete for InfluxDB connections.
    fetch(`/api/behaviour?${q}`).then(j).then((d) => {
      setData(d);
      fetch(`/api/behaviour/household?${q}`).then(j).then(setHousehold).catch(() => setHousehold(null));
    }).catch(() => setData({ summary: null, trends: [], body: null, persons: [], activities: [] }));
    // capability: which activities the model can't do reliably → dim them, so the
    // ribbon never presents an unreliable guess as if it were solid.
    fetch(`/api/capability${person ? `?person=${encodeURIComponent(person)}` : ""}`).then(j)
      .then((c: { activities?: { slug: string; tier: string }[] }) =>
        setUnreliable(new Set((c.activities ?? [])
          .filter((a) => a.tier === "unreliable" || a.tier === "blind").map((a) => a.slug))))
      .catch(() => setUnreliable(new Set()));
  }, [person, days]);
  useEffect(load, [load]);

  const pid = person || data?.persons?.[0]?.id || "";
  const toggleShare = () => {
    if (!pid || !household) return;
    const next = !household.self_shared;
    setHousehold({ ...household, self_shared: next });
    fetch("/api/behaviour/share", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person: pid, enabled: next }) }).then(() => load()).catch(() => {});
  };

  const colorOf = useMemo(() => {
    const m = new Map((data?.activities ?? []).map((a) => [a.slug, a.color]));
    return (slug: string) => (slug === "unknown" ? UNKNOWN_COLOR : (m.get(slug) ?? "var(--accent)"));
  }, [data]);
  const nameOf = useMemo(() => {
    const m = new Map((data?.activities ?? []).map((a) => [a.slug, a.name]));
    return (slug: string) => m.get(slug) ?? slug;
  }, [data]);

  if (!data) return <p style={{ color: "var(--text-dim)" }}>Loading…</p>;
  const s = data.summary;
  const sortedTotals = s ? Object.entries(s.totals).sort((a, b) => b[1] - a[1]) : [];
  const maxTotal = sortedTotals.length ? sortedTotals[0][1] : 1;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 18, maxWidth: 820 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>Behaviour</h2>
        <span style={{ flex: 1 }} />
        {data.persons.length > 1 && (
          <select value={person || data.persons[0]?.id} onChange={(e) => setPerson(e.target.value)}>
            {data.persons.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        )}
        <div style={{ display: "inline-flex", border: "1px solid var(--border)", borderRadius: 999, overflow: "hidden" }}>
          {[7, 30].map((d) => (
            <button key={d} onClick={() => setDays(d)}
              style={{ border: "none", cursor: "pointer", padding: "6px 12px", fontSize: 13,
                       background: days === d ? "var(--accent)" : "transparent",
                       color: days === d ? "#fff" : "var(--text-dim)" }}>
              {d}d
            </button>
          ))}
        </div>
        {digest !== null && (
          <label title="Get a friendly weekly recap via your Home Assistant notifications (opt-in)."
            style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5,
                     color: "var(--text-dim)", cursor: "pointer" }}>
            <input type="checkbox" checked={digest} onChange={toggleDigest} />
            Weekly digest
          </label>
        )}
      </div>

      {!s || s.total_min === 0 ? (
        <Card title="No activity yet">
          <p style={{ color: "var(--text-dim)", margin: 0 }}>
            Once Hearth has been predicting for a while, your day-to-day patterns show up here.
          </p>
        </Card>
      ) : (
        <>
          {/* honesty / coverage */}
          <Card title="How to read this"
            sub="Built from what Hearth observed. Solid = known for sure (you were away or asleep); lighter = the model's best guess.">
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13 }}>
              <span><strong>{Math.round(s.coverage * 100)}%</strong> of the time classified</span>
              <span style={{ color: "var(--text-dim)" }}>
                <strong style={{ color: "var(--text)" }}>{Math.round(s.known_fraction * 100)}%</strong> known
                · {Math.round((1 - s.known_fraction) * 100)}% inferred
              </span>
              <span style={{ color: "var(--text-dim)" }}>{fmtH(s.total_min - s.classified_min)} unclassified</span>
            </div>
          </Card>

          {/* what changed — week over week */}
          {data.trends.length > 0 && (
            <Card title="What changed" sub="This week vs the week before — only notable shifts are shown.">
              <TrendsCard trends={data.trends} colorOf={colorOf} nameOf={nameOf} />
            </Card>
          )}

          {/* today ribbon */}
          {s.today.length > 0 && (
            <Card title="Today">
              <div style={{ display: "flex", height: 26, borderRadius: 8, overflow: "hidden",
                            border: "1px solid var(--border)" }}>
                {s.today.map((seg, i) => {
                  const mins = (new Date(seg.end).getTime() - new Date(seg.start).getTime()) / 60000;
                  return (
                    <div key={i} title={`${nameOf(seg.activity)} · ${fmtH(mins)} — click for why`}
                      onClick={() => setWhy({ ts: seg.start, activity: seg.activity, basis: seg.basis })}
                      style={{ flex: mins, background: colorOf(seg.activity), cursor: "pointer",
                               opacity: seg.basis === "fact" ? 1 : seg.activity === "unknown" ? 0.5
                                 : unreliable.has(seg.activity) ? 0.3 : 0.62 }} />
                  );
                })}
              </div>
              {(data.marker_flags?.length ?? 0) > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 8 }}>
                  {data.marker_flags!.map((f, i) => (
                    <span key={i} style={{ fontSize: 12, color: "var(--text-dim)",
                      display: "inline-flex", alignItems: "center", gap: 5 }}>
                      <span style={{ width: 0, height: 0, borderLeft: "4px solid transparent",
                        borderRight: "4px solid transparent", borderBottom: `7px solid ${colorOf(f.to)}` }} />
                      {f.name} {new Date(f.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </span>
                  ))}
                </div>
              )}
            </Card>
          )}

          {/* time budget — totals */}
          <Card title={`Where the time went · ${days} days`}>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {sortedTotals.map(([slug, min]) => (
                <div key={slug} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ width: 110, fontSize: 13, textAlign: "right",
                                 color: "var(--text-dim)" }}>{nameOf(slug)}</span>
                  <div style={{ flex: 1, height: 16, background: "var(--surface-2)", borderRadius: 6, overflow: "hidden" }}>
                    <div style={{ width: `${(min / maxTotal) * 100}%`, height: "100%",
                                  background: colorOf(slug) }} />
                  </div>
                  <span style={{ width: 70, fontSize: 13 }}>{fmtH(min)}</span>
                </div>
              ))}
            </div>
          </Card>

          {/* per-day stacked composition */}
          <Card title="Day by day" sub="Each bar is a day, scaled to 24h; gaps are unclassified time.">
            <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
              {s.per_day.map((d) => (
                <div key={d.date} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ width: 64, fontSize: 12.5, color: "var(--text-dim)" }}>{dayLabel(d.date)}</span>
                  <div style={{ flex: 1, display: "flex", height: 18, borderRadius: 5, overflow: "hidden",
                                background: "var(--surface-2)" }}>
                    {Object.entries(d.totals).sort((a, b) => b[1] - a[1]).map(([slug, min]) => (
                      <div key={slug} title={`${nameOf(slug)} · ${fmtH(min)}`}
                        style={{ width: `${(min / 1440) * 100}%`, background: colorOf(slug),
                                 opacity: slug === "away" || slug === "asleep" ? 1 : 0.62 }} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </Card>

          {/* daily rhythm — when things happen */}
          {s.rhythm.length > 0 && (
            <Card title="Daily rhythm" sub="When things usually happen. Each cell is an hour of the week; darker = more often.">
              <RhythmHeatmap cells={s.rhythm} colorOf={colorOf} nameOf={nameOf}
                             activities={sortedTotals.map(([slug]) => slug)} />
            </Card>
          )}

          {/* sequences — what follows what */}
          {s.sequences.length > 0 && (
            <Card title="What follows what" sub="Observed transitions between activities — your home's narrative flow.">
              <Sequences seq={s.sequences} colorOf={colorOf} nameOf={nameOf} />
            </Card>
          )}

          {/* body activity — wearable counters (steps/distance/floors) */}
          {data.body && data.body.primary && (
            <Card title="Body activity"
              sub="From your wearable — steps, distance, floors. Independent of the activity model.">
              <BodyBand body={data.body} colorOf={colorOf} nameOf={nameOf} />
            </Card>
          )}

          {/* sleep & away — the trustworthy facts */}
          <Card title="Sleep & time away" sub="Read straight from your sensors — these are known, not guessed.">
            <FactRow label="Asleep" perDay={s.sleep_per_day_min} color={colorOf("asleep")} />
            <FactRow label="Away" perDay={s.away_per_day_min} color={colorOf("away")} />
          </Card>

          {/* sessions & routine consistency */}
          {(s.sessions.length > 0 || s.consistency.nights >= 2) && (
            <Card title="Sessions & routine"
              sub="How long activities run, and how regular your sleep is. Lengths are coarse (30-min windows).">
              <SessionsPanel sessions={s.sessions} consistency={s.consistency}
                             colorOf={colorOf} nameOf={nameOf} onWhy={setWhy} />
            </Card>
          )}

          {/* household co-occurrence — opt-in, consensual */}
          <Card title="Household"
            sub="How housemates' activities line up. Opt-in and private: shown only when everyone involved has shared.">
            <HouseholdPanel household={household} selfName={s.person_id} pid={pid}
                            onToggle={toggleShare} nameOf={nameOf} colorOf={colorOf} />
          </Card>
        </>
      )}
      {why && <WhyModal person={pid} ts={why.ts} activity={why.activity} basis={why.basis}
                        nameOf={nameOf} colorOf={colorOf} onClose={() => setWhy(null)} />}
    </section>
  );
}

const fmtVal = (v: number, unit: string) =>
  unit === "km" ? `${v.toFixed(1)} km`
  : unit === "floors" ? `${Math.round(v)} floors`
  : unit === "steps" ? `${Math.round(v).toLocaleString()} steps`
  : Math.round(v).toLocaleString();

function BodyBand({ body, colorOf, nameOf }: {
  body: Body; colorOf: (s: string) => string; nameOf: (s: string) => string;
}) {
  const primary = body.primary as string;
  const accent = "var(--accent)";
  const maxDay = Math.max(1, ...body.per_day.map((d) => d.totals[primary] ?? 0));
  const rByCell = new Map(body.rhythm.map((c) => [`${c.dow}-${c.hour}`, c.totals[primary] ?? 0]));
  const rMax = Math.max(1, ...body.rhythm.map((c) => c.totals[primary] ?? 0));
  const acts = Object.entries(body.by_activity).sort((a, b) => b[1] - a[1]);
  const maxAct = Math.max(1, ...acts.map(([, v]) => v));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* headline totals + worn honesty */}
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "baseline" }}>
        {body.signals.map((sig) => (
          <span key={sig} style={{ fontSize: 15, fontWeight: 600 }}>
            {fmtVal(body.total[sig] ?? 0, body.units[sig])}
          </span>
        ))}
      </div>

      {/* coverage honesty: worn vs charging vs absent (absent != still) */}
      {(() => {
        const tot = body.worn_min + body.charging_min + body.absent_min;
        if (!tot) return null;
        const pct = (m: number) => Math.round((m / tot) * 100);
        const segs: [string, number, string][] = [
          ["worn", body.worn_min, "var(--accent)"],
          ["charging", body.charging_min, "var(--text-dim)"],
          ["away", body.absent_min, "var(--surface-2)"],
        ];
        return (
          <div>
            <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", marginBottom: 5 }}>
              {segs.map(([k, m, c]) => m > 0 && (
                <div key={k} title={`${k}: ${pct(m)}%`} style={{ flex: m, background: c }} />
              ))}
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
              worn {pct(body.worn_min)}% · charging {pct(body.charging_min)}% · away {pct(body.absent_min)}%
              <span style={{ opacity: 0.7 }}> — charging/away time isn't counted as "still"</span>
            </div>
          </div>
        );
      })()}

      {/* active vs sedentary (of worn time) + body trend callouts */}
      {(body.active_min + body.sedentary_min) > 0 && (
        <div>
          <div style={{ display: "flex", height: 14, borderRadius: 5, overflow: "hidden", marginBottom: 5 }}>
            <div title={`active ${fmtH(body.active_min)}`}
              style={{ flex: Math.max(body.active_min, 0.001), background: "var(--accent)" }} />
            <div title={`sedentary ${fmtH(body.sedentary_min)}`}
              style={{ flex: Math.max(body.sedentary_min, 0.001), background: "var(--surface-2)" }} />
          </div>
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
            active {fmtH(body.active_min)} · sedentary {fmtH(body.sedentary_min)} <span style={{ opacity: 0.7 }}>(of worn time)</span>
          </div>
          {body.trends.map((t, i) => {
            const lab = t.activity === "active" ? "Active" : t.activity === "sedentary" ? "Sedentary" : t.activity;
            const arrow = t.direction === "up" ? "▲" : t.direction === "down" ? "▼" : t.direction === "new" ? "●" : "○";
            const sign = t.direction === "up" ? "+" : t.direction === "down" ? "−" : "";
            return (
              <div key={i} style={{ fontSize: 12.5, marginTop: 4 }}>
                <span style={{ color: "var(--text-dim)" }}>{arrow}</span> {lab} {fmtH(t.recent_avg_min)}/day
                {t.direction === "up" || t.direction === "down"
                  ? ` · ${sign}${fmtH(Math.abs(t.delta_min))}/day vs last week` : ` ${t.direction} this week`}
              </div>
            );
          })}
        </div>
      )}

      {/* per-day primary bars */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {body.per_day.map((d) => {
          const v = d.totals[primary] ?? 0;
          return (
            <div key={d.date} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ width: 64, fontSize: 12.5, color: "var(--text-dim)" }}>{dayLabel(d.date)}</span>
              <div style={{ flex: 1, height: 14, background: "var(--surface-2)", borderRadius: 5, overflow: "hidden" }}>
                <div style={{ width: `${(v / maxDay) * 100}%`, height: "100%", background: accent }} />
              </div>
              <span style={{ width: 96, fontSize: 12.5, textAlign: "right" }}>{fmtVal(v, body.units[primary])}</span>
            </div>
          );
        })}
      </div>

      {/* primary-signal rhythm (mono intensity) */}
      <div>
        <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginBottom: 6 }}>
          When you move (by hour of week)
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "30px repeat(24, 1fr)", gap: 2 }}>
          {DOW.map((label, dow) => (
            <Fragment key={dow}>
              <span style={{ fontSize: 11, color: "var(--text-dim)", alignSelf: "center" }}>{label}</span>
              {Array.from({ length: 24 }, (_, hour) => {
                const v = rByCell.get(`${dow}-${hour}`) ?? 0;
                return <div key={hour}
                  title={`${DOW[dow]} ${String(hour).padStart(2, "0")}:00 · ${fmtVal(v, body.units[primary])}`}
                  style={{ height: 13, borderRadius: 2, background: v ? accent : "var(--surface-2)",
                           opacity: v ? 0.2 + 0.8 * (v / rMax) : 0.35 }} />;
              })}
            </Fragment>
          ))}
        </div>
      </div>

      {/* steps per activity — validates the labels */}
      {acts.length > 0 && (
        <div>
          <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginBottom: 6 }}>
            {(body.units[primary] || "movement")} during each activity
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {acts.slice(0, 8).map(([slug, v]) => (
              <div key={slug} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6, width: 110 }}>
                  <span style={{ width: 9, height: 9, borderRadius: 2, background: colorOf(slug),
                                 display: "inline-block", flex: "none" }} />
                  <span style={{ fontSize: 12.5 }}>{nameOf(slug)}</span>
                </span>
                <div style={{ flex: 1, height: 12, background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }}>
                  <div style={{ width: `${(v / maxAct) * 100}%`, height: "100%", background: colorOf(slug) }} />
                </div>
                <span style={{ width: 96, fontSize: 12.5, textAlign: "right" }}>{fmtVal(v, body.units[primary])}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

type Probe = { predicted?: string; confidence?: number; probabilities?: Record<string, number>;
               features?: Record<string, number>; explanation?: [string, number][]; error?: string };

function WhyModal({ person, ts, activity, basis, nameOf, colorOf, onClose }: {
  person: string; ts: string; activity: string; basis: string;
  nameOf: (s: string) => string; colorOf: (s: string) => string; onClose: () => void;
}) {
  const [probe, setProbe] = useState<Probe | null>(null);
  const [loading, setLoading] = useState(false);
  const isModel = basis === "model" || !["fact", "rule", "unknown"].includes(basis);

  useEffect(() => {
    if (!isModel || !person) return;
    setLoading(true);
    fetch("/api/predict/probe", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person_id: person, window_ts: ts }) })
      .then(j).then(setProbe).catch(() => setProbe({ error: "unavailable" }))
      .finally(() => setLoading(false));
  }, [person, ts, isModel]);

  const when = (() => { try { return new Date(ts).toLocaleString(); } catch { return ts; } })();
  const note = basis === "fact"
    ? "Known fact — a trusted sensor (away/asleep) reported this, so the model was bypassed for this window."
    : basis === "rule" ? "Cold-start rule — there's no trained model yet, so a simple household rule decided this."
    : basis === "unknown" ? "Not classified — Hearth wasn't confident enough to label this window."
    : null;
  const maxAbs = Math.max(0.0001, ...(probe?.explanation || []).map(([, v]) => Math.abs(v)));

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, padding: 16 }}>
      <div onClick={(e) => e.stopPropagation()} className="card" style={{ maxWidth: 480, width: "100%",
        maxHeight: "82vh", overflow: "auto", padding: 18 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <span style={{ width: 11, height: 11, borderRadius: 3, background: colorOf(activity) }} />
          <strong style={{ fontSize: 15 }}>{nameOf(activity)}</strong>
          <span style={{ flex: 1 }} />
          <button className="btn btn-ghost" onClick={onClose} style={{ padding: "2px 8px" }}>✕</button>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginBottom: 12 }}>{when}</div>

        {note && <p style={{ fontSize: 13.5, margin: 0 }}>{note}</p>}

        {isModel && (
          loading ? <p style={{ color: "var(--text-dim)" }}>Working out why…</p>
          : probe?.error ? <p style={{ color: "var(--text-dim)" }}>The model couldn't explain this window (it may predate the current model).</p>
          : probe ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div style={{ fontSize: 13 }}>
                Model predicted <strong>{nameOf(probe.predicted || activity)}</strong>
                {probe.confidence != null && <> · {Math.round(probe.confidence * 100)}% confident</>}
              </div>
              {probe.probabilities && (
                <div>
                  <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 5 }}>How it weighed the options</div>
                  {Object.entries(probe.probabilities).slice(0, 5).map(([slug, p]) => (
                    <div key={slug} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
                      <span style={{ width: 96, fontSize: 12.5 }}>{nameOf(slug)}</span>
                      <div style={{ flex: 1, height: 10, background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }}>
                        <div style={{ width: `${Math.round(p * 100)}%`, height: "100%", background: colorOf(slug) }} />
                      </div>
                      <span style={{ width: 36, fontSize: 12, textAlign: "right", color: "var(--text-dim)" }}>{Math.round(p * 100)}%</span>
                    </div>
                  ))}
                </div>
              )}
              {probe.explanation && probe.explanation.length > 0 && (
                <div>
                  <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 5 }}>
                    Top signals (green pushed toward this, grey away)
                  </div>
                  {probe.explanation.map(([feat, val], i) => (
                    <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3, fontSize: 12.5 }}>
                      <span style={{ width: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={feat}>{feat}</span>
                      <div style={{ flex: 1, display: "flex", justifyContent: "center" }}>
                        <div style={{ width: "50%", display: "flex", justifyContent: "flex-end" }}>
                          {val < 0 && <div style={{ width: `${(Math.abs(val) / maxAbs) * 100}%`, height: 9, background: "var(--text-dim)", borderRadius: 2 }} />}
                        </div>
                        <div style={{ width: 1, background: "var(--border)" }} />
                        <div style={{ width: "50%" }}>
                          {val >= 0 && <div style={{ width: `${(Math.abs(val) / maxAbs) * 100}%`, height: 9, background: "var(--ok, #3a9)", borderRadius: 2 }} />}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : null
        )}
      </div>
    </div>
  );
}

function SessionsPanel({ sessions, consistency, colorOf, nameOf, onWhy }: {
  sessions: Sess[]; consistency: Consistency;
  colorOf: (s: string) => string; nameOf: (s: string) => string;
  onWhy: (w: { ts: string; activity: string; basis: string }) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {sessions.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {sessions.slice(0, 8).map((s) => {
            const clickable = !!s.last_ts;
            return (
              <div key={s.activity} title={clickable ? "Click for why" : undefined}
                onClick={clickable ? () => onWhy({ ts: s.last_ts!, activity: s.activity,
                  basis: s.last_basis || "model" }) : undefined}
                style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13,
                         cursor: clickable ? "pointer" : "default" }}>
                <span style={{ width: 9, height: 9, borderRadius: 2, background: colorOf(s.activity),
                               display: "inline-block", flex: "none" }} />
                <span style={{ width: 120 }}>{nameOf(s.activity)}</span>
                <span style={{ color: "var(--text-dim)" }}>
                  {s.count}× · avg {fmtH(s.mean_min)} · longest {fmtH(s.longest_min)}
                </span>
              </div>
            );
          })}
        </div>
      )}
      {consistency.nights >= 2 && (
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap", fontSize: 13,
                      borderTop: "1px solid var(--border)", paddingTop: 10 }}>
          <div>
            <div style={{ color: "var(--text-dim)", fontSize: 12 }}>Wake time</div>
            <div><strong>{fmtClock(consistency.wake_avg_min)}</strong> · {consistency.wake_band}
              <span style={{ color: "var(--text-dim)" }}> (±{Math.round(consistency.wake_spread_min ?? 0)}m)</span>
            </div>
          </div>
          <div>
            <div style={{ color: "var(--text-dim)", fontSize: 12 }}>Bedtime</div>
            <div><strong>{fmtClock(consistency.bed_avg_min)}</strong> · {consistency.bed_band}
              <span style={{ color: "var(--text-dim)" }}> (±{Math.round(consistency.bed_spread_min ?? 0)}m)</span>
            </div>
          </div>
          <div style={{ color: "var(--text-dim)", alignSelf: "center" }}>over {consistency.nights} nights</div>
        </div>
      )}
    </div>
  );
}

function HouseholdPanel({ household, selfName, pid, onToggle, nameOf, colorOf }: {
  household: Household | null; selfName: string; pid: string;
  onToggle: () => void; nameOf: (s: string) => string; colorOf: (s: string) => string;
}) {
  const dot = (slug: string) => (
    <span style={{ width: 9, height: 9, borderRadius: 2, background: colorOf(slug),
                   display: "inline-block", flex: "none" }} />
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
        <input type="checkbox" checked={!!household?.self_shared} onChange={onToggle} disabled={!pid} />
        Share {selfName || "this person"}'s patterns for the household view
      </label>
      {!household?.enabled ? (
        <p style={{ color: "var(--text-dim)", margin: 0, fontSize: 13 }}>
          {household?.self_shared
            ? "Waiting for at least one housemate to also share before anything is shown."
            : "Nothing is shared. Turn this on (and have a housemate do the same) to see how your routines line up."}
        </p>
      ) : household.pairs.length === 0 ? (
        <p style={{ color: "var(--text-dim)", margin: 0, fontSize: 13 }}>Not enough overlapping time yet.</p>
      ) : (
        household.pairs.map((pair) => (
          <div key={pair.other_id} style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            <div style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
              When {selfName} is… {pair.other_name} usually:
            </div>
            {pair.items.slice(0, 6).map((it, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 13 }}>
                {dot(it.a)}<span style={{ width: 96 }}>{nameOf(it.a)}</span>
                <span style={{ color: "var(--text-dim)" }}>→</span>
                {dot(it.b)}<span style={{ flex: 1 }}>{nameOf(it.b)}</span>
                <span style={{ color: "var(--text-dim)" }}>{Math.round(it.frac * 100)}%</span>
              </div>
            ))}
          </div>
        ))
      )}
    </div>
  );
}

function TrendsCard({ trends, colorOf, nameOf }: {
  trends: Trend[]; colorOf: (s: string) => string; nameOf: (s: string) => string;
}) {
  const phrase = (t: Trend) => {
    const now = fmtH(t.recent_avg_min), before = fmtH(t.prior_avg_min);
    if (t.direction === "new") return { arrow: "●", txt: `started — ${now}/day` };
    if (t.direction === "stopped") return { arrow: "○", txt: `stopped — was ${before}/day` };
    const sign = t.direction === "up" ? "+" : "−";
    return { arrow: t.direction === "up" ? "▲" : "▼",
             txt: `${now}/day · ${sign}${fmtH(Math.abs(t.delta_min))}/day vs last week` };
  };
  const tag = (b: string) => b === "fact" ? "known" : b === "mixed" ? "part-known" : "inferred";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
      {trends.map((t, i) => {
        const p = phrase(t);
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 13 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: colorOf(t.activity),
                           display: "inline-block", flex: "none" }} />
            <span style={{ width: 90, fontWeight: 500 }}>{nameOf(t.activity)}</span>
            <span style={{ color: "var(--text-dim)", width: 14 }}>{p.arrow}</span>
            <span style={{ flex: 1 }}>{p.txt}</span>
            <span style={{ fontSize: 11, color: "var(--text-dim)", border: "1px solid var(--border)",
                           borderRadius: 999, padding: "1px 7px", flex: "none" }}>{tag(t.basis)}</span>
          </div>
        );
      })}
    </div>
  );
}

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function RhythmHeatmap({ cells, colorOf, nameOf, activities }: {
  cells: Cell[]; colorOf: (s: string) => string; nameOf: (s: string) => string;
  activities: string[];
}) {
  const [filter, setFilter] = useState("");        // "" = dominant activity per cell
  const byCell = useMemo(() => {
    const m = new Map<string, Record<string, number>>();
    cells.forEach((c) => m.set(`${c.dow}-${c.hour}`, c.totals));
    return m;
  }, [cells]);
  const maxFiltered = useMemo(() => {
    if (!filter) return 0;
    let mx = 0;
    cells.forEach((c) => { mx = Math.max(mx, c.totals[filter] ?? 0); });
    return mx || 1;
  }, [cells, filter]);

  const cellStyle = (dow: number, hour: number): CSSProperties => {
    const t = byCell.get(`${dow}-${hour}`);
    const base: CSSProperties = { height: 13, borderRadius: 2,
      background: "var(--surface-2)", opacity: 0.35 };
    if (!t) return base;
    if (filter) {
      const v = t[filter] ?? 0;
      if (!v) return base;
      return { ...base, background: colorOf(filter), opacity: 0.2 + 0.8 * (v / maxFiltered) };
    }
    const top = Object.entries(t).sort((a, b) => b[1] - a[1])[0];
    const sum = Object.values(t).reduce((n, x) => n + x, 0) || 1;
    return { ...base, background: colorOf(top[0]), opacity: 0.35 + 0.65 * (top[1] / sum) };
  };
  const tip = (dow: number, hour: number) => {
    const t = byCell.get(`${dow}-${hour}`);
    const head = `${DOW[dow]} ${String(hour).padStart(2, "0")}:00`;
    if (!t) return `${head} · no data`;
    const parts = Object.entries(t).sort((a, b) => b[1] - a[1])
      .map(([s, m]) => `${nameOf(s)} ${fmtH(m)}`);
    return `${head} · ${parts.join(", ")}`;
  };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
        <select value={filter} onChange={(e) => setFilter(e.target.value)}
          style={{ fontSize: 12.5 }}>
          <option value="">Dominant activity</option>
          {activities.map((slug) => <option key={slug} value={slug}>{nameOf(slug)}</option>)}
        </select>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "30px repeat(24, 1fr)", gap: 2 }}>
        {DOW.map((label, dow) => (
          <Fragment key={dow}>
            <span style={{ fontSize: 11, color: "var(--text-dim)", alignSelf: "center" }}>{label}</span>
            {Array.from({ length: 24 }, (_, hour) => (
              <div key={hour} title={tip(dow, hour)} style={cellStyle(dow, hour)} />
            ))}
          </Fragment>
        ))}
        <span />
        {[0, 6, 12, 18].map((h, i) => (
          <span key={h} style={{ gridColumn: `${h + 2} / span 6`, fontSize: 10,
            color: "var(--text-dim)", marginTop: 2, textAlign: i ? "center" : "left" }}>
            {String(h).padStart(2, "0")}:00
          </span>
        ))}
      </div>
    </div>
  );
}

function Sequences({ seq, colorOf, nameOf }: {
  seq: Trans[]; colorOf: (s: string) => string; nameOf: (s: string) => string;
}) {
  const dot = (slug: string) => (
    <span style={{ width: 9, height: 9, borderRadius: 2, background: colorOf(slug),
                   display: "inline-block", flex: "none" }} />
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      {seq.slice(0, 12).map((t, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, width: 230 }}>
            {dot(t.src)}<span style={{ color: "var(--text-dim)" }}>{nameOf(t.src)}</span>
            <span style={{ color: "var(--text-dim)" }}>→</span>
            {dot(t.dst)}<span>{nameOf(t.dst)}</span>
          </span>
          <div style={{ flex: 1, height: 6, background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }}>
            <div style={{ width: `${Math.round(t.prob * 100)}%`, height: "100%", background: colorOf(t.dst) }} />
          </div>
          <span style={{ width: 38, textAlign: "right", color: "var(--text-dim)" }}>
            {Math.round(t.prob * 100)}%
          </span>
        </div>
      ))}
    </div>
  );
}

function FactRow({ label, perDay, color }: {
  label: string; perDay: Record<string, number>; color: string;
}) {
  const entries = Object.entries(perDay).sort();
  if (!entries.length) return (
    <div style={{ fontSize: 13, color: "var(--text-dim)" }}>{label}: no data yet.</div>
  );
  const avg = entries.reduce((n, [, m]) => n + m, 0) / entries.length;
  const max = Math.max(...entries.map(([, m]) => m), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
        <span style={{ fontWeight: 500 }}>{label}</span>
        <span style={{ color: "var(--text-dim)" }}>avg {fmtH(avg)}/day</span>
      </div>
      <div style={{ display: "flex", gap: 3, alignItems: "flex-end", height: 36 }}>
        {entries.map(([date, m]) => (
          <div key={date} title={`${date}: ${fmtH(m)}`}
            style={{ flex: 1, height: `${Math.max(6, (m / max) * 100)}%`, background: color,
                     borderRadius: 2, minWidth: 3 }} />
        ))}
      </div>
    </div>
  );
}

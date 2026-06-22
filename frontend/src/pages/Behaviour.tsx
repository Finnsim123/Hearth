/**
 * Behaviour — habits & routines over the published prediction timeline.
 * Descriptive, not judgmental; honest about KNOWN (facts) vs INFERRED (model).
 * v1: today ribbon · time budget (stacked bars + totals) · sleep/away facts.
 * Source: GET /api/behaviour?person=&days=.  Dependency-free (CSS bars).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import Card from "../components/Card";

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };

type Day = { date: string; totals: Record<string, number>; unknown_min: number;
             fact_min: number; inferred_min: number };
type Seg = { start: string; end: string; activity: string; basis: string };
type Summary = {
  person_id: string; start: string; end: string; window_min: number;
  totals: Record<string, number>; total_min: number; classified_min: number;
  coverage: number; fact_min: number; inferred_min: number; known_fraction: number;
  per_day: Day[]; today: Seg[];
  sleep_per_day_min: Record<string, number>; away_per_day_min: Record<string, number>;
};
type Data = { summary: Summary | null; persons: { id: string; name: string }[];
              activities: { slug: string; name: string; color: string }[] };

const UNKNOWN_COLOR = "var(--surface-2, #2a2f3a)";
const fmtH = (min: number) => {
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return h ? `${h}h${m ? ` ${m}m` : ""}` : `${m}m`;
};
const dayLabel = (iso: string) =>
  new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric" });

export default function Behaviour() {
  const [data, setData] = useState<Data | null>(null);
  const [person, setPerson] = useState<string>("");
  const [days, setDays] = useState(7);

  const load = useCallback(() => {
    const q = new URLSearchParams({ days: String(days) });
    if (person) q.set("person", person);
    fetch(`/api/behaviour?${q}`).then(j).then(setData)
      .catch(() => setData({ summary: null, persons: [], activities: [] }));
  }, [person, days]);
  useEffect(load, [load]);

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

          {/* today ribbon */}
          {s.today.length > 0 && (
            <Card title="Today">
              <div style={{ display: "flex", height: 26, borderRadius: 8, overflow: "hidden",
                            border: "1px solid var(--border)" }}>
                {s.today.map((seg, i) => {
                  const mins = (new Date(seg.end).getTime() - new Date(seg.start).getTime()) / 60000;
                  return (
                    <div key={i} title={`${nameOf(seg.activity)} · ${fmtH(mins)}`}
                      style={{ flex: mins, background: colorOf(seg.activity),
                               opacity: seg.basis === "fact" ? 1 : seg.activity === "unknown" ? 0.5 : 0.62 }} />
                  );
                })}
              </div>
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

          {/* sleep & away — the trustworthy facts */}
          <Card title="Sleep & time away" sub="Read straight from your sensors — these are known, not guessed.">
            <FactRow label="Asleep" perDay={s.sleep_per_day_min} color={colorOf("asleep")} />
            <FactRow label="Away" perDay={s.away_per_day_min} color={colorOf("away")} />
          </Card>
        </>
      )}
    </section>
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

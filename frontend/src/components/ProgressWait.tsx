/**
 * ProgressWait — full-screen wait with an honest progress bar.
 * Real durations vary (docker builds, restarts), so the bar eases
 * asymptotically toward `estimateS` and caps at 97% — the caller decides
 * when the wait is actually over (reload / state change).
 */
import { useEffect, useState } from "react";

export default function ProgressWait({ title, sub, estimateS, stages }: {
  title: string;
  sub: string;
  estimateS: number;
  /** [fromSecond, text] — first matching entry counting from the end wins. */
  stages: [number, string][];
}) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const pct = Math.min(97, (1 - Math.exp(-elapsed / estimateS)) * 100);
  const stage = [...stages].reverse().find(([from]) => elapsed >= from)?.[1] ?? stages[0][1];
  const mm = String(Math.floor(elapsed / 60));
  const ss = String(elapsed % 60).padStart(2, "0");
  return (
    <div style={{ padding: "120px 16px", maxWidth: 560, margin: "0 auto", textAlign: "center" }}>
      <h2>{title}</h2>
      <p style={{ color: "var(--text-dim)", fontSize: 14.5 }}>{sub}</p>
      <div aria-label="progress" style={{
        height: 6, borderRadius: 3, background: "var(--surface-2)",
        margin: "28px 0 10px", overflow: "hidden",
      }}>
        <div style={{
          height: "100%", width: `${pct}%`, borderRadius: 3,
          background: "var(--accent)", transition: "width 1s linear",
        }} />
      </div>
      <p style={{ color: "var(--text-dim)", fontSize: 13, fontVariantNumeric: "tabular-nums" }}>
        {mm}:{ss} elapsed · {stage}
      </p>
    </div>
  );
}

/**
 * Shared bubble cloud for the entity triage — each cluster a bubble sized by
 * entity count, accent-tinted if kept, dim if skipped. Read-only by default;
 * pass `kept` + `onToggle` to make bubbles clickable (keep/skip a whole group).
 * Used by the wizard "Scanning your home" step, the Welcome hand-off, and the
 * Sensors "Entity groups" panel.
 */
export type TriageCluster = {
  label: string; relevant: boolean; why?: string; count: number; kept?: number;
};

export default function BubbleCloud({ clusters, total, keptCount, by, kept, onToggle, max = 18 }: {
  clusters: TriageCluster[];
  total?: number;
  keptCount?: number;
  by?: string | null;
  kept?: Record<string, boolean>;
  onToggle?: (label: string) => void;
  max?: number;
}) {
  const shown = clusters.slice(0, max);
  if (!shown.length) return null;
  const maxCount = Math.max(...shown.map((c) => c.count), 1);
  const size = (n: number) => Math.round(46 + 52 * Math.sqrt(n / maxCount));   // 46–98px
  const interactive = !!onToggle;
  const on = (c: TriageCluster) => (kept ? !!kept[c.label] : c.relevant);
  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center",
                    justifyContent: "center" }}>
        {shown.map((c) => {
          const d = size(c.count);
          const keep = on(c);
          return (
            <button key={c.label} disabled={!interactive} onClick={() => onToggle?.(c.label)}
              title={`${c.label} · ${c.count} entities${c.why ? ` · ${c.why}` : ""}`
                     + (interactive ? (keep ? " · click to skip" : " · click to keep") : "")}
              style={{ width: d, height: d, borderRadius: "50%", flexShrink: 0, padding: 4,
                       display: "flex", flexDirection: "column", alignItems: "center",
                       justifyContent: "center", textAlign: "center", lineHeight: 1.15,
                       cursor: interactive ? "pointer" : "default",
                       border: `1.5px solid ${keep ? "var(--accent)" : "var(--border)"}`,
                       background: keep ? "color-mix(in srgb, var(--accent) 16%, transparent)" : "var(--surface)",
                       color: keep ? "var(--text)" : "var(--text-dim)", opacity: keep ? 1 : 0.6 }}>
              <span style={{ fontSize: Math.max(9, Math.min(12, d / 7)), fontWeight: 600,
                             overflow: "hidden", textOverflow: "ellipsis",
                             maxWidth: d - 8, whiteSpace: "nowrap" }}>{c.label}</span>
              <span style={{ fontSize: 10, opacity: 0.7 }}>{c.count}</span>
            </button>
          );
        })}
      </div>
      {keptCount != null && total != null && (
        <div style={{ fontSize: 12.5, color: "var(--text-dim)", textAlign: "center", marginTop: 10 }}>
          Keeping <strong style={{ color: "var(--accent)" }}>{keptCount}</strong> of {total} entities
          {by === "llm" ? " — chosen by AI" : by ? " — grouped by type" : ""}.
        </div>
      )}
    </div>
  );
}

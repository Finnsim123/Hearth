/**
 * Shared bubble cloud for the entity triage — each cluster a bubble sized by
 * entity count, with the category icon + count inside and the label as a caption
 * BELOW (so long names never get clipped). Accent-tinted if kept, dim if skipped.
 * Read-only by default; pass `kept` + `onToggle` to make bubbles clickable
 * (keep/skip a whole group). Toggle state is keyed by the stable `category`
 * (falling back to `label`), so it survives re-scans and translation.
 * Used by the wizard "Scanning your home" step, the Welcome hand-off, and the
 * Sensors "Entity groups" panel.
 */
import { Icon, ICON_NAMES, type IconName } from "../icons";

export type TriageCluster = {
  label: string; relevant: boolean; why?: string; count: number; kept?: number;
  category?: string; icon?: string;
};

const cid = (c: TriageCluster) => c.category ?? c.label;
const iconFor = (c: TriageCluster): IconName =>
  (c.icon && (ICON_NAMES as string[]).includes(c.icon) ? c.icon : "more") as IconName;

export default function BubbleCloud({ clusters, total, keptCount, by, kept, onToggle, max = 18 }: {
  clusters: TriageCluster[];
  total?: number;
  keptCount?: number;
  by?: string | null;
  kept?: Record<string, boolean>;
  onToggle?: (id: string) => void;
  max?: number;
}) {
  const shown = clusters.slice(0, max);
  if (!shown.length) return null;
  const maxCount = Math.max(...shown.map((c) => c.count), 1);
  const size = (n: number) => Math.round(56 + 44 * Math.sqrt(n / maxCount));   // 56–100px
  const interactive = !!onToggle;
  const on = (c: TriageCluster) => (kept ? !!kept[cid(c)] : c.relevant);
  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 14, alignItems: "flex-start",
                    justifyContent: "center" }}>
        {shown.map((c) => {
          const d = size(c.count);
          const keep = on(c);
          return (
            <button key={cid(c)} disabled={!interactive} onClick={() => onToggle?.(cid(c))}
              title={`${c.label} · ${c.count} entities${c.why ? ` · ${c.why}` : ""}`
                     + (interactive ? (keep ? " · click to skip" : " · click to keep") : "")}
              style={{ width: 100, background: "none", border: "none", padding: 0,
                       display: "flex", flexDirection: "column", alignItems: "center", gap: 6,
                       cursor: interactive ? "pointer" : "default",
                       color: keep ? "var(--text)" : "var(--text-dim)", opacity: keep ? 1 : 0.55 }}>
              <span style={{ width: d, height: d, borderRadius: "50%", display: "flex",
                             flexDirection: "column", alignItems: "center", justifyContent: "center",
                             gap: 2, flexShrink: 0,
                             border: `1.5px solid ${keep ? "var(--accent)" : "var(--border)"}`,
                             background: keep ? "color-mix(in srgb, var(--accent) 16%, transparent)"
                                              : "var(--surface)",
                             color: keep ? "var(--accent)" : "var(--text-dim)" }}>
                <Icon name={iconFor(c)} size={Math.round(d / 3.2)} />
                <span style={{ fontSize: 11, fontWeight: 600, fontVariantNumeric: "tabular-nums",
                               color: keep ? "var(--text)" : "var(--text-dim)" }}>{c.count}</span>
              </span>
              <span style={{ fontSize: 12, lineHeight: 1.2, textAlign: "center", maxWidth: 100,
                             display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                             overflow: "hidden" }}>{c.label}</span>
            </button>
          );
        })}
      </div>
      {keptCount != null && total != null && (
        <div style={{ fontSize: 12.5, color: "var(--text-dim)", textAlign: "center", marginTop: 12 }}>
          Keeping <strong style={{ color: "var(--accent)" }}>{keptCount}</strong> of {total} entities
          {by === "llm" ? " — chosen by AI" : by ? " — grouped by type" : ""}.
        </div>
      )}
    </div>
  );
}

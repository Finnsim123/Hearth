/**
 * Entity-group selector for the triage step. Each group is a toggle CARD —
 * icon, name, entity count, and the (AI- or rule-derived) reason it's grouped —
 * with a switch to keep or skip the whole group. Cards beat the old bubble
 * cloud here: the job is a keep/skip decision, so state must be legible at a
 * glance (switch + fill, not size + opacity) and the "why" has to be visible on
 * touch, not buried in a tooltip.
 *
 * The DEFAULTS (each cluster's `relevant`) are Hearth's recommendation — tuned
 * for accuracy at a point where there's no data yet to judge predictiveness, so
 * they're the safe pick. This UI is opt-OUT: the user only needs to switch a
 * group off to exclude something private; any deviation from the recommendation
 * is flagged (it may affect results) with a one-click reset. Read-only by
 * default; pass `kept` + `onToggle` to make cards interactive, and `onReset` to
 * enable the "reset to recommended" action. Toggle state is keyed by the stable
 * `category` (falling back to `label`), so it survives re-scans and translation.
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

/** Pure-CSS switch — accent track when on, muted when off. Decorative
 *  (the whole card is the button); state comes from `on`. */
function Switch({ on }: { on: boolean }) {
  return (
    <span aria-hidden style={{ flexShrink: 0, width: 36, height: 20, borderRadius: 999,
        background: on ? "var(--accent)" : "var(--surface-2)",
        border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
        position: "relative", transition: "background 150ms ease-out" }}>
      <span style={{ position: "absolute", top: 1, left: on ? 17 : 1, width: 16, height: 16,
        borderRadius: "50%", background: on ? "var(--on-accent)" : "var(--text-dim)",
        transition: "left 150ms ease-out" }} />
    </span>
  );
}

export default function BubbleCloud({ clusters, total, keptCount, by, kept, onToggle, onReset, max = 18 }: {
  clusters: TriageCluster[];
  total?: number;
  keptCount?: number;
  by?: string | null;
  kept?: Record<string, boolean>;
  onToggle?: (id: string) => void;
  onReset?: () => void;
  max?: number;
}) {
  const shown = clusters.slice(0, max);
  if (!shown.length) return null;
  const interactive = !!onToggle;
  const on = (c: TriageCluster) => (kept ? !!kept[cid(c)] : c.relevant);
  const off = (c: TriageCluster) => on(c) !== c.relevant;   // deviates from recommended
  const keptN = shown.filter(on).length;
  const changedN = shown.filter(off).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {interactive && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5,
                      color: "var(--text-dim)", flexWrap: "wrap" }}>
          <span><strong style={{ color: "var(--text)" }}>{keptN}</strong> of {shown.length} kept</span>
          {changedN > 0 ? (
            <span style={{ color: "var(--warn)" }}>
              · {changedN} changed from recommended — may affect accuracy
            </span>
          ) : (
            <span>· using Hearth's recommended picks</span>
          )}
          {onReset && changedN > 0 && (
            <button type="button" onClick={onReset} className="btn btn-ghost"
              style={{ marginLeft: "auto", fontSize: 12, minHeight: 0, padding: "4px 10px" }}>
              Reset to recommended
            </button>
          )}
        </div>
      )}
      <div style={{ display: "grid", gap: 10,
                    gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))" }}>
        {shown.map((c) => {
          const keep = on(c);
          return (
            <button key={cid(c)} type="button" disabled={!interactive}
              onClick={() => onToggle?.(cid(c))}
              aria-pressed={interactive ? keep : undefined}
              title={interactive ? (keep ? "Click to skip this group" : "Click to keep this group") : undefined}
              style={{ display: "flex", alignItems: "center", gap: 12, textAlign: "left",
                       padding: "11px 13px", borderRadius: "var(--radius-card)",
                       cursor: interactive ? "pointer" : "default",
                       border: `1px solid ${keep ? "var(--accent)" : "var(--border)"}`,
                       background: keep ? "color-mix(in srgb, var(--accent) 9%, var(--surface))"
                                        : "var(--surface)",
                       opacity: keep ? 1 : 0.72, transition: "border-color 150ms, background 150ms, opacity 150ms" }}>
              {/* icon tile */}
              <span style={{ flexShrink: 0, width: 38, height: 38, borderRadius: 10,
                       display: "flex", alignItems: "center", justifyContent: "center",
                       border: `1px solid ${keep ? "var(--accent)" : "var(--border)"}`,
                       background: keep ? "color-mix(in srgb, var(--accent) 16%, transparent)"
                                        : "var(--surface-2)",
                       color: keep ? "var(--accent)" : "var(--text-dim)" }}>
                <Icon name={iconFor(c)} size={20} />
              </span>
              {/* name + count + why */}
              <span style={{ minWidth: 0, flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
                <span style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                  <span style={{ fontSize: 13.5, fontWeight: 600, color: "var(--text)",
                           overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.label}</span>
                  <span style={{ flexShrink: 0, fontSize: 11.5, fontWeight: 600,
                           fontVariantNumeric: "tabular-nums", color: "var(--text-dim)" }}>{c.count}</span>
                  {interactive && off(c) && (
                    <span title={`Changed from Hearth's recommendation (${c.relevant ? "keep" : "skip"})`}
                      style={{ flexShrink: 0, width: 6, height: 6, borderRadius: "50%",
                               background: "var(--warn)" }} />
                  )}
                </span>
                {c.why && (
                  <span style={{ fontSize: 11.5, lineHeight: 1.3, color: "var(--text-dim)",
                           display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                           overflow: "hidden" }}>{c.why}</span>
                )}
              </span>
              {interactive && <Switch on={keep} />}
            </button>
          );
        })}
      </div>
      {keptCount != null && total != null && (
        <div style={{ fontSize: 12.5, color: "var(--text-dim)", textAlign: "center", marginTop: 2 }}>
          Keeping <strong style={{ color: "var(--accent)" }}>{keptCount}</strong> of {total} entities
          {by === "llm" ? " — chosen by AI" : by ? " — grouped by type" : ""}.
        </div>
      )}
    </div>
  );
}

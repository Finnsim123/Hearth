/**
 * Card — the standard dashboard/section surface. Use this for every new card so
 * they stay consistent (no per-card design from scratch).
 *
 * Rules:
 *  - Optional header = leading icon + title (+ a right-aligned `action`).
 *  - Optional one-line `sub` under the title for context.
 *  - Pass `onClick` to make the whole card a button (gets a pointer cursor).
 *  - Body is whatever you put in `children`.
 * Visuals (bg/border/radius/shadow) come from the global `.card` class, so the
 * look stays uniform; only padding/gap live here.
 */
import type { CSSProperties, ReactNode } from "react";
import { Icon, type IconName } from "../icons";

export default function Card({ icon, title, sub, action, onClick, children, style }: {
  icon?: IconName;
  title?: string;
  sub?: ReactNode;
  action?: ReactNode;
  onClick?: () => void;
  children?: ReactNode;
  style?: CSSProperties;
}) {
  const hasHeader = Boolean(icon || title || action);
  return (
    <section className="card" onClick={onClick}
      style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12,
               cursor: onClick ? "pointer" : undefined, ...style }}>
      {hasHeader && (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {icon && <Icon name={icon} size={18} />}
          {title && <h3 style={{ margin: 0, fontSize: 16 }}>{title}</h3>}
          {action && <div style={{ marginLeft: "auto" }}>{action}</div>}
        </div>
      )}
      {sub && <p style={{ margin: 0, fontSize: 13, color: "var(--text-dim)" }}>{sub}</p>}
      {children}
    </section>
  );
}

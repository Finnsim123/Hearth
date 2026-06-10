/**
 * Wizard UI primitives — every onboarding step is built from these, so the
 * flow feels like ONE thing: label → title → explainer → fields → callout.
 * Tokens from theme.css; rules from docs/DESIGN.md.
 */
import type { ReactNode } from "react";
import { Icon, type IconName } from "../icons";

export function StepShell(props: {
  step: number;
  total: number;
  title: string;
  explainer: ReactNode;
  children: ReactNode;
}) {
  return (
    <section style={{ maxWidth: 560, margin: "0 auto" }}>
      <p className="label" style={{ margin: "0 0 4px" }}>
        Step {props.step} of {props.total}
      </p>
      <h2 style={{ margin: "0 0 8px" }}>{props.title}</h2>
      <p style={{ color: "var(--text-dim)", margin: "0 0 24px", fontSize: 15 }}>
        {props.explainer}
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>{props.children}</div>
    </section>
  );
}

export function Progress({ current, total }: { current: number; total: number }) {
  return (
    <div style={{ display: "flex", gap: 6, maxWidth: 560, margin: "0 auto 28px" }} aria-label={`Step ${current} of ${total}`}>
      {Array.from({ length: total }, (_, i) => (
        <span
          key={i}
          style={{
            flex: 1,
            height: 4,
            borderRadius: 2,
            background: i < current ? "var(--accent)" : "var(--surface-2)",
            transition: "background 250ms ease-out",
          }}
        />
      ))}
    </div>
  );
}

export function Field(props: {
  label: string;
  hint?: ReactNode;
  error?: string;
  children: ReactNode;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ fontSize: 14, fontWeight: 500 }}>{props.label}</span>
      {props.children}
      {props.hint && <span style={{ fontSize: 13, color: "var(--text-dim)" }}>{props.hint}</span>}
      {props.error && <span style={{ fontSize: 13, color: "var(--danger)" }}>{props.error}</span>}
    </label>
  );
}

/** "What's happening" box — the wizard's explanatory voice. */
export function Callout({ icon = "info", children }: { icon?: IconName; children: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        gap: 10,
        padding: "12px 14px",
        background: "var(--surface-2)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-ctl)",
        fontSize: 14,
        color: "var(--text-dim)",
        lineHeight: 1.5,
      }}
    >
      <span style={{ color: "var(--accent)", flexShrink: 0, marginTop: 1 }}>
        <Icon name={icon} size={18} />
      </span>
      <span>{children}</span>
    </div>
  );
}

/** Big either/or selector (e.g. existing InfluxDB vs bundled). */
export function ChoiceCard(props: {
  icon: IconName;
  title: string;
  description: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={props.onSelect}
      style={{
        textAlign: "left",
        display: "flex",
        gap: 12,
        padding: 16,
        background: "var(--surface)",
        border: props.selected ? "2px solid var(--accent)" : "1px solid var(--border)",
        borderRadius: "var(--radius-card)",
        color: "var(--text)",
        cursor: "pointer",
      }}
    >
      <span style={{ color: props.selected ? "var(--accent)" : "var(--text-dim)" }}>
        <Icon name={props.icon} size={22} />
      </span>
      <span>
        <span style={{ display: "block", fontWeight: 500, fontSize: 15 }}>{props.title}</span>
        <span style={{ display: "block", fontSize: 13.5, color: "var(--text-dim)", marginTop: 2 }}>
          {props.description}
        </span>
      </span>
    </button>
  );
}

export type TestState = "idle" | "testing" | "ok" | "fail";

/** Connection test row: button + live status, used by HA / Influx / MQTT steps. */
export function TestRow(props: {
  state: TestState;
  okText: string;
  failText: string;
  onTest: () => void;
}) {
  const { state } = props;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <button className="btn btn-secondary" onClick={props.onTest} disabled={state === "testing"}>
        {state === "testing" ? "Testing…" : "Test connection"}
      </button>
      {state === "ok" && (
        <span style={{ color: "var(--ok)", fontSize: 14, display: "flex", gap: 6, alignItems: "center" }}>
          <Icon name="check" size={16} /> {props.okText}
        </span>
      )}
      {state === "fail" && (
        <span style={{ color: "var(--danger)", fontSize: 14, display: "flex", gap: 6, alignItems: "center" }}>
          <Icon name="x" size={16} /> {props.failText}
        </span>
      )}
    </div>
  );
}

export function FooterNav(props: {
  onBack?: () => void;
  onNext: () => void;
  nextLabel?: string;
  nextDisabled?: boolean;
  skip?: { label: string; onSkip: () => void };
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", maxWidth: 560, margin: "32px auto 0", gap: 12 }}>
      {props.onBack && (
        <button className="btn btn-ghost" onClick={props.onBack}>
          Back
        </button>
      )}
      <span style={{ marginLeft: "auto" }} />
      {props.skip && (
        <button className="btn btn-ghost" onClick={props.skip.onSkip}>
          {props.skip.label}
        </button>
      )}
      <button className="btn btn-primary" onClick={props.onNext} disabled={props.nextDisabled}>
        {props.nextLabel ?? "Continue"}
      </button>
    </div>
  );
}

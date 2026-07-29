/**
 * Settings — household notification controls, connections, appearance,
 * account, system. Spec: docs/UI_SPEC.md §Settings.
 *
 * Design rules: one card per concern, every control explains itself,
 * Save is per-card so a half-edited page never clobbers anything.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Avatar, { PRESET_HUES } from "../components/Avatar";
import FoundationalFacts from "../components/FoundationalFacts";
import TransitionMarkers from "../components/TransitionMarkers";
import { Icon, type IconName } from "../icons";
import { applyTheme, getTheme, type ThemeMode } from "../theme";
import Logs from "./Logs";
import Methodology from "./Methodology";

// ── shared bits ─────────────────────────────────────────────────────────────

type SaveState = "idle" | "saving" | "ok" | "fail";

function SaveButton({ state, onClick, label = "Save" }: {
  state: SaveState; onClick: () => void; label?: string;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <button className="btn btn-primary" disabled={state === "saving"} onClick={onClick}>
        {state === "saving" ? "Saving…" : label}
      </button>
      {state === "ok" && <span style={{ color: "var(--ok, #34D399)", fontSize: 13 }}>Saved ✓</span>}
      {state === "fail" && <span style={{ color: "var(--danger)", fontSize: 13 }}>Couldn't save — check logs</span>}
    </div>
  );
}

function Card({ title, sub, children }: {
  title: string; sub?: string; children: React.ReactNode;
}) {
  return (
    <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
      <div>
        <h3 style={{ margin: 0, fontSize: 16 }}>{title}</h3>
        {sub && <p style={{ margin: "4px 0 0", fontSize: 13.5, color: "var(--text-dim)" }}>{sub}</p>}
      </div>
      {children}
    </div>
  );
}

function Row({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <span style={{ fontSize: 13.5, fontWeight: 500 }}>{label}</span>
      {children}
      {hint && <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>{hint}</span>}
    </label>
  );
}

const j = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); };
const post = (url: string, body: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** Restart the container, then reload once it has gone down AND come back up. */
async function restartAndReload(setBusy: (b: boolean) => void) {
  setBusy(true);
  try { await fetch("/api/system/restart", { method: "POST" }); } catch { /* it's exiting */ }
  let down = false;
  setTimeout(() => {
    const id = setInterval(async () => {
      try {
        const r = await fetch("/api/health");
        if (!r.ok) throw new Error();
        if (down) { clearInterval(id); window.location.reload(); }
      } catch { down = true; }
    }, 1500);
  }, 1500);
}

// ── household ───────────────────────────────────────────────────────────────

type Person = {
  id: string; name: string; avatar: string | null;
  ha_person_entity: string | null; notify_service: string | null;
  has_device: boolean; notify_system: boolean; ask_budget_per_day: number;
  quiet_hours: [number, number]; enabled: boolean;
};

const pad2 = (h: number) => String(h).padStart(2, "0");

function PersonCard({ p: initial, onChanged, orphans = [] }:
                    { p: Person; onChanged?: () => void; orphans?: string[] }) {
  const [p, setP] = useState(initial);
  const [state, setState] = useState<SaveState>("idle");
  const [open, setOpen] = useState(false);
  const [photoErr, setPhotoErr] = useState("");
  const [danger, setDanger] = useState(false);
  const [confirmName, setConfirmName] = useState("");
  const [reclaimId, setReclaimId] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const u = (patch: Partial<Person>) => { setP({ ...p, ...patch }); setState("idle"); };
  const save = async () => {
    setState("saving");
    try { await post("/api/persons", p).then(j); setState("ok"); }
    catch { setState("fail"); }
  };
  // Disable = pause (keep everything); honoured by ingest + trainer. Saves at once.
  const setEnabled = async (enabled: boolean) => {
    const next = { ...p, enabled };
    setP(next);
    try { await post("/api/persons", next).then(j); } catch { setP(p); }
  };
  // Remove & forget = irreversible erasure of this member (backend purges their
  // time-series + app-DB rows, keeps the rest of the household, retrains the rest).
  const forget = async () => {
    setBusy(true);
    try { await post(`/api/persons/${p.id}/forget`, {}).then(j); onChanged?.(); }
    catch { setBusy(false); }
  };
  // Re-link: reclaim history orphaned under a previous identity (rename+reseed).
  const relink = async () => {
    if (!reclaimId) return;
    setBusy(true);
    try { await post(`/api/persons/${p.id}/relink`, { old_id: reclaimId }).then(j); onChanged?.(); }
    catch { setBusy(false); }
  };
  const onPhoto = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setPhotoErr("");
    if (f.size > 4 * 1024 * 1024) { setPhotoErr("Image too large (max 4 MB)."); return; }
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const r = await post(`/api/persons/${p.id}/avatar`, { image: reader.result }).then(j);
        setP((cur) => ({ ...cur, avatar: r.avatar }));
      } catch { setPhotoErr("Upload failed — try a smaller PNG or JPEG."); }
    };
    reader.readAsDataURL(f);
  };
  const hours = Array.from({ length: 24 }, (_, h) => h);
  const summary = [
    `${p.ask_budget_per_day}/day`,
    `quiet ${pad2(p.quiet_hours[0])}–${pad2(p.quiet_hours[1])}`,
    p.notify_service ? null : "no phone",
    p.notify_system ? "system updates" : null,
  ].filter(Boolean).join(" · ");

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden" }}>
      <button onClick={() => setOpen(!open)}
        style={{ display: "flex", alignItems: "center", gap: 12, width: "100%", border: "none",
                 background: open ? "var(--surface-2)" : "transparent", cursor: "pointer",
                 color: "var(--text)", padding: "12px 14px", textAlign: "left" }}>
        <Avatar name={p.name} value={p.avatar} size={40} />
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <strong style={{ fontSize: 14.5 }}>
            {p.name || "Unnamed"}
            {!p.enabled && <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-dim)",
              border: "1px solid var(--border)", borderRadius: 99, padding: "1px 7px", marginLeft: 8 }}>
              Paused</span>}
          </strong>
          <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>{summary}</span>
        </div>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden
             style={{ marginLeft: "auto", color: "var(--text-dim)",
                      transition: "transform .18s", transform: open ? "none" : "rotate(-90deg)" }}>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12,
                    borderTop: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div style={{ position: "relative" }}>
          <Avatar name={p.name} value={p.avatar} size={48} />
          <button onClick={() => fileRef.current?.click()} aria-label="Upload photo"
            title="Upload a photo"
            style={{ position: "absolute", right: -4, bottom: -4, width: 22, height: 22,
                     borderRadius: "50%", border: "1px solid var(--border)", cursor: "pointer",
                     background: "var(--surface)", display: "flex", alignItems: "center",
                     justifyContent: "center", padding: 0, color: "var(--text)" }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M14.5 4h-5L8 6H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V7a1 1 0 0 0-1-1h-4z" />
              <circle cx="12" cy="13" r="3.2" />
            </svg>
          </button>
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp"
                 onChange={onPhoto} style={{ display: "none" }} />
        </div>
        <input value={p.name} onChange={(e) => u({ name: e.target.value })}
               style={{ fontWeight: 600, fontSize: 15, maxWidth: 200 }} />
        <div style={{ display: "flex", gap: 6, marginLeft: "auto", flexWrap: "wrap" }}>
          {Object.keys(PRESET_HUES).map((hue) => (
            <button key={hue} onClick={() => u({ avatar: `preset:${hue}` })}
              aria-label={`avatar ${hue}`}
              style={{ width: 20, height: 20, borderRadius: "50%", cursor: "pointer",
                       background: PRESET_HUES[hue], padding: 0,
                       border: p.avatar === `preset:${hue}`
                         ? "2px solid var(--text)" : "2px solid transparent" }} />
          ))}
        </div>
      </div>
      {photoErr && <span style={{ fontSize: 12.5, color: "var(--danger)" }}>{photoErr}</span>}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
        <Row label="Notify service" hint="HA → Developer tools → Actions → “notify.mobile_app…”. Empty = no phone.">
          <input placeholder="mobile_app_phone" value={p.notify_service ?? ""}
                 onChange={(e) => u({ notify_service: e.target.value || null,
                                      has_device: !!e.target.value })} />
        </Row>
        <Row label="Max questions per day" hint="Training questions only. Set low for minimal interruptions.">
          <input type="number" min={0} max={20} value={p.ask_budget_per_day}
                 onChange={(e) => u({ ask_budget_per_day: Number(e.target.value) })}
                 style={{ width: 90 }} />
        </Row>
        <Row label="Quiet hours" hint="No notifications of any kind in this window.">
          <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <select value={p.quiet_hours[0]}
                    onChange={(e) => u({ quiet_hours: [Number(e.target.value), p.quiet_hours[1]] })}>
              {hours.map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
            </select>
            →
            <select value={p.quiet_hours[1]}
                    onChange={(e) => u({ quiet_hours: [p.quiet_hours[0], Number(e.target.value)] })}>
              {hours.map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
            </select>
          </span>
        </Row>
      </div>

      <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14, cursor: "pointer" }}>
        <input type="checkbox" checked={p.notify_system}
               onChange={(e) => u({ notify_system: e.target.checked })}
               style={{ width: 16, height: 16, marginTop: 2 }} />
        <span>
          System updates
          <span style={{ display: "block", fontSize: 12.5, color: "var(--text-dim)" }}>
            “Model trained”, “sensors stopped flowing”, milestones. Usually only whoever
            runs the homelab wants these — everyone else gets only training questions.
          </span>
        </span>
      </label>

      <SaveButton state={state} onClick={save} />

      {/* membership lifecycle: reclaim · pause (reversible) · remove & forget */}
      <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12,
                    display: "flex", flexDirection: "column", gap: 10 }}>
        {orphans.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontSize: 12.5, color: "var(--text-dim)", flex: 1, minWidth: 180 }}>
              Set up under a different name before? Reclaim that earlier history.
            </span>
            <select value={reclaimId} onChange={(e) => setReclaimId(e.target.value)}
                    style={{ fontSize: 12.5 }}>
              <option value="">Earlier identity…</option>
              {orphans.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
            <button className="btn btn-secondary" disabled={!reclaimId || busy} onClick={relink}>
              {busy ? "Reclaiming…" : "Reclaim"}
            </button>
          </div>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <button className="btn btn-ghost" onClick={() => setEnabled(!p.enabled)}>
            {p.enabled ? "Pause" : "Resume"}
          </button>
          <span style={{ fontSize: 12.5, color: "var(--text-dim)", flex: 1, minWidth: 180 }}>
            {p.enabled
              ? "Stops predicting and asking about them — keeps everything, reversible."
              : "Paused: no predictions or questions. Resume anytime; nothing was lost."}
          </span>
          {!danger && (
            <button onClick={() => setDanger(true)}
              style={{ background: "none", border: "none", cursor: "pointer", fontSize: 13,
                       color: "var(--danger)", padding: "6px 8px" }}>
              Remove…
            </button>
          )}
        </div>

        {danger && (
          <div style={{ border: "1px solid var(--danger)", borderRadius: 10, padding: 14,
                        display: "flex", flexDirection: "column", gap: 10,
                        background: "color-mix(in srgb, var(--danger) 7%, transparent)" }}>
            <strong style={{ fontSize: 14 }}>Remove {p.name} and forget everything</strong>
            <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)", lineHeight: 1.5 }}>
              Permanently erases {p.name || "this person"}: their sensors' history, labels, models
              and rules. The rest of your household is untouched, and Hearth retrains the others so
              they stop relying on {p.name || "them"}. <strong style={{ color: "var(--text)" }}>This
              can't be undone.</strong>
            </p>
            <input value={confirmName} onChange={(e) => setConfirmName(e.target.value)}
                   placeholder={`Type "${p.name}" to confirm`}
                   style={{ fontSize: 13, padding: "7px 10px" }} />
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={forget}
                disabled={busy || confirmName.trim() !== (p.name || "").trim()}
                style={{ background: "var(--danger)", color: "#fff", border: "none",
                         borderRadius: 8, padding: "8px 16px", fontSize: 13, fontWeight: 600,
                         cursor: "pointer", opacity: busy || confirmName.trim() !== (p.name || "").trim() ? 0.5 : 1 }}>
                {busy ? "Removing…" : "Remove permanently"}
              </button>
              <button className="btn btn-ghost"
                      onClick={() => { setDanger(false); setConfirmName(""); }}>Cancel</button>
            </div>
          </div>
        )}
      </div>
      </div>
      )}
    </div>
  );
}

function Household() {
  const [persons, setPersons] = useState<Person[] | null>(null);
  const [orphans, setOrphans] = useState<string[]>([]);
  const load = () => {
    fetch("/api/persons").then(j).then(setPersons).catch(() => setPersons([]));
    fetch("/api/persons/orphans").then(j).then((d) => setOrphans(d.orphans ?? [])).catch(() => {});
  };
  useEffect(() => { load(); }, []);
  return (
    <Card title="Household"
          sub="Who Hearth predicts for, and how much each person wants to hear from it.">
      {persons === null && <p style={{ color: "var(--text-dim)", fontSize: 14 }}>Loading…</p>}
      {persons?.map((p) => <PersonCard key={p.id} p={p} onChanged={load} orphans={orphans} />)}
    </Card>
  );
}

// ── connections ─────────────────────────────────────────────────────────────

const LLM_MODELS = [
  ["openai/gpt-4o-mini", "GPT-4o mini — fast & cheap (default)"],
  ["anthropic/claude-sonnet-4.6", "Claude Sonnet 4.6 — best mapping quality"],
  ["openai/gpt-4o", "GPT-4o"],
  ["google/gemini-flash-1.5", "Gemini Flash"],
] as const;

type LlmUsage = { calls: number; input_tokens: number; output_tokens: number;
                  est_usd: number; since?: string; last_at?: string };

function ConnectionCard({ kind, title, sub, fields }: {
  kind: string; title: string; sub: string;
  fields: { key: string; label: string; hint?: string; fromOptions?: boolean }[];
}) {
  const [conn, setConn] = useState<Record<string, string>>({});
  const [masked, setMasked] = useState<string | null>(null);
  const [state, setState] = useState<SaveState>("idle");
  const [restarting, setRestarting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [llmStatus, setLlmStatus] = useState<{ ok: boolean; code: number } | null>(null);
  const [usage, setUsage] = useState<LlmUsage | null>(null);
  const [uiPw, setUiPw] = useState("");
  const [uiState, setUiState] = useState<SaveState>("idle");
  const setInfluxUiPw = async () => {
    setUiState("saving");
    try { await post("/api/influx/ui-password", { password: uiPw }).then(j); setUiState("ok"); setUiPw(""); }
    catch { setUiState("fail"); }
  };
  const needsRestart = kind === "ha" || kind === "influx";
  const loadConn = () => fetch(`/api/connections/${kind}`).then(j).then((c) => {
    if (!c.configured) return;
    const init: Record<string, string> = { url: c.url ?? "" };
    for (const f of fields) if (f.fromOptions) init[f.key] = c.options?.[f.key] ?? "";
    setConn(init);
    setMasked(c.token_masked ?? null);
    setLlmStatus(c.status ?? null);
    setUsage(c.usage ?? null);
  }).catch(() => {});
  useEffect(() => { loadConn(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [kind]);
  const resetUsage = async () => {
    if (!window.confirm("Reset the AI usage counter to zero?")) return;
    await fetch("/api/llm/usage/reset", { method: "POST" });
    loadConn();
  };
  // After a top-up / new key: persist the key (if freshly typed), re-queue the
  // sensor mapping that fell back to basic rules, then poll until the remap's
  // live LLM calls report the key healthy — at which point the banner clears.
  const tryAgain = async () => {
    setRetrying(true);
    try {
      if (conn.token) {
        const options: Record<string, string> = {};
        for (const f of fields) if (f.fromOptions) options[f.key] = conn[f.key] ?? "";
        await post(`/api/connections/${kind}`, {
          url: conn.url ?? "https://openrouter.ai/api/v1", token: conn.token,
          ...(Object.keys(options).length ? { options } : {}),
        }).then(j);
      }
      await post("/api/llm/retry", {}).then(j);
    } catch { setRetrying(false); return; }
    let tries = 0;
    const id = setInterval(async () => {
      tries += 1;
      try {
        const c = await fetch(`/api/connections/${kind}`).then(j);
        setLlmStatus(c.status ?? null);
        setUsage(c.usage ?? null);
        if (c.status?.ok || tries >= 24) { clearInterval(id); setRetrying(false); }
      } catch { /* remap still running — keep polling */ }
    }, 2500);
  };
  const save = async () => {
    setState("saving");
    const options: Record<string, string> = {};
    for (const f of fields) if (f.fromOptions) options[f.key] = conn[f.key] ?? "";
    try {
      await post(`/api/connections/${kind}`, {
        url: conn.url ?? (kind === "llm" ? "https://openrouter.ai/api/v1" : ""),
        token: conn.token ?? "",
        ...(Object.keys(options).length ? { options } : {}),
      }).then(j);
      setState("ok");
    } catch { setState("fail"); }
  };
  const llmBad = kind === "llm" && llmStatus && !llmStatus.ok;
  const llmMsg = llmStatus?.code === 402 ? "out of credit"
    : llmStatus?.code === 429 ? "rate-limited"
    : "rejected (check the key)";
  return (
    <Card title={title} sub={sub}>
      {llmBad && (
        <div style={{ padding: "9px 12px", borderRadius: 8, fontSize: 13,
                      display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
                      background: "color-mix(in srgb, var(--danger) 12%, transparent)",
                      border: "1px solid var(--danger)" }}>
          <span style={{ flex: 1, minWidth: 200 }}>
            Your AI key is <strong>{llmMsg}</strong> — sensor mapping fell back to the basic rules.{" "}
            {(conn.url ?? "").includes("openrouter")
              ? <a href="https://openrouter.ai/credits" target="_blank" rel="noopener">Top up OpenRouter →</a>
              : "Update the key below."}
          </span>
          <button className="btn btn-secondary" style={{ fontSize: 12.5 }}
                  disabled={retrying} onClick={tryAgain}>
            {retrying ? "Re-mapping…" : "Try again"}
          </button>
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
        {fields.map((f) => (
          <Row key={f.key} label={f.label} hint={f.hint}>
            {f.key === "model" ? (
              <select value={conn.model ?? LLM_MODELS[0][0]}
                      onChange={(e) => setConn({ ...conn, model: e.target.value })}>
                {LLM_MODELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            ) : (
              <input
                type={f.key === "token" ? "password" : "text"}
                placeholder={f.key === "token" ? (masked ?? "paste token") : undefined}
                value={conn[f.key] ?? ""}
                onChange={(e) => setConn({ ...conn, [f.key]: e.target.value })}
              />
            )}
          </Row>
        ))}
      </div>
      {kind === "influx" && (conn.url ?? "").includes("influxdb:8086") && (() => {
        const webUrl = `${window.location.protocol}//${window.location.hostname}:8086`;
        const org = conn.org || "hearth";
        return (
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12,
                        display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Bundled database access</div>
            <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
              Log in to InfluxDB directly to browse raw data, run Flux, or add buckets.
            </span>
            {[["Web UI", <a key="u" href={webUrl} target="_blank" rel="noreferrer"
                            style={{ color: "var(--accent)" }}>{webUrl} ↗</a>],
              ["Username", <code key="n">hearth</code>],
              ["Organization", <code key="o">{org}</code>]]
              .map(([k, v]) => (
              <div key={k as string} style={{ display: "flex", gap: 10, fontSize: 12.5 }}>
                <span style={{ color: "var(--text-dim)", minWidth: 96 }}>{k}</span>
                <span>{v}</span>
              </div>
            ))}
            <div style={{ display: "flex", gap: 10, fontSize: 12.5, alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ color: "var(--text-dim)", minWidth: 96 }}>Password</span>
              <input type="password" placeholder="set a web-UI password (min 8)" value={uiPw}
                     onChange={(e) => { setUiPw(e.target.value); setUiState("idle"); }}
                     style={{ flex: "1 1 180px", minWidth: 160 }} />
              <button className="btn btn-secondary" style={{ fontSize: 12.5 }}
                      disabled={uiPw.length < 8 || uiState === "saving"} onClick={setInfluxUiPw}>
                {uiState === "saving" ? "Setting…" : "Set"}
              </button>
            </div>
            <span style={{ fontSize: 11.5, color: uiState === "fail" ? "var(--danger)" : "var(--text-dim)" }}>
              {uiState === "ok" ? "Password set — log in as “hearth” with it."
                : uiState === "fail" ? "Couldn't set the password — check the logs."
                : "Hearth sets this on the database for you (it doesn't store it). Reset it here anytime."}
            </span>
          </div>
        );
      })()}
      {kind === "llm" && usage && usage.calls > 0 && (
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12,
                      display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>Usage so far</span>
            <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={resetUsage}>Reset</button>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))", gap: 10 }}>
            {[[`~$${usage.est_usd.toFixed(usage.est_usd < 1 ? 3 : 2)}`, "estimated spend"],
              [usage.calls.toLocaleString(), `AI call${usage.calls !== 1 ? "s" : ""}`],
              [(usage.input_tokens + usage.output_tokens).toLocaleString(), "tokens"]].map(([n, l]) => (
              <div key={l} style={{ textAlign: "center", padding: "8px 6px", borderRadius: 8,
                                    background: "var(--surface-2)" }}>
                <div style={{ fontSize: 18, fontWeight: 600 }}>{n}</div>
                <div style={{ fontSize: 11.5, color: "var(--text-dim)" }}>{l}</div>
              </div>
            ))}
          </div>
          <span style={{ fontSize: 11.5, color: "var(--text-dim)" }}>
            Rough estimate from token counts at approximate prices — your provider's bill is the
            source of truth. Predictions are local and free; this is setup &amp; maintenance only.
          </span>
        </div>
      )}
      <SaveButton state={state} onClick={save} />
      {state === "ok" && needsRestart && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
            Saved — this connection applies after a restart.
          </span>
          <button className="btn btn-secondary" disabled={restarting}
                  onClick={() => restartAndReload(setRestarting)}>
            {restarting ? "Restarting…" : "Restart now"}
          </button>
        </div>
      )}
    </Card>
  );
}

// ── api tokens (HA integration) ─────────────────────────────────────────────

type TokenInfo = {
  id: number; name: string; scope: string;
  created_at: string | null; last_used_at: string | null; revoked: boolean;
};

function ApiTokens() {
  const [tokens, setTokens] = useState<TokenInfo[]>([]);
  const [name, setName] = useState("Home Assistant");
  const [fresh, setFresh] = useState<string | null>(null);   // plaintext, shown once
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = () => fetch("/api/tokens").then(j).then(setTokens).catch(() => {});
  useEffect(() => { load(); }, []);
  const mint = async () => {
    setBusy(true); setFresh(null); setCopied(false);
    try {
      const r = await post("/api/tokens", { name }).then(j);
      setFresh(r.token);
      load();
    } catch { /* surfaced by absence of token */ }
    setBusy(false);
  };
  const revoke = async (t: TokenInfo) => {
    if (!window.confirm(`Revoke “${t.name}”? Anything using it (your HA integration) stops working until you paste a new token there.`)) return;
    await fetch(`/api/tokens/${t.id}`, { method: "DELETE" });
    load();
  };
  const fmt = (iso: string | null) => iso ? new Date(iso).toLocaleDateString() : "never";
  return (
    <Card title="API tokens"
          sub="For the Home Assistant integration. Mint one here, paste it into HA when the Hearth integration asks. Tokens can only read predictions and receive notification answers.">
      {tokens.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {tokens.map((t) => (
            <div key={t.id} style={{ display: "flex", alignItems: "center", gap: 12,
                                     padding: "8px 12px", border: "1px solid var(--border)",
                                     borderRadius: 10, fontSize: 13.5,
                                     opacity: t.revoked ? 0.5 : 1 }}>
              <Icon name="key" size={15} />
              <span style={{ fontWeight: 500 }}>{t.name}</span>
              <span style={{ color: "var(--text-dim)" }}>
                created {fmt(t.created_at)} · last used {fmt(t.last_used_at)}
              </span>
              {t.revoked
                ? <span style={{ marginLeft: "auto", color: "var(--text-dim)" }}>revoked</span>
                : <button className="btn btn-ghost" style={{ marginLeft: "auto", minHeight: 28, padding: "2px 10px", fontSize: 12.5, color: "var(--danger)" }}
                          onClick={() => revoke(t)}>Revoke</button>}
            </div>
          ))}
        </div>
      )}
      {fresh && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <code style={{ flex: 1, padding: "10px 12px", background: "var(--surface-2)",
                           border: "1px solid var(--accent)", borderRadius: 8, fontSize: 13,
                           overflowWrap: "anywhere" }}>{fresh}</code>
            <button className="btn btn-secondary" aria-label="Copy token"
                    onClick={() => { navigator.clipboard.writeText(fresh); setCopied(true); }}>
              {copied ? "Copied ✓" : <Icon name="copy" size={16} />}
            </button>
          </div>
          <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
            Copy it now — for your security it's shown only this once.
          </p>
        </div>
      )}
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <input value={name} onChange={(e) => setName(e.target.value)}
               placeholder="Token name" style={{ maxWidth: 220 }} />
        <button className="btn btn-primary" disabled={busy || !name.trim()} onClick={mint}>
          {busy ? "Generating…" : "Generate token"}
        </button>
      </div>
    </Card>
  );
}

// ── model behaviour ─────────────────────────────────────────────────────────

const TIME_CHOICES: [string, string, string][] = [
  ["coarse", "Part of day (recommended)", "Night / morning / afternoon / evening — keeps the useful 'it's night-ish' prior without letting the model memorize a per-hour schedule."],
  ["full", "Exact hour", "Raw 0–23 hour. More precise, but the model can lean on the clock instead of reading sensors."],
  ["none", "Ignore time", "No time feature at all — predictions come purely from sensors. Strictest, needs the most labels."],
];

function ModelBehaviour() {
  const [tg, setTg] = useState<string>("coarse");
  const [state, setState] = useState<SaveState>("idle");
  useEffect(() => {
    fetch("/api/settings/model").then(j)
      .then((r) => setTg(r.time_granularity ?? "coarse")).catch(() => {});
  }, []);
  const save = async (value: string) => {
    setTg(value); setState("saving");
    try { await post("/api/settings/model", { time_granularity: value }).then(j); setState("ok"); }
    catch { setState("fail"); }
  };
  return (
    <Card title="How much the model trusts the clock"
          sub="Hearth can lean on time-of-day as a shortcut instead of reading your sensors. Coarser time forces it to use presence, bed and media signals.">
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {TIME_CHOICES.map(([value, label, desc]) => (
          <label key={value} style={{ display: "flex", gap: 10, alignItems: "flex-start",
                                      padding: "10px 12px", borderRadius: 10, cursor: "pointer",
                                      border: `1px solid ${tg === value ? "var(--accent)" : "var(--border)"}` }}>
            <input type="radio" name="tg" checked={tg === value}
                   onChange={() => save(value)} style={{ marginTop: 3 }} />
            <span>
              <span style={{ fontWeight: 500, fontSize: 14 }}>{label}</span>
              <span style={{ display: "block", fontSize: 12.5, color: "var(--text-dim)" }}>{desc}</span>
            </span>
          </label>
        ))}
      </div>
      <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
        {state === "ok" ? "Saved — retrain (Models → Train now) to apply; the feature set changed."
          : state === "fail" ? "Couldn't save — check logs."
          : "Changing this alters the feature set, so a retrain is needed to take effect."}
      </p>
    </Card>
  );
}

// ── AI & model levers ───────────────────────────────────────────────────────

function ChoiceList({ value, choices, onPick }: {
  value: string; choices: [string, string, string][]; onPick: (v: string) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {choices.map(([v, label, desc]) => (
        <label key={v} style={{ display: "flex", gap: 10, alignItems: "flex-start",
                                padding: "10px 12px", borderRadius: 10, cursor: "pointer",
                                border: `1px solid ${value === v ? "var(--accent)" : "var(--border)"}` }}>
          <input type="radio" checked={value === v} onChange={() => onPick(v)} style={{ marginTop: 3 }} />
          <span>
            <span style={{ fontWeight: 500, fontSize: 14 }}>{label}</span>
            <span style={{ display: "block", fontSize: 12.5, color: "var(--text-dim)" }}>{desc}</span>
          </span>
        </label>
      ))}
    </div>
  );
}

function savedNote(state: SaveState, retrain = false): string {
  if (state === "ok") return retrain ? "Saved — applies on the next training run." : "Saved ✓";
  if (state === "fail") return "Couldn't save — check logs.";
  return "";
}

const CONSENT_CHOICES: [string, string, string][] = [
  ["yes", "Share aggregate stats (recommended)",
   "The assistant sees per-sensor summaries — how often each changes, its value range, how often it's missing, a few example states. NEVER your raw history or a timeline. Lets it flag broken sensors and pick better features."],
  ["no", "Metadata only",
   "The assistant sees only sensor names, types and units (the labels shown in Home Assistant). Most private; it can't detect unreliable sensors and guesses features from names alone."],
];

function StatsConsent() {
  const [share, setShare] = useState<string | null>(null);
  const [state, setState] = useState<SaveState>("idle");
  useEffect(() => {
    fetch("/api/stats-consent").then(j)
      .then((r) => setShare(r.decided ? (r.share_stats ? "yes" : "no") : ""))
      .catch(() => setShare(""));
  }, []);
  const choose = async (v: string) => {
    setShare(v); setState("saving");
    try { await post("/api/stats-consent", { share: v === "yes" }).then(j); setState("ok"); }
    catch { setState("fail"); }
  };
  return (
    <Card title="AI assistant: data sharing"
          sub="What the optional AI assistant may see when it maps sensors and designs features. Your choice; change it any time.">
      {share === null ? <p style={{ color: "var(--text-dim)", fontSize: 14 }}>Loading…</p>
        : <ChoiceList value={share} choices={CONSENT_CHOICES} onPick={choose} />}
      <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>{savedNote(state)}</p>
    </Card>
  );
}

const FAMILY_LABELS: Record<string, [string, string]> = {
  random_forest: ["Random forest (default)", "Robust on small data, no tuning needed, interpretable. The recommended starting point."],
  gradient_boosting: ["Gradient boosted trees", "Usually a few points stronger once you have plenty of labels, but easier to overfit early."],
  logistic: ["Logistic regression", "A simple linear baseline — fast and very stable; mainly a sanity check."],
  embedding: ["Learned embeddings (experimental)", "Classifies in a self-supervised embedding space (the JEPA / world-model direction). Behaves like random forest until a HEPA-style encoder is installed — research preview."],
};

function ModelFamily() {
  const [family, setFamily] = useState<string | null>(null);
  const [families, setFamilies] = useState<string[]>([]);
  const [state, setState] = useState<SaveState>("idle");
  useEffect(() => {
    fetch("/api/model-family").then(j)
      .then((r) => { setFamily(r.family); setFamilies(r.families ?? []); }).catch(() => setFamily("random_forest"));
  }, []);
  const save = async (f: string) => {
    setFamily(f); setState("saving");
    try { await post("/api/model-family", { family: f }).then(j); setState("ok"); }
    catch { setState("fail"); }
  };
  const choices = families.map((f) => [f, FAMILY_LABELS[f]?.[0] ?? f,
                                       FAMILY_LABELS[f]?.[1] ?? ""] as [string, string, string]);
  return (
    <Card title="Model family" sub="The classifier Hearth trains. Random forest is the safe default.">
      {family === null ? <p style={{ color: "var(--text-dim)", fontSize: 14 }}>Loading…</p>
        : <ChoiceList value={family} choices={choices} onPick={save} />}
      <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>{savedNote(state, true)}</p>
    </Card>
  );
}

const POWER_CHOICES: [string, string, string][] = [
  ["conservative", "Conservative (recommended)",
   "The AI picks sensor roles and basic combinations only. Smallest, safest feature set."],
  ["full", "Full",
   "The AI may also design richer per-sensor transforms (slopes, baselines, sequences). More powerful, slightly more risk of noisy features."],
];

function FeaturePower() {
  const [mode, setMode] = useState<string | null>(null);
  const [state, setState] = useState<SaveState>("idle");
  useEffect(() => {
    fetch("/api/feature-power").then(j).then((r) => setMode(r.mode)).catch(() => setMode("conservative"));
  }, []);
  const save = async (m: string) => {
    setMode(m); setState("saving");
    try { await post("/api/feature-power", { mode: m }).then(j); setState("ok"); }
    catch { setState("fail"); }
  };
  return (
    <Card title="Feature engineering power"
          sub="How much freedom the AI assistant has when designing features from your sensors.">
      {mode === null ? <p style={{ color: "var(--text-dim)", fontSize: 14 }}>Loading…</p>
        : <ChoiceList value={mode} choices={POWER_CHOICES} onPick={save} />}
      <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
        {state === "ok" ? "Saved — applies the next time you run an AI analysis." : savedNote(state)}
      </p>
    </Card>
  );
}

// ── data history retention ───────────────────────────────────────────────────

const RETENTION_PRESETS: [number, string][] = [
  [45, "45 days"], [90, "90 days (recommended)"], [180, "6 months"],
  [365, "1 year"], [0, "Keep forever"],
];

function DataRetention() {
  const [days, setDays] = useState<number | null>(null);
  const [state, setState] = useState<SaveState>("idle");
  const [note, setNote] = useState<string>("");
  useEffect(() => {
    fetch("/api/settings/retention").then(j).then((r) => setDays(r.days)).catch(() => setDays(90));
  }, []);
  const save = async (value: number) => {
    setDays(value); setState("saving"); setNote("");
    try {
      const r = await post("/api/settings/retention", { days: value }).then(j);
      setState("ok"); setNote(r.note ?? "");
    } catch { setState("fail"); }
  };
  // a custom value (not in the preset list) is shown as its own option so the
  // select still reflects the true setting
  const known = RETENTION_PRESETS.some(([d]) => d === days);
  return (
    <Card title="Raw signal retention"
          sub="The model data — the features every training run learns from — and your predictions and confirmed labels are kept FOREVER. This only bounds RAW sensor events, which are just the source features are built from and the look-back for the live coverage/behaviour views. Trade-off: after a feature-set change, history can only be rebuilt as far back as raw is kept.">
      <Row label="Keep raw signal for"
           hint="Applies to InfluxDB immediately when connected. Shortening it deletes raw older than the window — that can't be undone. Features and labels are unaffected.">
        <select value={days ?? 90} onChange={(e) => save(Number(e.target.value))}
                disabled={days === null} style={{ maxWidth: 260 }}>
          {!known && days !== null && (
            <option value={days}>{days === 0 ? "Keep forever" : `${days} days (custom)`}</option>
          )}
          {RETENTION_PRESETS.map(([d, label]) => <option key={d} value={d}>{label}</option>)}
        </select>
      </Row>
      <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
        {state === "ok" ? (note || "Saved ✓")
          : state === "fail" ? "Couldn't save — check logs."
          : "Raw events beyond this age are dropped by InfluxDB; features and labels are kept forever."}
      </p>
    </Card>
  );
}

// ── training look-back window ────────────────────────────────────────────────

const TRAIN_WEEKS_PRESETS: [number, string][] = [
  [8, "8 weeks (default)"], [26, "6 months"], [52, "1 year"],
  [104, "2 years"], [0, "All retained history"],
];

function TrainingWindow() {
  const [weeks, setWeeks] = useState<number | null>(null);
  const [state, setState] = useState<SaveState>("idle");
  useEffect(() => {
    fetch("/api/training-window").then(j).then((r) => setWeeks(r.weeks)).catch(() => setWeeks(8));
  }, []);
  const save = async (value: number) => {
    setWeeks(value); setState("saving");
    try { await post("/api/training-window", { weeks: value }).then(j); setState("ok"); }
    catch { setState("fail"); }
  };
  const known = TRAIN_WEEKS_PRESETS.some(([w]) => w === weeks);
  return (
    <Card title="How far back training learns"
          sub="Each training run reads this much feature history. More history lets the model learn slow and seasonal routines and smooths out odd weeks, but training takes longer. Recent windows already count more (recency weighting), so old data refines rather than dominates.">
      <Row label="Training window"
           hint="Capped by what's still retained above — you can't train on data InfluxDB has dropped. Applies on the next training run.">
        <select value={weeks ?? 8} onChange={(e) => save(Number(e.target.value))}
                disabled={weeks === null} style={{ maxWidth: 260 }}>
          {!known && weeks !== null && (
            <option value={weeks}>{weeks === 0 ? "All retained history" : `${weeks} weeks (custom)`}</option>
          )}
          {TRAIN_WEEKS_PRESETS.map(([w, label]) => <option key={w} value={w}>{label}</option>)}
        </select>
      </Row>
      <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
        {state === "ok" ? "Saved — applies on the next training run (Train now to apply at once)."
          : state === "fail" ? "Couldn't save — check logs."
          : "Longer = more data per run, slower fit."}
      </p>
    </Card>
  );
}

const ADV_FIELDS: { key: string; label: string; hint: string; step: number }[] = [
  { key: "promotion_margin", label: "Promotion margin", step: 0.01,
    hint: "How much worse a new model may be before it's rejected (CI slack). Lower = stricter." },
  { key: "recency_half_life_days", label: "Recency half-life (days)", step: 1,
    hint: "How fast old data fades in training. Lower = reacts faster to routine changes, remembers less." },
  { key: "val_days", label: "Validation window (days)", step: 1,
    hint: "Days held out to score each model. 7 = one full weekly cycle." },
  { key: "tune_every_days", label: "Re-tune every (days)", step: 1,
    hint: "How often hyper-parameters are re-tuned. Monthly avoids chasing weekly noise." },
  { key: "min_confirmed_for_validated", label: "Labels to validate", step: 1,
    hint: "Confirmed labels needed before a model is called “validated” instead of “provisional”." },
];

function AdvancedTraining() {
  const [open, setOpen] = useState(false);
  const [cfg, setCfg] = useState<Record<string, number> | null>(null);
  const [state, setState] = useState<SaveState>("idle");
  useEffect(() => {
    fetch("/api/settings/training-config").then(j).then((r) => setCfg(r.config)).catch(() => {});
  }, []);
  const saveField = async (key: string, value: number) => {
    setState("saving");
    try {
      const r = await post("/api/settings/training-config", { [key]: value }).then(j);
      setCfg(r.config); setState("ok");
    } catch { setState("fail"); }
  };
  return (
    <Card title="Advanced model tuning"
          sub="Sensible defaults — most homes never touch these. Each takes effect on the next training run.">
      <button className="btn btn-ghost" style={{ alignSelf: "flex-start" }}
              onClick={() => setOpen(!open)}>{open ? "Hide advanced" : "Show advanced"}</button>
      {open && cfg && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {ADV_FIELDS.map((f) => (
            <Row key={f.key} label={f.label} hint={f.hint}>
              <input type="number" step={f.step} defaultValue={cfg[f.key]}
                     style={{ width: 120 }}
                     onBlur={(e) => { const v = Number(e.target.value);
                       if (!Number.isNaN(v) && v !== cfg[f.key]) saveField(f.key, v); }} />
            </Row>
          ))}
          <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>{savedNote(state, true)}</p>
        </div>
      )}
    </Card>
  );
}

const ASK_FIELDS: { key: string; label: string; hint: string; step: number }[] = [
  { key: "ask_threshold", label: "Ask below confidence", step: 0.05,
    hint: "Ask when the top guess is below this confidence. Higher = asks more often." },
  { key: "margin_threshold", label: "Ask on close calls (margin)", step: 0.05,
    hint: "Ask when the top two guesses are within this gap. Higher = asks more on toss-ups." },
  { key: "epsilon", label: "Random spot-check rate", step: 0.01,
    hint: "Fraction of confident windows asked anyway, at random. These power the unbiased real-world accuracy — don't set it to 0." },
  { key: "cooldown_min", label: "Min minutes between questions", step: 5,
    hint: "How long Hearth waits after any question before asking again." },
  { key: "repeat_min", label: "Min minutes before repeating", step: 5,
    hint: "How long before Hearth re-asks about the same activity." },
  { key: "expire_hours", label: "Question expiry (hours)", step: 1,
    hint: "Unanswered questions disappear from the inbox after this long." },
];

/** Vacation / do-not-disturb: point Hearth at any HA boolean; while it's ON,
 *  every phone push is muted. Predictions and the Inbox keep working. */
function MuteCard() {
  const [entity, setEntity] = useState("");
  const [muted, setMuted] = useState(false);
  const [state, setState] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    fetch("/api/notify/mute").then(j)
      .then((r) => { setEntity(r.entity || ""); setMuted(!!r.muted); setState(r.state ?? null); })
      .catch(() => {});
  }, []);
  const save = async () => {
    setSaving(true); setMsg("");
    try {
      const r = await post("/api/notify/mute", { entity: entity.trim() }).then(j);
      setMuted(!!r.muted); setState(r.state ?? null);
      setMsg(r.warning ? r.warning
        : !r.entity ? "Cleared — pushes always go out."
        : r.muted ? "Saved — and it's ON right now, so pushes are muted."
        : `Saved — currently ${r.state ?? "off"}, pushes go out normally.`);
    } catch { setMsg("Couldn't save — is the backend up?"); }
    setSaving(false);
  };
  return (
    <Card title="Vacation & do-not-disturb"
          sub="Pick any Home Assistant toggle (a vacation mode, guest mode…). While it's on, Hearth sends no phone notifications — questions wait quietly in the Inbox, predictions and automations keep running.">
      {muted && (
        <p style={{ margin: 0, fontSize: 13, color: "var(--accent)", fontWeight: 500 }}>
          Muted right now — {entity} is on. No pushes until it turns off.
        </p>
      )}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input placeholder="input_boolean.vacation_mode" value={entity}
               onChange={(e) => setEntity(e.target.value)}
               style={{ flex: 1, minWidth: 220 }} />
        <button className="btn btn-secondary" disabled={saving} onClick={save}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      {msg && <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>{msg}</span>}
      <p style={{ margin: 0, fontSize: 11.5, color: "var(--text-dim)" }}>
        Fail-open by design: if Hearth can't read the entity, it sends rather than
        going silently mute. Leave empty to disable. State now: {state ?? "—"}.
      </p>
    </Card>
  );
}

function AdvancedAsking() {
  const [open, setOpen] = useState(false);
  const [pol, setPol] = useState<Record<string, number> | null>(null);
  const [state, setState] = useState<SaveState>("idle");
  useEffect(() => {
    fetch("/api/settings/asking-policy").then(j).then((r) => setPol(r.policy)).catch(() => {});
  }, []);
  const saveField = async (key: string, value: number) => {
    setState("saving");
    try {
      const r = await post("/api/settings/asking-policy", { [key]: value }).then(j);
      setPol(r.policy); setState("ok");
    } catch { setState("fail"); }
  };
  return (
    <Card title="Advanced — how Hearth asks"
          sub="Sensible defaults — most homes never touch these. Applies to new questions right away.">
      <button className="btn btn-ghost" style={{ alignSelf: "flex-start" }}
              onClick={() => setOpen(!open)}>{open ? "Hide advanced" : "Show advanced"}</button>
      {open && pol && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {ASK_FIELDS.map((f) => (
            <Row key={f.key} label={f.label} hint={f.hint}>
              <input type="number" step={f.step} defaultValue={pol[f.key]} style={{ width: 120 }}
                     onBlur={(e) => { const v = Number(e.target.value);
                       if (!Number.isNaN(v) && v !== pol[f.key]) saveField(f.key, v); }} />
            </Row>
          ))}
          <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>{savedNote(state)}</p>
        </div>
      )}
    </Card>
  );
}

type CoveragePoint = { t: number; coverage: number; precision: number | null };

function OutputPolicy() {
  const [enabled, setEnabled] = useState(true);
  const [threshold, setThreshold] = useState(0.4);
  const [state, setState] = useState<SaveState>("idle");
  const [curve, setCurve] = useState<CoveragePoint[]>([]);
  useEffect(() => {
    fetch("/api/output-policy").then(j)
      .then((r) => { setEnabled(r.abstain_enabled); setThreshold(r.abstain_threshold); }).catch(() => {});
    // coverage/precision curve from the live model → "set with preview" (UX6)
    fetch("/api/models").then(j).then((ms: { promoted: boolean; node?: string;
      metrics?: { coverage_curve?: CoveragePoint[] } }[]) => {
      const live = ms.find((m) => m.promoted && m.metrics?.coverage_curve);
      if (live?.metrics?.coverage_curve) setCurve(live.metrics.coverage_curve);
    }).catch(() => {});
  }, []);
  const pt = curve.length
    ? curve.reduce((b, c) => Math.abs(c.t - threshold) < Math.abs(b.t - threshold) ? c : b)
    : null;
  const save = async (patch: { abstain_enabled?: boolean; abstain_threshold?: number }) => {
    setState("saving");
    try {
      const r = await post("/api/output-policy", patch).then(j);
      setEnabled(r.abstain_enabled); setThreshold(r.abstain_threshold); setState("ok");
    } catch { setState("fail"); }
  };
  return (
    <Card title="When Hearth commits to a guess"
          sub="Below a confidence level Hearth publishes “unknown” instead of guessing, so automations don't act on a shaky prediction.">
      <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14, cursor: "pointer" }}>
        <input type="checkbox" checked={enabled} style={{ width: 16, height: 16, marginTop: 2 }}
               onChange={(e) => { setEnabled(e.target.checked); save({ abstain_enabled: e.target.checked }); }} />
        <span>Emit an “unknown” state when unsure
          <span style={{ display: "block", fontSize: 12.5, color: "var(--text-dim)" }}>
            Recommended — a wrong guess can trigger the wrong automation; “unknown” does nothing.
          </span>
        </span>
      </label>
      <Row label={`Commit threshold — ${Math.round(threshold * 100)}% confidence`}
           hint="Below this, the published state is “unknown”. Higher = more cautious (more unknowns).">
        <input type="range" min={0} max={0.9} step={0.05} value={threshold} disabled={!enabled}
               onChange={(e) => setThreshold(Number(e.target.value))}
               onMouseUp={(e) => save({ abstain_threshold: Number((e.target as HTMLInputElement).value) })}
               onTouchEnd={(e) => save({ abstain_threshold: Number((e.target as HTMLInputElement).value) })} />
      </Row>
      {enabled && pt && pt.precision != null && (
        <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)",
                    background: "var(--surface-2)", borderRadius: 8, padding: "8px 12px" }}>
          On your model's recent data, Hearth would commit on{" "}
          <strong>~{Math.round(pt.coverage * 100)}%</strong> of windows and be right{" "}
          <strong>~{Math.round(pt.precision * 100)}%</strong> of those; the rest become
          “unknown” or a question. <span style={{ opacity: 0.7 }}>Indicative — moves with the slider.</span>
        </p>
      )}
      <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>{savedNote(state)}</p>
    </Card>
  );
}

// ── appearance ──────────────────────────────────────────────────────────────

function Appearance() {
  const [mode, setMode] = useState<ThemeMode>(getTheme());
  const pick = (m: ThemeMode) => { setMode(m); applyTheme(m); };
  return (
    <Card title="Appearance" sub="Follows your device by default.">
      <div style={{ display: "flex", gap: 8 }}>
        {(["system", "light", "dark"] as ThemeMode[]).map((m) => (
          <button key={m} onClick={() => pick(m)}
                  className={mode === m ? "btn btn-primary" : "btn btn-ghost"}
                  style={{ textTransform: "capitalize" }}>
            {m}
          </button>
        ))}
      </div>
    </Card>
  );
}

// ── account ─────────────────────────────────────────────────────────────────

function Account() {
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const [state, setState] = useState<SaveState>("idle");
  const [err, setErr] = useState("");
  const mismatch = pw.confirm.length > 0 && pw.next !== pw.confirm;
  const ready = pw.current && pw.next.length >= 10 && pw.next === pw.confirm;
  const save = async () => {
    setState("saving"); setErr("");
    const r = await post("/api/auth/password", { current: pw.current, new: pw.next });
    if (r.ok) { setState("ok"); setPw({ current: "", next: "", confirm: "" }); }
    else {
      setState("fail");
      setErr(r.status === 403 ? "Current password is wrong."
           : "Couldn't change the password — try again.");
    }
  };
  return (
    <Card title="Account" sub="Changing your password signs out every other device. This browser stays signed in.">
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
        <Row label="Current password">
          <input type="password" value={pw.current} autoComplete="current-password"
                 onChange={(e) => { setPw({ ...pw, current: e.target.value }); setState("idle"); }} />
        </Row>
        <Row label="New password" hint="At least 10 characters.">
          <input type="password" value={pw.next} autoComplete="new-password"
                 onChange={(e) => { setPw({ ...pw, next: e.target.value }); setState("idle"); }} />
        </Row>
        <Row label="Repeat new password">
          <input type="password" value={pw.confirm} autoComplete="new-password"
                 onChange={(e) => { setPw({ ...pw, confirm: e.target.value }); setState("idle"); }} />
        </Row>
      </div>
      {mismatch && <p style={{ margin: 0, fontSize: 13, color: "var(--danger)" }}>Passwords don't match yet.</p>}
      {err && <p style={{ margin: 0, fontSize: 13, color: "var(--danger)" }}>{err}</p>}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button className="btn btn-primary" disabled={!ready || state === "saving"} onClick={save}>
          {state === "saving" ? "Changing…" : "Change password"}
        </button>
        {state === "ok" && <span style={{ color: "var(--ok, #34D399)", fontSize: 13 }}>Password changed ✓</span>}
      </div>
    </Card>
  );
}

function TwoFactor() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [setup, setSetup] = useState<{ secret: string; uri: string; qr: string | null } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);  // shown once
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const load = () => fetch("/api/auth/2fa").then(j).then((d) => setEnabled(d.enabled)).catch(() => setEnabled(false));
  useEffect(() => { load(); }, []);
  const begin = async () => {
    setErr(""); setCodes(null);
    try { setSetup(await post("/api/auth/2fa/setup", {}).then(j)); } catch { setErr("Couldn't start setup."); }
  };
  const confirm = async () => {
    setErr("");
    try {
      const r = await post("/api/auth/2fa/enable", { code }).then(j);
      setCodes(r.recovery_codes); setSetup(null); setCode(""); load();
    } catch { setErr("That code didn't match — check the app and try again."); }
  };
  const disable = async () => {
    setErr("");
    try { await post("/api/auth/2fa/disable", { password: pw }).then(j); setPw(""); setCodes(null); load(); }
    catch { setErr("Current password is wrong."); }
  };
  if (enabled === null) return null;
  const mono: React.CSSProperties = { fontFamily: "ui-monospace, monospace", fontSize: 13 };
  return (
    <Card title="Two-factor authentication"
          sub="Add a one-time code from an authenticator app (Google Authenticator, Authy, 1Password…) on top of your password. Email handles recovery if you lose it.">
      {codes && (
        <div style={{ padding: 14, borderRadius: 10, background: "var(--surface-2)", border: "1px solid var(--accent)" }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Save your recovery codes</div>
          <p style={{ margin: "0 0 10px", fontSize: 13, color: "var(--text-dim)" }}>
            Each works once if you lose your authenticator. Store them somewhere safe — they won't be shown again.
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px,1fr))", gap: 8 }}>
            {codes.map((c) => <span key={c} style={{ ...mono, padding: "6px 8px", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 6, textAlign: "center" }}>{c}</span>)}
          </div>
          <button className="btn btn-ghost" style={{ marginTop: 10 }}
                  onClick={() => navigator.clipboard?.writeText(codes.join("\n"))}>Copy all</button>
        </div>
      )}
      {!enabled && !setup && (
        <button className="btn btn-primary" onClick={begin}>Turn on two-factor</button>
      )}
      {setup && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {setup.qr
            ? <img src={setup.qr} alt="2FA QR code" width={176} height={176}
                   style={{ borderRadius: 8, border: "1px solid var(--border)", background: "var(--surface-2)" }} />
            : <p style={{ margin: 0, fontSize: 13, color: "var(--text-dim)" }}>Add this account to your authenticator app:</p>}
          <div style={{ fontSize: 13 }}>Or enter this key manually: <span style={mono}>{setup.secret}</span></div>
          <Row label="6-digit code from the app">
            <input value={code} inputMode="numeric" placeholder="123456"
                   onChange={(e) => setCode(e.target.value)} style={{ maxWidth: 160 }} />
          </Row>
          {err && <span style={{ color: "var(--danger)", fontSize: 13 }}>{err}</span>}
          <div style={{ display: "flex", gap: 10 }}>
            <button className="btn btn-primary" disabled={!code} onClick={confirm}>Verify &amp; enable</button>
            <button className="btn btn-ghost" onClick={() => { setSetup(null); setErr(""); }}>Cancel</button>
          </div>
        </div>
      )}
      {enabled && !setup && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span style={{ color: "var(--ok, #34D399)", fontSize: 14, fontWeight: 600 }}>Two-factor is on ✓</span>
          <Row label="Current password to turn off">
            <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} style={{ maxWidth: 220 }} />
          </Row>
          {err && <span style={{ color: "var(--danger)", fontSize: 13 }}>{err}</span>}
          <button className="btn btn-ghost" style={{ color: "var(--danger)", alignSelf: "flex-start" }}
                  disabled={!pw} onClick={disable}>Turn off two-factor</button>
        </div>
      )}
    </Card>
  );
}

// ── system ──────────────────────────────────────────────────────────────────

function System() {
  const [info, setInfo] = useState<{ build?: string; behind?: number; subject?: string }>({});
  const [restarting, setRestarting] = useState(false);
  useEffect(() => {
    fetch("/api/health").then(j).then((h) => setInfo((i) => ({ ...i, build: h.build }))).catch(() => {});
    fetch("/api/system/update").then(j)
      .then((u) => setInfo((i) => ({ ...i, behind: u.behind ?? 0, subject: u.latest_subject })))
      .catch(() => {});
  }, []);
  const restart = () => {
    if (!window.confirm("Restart Hearth? It comes back in a few seconds. Needed to apply new Home Assistant or InfluxDB connections.")) return;
    restartAndReload(setRestarting);
  };
  return (
    <Card title="Version & updates"
          sub="Build, update status, and restart. Live load lives on the System page.">
      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", fontSize: 14 }}>
        <span><span style={{ color: "var(--text-dim)" }}>Version&nbsp;</span><code>{info.build ?? "…"}</code></span>
        <span>
          <span style={{ color: "var(--text-dim)" }}>Updates&nbsp;</span>
          {info.behind === undefined ? "…"
            : info.behind === 0 ? "Up to date ✓"
            : `${info.behind} commit${info.behind > 1 ? "s" : ""} behind — “${info.subject ?? ""}”`}
        </span>
      </div>
      <div>
        <button className="btn btn-secondary" disabled={restarting} onClick={restart}>
          {restarting ? "Restarting…" : "Restart Hearth"}
        </button>
      </div>
    </Card>
  );
}

// ── page ────────────────────────────────────────────────────────────────────

function DangerZone() {
  const [mode, setMode] = useState<null | "config" | "factory">(null);
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      await post("/api/system/reset", { wipe_data: mode === "factory" }).then(j);
      window.location.href = "/";          // session cleared → wizard reloads
    } catch { setBusy(false); }
  };
  return (
    <div className="card" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12,
                                   border: "1px solid var(--danger)" }}>
      <div>
        <h3 style={{ margin: 0, fontSize: 16, color: "var(--danger)" }}>Reset</h3>
        <p style={{ margin: "4px 0 0", fontSize: 13.5, color: "var(--text-dim)" }}>
          Start over with the setup wizard — handy after big changes, or to hand Hearth to someone else.
        </p>
      </div>
      {!mode && (
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <button className="btn btn-secondary" onClick={() => { setMode("config"); setConfirm(""); }}>
            Re-run setup
          </button>
          <button className="btn btn-secondary" onClick={() => { setMode("factory"); setConfirm(""); }}
                  style={{ borderColor: "var(--danger)", color: "var(--danger)" }}>
            Factory reset
          </button>
        </div>
      )}
      {mode && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <p style={{ margin: 0, fontSize: 13.5 }}>
            {mode === "config"
              ? "Clears your configuration — household, sensors, activities, rules, models and account — and re-runs the setup wizard. Your recorded sensor history is kept."
              : "Erases EVERYTHING — configuration, account, and all recorded sensor history, features and trained models. This cannot be undone."}
          </p>
          <Row label="Type RESET to confirm">
            <input value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="RESET"
                   style={{ maxWidth: 160 }} />
          </Row>
          <div style={{ display: "flex", gap: 10 }}>
            <button className="btn btn-primary" disabled={confirm !== "RESET" || busy} onClick={run}
                    style={{ background: "var(--danger)", borderColor: "var(--danger)" }}>
              {busy ? "Resetting…" : mode === "factory" ? "Erase everything" : "Reset & re-run setup"}
            </button>
            <button className="btn btn-ghost" disabled={busy} onClick={() => setMode(null)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── settings hub (tiles → sections) ──────────────────────────────────────────

// ── editable AI system prompts ───────────────────────────────────────────────

type PromptDef = {
  key: string; title: string; description: string;
  tokens: string[]; default: string; override: string | null;
};

function PromptEditor({ p, onSaved }: { p: PromptDef; onSaved: () => void }) {
  const [text, setText] = useState(p.override ?? "");
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<SaveState>("idle");
  const overridden = p.override != null;
  const save = async () => {
    setState("saving");
    try { await post("/api/prompts", { key: p.key, text }).then(j); setState("ok"); onSaved(); }
    catch { setState("fail"); }
  };
  const reset = async () => {
    if (!window.confirm(`Reset “${p.title}” to the built-in default?`)) return;
    setState("saving");
    try {
      await post("/api/prompts", { key: p.key, reset: true }).then(j);
      setText(""); setState("ok"); onSaved();
    } catch { setState("fail"); }
  };
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden" }}>
      <button onClick={() => setOpen(!open)}
        style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", border: "none",
                 background: open ? "var(--surface-2)" : "transparent", cursor: "pointer",
                 color: "var(--text)", padding: "12px 14px", textAlign: "left" }}>
        <strong style={{ fontSize: 14 }}>{p.title}</strong>
        {overridden && (
          <span style={{ fontSize: 11, padding: "1px 7px", borderRadius: 99, fontWeight: 600,
                         background: "color-mix(in srgb, var(--accent) 16%, transparent)",
                         color: "var(--accent)" }}>edited</span>
        )}
        <span style={{ fontSize: 12.5, color: "var(--text-dim)", overflow: "hidden",
                       textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.description}</span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden
             style={{ marginLeft: "auto", flexShrink: 0, color: "var(--text-dim)",
                      transition: "transform .18s", transform: open ? "none" : "rotate(-90deg)" }}>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10,
                      borderTop: "1px solid var(--border)" }}>
          {p.tokens.length > 0 && (
            <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
              Auto-filled placeholders (keep them where you want the live data):{" "}
              {p.tokens.map((t) => <code key={t} style={{ marginRight: 6 }}>{`[[${t}]]`}</code>)}
            </p>
          )}
          <textarea value={text} placeholder={p.default}
                    onChange={(e) => { setText(e.target.value); setState("idle"); }}
                    rows={12} spellCheck={false}
                    style={{ width: "100%", fontFamily: "var(--mono, monospace)", fontSize: 12.5,
                             lineHeight: 1.5, resize: "vertical", padding: 10 }} />
          <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
            Empty = use the built-in default (shown faint above as the placeholder).
            Saved edits apply the next time the AI assistant runs.
          </p>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            {text.trim() === "" && (
              <button className="btn btn-ghost" onClick={() => setText(p.default)}>
                Load default to edit
              </button>
            )}
            <SaveButton state={state} onClick={save} />
            {overridden && (
              <button className="btn btn-ghost" style={{ color: "var(--danger)" }} onClick={reset}>
                Reset to default
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function AiPrompts() {
  const [prompts, setPrompts] = useState<PromptDef[] | null>(null);
  const load = () => fetch("/api/prompts").then(j)
    .then((d) => setPrompts(d.prompts)).catch(() => setPrompts([]));
  useEffect(() => { load(); }, []);
  return (
    <Card title="AI assistant prompts"
          sub="Every instruction Hearth sends to the language model, editable. Make them stricter, soften them, or tailor them to your home. Prompts only run when an AI key is set, during setup, re-mapping, feature design and pattern naming — never during prediction. The JSON-output part lives in the text, so a careless edit can break a pass; each prompt resets in one click.">
      {prompts === null && <p style={{ color: "var(--text-dim)", fontSize: 14 }}>Loading…</p>}
      {prompts?.length === 0 && <p style={{ color: "var(--text-dim)", fontSize: 14 }}>No prompts found.</p>}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {prompts?.map((p) => <PromptEditor key={p.key} p={p} onSaved={load} />)}
      </div>
    </Card>
  );
}

type SectionKey =
  | "household" | "model" | "data" | "privacy" | "integrations" | "prompts"
  | "logs" | "account" | "general" | "methodology";

const SECTIONS: { key: SectionKey; icon: IconName; title: string; desc: string }[] = [
  { key: "household", icon: "household", title: "Household",
    desc: "People, avatars, notifications, the weekly newsletter, and how often Hearth asks." },
  { key: "model", icon: "models", title: "Model",
    desc: "How the model learns and commits: feature power, model family, clock trust, the confidence threshold, and advanced tuning." },
  { key: "data", icon: "sensors", title: "Data & labels",
    desc: "Ground-truth facts, transition markers, how long raw history is kept, and how far back training reaches." },
  { key: "privacy", icon: "lock", title: "Privacy",
    desc: "What, if anything, Hearth shares — anonymous aggregate stats to improve the defaults." },
  { key: "integrations", icon: "flow", title: "Integrations",
    desc: "Home Assistant, InfluxDB, the AI assistant, email, and API tokens for the HA integration." },
  { key: "prompts", icon: "models", title: "AI prompts",
    desc: "Read and edit every system prompt Hearth sends to the language model." },
  { key: "account", icon: "user", title: "Account",
    desc: "Your password, two-factor authentication and sign-in." },
  { key: "logs", icon: "monitor", title: "Logs",
    desc: "Recent backend activity, live." },
  { key: "general", icon: "settings", title: "General",
    desc: "Appearance, version & updates, and the danger zone." },
  { key: "methodology", icon: "info", title: "How it works",
    desc: "The Hearth pipeline, end to end." },
];

// Tiles that jump to a full page (not an in-Settings section).
const LINK_TILES: { href: string; icon: IconName; title: string; desc: string }[] = [
  { href: "/system", icon: "monitor", title: "System",
    desc: "Live load — CPU, temperature, memory, power, and what Hearth pauses under pressure." },
];

const TLS_OPTS: [string, string][] = [
  ["starttls", "STARTTLS (587)"], ["ssl", "SSL/TLS (465)"], ["none", "None"],
];

// SMTP relay for the weekly newsletter + password recovery. Outbound only — not
// a mail server. More fields + a test-send than the generic ConnectionCard, so
// it's its own panel hitting /api/settings/email.
function EmailSettings() {
  const [cfg, setCfg] = useState<Record<string, any> | null>(null);
  const [state, setState] = useState<SaveState>("idle");
  const [note, setNote] = useState("");
  const [testTo, setTestTo] = useState("");
  const load = () => fetch("/api/settings/email").then(j).then(setCfg).catch(() => setCfg({}));
  useEffect(() => { load(); }, []);
  const up = (k: string, v: any) => setCfg((c) => ({ ...(c ?? {}), [k]: v }));
  const save = async () => {
    if (!cfg) return;
    setState("saving"); setNote("");
    try {
      await post("/api/settings/email", {
        host: cfg.host, port: Number(cfg.port) || 587, username: cfg.username,
        from: cfg.from, from_name: cfg.from_name, tls: cfg.tls,
        // a value with "****" is the masked existing secret — send blank to keep it
        password: cfg.password && !String(cfg.password).includes("****") ? cfg.password : "",
      }).then(j);
      setState("ok"); setNote("Saved ✓"); load();
    } catch { setState("fail"); setNote("Couldn't save"); }
  };
  const test = async () => {
    setNote("Sending…");
    try { await post("/api/settings/email/test", { to: testTo }).then(j); setNote("Sent ✓ — check the inbox"); }
    catch { setNote("Send failed — check host/port/credentials"); }
  };
  if (!cfg) return null;
  return (
    <Card title="Email (SMTP relay)"
          sub="Sends the weekly newsletter and password-recovery mail through your own provider — e.g. Gmail with an app-password. Outbound only, not a mail server; the password is encrypted at rest.">
      <Row label="SMTP host" hint="e.g. smtp.gmail.com">
        <input value={cfg.host || ""} onChange={(e) => up("host", e.target.value)} placeholder="smtp.gmail.com" />
      </Row>
      <Row label="Port"><input value={cfg.port ?? 587} onChange={(e) => up("port", e.target.value)} style={{ maxWidth: 120 }} /></Row>
      <Row label="Encryption">
        <select value={cfg.tls || "starttls"} onChange={(e) => up("tls", e.target.value)}>
          {TLS_OPTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </Row>
      <Row label="Username"><input value={cfg.username || ""} onChange={(e) => up("username", e.target.value)} placeholder="you@gmail.com" /></Row>
      <Row label="App password" hint="Gmail → Security → App passwords. Leave blank to keep the current one.">
        <input type="password" value={cfg.password || ""} onChange={(e) => up("password", e.target.value)}
               placeholder={cfg.configured ? "•••• (unchanged)" : ""} />
      </Row>
      <Row label="From address"><input value={cfg.from || ""} onChange={(e) => up("from", e.target.value)} placeholder="you@gmail.com" /></Row>
      <Row label="From name"><input value={cfg.from_name || "Hearth"} onChange={(e) => up("from_name", e.target.value)} /></Row>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 4 }}>
        <button className="btn btn-primary" onClick={save} disabled={state === "saving"}>Save</button>
        <input value={testTo} onChange={(e) => setTestTo(e.target.value)} placeholder="test@address" style={{ maxWidth: 200 }} />
        <button className="btn btn-ghost" onClick={test} disabled={!cfg.configured || !testTo}>Send test</button>
        <span style={{ fontSize: 12.5, color: state === "fail" ? "var(--danger)" : "var(--text-dim)" }}>{note}</span>
      </div>
    </Card>
  );
}

const NL_TIERS: [string, string, string][] = [
  ["overview", "Overview", "Headline stats + the week's split. Short."],
  ["medium", "Medium", "+ rhythm heatmap, weekly shifts, top routines."],
  ["detailed", "Detailed", "+ per-day breakdown, full transitions, rest & away."],
];

// The weekly habits newspaper: pick a detail tier, preview the real design, and
// send. Opt-in + per-member email live on each person (Household / wizard).
function NewsletterDesign() {
  const [cfg, setCfg] = useState<Record<string, any> | null>(null);
  const [detail, setDetail] = useState("medium");
  const [person, setPerson] = useState("");
  const [html, setHtml] = useState("");
  const [note, setNote] = useState("");
  const load = () => fetch("/api/settings/newsletter").then(j).then((d) => {
    setCfg(d); setDetail(d.detail);
    if (d.recipients?.[0] && !person) setPerson(d.recipients[0].id);
  }).catch(() => setCfg({}));
  useEffect(() => { load(); }, []);
  // live preview — refetch the rendered HTML when tier or recipient changes
  useEffect(() => {
    const qs = new URLSearchParams({ detail, ...(person ? { person } : {}) });
    fetch(`/api/newsletter/preview?${qs}`).then((r) => r.text()).then(setHtml).catch(() => setHtml(""));
  }, [detail, person]);
  const save = (d: string) => {
    setDetail(d);
    post("/api/settings/newsletter", { detail: d }).then(j).catch(() => {});
  };
  const send = async (toAll: boolean) => {
    setNote(toAll ? "Sending to everyone…" : "Sending test…");
    try {
      const r = await post("/api/newsletter/send", toAll ? {} : { person }).then(j);
      setNote(toAll ? `Sent to ${r.sent ?? 0} member(s)` : `Sent to ${r.sent_to}`);
    } catch { setNote("Send failed — check email settings and logs"); }
  };
  if (!cfg) return null;
  const recips: { id: string; name: string }[] = cfg.recipients ?? [];
  return (
    <Card title="Weekly newsletter"
          sub="A designed weekly recap of each member's habits, emailed Sunday morning. Pick how much detail to include — more detail means a more revealing document leaving your local box. Opt-in and email address are set per member above.">
      {!cfg.email_ready && (
        <p style={{ margin: 0, fontSize: 13, color: "var(--warn, #fbbf24)" }}>
          Set up email under Settings → Integrations first — the newsletter needs an SMTP relay.
        </p>
      )}
      <Row label="Detail level">
        <span style={{ display: "inline-flex", border: "1px solid var(--border)", borderRadius: 999, overflow: "hidden" }}>
          {NL_TIERS.map(([v, l]) => (
            <button key={v} onClick={() => save(v)} title={NL_TIERS.find((t) => t[0] === v)![2]}
              style={{ border: "none", cursor: "pointer", padding: "6px 14px", fontSize: 12.5, fontWeight: 600,
                       background: detail === v ? "var(--accent)" : "transparent",
                       color: detail === v ? "#fff" : "var(--text-dim)" }}>{l}</button>
          ))}
        </span>
      </Row>
      <p style={{ margin: "0 0 4px", fontSize: 12.5, color: "var(--text-dim)" }}>
        {NL_TIERS.find((t) => t[0] === detail)?.[2]}
      </p>
      {recips.length > 0 && (
        <Row label="Preview member">
          <select value={person} onChange={(e) => setPerson(e.target.value)}>
            {recips.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </Row>
      )}
      <div style={{ border: "1px solid var(--border)", borderRadius: 10, overflow: "hidden", background: "#0f1115" }}>
        <iframe title="Newsletter preview" srcDoc={html} style={{ width: "100%", height: 520, border: "none", display: "block" }} />
      </div>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 4 }}>
        <button className="btn btn-ghost" disabled={!cfg.email_ready || !person} onClick={() => send(false)}>Send test to selected</button>
        <button className="btn btn-primary" disabled={!cfg.email_ready || recips.length === 0} onClick={() => send(true)}>Send this week's now</button>
        <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>{note}</span>
      </div>
      {recips.length === 0 && (
        <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
          No opted-in recipients yet — turn on the weekly email for a member (with an address) above.
        </p>
      )}
    </Card>
  );
}

function ConnectionsSection() {
  return (
    <>
      <ConnectionCard kind="ha" title="Home Assistant"
        sub="Where Hearth reads sensors and sends notifications."
        fields={[
          { key: "url", label: "URL" },
          { key: "token", label: "Long-lived access token", hint: "Leave empty to keep the current one." },
        ]} />
      <ConnectionCard kind="influx" title="InfluxDB"
        sub="Where raw events, features and predictions live."
        fields={[
          { key: "url", label: "URL" },
          { key: "org", label: "Organization", fromOptions: true },
          { key: "source_bucket", label: "Source bucket", hint: "Bucket with your HA history (fast-track imports).", fromOptions: true },
          { key: "token", label: "API token", hint: "Leave empty to keep the current one." },
        ]} />
      <ConnectionCard kind="llm" title="AI assistant (OpenRouter)"
        sub="Maps sensor names to roles and writes household rules. Only used during setup and re-mapping."
        fields={[
          { key: "model", label: "Model", fromOptions: true },
          { key: "token", label: "API key", hint: "Leave empty to keep the current one." },
        ]} />
      <EmailSettings />
      <ApiTokens />
    </>
  );
}

function SectionBody({ section }: { section: SectionKey }) {
  switch (section) {
    case "household": return (<><Household /><MuteCard /><NewsletterDesign /><AdvancedAsking /></>);
    case "model": return (<><FeaturePower /><ModelFamily /><ModelBehaviour /><OutputPolicy /><AdvancedTraining /></>);
    case "data": return (<><FoundationalFacts /><TransitionMarkers /><DataRetention /><TrainingWindow /></>);
    case "privacy": return <StatsConsent />;
    case "integrations": return <ConnectionsSection />;
    case "prompts": return <AiPrompts />;
    case "logs": return <Logs />;
    case "account": return (<><Account /><TwoFactor /></>);
    case "general": return (<><Appearance /><System /><DangerZone /></>);
    case "methodology": return <Methodology />;
  }
}

const hashToSection = (): SectionKey | null => {
  const h = window.location.hash.replace(/^#/, "");
  return SECTIONS.some((s) => s.key === h) ? (h as SectionKey) : null;
};

export default function Settings() {
  const [section, setSection] = useState<SectionKey | null>(hashToSection);
  useEffect(() => {
    const onHash = () => setSection(hashToSection());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const navigate = useNavigate();
  const open = (k: SectionKey) => { window.location.hash = k; setSection(k); };
  const back = () => { window.location.hash = ""; setSection(null); };

  if (section) {
    const meta = SECTIONS.find((s) => s.key === section)!;
    return (
      <section style={{ display: "flex", flexDirection: "column", gap: 18, maxWidth: 760 }}>
        <button onClick={back}
          style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: 6,
                   background: "none", border: "none", cursor: "pointer", padding: "2px 0",
                   color: "var(--text-dim)", fontSize: 13.5 }}>
          <Icon name="rollback" size={15} /> All settings
        </button>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Icon name={meta.icon} size={22} />
          <h2 style={{ margin: 0 }}>{meta.title}</h2>
        </div>
        {SectionBody({ section })}
      </section>
    );
  }

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="settings" size={22} />
        <h2 style={{ margin: 0 }}>Settings</h2>
      </div>
      <div style={{ display: "grid", gap: 14,
                    gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
        {SECTIONS.map((s) => (
          <button key={s.key} className="card" onClick={() => open(s.key)}
            style={{ textAlign: "left", cursor: "pointer", padding: 18, display: "flex",
                     flexDirection: "column", gap: 8, border: "1px solid var(--border)",
                     background: "var(--surface)", color: "var(--text)" }}>
            <span style={{ width: 38, height: 38, borderRadius: 10, display: "flex",
                           alignItems: "center", justifyContent: "center",
                           background: "color-mix(in srgb, var(--accent) 14%, transparent)",
                           color: "var(--accent)" }}>
              <Icon name={s.icon} size={20} />
            </span>
            <span style={{ fontSize: 15.5, fontWeight: 600 }}>{s.title}</span>
            <span style={{ fontSize: 13, color: "var(--text-dim)", lineHeight: 1.45 }}>{s.desc}</span>
          </button>
        ))}
        {LINK_TILES.map((t) => (
          <button key={t.href} className="card" onClick={() => navigate(t.href)}
            style={{ textAlign: "left", cursor: "pointer", padding: 18, display: "flex",
                     flexDirection: "column", gap: 8, border: "1px solid var(--border)",
                     background: "var(--surface)", color: "var(--text)" }}>
            <span style={{ width: 38, height: 38, borderRadius: 10, display: "flex",
                           alignItems: "center", justifyContent: "center",
                           background: "color-mix(in srgb, var(--accent) 14%, transparent)",
                           color: "var(--accent)" }}>
              <Icon name={t.icon} size={20} />
            </span>
            <span style={{ fontSize: 15.5, fontWeight: 600 }}>{t.title}</span>
            <span style={{ fontSize: 13, color: "var(--text-dim)", lineHeight: 1.45 }}>{t.desc}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

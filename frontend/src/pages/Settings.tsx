/**
 * Settings — household notification controls, connections, appearance,
 * account, system. Spec: docs/UI_SPEC.md §Settings.
 *
 * Design rules: one card per concern, every control explains itself,
 * Save is per-card so a half-edited page never clobbers anything.
 */
import { useEffect, useRef, useState } from "react";
import Avatar, { PRESET_HUES } from "../components/Avatar";
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

function PersonCard({ p: initial }: { p: Person }) {
  const [p, setP] = useState(initial);
  const [state, setState] = useState<SaveState>("idle");
  const [open, setOpen] = useState(false);
  const [photoErr, setPhotoErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const u = (patch: Partial<Person>) => { setP({ ...p, ...patch }); setState("idle"); };
  const save = async () => {
    setState("saving");
    try { await post("/api/persons", p).then(j); setState("ok"); }
    catch { setState("fail"); }
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
          <strong style={{ fontSize: 14.5 }}>{p.name || "Unnamed"}</strong>
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
      </div>
      )}
    </div>
  );
}

function Household() {
  const [persons, setPersons] = useState<Person[] | null>(null);
  useEffect(() => { fetch("/api/persons").then(j).then(setPersons).catch(() => setPersons([])); }, []);
  return (
    <Card title="Household"
          sub="Who Hearth predicts for, and how much each person wants to hear from it.">
      {persons === null && <p style={{ color: "var(--text-dim)", fontSize: 14 }}>Loading…</p>}
      {persons?.map((p) => <PersonCard key={p.id} p={p} />)}
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
  const [llmStatus, setLlmStatus] = useState<{ ok: boolean; code: number } | null>(null);
  const [usage, setUsage] = useState<LlmUsage | null>(null);
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
                      background: "color-mix(in srgb, var(--danger) 12%, transparent)",
                      border: "1px solid var(--danger)" }}>
          Your AI key is <strong>{llmMsg}</strong> — sensor mapping fell back to the basic rules.{" "}
          {(conn.url ?? "").includes("openrouter")
            ? <a href="https://openrouter.ai/credits" target="_blank" rel="noopener">Top up OpenRouter →</a>
            : "Update the key below."}
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

function OutputPolicy() {
  const [enabled, setEnabled] = useState(true);
  const [threshold, setThreshold] = useState(0.4);
  const [state, setState] = useState<SaveState>("idle");
  useEffect(() => {
    fetch("/api/output-policy").then(j)
      .then((r) => { setEnabled(r.abstain_enabled); setThreshold(r.abstain_threshold); }).catch(() => {});
  }, []);
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
    <Card title="System"
          sub="Updates install from the top bar. Restart to apply new Home Assistant / InfluxDB connections.">
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

type SectionKey =
  | "household" | "model" | "integrations" | "logs" | "account" | "general" | "methodology";

const SECTIONS: { key: SectionKey; icon: IconName; title: string; desc: string }[] = [
  { key: "household", icon: "household", title: "Household",
    desc: "People, avatars, notifications and daily question budgets." },
  { key: "model", icon: "models", title: "Model",
    desc: "Data sharing, feature power, model family, clock trust and commit threshold." },
  { key: "integrations", icon: "flow", title: "Integrations",
    desc: "Home Assistant, InfluxDB, the AI assistant, and API tokens for the HA integration." },
  { key: "logs", icon: "monitor", title: "Logs",
    desc: "Recent backend activity, live." },
  { key: "account", icon: "user", title: "Account",
    desc: "Your password and sign-in." },
  { key: "general", icon: "settings", title: "General",
    desc: "Appearance, system info, updates and the danger zone." },
  { key: "methodology", icon: "info", title: "How it works",
    desc: "The Hearth pipeline, end to end." },
];

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
      <ApiTokens />
    </>
  );
}

function SectionBody({ section }: { section: SectionKey }) {
  switch (section) {
    case "household": return <Household />;
    case "model": return (<><StatsConsent /><FeaturePower /><ModelFamily /><ModelBehaviour /><OutputPolicy /></>);
    case "integrations": return <ConnectionsSection />;
    case "logs": return <Logs />;
    case "account": return <Account />;
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
      </div>
    </section>
  );
}

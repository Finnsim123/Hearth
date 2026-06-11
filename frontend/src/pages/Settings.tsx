/**
 * Settings — household notification controls, connections, appearance,
 * account, system. Spec: docs/UI_SPEC.md §Settings.
 *
 * Design rules: one card per concern, every control explains itself,
 * Save is per-card so a half-edited page never clobbers anything.
 */
import { useEffect, useRef, useState } from "react";
import Avatar, { PRESET_HUES } from "../components/Avatar";
import { Icon } from "../icons";
import { applyTheme, getTheme, type ThemeMode } from "../theme";

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
                 padding: "12px 14px", textAlign: "left" }}>
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

function ConnectionCard({ kind, title, sub, fields }: {
  kind: string; title: string; sub: string;
  fields: { key: string; label: string; hint?: string; fromOptions?: boolean }[];
}) {
  const [conn, setConn] = useState<Record<string, string>>({});
  const [masked, setMasked] = useState<string | null>(null);
  const [state, setState] = useState<SaveState>("idle");
  useEffect(() => {
    fetch(`/api/connections/${kind}`).then(j).then((c) => {
      if (!c.configured) return;
      const init: Record<string, string> = { url: c.url ?? "" };
      for (const f of fields) if (f.fromOptions) init[f.key] = c.options?.[f.key] ?? "";
      setConn(init);
      setMasked(c.token_masked ?? null);
    }).catch(() => {});
  }, [kind]);
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
  return (
    <Card title={title} sub={sub}>
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
      <SaveButton state={state} onClick={save} />
      {state === "ok" && (
        <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-dim)" }}>
          Saved. Restart the container to apply: <code>docker compose restart hearth</code>
        </p>
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
  useEffect(() => {
    fetch("/api/health").then(j).then((h) => setInfo((i) => ({ ...i, build: h.build }))).catch(() => {});
    fetch("/api/system/update").then(j)
      .then((u) => setInfo((i) => ({ ...i, behind: u.behind ?? 0, subject: u.latest_subject })))
      .catch(() => {});
  }, []);
  return (
    <Card title="System" sub="Updates install from the top bar when a new version is available.">
      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", fontSize: 14 }}>
        <span><span style={{ color: "var(--text-dim)" }}>Version&nbsp;</span><code>{info.build ?? "…"}</code></span>
        <span>
          <span style={{ color: "var(--text-dim)" }}>Updates&nbsp;</span>
          {info.behind === undefined ? "…"
            : info.behind === 0 ? "Up to date ✓"
            : `${info.behind} commit${info.behind > 1 ? "s" : ""} behind — “${info.subject ?? ""}”`}
        </span>
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

export default function Settings() {
  useEffect(() => {
    if (window.location.hash) {
      const el = document.querySelector(window.location.hash);
      if (el) setTimeout(() => el.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
    }
  }, []);
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="settings" size={22} />
        <h2 style={{ margin: 0 }}>Settings</h2>
      </div>
      <Household />
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
      <ModelBehaviour />
      <Appearance />
      <div id="account"><Account /></div>
      <System />
      <DangerZone />
    </section>
  );
}

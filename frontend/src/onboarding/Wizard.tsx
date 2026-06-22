/**
 * Onboarding wizard — 10 steps, resumable (localStorage), every step explains
 * itself before asking for anything. Spec: docs/UI_SPEC.md §1.
 * All steps are wired to real endpoints (HA test, inventory scan, Influx
 * inspection, token minting, setup completion + fast track).
 */
import { useEffect, useState } from "react";
import { PRESET_HUES } from "../components/Avatar";
import FoundationalFacts from "../components/FoundationalFacts";
import Welcome from "./Welcome";
import BubbleCloud, { type TriageCluster } from "../components/BubbleCloud";
import { Icon } from "../icons";
import {
  Callout, ChoiceCard, Field, FooterNav, Progress, StepShell, TestRow, type TestState,
} from "./ui";

const TOTAL = 11;
const STORE = "hearth.onboarding";

type Member = { name: string; personEntity: string; hasDevice: boolean; notifyService: string; avatar: string; notifySystem: boolean; askBudget: number; email: string; newsletter: boolean };

type WizardData = {
  account: { name: string; email: string; password: string; confirm: string };
  ha: { url: string; token: string };
  influx: { mode: "external" | "bundled" | null; url: string; org: string; token: string; sourceBucket: string; importHistory: boolean };
  mqtt: { use: "ha-broker" | "custom" | "skip"; host: string };
  members: Member[];
  llmKey: string;
  llmModel: string;
  shareStats: boolean;        // AI data-sharing consent (metadata-only vs +stats)
  taxonomyPreset: "minimal" | "standard" | "custom";
  modelFamily: "random_forest" | "gradient_boosting" | "logistic" | "embedding";
  haEntities: number;         // total entity count from the HA test — for the AI cost estimate
  inventoryCount: number;     // kept entities after triage — shown on the scan step
  triageExcluded: string[];   // cluster labels the user toggled OFF in the scan step
  triageIncluded: string[];   // irrelevant clusters the user toggled back ON
};

const empty: WizardData = {
  account: { name: "", email: "", password: "", confirm: "" },
  ha: { url: "http://homeassistant.local:8123", token: "" },
  influx: { mode: null, url: "", org: "", token: "", sourceBucket: "", importHistory: true },
  mqtt: { use: "ha-broker", host: "" },
  members: [{ name: "", personEntity: "", hasDevice: true, notifyService: "", avatar: "preset:ember", notifySystem: true, askBudget: 8, email: "", newsletter: false }],
  llmKey: "",
  llmModel: "openai/gpt-4o-mini",
  shareStats: false,
  taxonomyPreset: "standard",
  modelFamily: "random_forest",
  haEntities: 0,
  inventoryCount: 0,
  triageExcluded: [],
  triageIncluded: [],
};


export default function Wizard() {
  const [step, setStep] = useState(1);
  const [data, setData] = useState<WizardData>(empty);
  useEffect(() => {
    const saved = localStorage.getItem(STORE);
    if (saved) {
      const s = JSON.parse(saved);
      setData({ ...empty, ...s.data });
      // Passwords are never persisted — a resumed session must pass through
      // step 1 again so the account is created with a REAL password.
      const restored = s.data?.account?.password ?? "";
      setStep(restored.length >= 10 ? (s.step ?? 1) : 1);
    }
  }, []);
  useEffect(() => {
    localStorage.setItem(STORE, JSON.stringify({ step, data: { ...data, account: { ...data.account, password: "", confirm: "" } } }));
  }, [step, data]);

  const next = () => setStep((s) => Math.min(s + 1, TOTAL));
  const back = () => setStep((s) => Math.max(s - 1, 1));
  const set = <K extends keyof WizardData>(k: K, v: WizardData[K]) => setData((d) => ({ ...d, [k]: v }));
  const [applying, setApplying] = useState<"idle" | "welcome">("idle");

  const finishSetup = async () => {
    // Hand straight off to the live Welcome screen — it IS the loading
    // experience now: it shows the buddy intro while Hearth restarts and lights
    // up the pipeline once the backend is back. No separate progress page.
    localStorage.removeItem(STORE);
    // Warm start always runs now: an external bucket gives the longest history,
    // otherwise we pull ~10 days from HA's recorder — so the live arc is for
    // everyone. `source` only tweaks the greeting copy.
    const hasBucket = data.influx.mode === "external" && !!data.influx.sourceBucket;
    // greet only whoever receives system messages (the operator), not every member
    localStorage.setItem("hearth.welcome", JSON.stringify({
      fastTrack: true,
      source: hasBucket ? "bucket" : "recorder",
      members: data.members.filter((m) => m.notifySystem).map((m) => m.name).filter(Boolean) }));
    setApplying("welcome");
    try {
      await fetch("/api/setup/complete", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...data, appBaseUrl: window.location.origin,
          triage: { excluded_labels: data.triageExcluded, included_labels: data.triageIncluded } }) });
    } catch { /* server restarts mid-request; expected — Welcome polls until it's up */ }
  };

  if (applying === "welcome") return <Welcome />;

  return (
    <div style={{ padding: "32px 16px" }}>
      <Progress current={step} total={TOTAL} />
      {step === 1 && <StepAccount d={data} set={set} next={next} />}
      {step === 2 && <StepHA d={data} set={set} next={next} back={back} />}
      {step === 3 && <StepInflux d={data} set={set} next={next} back={back} />}
      {step === 4 && <StepMqtt d={data} set={set} next={next} back={back} />}
      {step === 5 && <StepHousehold d={data} set={set} next={next} back={back} />}
      {step === 6 && <StepAiAssist d={data} set={set} next={next} back={back} />}
      {step === 7 && <StepInventory d={data} set={set} next={next} back={back} />}
      {step === 8 && <StepActivities d={data} set={set} next={next} back={back} />}
      {step === 9 && <StepFoundational d={data} set={set} next={next} back={back} />}
      {step === 10 && <StepOutput d={data} set={set} next={finishSetup} back={back} />}
      {step === 11 && <StepDone d={data} />}
    </div>
  );
}

type StepProps = {
  d: WizardData;
  set: <K extends keyof WizardData>(k: K, v: WizardData[K]) => void;
  next: () => void;
  back?: () => void;
};

function StepAccount({ d, set, next }: StepProps) {
  const a = d.account;
  const mismatch = a.confirm.length > 0 && a.password !== a.confirm;
  const ready = a.name && a.email.includes("@") && a.password.length >= 10 && a.password === a.confirm;
  return (
    <>
      <StepShell step={1} total={TOTAL} title="Welcome to Hearth"
        explainer="Hearth learns what's happening in your home and feeds it back to Home Assistant — all on your own hardware. First, create the admin account you'll use to sign in.">
        <Field label="Your name">
          <input value={a.name} placeholder="Alex" onChange={(e) => set("account", { ...a, name: e.target.value })} />
        </Field>
        <Field label="Email" hint="Only used to sign in to this Hearth — nothing is ever sent anywhere.">
          <input type="email" value={a.email} placeholder="you@example.com" onChange={(e) => set("account", { ...a, email: e.target.value })} />
        </Field>
        <Field label="Password" hint="At least 10 characters. A passphrase of a few words works great.">
          <input type="password" value={a.password} onChange={(e) => set("account", { ...a, password: e.target.value })} />
        </Field>
        <Field label="Repeat password" error={mismatch ? "Passwords don't match yet." : undefined}>
          <input type="password" value={a.confirm} onChange={(e) => set("account", { ...a, confirm: e.target.value })} />
        </Field>
        <Callout icon="lock">
          Everything Hearth stores stays on this machine. Your password is hashed (argon2id), and
          tokens you enter later are encrypted at rest. There is no cloud and no account but yours.
        </Callout>
      </StepShell>
      <FooterNav onNext={next} nextDisabled={!ready} nextLabel="Create account" />
    </>
  );
}

function StepHA({ d, set, next, back }: StepProps) {
  const [test, setTest] = useState<TestState>("idle");
  const [okMsg, setOkMsg] = useState("Connected");
  const [failMsg, setFailMsg] = useState("Couldn't reach HA — check the URL and token");
  const h = d.ha;
  return (
    <>
      <StepShell step={2} total={TOTAL} title="Connect Home Assistant"
        explainer="Hearth reads your sensors through Home Assistant's local API — no YAML edits, no restarts. It only ever reads the entities you choose later.">
        <Field label="Home Assistant URL" hint="The address you open HA on, from this machine's point of view.">
          <input value={h.url} onChange={(e) => { setTest("idle"); set("ha", { ...h, url: e.target.value }); }} />
        </Field>
        <Field label="Long-lived access token"
          hint={<>Create one in HA: click your user (bottom-left) → Security → “Long-lived access tokens” → Create token. Paste it here.</>}>
          <input type="password" value={h.token} placeholder="eyJhbGciOi…" onChange={(e) => { setTest("idle"); set("ha", { ...h, token: e.target.value }); }} />
        </Field>
        <TestRow state={test} okText={okMsg} failText={failMsg}
          onTest={async () => {
            setTest("testing");
            try {
              const r = await fetch("/api/ha/test", { method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: h.url, token: h.token }) });
              const j = await r.json();
              if (j.authed) {
                setOkMsg(`Connected — HA ${j.version ?? ""}, ${j.entities.toLocaleString()} entities found`);
                set("haEntities", j.entities ?? 0);
                setTest("ok");
              } else {
                setFailMsg(j.error ?? "Couldn't reach HA — check the URL and token");
                setTest("fail");
              }
            } catch { setFailMsg("Hearth backend unreachable"); setTest("fail"); }
          }} />
        <Callout>
          What happens with this: Hearth subscribes to live state changes over HA's WebSocket and
          stores them in your time-series database. The token is encrypted before it touches disk.
        </Callout>
      </StepShell>
      <FooterNav onBack={back} onNext={next} nextDisabled={test !== "ok"} />
    </>
  );
}

type Inspect = { reachable: boolean; authed: boolean; error: string | null;
                 buckets: { name: string; measurements: number | null;
                            points_24h: number | null; earliest: string | null }[] };

function Stage({ ok, okText, failText }: { ok: boolean; okText: string; failText: string }) {
  return (
    <span style={{ display: "flex", gap: 8, alignItems: "center",
                   color: ok ? "var(--ok)" : "var(--danger)" }}>
      <Icon name={ok ? "check" : "x"} size={15} /> {ok ? okText : failText}
    </span>
  );
}

function StepInflux({ d, set, next, back }: StepProps) {
  const [test, setTest] = useState<TestState>("idle");
  const [inspect, setInspect] = useState<Inspect | null>(null);
  const [bundled, setBundled] = useState<"checking" | "found" | "missing">("checking");
  const [recheck, setRecheck] = useState(0);
  const i = d.influx;
  // The bundled InfluxDB ships with the stack and is always running — auto-connect
  // to it, polling a few times in case it's still starting on first boot.
  useEffect(() => {
    if (i.mode !== "bundled") return;
    let alive = true, tries = 0;
    const check = async () => {
      setBundled("checking");
      try {
        const r = await fetch("/api/influx/inspect", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "bundled" }) }).then((x) => x.json());
        if (!alive) return;
        if (r.reachable && r.authed) { setBundled("found"); return; }
      } catch { /* retry below */ }
      if (!alive) return;
      if (tries++ < 8) setTimeout(check, 2500);
      else setBundled("missing");
    };
    check();
    return () => { alive = false; };
  }, [i.mode, recheck]);
  return (
    <>
      <StepShell step={3} total={TOTAL} title="Where should sensor history live?"
        explainer="Hearth keeps raw sensor data, engineered features and predictions in InfluxDB. Many homelabs already run one — reuse it, or let the stack bring its own.">
        <ChoiceCard icon="sensors" title="I already run InfluxDB"
          description="Connect your existing instance. Hearth creates its own three buckets — nothing else is touched, and any HA history already in there can be imported."
          selected={i.mode === "external"} onSelect={() => set("influx", { ...i, mode: "external" })} />
        <ChoiceCard icon="download" title="Set it up for me"
          description="Use the InfluxDB that comes with Hearth — already running alongside it, connects in one click, zero configuration."
          selected={i.mode === "bundled"} onSelect={() => set("influx", { ...i, mode: "bundled" })} />

        {i.mode === "external" && (
          <>
            <Field label="InfluxDB URL"><input placeholder="http://192.168.1.240:8086" value={i.url} onChange={(e) => { setTest("idle"); setInspect(null); set("influx", { ...i, url: e.target.value }); }} /></Field>
            <Field label="Organization"><input placeholder="homelab" value={i.org} onChange={(e) => set("influx", { ...i, org: e.target.value })} /></Field>
            <Field label="API token" hint="Needs read/write. InfluxDB UI → Load Data → API Tokens.">
              <input type="password" value={i.token} onChange={(e) => { setTest("idle"); setInspect(null); set("influx", { ...i, token: e.target.value }); }} />
            </Field>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <button className="btn btn-secondary" disabled={test === "testing"}
                onClick={async () => {
                  setTest("testing");
                  try {
                    const r = await fetch("/api/influx/inspect", { method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ url: i.url, org: i.org, token: i.token }) });
                    const j: Inspect = await r.json();
                    setInspect(j);
                    setTest(j.authed ? "ok" : "fail");
                    const guess = (j.buckets ?? []).filter((b) => !b.name.startsWith("hearth_"))
                      .sort((a, b) => (b.points_24h ?? 0) - (a.points_24h ?? 0))[0];
                    if (guess && !i.sourceBucket) set("influx", { ...i, sourceBucket: guess.name });
                  } catch { setTest("fail"); setInspect(null); }
                }}>
                {test === "testing" ? "Checking…" : "Check connection"}
              </button>
            </div>
            {inspect && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14 }}>
                <Stage ok={inspect.reachable} okText="Instance reachable" failText={inspect.error ?? "No InfluxDB at this URL"} />
                <Stage ok={inspect.authed} okText="Token accepted" failText={inspect.error ?? "Token rejected"} />
                {inspect.authed && (
                  <Stage ok={(inspect.buckets ?? []).length > 0}
                         okText={`${inspect.buckets.length} buckets found — Hearth will add its own three`}
                         failText="No buckets visible to this token" />
                )}
              </div>
            )}
            {inspect?.authed && (inspect.buckets ?? []).length > 0 && (
              <>
                <Field label="Import existing history from"
                  hint="Your HA→InfluxDB bucket, if you have one — months of history mean patterns and a first model on day one.">
                  <select value={i.sourceBucket}
                          onChange={(e) => set("influx", { ...i, sourceBucket: e.target.value })}>
                    <option value="">— no import —</option>
                    {inspect.buckets.map((b) => (
                      <option key={b.name} value={b.name}>
                        {b.name}
                        {b.measurements != null ? ` · ${b.measurements} measurements` : ""}
                        {b.points_24h != null ? ` · ${b.points_24h.toLocaleString()} pts/24h` : ""}
                        {b.earliest ? ` · since ${b.earliest.slice(0, 10)}` : ""}
                      </option>
                    ))}
                  </select>
                </Field>
                {i.sourceBucket && (
                  <Callout icon="check">
                    Data found in “{i.sourceBucket}” — after setup Hearth imports it for your
                    bound sensors, so the journey can skip straight ahead. Sensors with no
                    history are skipped automatically — review them anytime on the Sensors page.
                  </Callout>
                )}
              </>
            )}
          </>
        )}

        {i.mode === "bundled" && (
          bundled === "found" ? (
            <Callout icon="check">InfluxDB is ready and connected — nothing to configure.</Callout>
          ) : bundled === "missing" ? (
            <>
              <Callout icon="warning">
                The bundled InfluxDB hasn't come up. It ships with Hearth and starts
                automatically, so this is rare — give it a moment, or check
                <code> docker compose logs influxdb</code> on the host.
              </Callout>
              <button className="btn btn-secondary" style={{ alignSelf: "flex-start" }}
                      onClick={() => setRecheck((n) => n + 1)}>Re-check</button>
            </>
          ) : (
            <Callout icon="refresh">Connecting to the bundled InfluxDB…</Callout>
          )
        )}
      </StepShell>
      <FooterNav onBack={back} onNext={next}
        nextDisabled={!(i.mode === "external" ? test === "ok" : i.mode === "bundled" && bundled === "found")} />
    </>
  );
}

function StepMqtt({ d, set, next, back }: StepProps) {
  const m = d.mqtt;
  return (
    <>
      <StepShell step={4} total={TOTAL} title="MQTT (optional)"
        explainer="Hearth's recommended way into HA is its own integration — no broker needed. MQTT is an alternative output channel; if you run HA's Mosquitto add-on you can hook it up now, or skip entirely.">
        <ChoiceCard icon="check" title="Skip — use the Hearth integration" description="Recommended. You'll connect it in step 10 with one token." selected={m.use === "skip"} onSelect={() => set("mqtt", { ...m, use: "skip" })} />
        <ChoiceCard icon="sensors" title="Use my HA broker" description="Hearth publishes MQTT-discovery entities through your existing Mosquitto." selected={m.use === "ha-broker"} onSelect={() => set("mqtt", { ...m, use: "ha-broker" })} />
        {m.use === "ha-broker" && (
          <Field label="Broker host" hint="Usually your HA host. Default port 1883; credentials can be added in Settings later.">
            <input placeholder="homeassistant.local" value={m.host} onChange={(e) => set("mqtt", { ...m, host: e.target.value })} />
          </Field>
        )}
      </StepShell>
      <FooterNav onBack={back} onNext={next} />
    </>
  );
}

function StepHousehold({ d, set, next, back }: StepProps) {
  const ms = d.members;
  const upd = (idx: number, patch: Partial<Member>) =>
    set("members", ms.map((m, j) => (j === idx ? { ...m, ...patch } : m)));
  return (
    <>
      <StepShell step={5} total={TOTAL} title="Who lives here?"
        explainer="Hearth predicts one activity per person, so it needs to know the household. Add everyone you want predictions for — each member gets their own model.">
        {ms.map((m, idx) => (
          <div key={idx} className="card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", gap: 12 }}>
              <Field label="Name"><input placeholder={idx === 0 ? "Alex" : "Sam"} value={m.name} onChange={(e) => upd(idx, { name: e.target.value })} /></Field>
              <Field label="HA person entity (optional)" hint="Used for home/away.">
                <input placeholder="person.alex" value={m.personEntity} onChange={(e) => upd(idx, { personEntity: e.target.value })} />
              </Field>
            </div>
            <Field label="Email (optional)" hint="For password recovery and, if enabled, the weekly habits newsletter.">
              <input type="email" placeholder="alex@example.com" value={m.email}
                     onChange={(e) => upd(idx, { email: e.target.value })} />
            </Field>
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14,
                            opacity: m.email.trim() ? 1 : 0.5 }}>
              <input type="checkbox" checked={m.newsletter} disabled={!m.email.trim()}
                     onChange={(e) => upd(idx, { newsletter: e.target.checked })}
                     style={{ width: 16, height: 16 }} />
              Send a weekly habits email (detailed — leaves your local box; off by default)
            </label>
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <span style={{ fontSize: 14, fontWeight: 500 }}>Avatar</span>
              {Object.entries(PRESET_HUES).map(([key, hue]) => (
                <button key={key} aria-label={`avatar color ${key}`}
                  onClick={() => upd(idx, { avatar: `preset:${key}` })}
                  style={{ width: 26, height: 26, borderRadius: "50%", cursor: "pointer",
                           background: `color-mix(in srgb, ${hue} 30%, transparent)`,
                           border: m.avatar === `preset:${key}` ? `2px solid ${hue}` : "2px solid transparent" }} />
              ))}
              <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>photo upload in Settings later</span>
            </div>
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}>
              <input type="checkbox" checked={m.hasDevice} onChange={(e) => upd(idx, { hasDevice: e.target.checked })} style={{ width: 16, height: 16 }} />
              Has a phone with the HA companion app
            </label>
            {m.hasDevice && (
              <Field label="Notify service" hint="HA → Developer tools → Actions → search “notify.mobile_app”.">
                <input placeholder="mobile_app_alexs_iphone" value={m.notifyService} onChange={(e) => upd(idx, { notifyService: e.target.value })} />
              </Field>
            )}
            {m.hasDevice && (
              <div style={{ display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap" }}>
                <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}>
                  <input type="checkbox" checked={m.notifySystem}
                         onChange={(e) => upd(idx, { notifySystem: e.target.checked })}
                         style={{ width: 16, height: 16 }} />
                  System updates (model trained, issues)
                </label>
                <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}>
                  Max questions/day
                  <input type="number" min={0} max={20} value={m.askBudget}
                         onChange={(e) => upd(idx, { askBudget: Number(e.target.value) })}
                         style={{ width: 64 }} />
                </label>
              </div>
            )}
            {ms.length > 1 && (
              <button className="btn btn-ghost" style={{ alignSelf: "flex-start" }} onClick={() => set("members", ms.filter((_, j) => j !== idx))}>
                Remove
              </button>
            )}
          </div>
        ))}
        <button className="btn btn-secondary" style={{ alignSelf: "flex-start" }}
          onClick={() => set("members", [...ms, { name: "", personEntity: "", hasDevice: true, notifyService: "", avatar: "preset:indigo", notifySystem: false, askBudget: 5, email: "", newsletter: false }])}>
          + Add another person
        </button>
        <Callout icon="household">
          Two notification channels, per person: training questions (capped by max/day — set it
          low for anyone who wants minimal pings) and system updates (model trained, problems) —
          usually only whoever runs the homelab wants those. Kids without a phone get neither;
          someone else answers for them in the Inbox.
        </Callout>
      </StepShell>
      <FooterNav onBack={back} onNext={next} nextDisabled={!ms.every((m) => m.name.trim())} />
    </>
  );
}

type Inv = { entity_id: string; friendly_name: string | null; domain: string | null };
type TriageResp = { by: string | null; total: number; kept_count: number;
                    clusters: TriageCluster[] };

function StepInventory({ d, set, next, back }: StepProps & { back: () => void }) {
  const [state, setState] = useState<"scanning" | "grouping" | "done" | "error">("scanning");
  const [inventory, setInventory] = useState<Inv[]>([]);
  const [scan, setScan] = useState<{ count: number; domains: number } | null>(null);
  const [triage, setTriage] = useState<TriageResp | null>(null);
  const [kept, setKept] = useState<Record<string, boolean>>({});
  const [cursor, setCursor] = useState(0);     // rolling "reading…" ticker index

  // 1) pull the inventory, then 2) run the coarse triage (AI if a key is set)
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const inv = await fetch("/api/ha/inventory", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: d.ha.url, token: d.ha.token }) }).then((r) => r.json());
        if (!live) return;
        const items: Inv[] = inv.inventory ?? [];
        setInventory(items);
        setScan({ count: inv.count ?? items.length, domains: inv.domains ?? 0 });
        setState("grouping");
        const tr: TriageResp = await fetch("/api/triage/preview", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ inventory: items,
            llm: d.llmKey ? { key: d.llmKey, model: d.llmModel } : null }) }).then((r) => r.json());
        if (!live) return;
        setTriage(tr);
        setKept(Object.fromEntries(tr.clusters.map((c) => [c.category ?? c.label, c.relevant])));
        setState("done");
      } catch { if (live) setState("error"); }
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // rolling entity ticker while we read + group (the "scanning through" feel)
  useEffect(() => {
    if (state === "done" || state === "error" || inventory.length === 0) return;
    const id = setInterval(() => setCursor((c) => (c + 1) % inventory.length), 90);
    return () => clearInterval(id);
  }, [state, inventory.length]);

  // persist the selection into wizard data whenever a group is toggled
  const persist = (next: Record<string, boolean>) => {
    setKept(next);
    if (!triage) return;
    const id = (c: TriageCluster) => c.category ?? c.label;
    const excluded = triage.clusters.filter((c) => c.relevant && !next[id(c)]).map(id);
    const included = triage.clusters.filter((c) => !c.relevant && next[id(c)]).map(id);
    const keptCount = triage.clusters.reduce((n, c) => n + (next[id(c)] ? c.count : 0), 0);
    set("triageExcluded", excluded);
    set("triageIncluded", included);
    set("inventoryCount", keptCount);
  };
  useEffect(() => { if (triage) persist(kept); /* eslint-disable-line */ }, [triage]);

  const keptCount = triage
    ? triage.clusters.reduce((n, c) => n + (kept[c.category ?? c.label] ? c.count : 0), 0) : 0;
  const busy = state === "scanning" || state === "grouping";
  return (
    <>
      <StepShell step={7} total={TOTAL} title="Scanning your home"
        explainer={d.llmKey
          ? "Hearth reads every entity and, with your AI key, groups them and judges which matter for activity. This can take a moment."
          : "Hearth reads every entity and groups them by type, keeping the ones useful for activity. Takes a few seconds."}>
        {busy && (
          <Callout icon="refresh">
            {state === "scanning" ? "Reading your entities from Home Assistant…"
              : d.llmKey ? "Grouping them with AI and judging relevance…" : "Grouping by type…"}
            {inventory.length > 0 && (
              <div style={{ fontFamily: "ui-monospace, Menlo, monospace", fontSize: 12,
                            color: "var(--text-dim)", marginTop: 6, whiteSpace: "nowrap",
                            overflow: "hidden", textOverflow: "ellipsis" }}>
                {inventory[cursor]?.friendly_name || inventory[cursor]?.entity_id}
              </div>
            )}
          </Callout>
        )}
        {state === "error" && (
          <Callout icon="warning">Scan failed — go back a step and re-test the HA connection.</Callout>
        )}
        {state === "done" && scan && triage && (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))", gap: 12 }}>
              {[[scan.count.toLocaleString(), "entities found"],
                [keptCount.toLocaleString(), "kept for activity"],
                [scan.domains.toLocaleString(), "entity types"]].map(([n, l]) => (
                <div key={l} className="card" style={{ textAlign: "center", padding: 16 }}>
                  <div style={{ fontSize: 25, fontWeight: 600 }}>{n}</div>
                  <div style={{ fontSize: 12, color: "var(--text-dim)" }}>{l}</div>
                </div>
              ))}
            </div>
            <p style={{ margin: 0, fontSize: 13.5, color: "var(--text-dim)" }}>
              {triage.by === "llm" ? "Hearth's AI picked" : "Hearth picked"} the groups most useful for
              learning your routines — these defaults are tuned for accuracy, so you don't need to change
              anything. Switch a group off only to exclude something private; edits may affect results.
            </p>
            <BubbleCloud clusters={triage.clusters} kept={kept}
              onToggle={(l) => persist({ ...kept, [l]: !kept[l] })}
              onReset={() => persist(Object.fromEntries(
                triage.clusters.map((c) => [c.category ?? c.label, c.relevant])))} />
          </>
        )}
      </StepShell>
      <FooterNav onBack={back} onNext={next} nextDisabled={state !== "done"} />
    </>
  );
}

type CostEst = { est_usd: number; est_total_tokens: number; model: string };
const fmtUsd = (u: number) => (u < 0.01 ? "<$0.01" : `$${u.toFixed(2)}`);

function StepAiAssist({ d, set, next, back }: StepProps) {
  const [est, setEst] = useState<CostEst | null>(null);
  const [estErr, setEstErr] = useState(false);
  const hasKey = !!d.llmKey;
  const nEst = d.haEntities || d.inventoryCount;
  useEffect(() => {
    if (!hasKey) { setEst(null); setEstErr(false); return; }
    let live = true;
    fetch("/api/feature-spec/estimate", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entity_count: nEst, model: d.llmModel }) })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((j) => { if (live) { setEst(j); setEstErr(false); } })
      .catch(() => { if (live) setEstErr(true); });
    return () => { live = false; };
  }, [hasKey, d.llmModel, nEst]);
  return (
    <>
      <StepShell step={6} total={TOTAL} title="Want an AI to do the boring part?"
        explainer="Next, Hearth reads your whole entity list and groups it. With an AI key it clusters and judges relevance far better than rules — and later maps sensors to roles and writes starter rules. Built-in heuristics do all of this for free if you skip. You approve everything either way.">
        <Field label="OpenRouter or OpenAI-compatible API key (optional)"
          hint="Estimated one-time cost for a typical home: a few cents. Stored encrypted, removable in Settings, never needed again after first training.">
          <input type="password" placeholder="sk-or-…" value={d.llmKey} onChange={(e) => set("llmKey", e.target.value)} />
        </Field>
        {d.llmKey && (
          <Field label="Model"
            hint="Smarter models map unusual entity names and write better rules — for a few cents more. Any OpenRouter model id works.">
            <select value={d.llmModel} onChange={(e) => set("llmModel", e.target.value)}>
              <option value="openai/gpt-4o-mini">gpt-4o-mini — fast and cheap (default)</option>
              <option value="anthropic/claude-sonnet-4.6">claude-sonnet — strongest mapping</option>
              <option value="openai/gpt-4o">gpt-4o — strong all-rounder</option>
              <option value="google/gemini-2.5-flash">gemini-flash — cheap, large context</option>
            </select>
          </Field>
        )}
        {d.llmKey && (
          <input placeholder="…or type any OpenRouter model id" value={d.llmModel}
                 onChange={(e) => set("llmModel", e.target.value)} />
        )}
        {d.llmKey && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px",
                        borderRadius: 10, background: "var(--surface-2)", border: "1px solid var(--border)",
                        fontSize: 13.5 }}>
            <Icon name="patterns" size={16} />
            {estErr ? (
              <span style={{ color: "var(--text-dim)" }}>
                Couldn't estimate the cost right now — it's typically a few cents, one time.
              </span>
            ) : est ? (
              <span>
                Estimated one-time cost: <strong>~{fmtUsd(est.est_usd)}</strong>
                <span style={{ color: "var(--text-dim)" }}>
                  {" "}· ~{est.est_total_tokens.toLocaleString()} tokens across ~{nEst.toLocaleString()}{" "}
                  entities · charged by your provider, not us
                </span>
              </span>
            ) : (
              <span style={{ color: "var(--text-dim)" }}>Estimating cost…</span>
            )}
          </div>
        )}
        {d.llmKey && (
          <Field label="What may the AI see?"
            hint="This shapes how well it designs features. You can change it later in Settings.">
            <ChoiceCard icon="lock" title="Names & device classes only (recommended)"
              description="Metadata only — entity names, types, units. Never raw history."
              selected={!d.shareStats} onSelect={() => set("shareStats", false)} />
            <ChoiceCard icon="patterns" title="Also share aggregate stats"
              description="Adds per-sensor summaries (ranges, how often it changes) — sharper feature design, still never raw history or a timeline."
              selected={d.shareStats} onSelect={() => set("shareStats", true)} />
          </Field>
        )}
        <Callout icon="lock">
          Privacy: the model only ever sees what you pick above — never raw sensor history or a
          timeline of your life, and nothing after setup unless you ask. Once your first model is
          trained, predictions are 100% local and the key is dead weight.
        </Callout>
      </StepShell>
      <FooterNav onBack={back} onNext={next}
        skip={d.llmKey ? undefined : { label: "Skip — use heuristics", onSkip: next }} />
    </>
  );
}

function StepActivities({ d, set, next, back }: StepProps) {
  const presets = {
    minimal: ["sleeping", "away", "home"],
    standard: ["sleeping", "away", "home", "cooking", "eating", "movie", "working"],
    custom: [],
  } as const;
  return (
    <>
      <StepShell step={8} total={TOTAL} title="What should Hearth recognize?"
        explainer="These are the activities Hearth will learn to predict — your vocabulary, not ours. Start small; you can add, rename or split activities any time, and discovered patterns can become new ones later.">
        <ChoiceCard icon="home" title="Essential" description="sleeping · away · home — reliable within a week, great starting point." selected={d.taxonomyPreset === "minimal"} onSelect={() => set("taxonomyPreset", "minimal")} />
        <ChoiceCard icon="activities" title="Standard" description="adds cooking · eating · movie · working — needs a few weeks of feedback to get sharp." selected={d.taxonomyPreset === "standard"} onSelect={() => set("taxonomyPreset", "standard")} />
        <ChoiceCard icon="edit" title="Custom" description="start from a blank taxonomy and define your own." selected={d.taxonomyPreset === "custom"} onSelect={() => set("taxonomyPreset", "custom")} />
        {d.taxonomyPreset !== "custom" && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {presets[d.taxonomyPreset].map((a) => (
              <span key={a} className="chip" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                <Icon name={a as never} size={14} /> {a}
              </span>
            ))}
          </div>
        )}
        <Callout icon="patterns">
          Rare activities need more examples: something that happens once a day takes roughly a month
          to learn well. The Models page shows exactly when each activity has enough data.
        </Callout>
        <details style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
          <summary style={{ cursor: "pointer", fontSize: 13, color: "var(--text-dim)" }}>
            Advanced: learning engine
          </summary>
          <Field label="Model family"
            hint="The algorithm Hearth trains. Random forest is the reliable default and what we recommend. You can change this any time in Settings — switching just retrains.">
            <select value={d.modelFamily}
                    onChange={(e) => set("modelFamily", e.target.value as WizardData["modelFamily"])}>
              <option value="random_forest">Random forest — robust default (recommended)</option>
              <option value="gradient_boosting">Gradient boosting — sharper, slower to train</option>
              <option value="logistic">Logistic — simple, fast, very interpretable</option>
              <option value="embedding">Embedding — experimental, for future encoders</option>
            </select>
          </Field>
        </details>
      </StepShell>
      <FooterNav onBack={back} onNext={next} />
    </>
  );
}

function StepFoundational({ next, back }: StepProps) {
  return (
    <>
      <StepShell step={9} total={TOTAL} title="What I can know for sure"
        explainer="Some things Hearth never has to guess. When you're out, your presence already says so — Hearth marks you away and skips the model entirely (a fact beats a prediction, and saves compute). With a reliable sleep or bed sensor it can do the same for “asleep”. Bind those here or later in Settings — each one is checked for reliability before it's trusted as a fact.">
        <FoundationalFacts wizard />
      </StepShell>
      <FooterNav onBack={back} onNext={next} skip={{ label: "Skip — set up later", onSkip: next }} />
    </>
  );
}

function StepOutput({ d, next, back }: StepProps) {
  const [token, setToken] = useState<string | null>(null);
  const [minting, setMinting] = useState(false);
  const [mintErr, setMintErr] = useState("");
  const mint = async () => {
    setMinting(true); setMintErr("");
    try {
      const r = await fetch("/api/tokens", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "Home Assistant" }) });
      if (!r.ok) throw new Error(String(r.status));
      setToken((await r.json()).token);
    } catch { setMintErr("Couldn't mint a token — is the backend up?"); }
    setMinting(false);
  };
  // Deep links into the USER'S OWN HA instance (URL from step 2) using the
  // same /_my_redirect/ endpoints the my.home-assistant.io buttons resolve to.
  const ha = d.ha.url.replace(/\/+$/, "");
  const HEARTH_REPO = { owner: "Finnsim123", repository: "Hearth" };
  const hacsLink = `${ha}/_my_redirect/hacs_repository?owner=${HEARTH_REPO.owner}&repository=${HEARTH_REPO.repository}&category=integration`;
  const flowLink = `${ha}/_my_redirect/config_flow_start?domain=hearth`;
  return (
    <>
      <StepShell step={10} total={TOTAL} title="Send predictions back to Home Assistant"
        explainer="Install the Hearth integration in HA and it creates one device per person — sensors you can build automations on, like dimming the lights when a movie starts. The buttons below open the right screens directly in YOUR Home Assistant.">
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <span className="label" style={{ minWidth: 14 }}>1</span>
            <a className="btn btn-secondary" href={hacsLink} target="_blank" rel="noreferrer"
               style={{ display: "inline-flex", gap: 8, alignItems: "center", textDecoration: "none" }}>
              <Icon name="download" size={16} /> Add Hearth repo to HACS <Icon name="external" size={14} />
            </a>
            <span style={{ fontSize: 13, color: "var(--text-dim)" }}>opens in your HA, confirm + install, restart HA</span>
          </div>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <span className="label" style={{ minWidth: 14 }}>2</span>
            <a className="btn btn-secondary" href={flowLink} target="_blank" rel="noreferrer"
               style={{ display: "inline-flex", gap: 8, alignItems: "center", textDecoration: "none" }}>
              <Icon name="plus" size={16} /> Add the Hearth integration <Icon name="external" size={14} />
            </a>
            <span style={{ fontSize: 13, color: "var(--text-dim)" }}>host is pre-filled — Hearth announces itself on your network</span>
          </div>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <span className="label" style={{ minWidth: 14 }}>3</span>
            <span style={{ fontSize: 14, color: "var(--text-dim)" }}>Paste the token below when HA asks for it.</span>
          </div>
        </div>
        {token ? (
          <>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <code style={{ flex: 1, padding: "10px 12px", background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "var(--radius-ctl)", fontSize: 13 }}>{token}</code>
              <button className="btn btn-secondary" onClick={() => navigator.clipboard.writeText(token)} aria-label="Copy token"><Icon name="copy" size={16} /></button>
            </div>
            <Callout icon="warning">
              Copy it now — for your security this token is shown only once. You can revoke it or
              mint another any time in Settings → API tokens.
            </Callout>
          </>
        ) : (
          <>
            <button className="btn btn-primary" style={{ alignSelf: "flex-start" }}
              disabled={minting} onClick={mint}>
              <Icon name="key" size={16} /> {minting ? "Generating…" : "Generate integration token"}
            </button>
            {mintErr && <p style={{ margin: 0, fontSize: 13, color: "var(--danger)" }}>{mintErr}</p>}
          </>
        )}
        <Callout>
          No HACS, or buttons not opening? Manual path: HACS → custom repositories → add this
          repo as “Integration”, then Settings → Devices &amp; services → Add integration → Hearth,
          and enter {`${window.location.origin}`} plus the token.
        </Callout>
      </StepShell>
      <FooterNav onBack={back} onNext={next} nextLabel="Finish setup"
        skip={{ label: "I'll do this later", onSkip: next }} />
    </>
  );
}

function StepDone({ d }: { d: WizardData }) {
  const fastTrack = d.influx.mode === "external" && !!d.influx.sourceBucket;
  const items: [string, string][] = fastTrack
    ? [
        ["Right now", `Hearth is importing your history from “${d.influx.sourceBucket}” and building features — minutes, not days.`],
        ["Within the hour", "A first model trains on your imported data. Predictions and the activity ribbon go live today — watch the dashboard. It starts marked “provisional”."],
        ["Today", "Hearth may already start asking “was this right?” — early answers sharpen the model fastest, and once enough are confirmed the model becomes “validated” on the Models page."],
        ["Ongoing", "Live recording keeps improving on the imported foundation; retraining runs weekly."],
      ]
    : [
        ["Right now", "Hearth is recording. Close this tab, go live your normal life around the house — that IS the training data."],
        ["In ~3 days", "First patterns appear — we'll send a phone notification when they're ready to name."],
        ["In ~1 week", "Enough data for a first model. You'll get a “Hearth is live ✨” notification when predictions start flowing into HA."],
        ["Ongoing", "Hearth occasionally asks “was this right?” — every answer makes next week's model better."],
      ];
  return (
    <StepShell step={11} total={TOTAL}
      title={fastTrack ? "You're all set — and you brought history" : "You're all set — come back in a few days"}
      explainer={fastTrack
        ? "Setup is done — and because you imported existing data, Hearth is skipping the waiting week and processing it right now:"
        : "Setup is done. Hearth learns by watching normal life, so the best thing you can do now is nothing at all. We'll notify your phone at each milestone:"}>
      {items.map(([when, what]) => (
        <div key={when} style={{ display: "flex", gap: 12 }}>
          <span style={{ minWidth: 90, fontWeight: 500, fontSize: 14 }}>{when}</span>
          <span style={{ color: "var(--text-dim)", fontSize: 14.5 }}>{what}</span>
        </div>
      ))}
      <FooterNav onNext={() => { window.location.href = "/"; }} nextLabel="Go to dashboard" />
    </StepShell>
  );
}

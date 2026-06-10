/**
 * Onboarding wizard — 10 steps, resumable (localStorage), every step explains
 * itself before asking for anything. Spec: docs/UI_SPEC.md §1.
 *
 * Backend wiring is stubbed (fakeApi) until Phase 1/2 land the real
 * endpoints — the flow, copy, fields and validation are final.
 */
import { useEffect, useState } from "react";
import { PRESET_HUES } from "../components/Avatar";
import { Icon } from "../icons";
import {
  Callout, ChoiceCard, Field, FooterNav, Progress, StepShell, TestRow, type TestState,
} from "./ui";

const TOTAL = 10;
const STORE = "hearth.onboarding";

type Member = { name: string; personEntity: string; hasDevice: boolean; notifyService: string; avatar: string };

type WizardData = {
  account: { name: string; email: string; password: string; confirm: string };
  ha: { url: string; token: string };
  influx: { mode: "external" | "bundled" | null; url: string; org: string; token: string };
  mqtt: { use: "ha-broker" | "custom" | "skip"; host: string };
  members: Member[];
  llmKey: string;
  taxonomyPreset: "minimal" | "standard" | "custom";
};

const empty: WizardData = {
  account: { name: "", email: "", password: "", confirm: "" },
  ha: { url: "http://homeassistant.local:8123", token: "" },
  influx: { mode: null, url: "", org: "", token: "" },
  mqtt: { use: "ha-broker", host: "" },
  members: [{ name: "", personEntity: "", hasDevice: true, notifyService: "", avatar: "preset:ember" }],
  llmKey: "",
  taxonomyPreset: "standard",
};

// TODO(phase 1/2): replace with real API calls.
const fakeApi = (ok = true) => new Promise<boolean>((r) => setTimeout(() => r(ok), 900));

export default function Wizard() {
  const [step, setStep] = useState(1);
  const [data, setData] = useState<WizardData>(empty);
  useEffect(() => {
    const saved = localStorage.getItem(STORE);
    if (saved) {
      const s = JSON.parse(saved);
      setStep(s.step ?? 1);
      setData({ ...empty, ...s.data });
    }
  }, []);
  useEffect(() => {
    localStorage.setItem(STORE, JSON.stringify({ step, data: { ...data, account: { ...data.account, password: "", confirm: "" } } }));
  }, [step, data]);

  const next = () => setStep((s) => Math.min(s + 1, TOTAL));
  const back = () => setStep((s) => Math.max(s - 1, 1));
  const set = <K extends keyof WizardData>(k: K, v: WizardData[K]) => setData((d) => ({ ...d, [k]: v }));

  return (
    <div style={{ padding: "32px 16px" }}>
      <Progress current={step} total={TOTAL} />
      {step === 1 && <StepAccount d={data} set={set} next={next} />}
      {step === 2 && <StepHA d={data} set={set} next={next} back={back} />}
      {step === 3 && <StepInflux d={data} set={set} next={next} back={back} />}
      {step === 4 && <StepMqtt d={data} set={set} next={next} back={back} />}
      {step === 5 && <StepHousehold d={data} set={set} next={next} back={back} />}
      {step === 6 && <StepInventory next={next} back={back} />}
      {step === 7 && <StepAiAssist d={data} set={set} next={next} back={back} />}
      {step === 8 && <StepActivities d={data} set={set} next={next} back={back} />}
      {step === 9 && <StepOutput d={data} set={set} next={next} back={back} />}
      {step === 10 && <StepDone />}
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
        <TestRow state={test} okText="Connected — 214 entities found" failText="Couldn't reach HA — check the URL and token"
          onTest={async () => { setTest("testing"); setTest((await fakeApi()) ? "ok" : "fail"); }} />
        <Callout>
          What happens with this: Hearth subscribes to live state changes over HA's WebSocket and
          stores them in your time-series database. The token is encrypted before it touches disk.
        </Callout>
      </StepShell>
      <FooterNav onBack={back} onNext={next} nextDisabled={test !== "ok"} />
    </>
  );
}

function StepInflux({ d, set, next, back }: StepProps) {
  const [test, setTest] = useState<TestState>("idle");
  const [bundled, setBundled] = useState<"checking" | "found" | "missing">("missing");
  const i = d.influx;
  const cmd = "docker compose --profile influxdb up -d";
  return (
    <>
      <StepShell step={3} total={TOTAL} title="Where should sensor history live?"
        explainer="Hearth keeps raw sensor data, engineered features and predictions in InfluxDB. Many homelabs already run one — reuse it, or let the stack bring its own.">
        <ChoiceCard icon="sensors" title="I already run InfluxDB"
          description="Connect your existing instance. Hearth creates its own three buckets — nothing else is touched, and any HA history already in there can be imported."
          selected={i.mode === "external"} onSelect={() => set("influx", { ...i, mode: "external" })} />
        <ChoiceCard icon="download" title="Set it up for me"
          description="Use the InfluxDB bundled with this stack. One command, runs alongside Hearth, zero configuration."
          selected={i.mode === "bundled"} onSelect={() => set("influx", { ...i, mode: "bundled" })} />

        {i.mode === "external" && (
          <>
            <Field label="InfluxDB URL"><input placeholder="http://192.168.1.240:8086" value={i.url} onChange={(e) => { setTest("idle"); set("influx", { ...i, url: e.target.value }); }} /></Field>
            <Field label="Organization"><input placeholder="homelab" value={i.org} onChange={(e) => set("influx", { ...i, org: e.target.value })} /></Field>
            <Field label="API token" hint="Needs read/write. InfluxDB UI → Load Data → API Tokens.">
              <input type="password" value={i.token} onChange={(e) => { setTest("idle"); set("influx", { ...i, token: e.target.value }); }} />
            </Field>
            <TestRow state={test} okText="Connected — buckets created" failText="Couldn't connect — check URL, org and token"
              onTest={async () => { setTest("testing"); setTest((await fakeApi()) ? "ok" : "fail"); }} />
          </>
        )}

        {i.mode === "bundled" && (
          <>
            {bundled === "found" ? (
              <Callout icon="check">Bundled InfluxDB detected and connected. Nothing else to do here.</Callout>
            ) : (
              <>
                <Callout icon="warning">
                  The bundled InfluxDB isn't running yet. Run this once on the machine hosting Hearth,
                  then come back — this page re-checks automatically.
                </Callout>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <code style={{ flex: 1, padding: "10px 12px", background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "var(--radius-ctl)", fontSize: 13 }}>{cmd}</code>
                  <button className="btn btn-secondary" onClick={() => navigator.clipboard.writeText(cmd)} aria-label="Copy command"><Icon name="copy" size={16} /></button>
                  <button className="btn btn-secondary" onClick={async () => { setBundled("checking"); setBundled((await fakeApi()) ? "found" : "missing"); }}>
                    {bundled === "checking" ? "Checking…" : "Re-check"}
                  </button>
                </div>
              </>
            )}
          </>
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
        <ChoiceCard icon="check" title="Skip — use the Hearth integration" description="Recommended. You'll connect it in step 9 with one token." selected={m.use === "skip"} onSelect={() => set("mqtt", { ...m, use: "skip" })} />
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
            {ms.length > 1 && (
              <button className="btn btn-ghost" style={{ alignSelf: "flex-start" }} onClick={() => set("members", ms.filter((_, j) => j !== idx))}>
                Remove
              </button>
            )}
          </div>
        ))}
        <button className="btn btn-secondary" style={{ alignSelf: "flex-start" }}
          onClick={() => set("members", [...ms, { name: "", personEntity: "", hasDevice: true, notifyService: "", avatar: "preset:indigo" }])}>
          + Add another person
        </button>
        <Callout icon="household">
          Kids without a phone? Add them with the phone box unchecked — Hearth still predicts their
          activity, it just never sends them questions. Someone else answers for them in the Inbox.
        </Callout>
      </StepShell>
      <FooterNav onBack={back} onNext={next} nextDisabled={!ms.every((m) => m.name.trim())} />
    </>
  );
}

function StepInventory({ next, back }: { next: () => void; back: () => void }) {
  const [state, setState] = useState<"scanning" | "done">("scanning");
  useEffect(() => { fakeApi().then(() => setState("done")); }, []);
  return (
    <>
      <StepShell step={6} total={TOTAL} title="Scanning your home"
        explainer="Hearth is reading your entity list and — where history exists — computing per-sensor statistics. Nothing to fill in; this takes a few seconds.">
        {state === "scanning" ? (
          <Callout icon="refresh">Pulling entities, areas and device classes from Home Assistant…</Callout>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
              {[["214", "entities found"], ["31", "with useful signal"], ["14 d", "of history"]].map(([n, l]) => (
                <div key={l} className="card" style={{ textAlign: "center", padding: 16 }}>
                  <div style={{ fontSize: 25, fontWeight: 600 }}>{n}</div>
                  <div style={{ fontSize: 12, color: "var(--text-dim)" }}>{l}</div>
                </div>
              ))}
            </div>
            <button className="btn btn-secondary" style={{ alignSelf: "flex-start" }}>
              <Icon name="download" size={16} /> Download inventory.json
            </button>
            <Callout>
              This inventory — names, device classes and aggregate stats, never raw history — is all
              Hearth's suggestions are based on. It's also exactly what the optional AI assistant in
              the next step would see. Download it if you want to check first.
            </Callout>
          </>
        )}
      </StepShell>
      <FooterNav onBack={back} onNext={next} nextDisabled={state !== "done"} />
    </>
  );
}

function StepAiAssist({ d, set, next, back }: StepProps) {
  return (
    <>
      <StepShell step={7} total={TOTAL} title="Want an AI to do the boring part?"
        explainer="Next you'll map sensors to roles and pick activities. Built-in heuristics pre-fill everything for free — or paste an LLM API key and a model reads your inventory and proposes smarter mappings, tailored activities and starter rules. You approve every single row either way.">
        <Field label="OpenRouter or OpenAI-compatible API key (optional)"
          hint="Estimated one-time cost for a typical home: a few cents. Stored encrypted, removable in Settings, never needed again after first training.">
          <input type="password" placeholder="sk-or-…" value={d.llmKey} onChange={(e) => set("llmKey", e.target.value)} />
        </Field>
        <Callout icon="lock">
          Privacy: the model receives entity names and aggregate stats from the inventory you just
          saw — never raw sensor history, and never anything after setup unless you ask. Once your
          first model is trained, predictions are 100% local and the key is dead weight.
        </Callout>
      </StepShell>
      <FooterNav onBack={back} onNext={next}
        nextLabel={d.llmKey ? "Analyze my home" : "Continue"}
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
      </StepShell>
      <FooterNav onBack={back} onNext={next} />
    </>
  );
}

function StepOutput({ d, next, back }: StepProps) {
  const [token, setToken] = useState<string | null>(null);
  // Deep links into the USER'S OWN HA instance (URL from step 2) using the
  // same /_my_redirect/ endpoints the my.home-assistant.io buttons resolve to.
  const ha = d.ha.url.replace(/\/+$/, "");
  const HEARTH_REPO = { owner: "your-org", repository: "hearth" }; // TODO: real repo
  const hacsLink = `${ha}/_my_redirect/hacs_repository?owner=${HEARTH_REPO.owner}&repository=${HEARTH_REPO.repository}&category=integration`;
  const flowLink = `${ha}/_my_redirect/config_flow_start?domain=hearth`;
  return (
    <>
      <StepShell step={9} total={TOTAL} title="Send predictions back to Home Assistant"
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
          <button className="btn btn-primary" style={{ alignSelf: "flex-start" }}
            onClick={() => setToken("hrt_k3J9…demo…Xq")}>
            <Icon name="key" size={16} /> Generate integration token
          </button>
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

function StepDone() {
  const items: [string, string][] = [
    ["Right now", "Hearth is recording. Close this tab, go live your normal life around the house — that IS the training data."],
    ["In ~3 days", "First patterns appear — we'll send a phone notification when they're ready to name."],
    ["In ~1 week", "Enough data for a first model. You'll get a “Hearth is live ✨” notification when predictions start flowing into HA."],
    ["Ongoing", "Hearth occasionally asks “was this right?” — every answer makes next week's model better."],
  ];
  return (
    <StepShell step={10} total={TOTAL} title="You're all set — come back in a few days"
      explainer="Setup is done. Hearth learns by watching normal life, so the best thing you can do now is nothing at all. We'll notify your phone at each milestone:">
      {items.map(([when, what]) => (
        <div key={when} style={{ display: "flex", gap: 12 }}>
          <span style={{ minWidth: 90, fontWeight: 500, fontSize: 14 }}>{when}</span>
          <span style={{ color: "var(--text-dim)", fontSize: 14.5 }}>{what}</span>
        </div>
      ))}
      <FooterNav onNext={() => { localStorage.removeItem(STORE); window.location.href = "/"; }} nextLabel="Go to dashboard" />
    </StepShell>
  );
}

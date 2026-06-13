/**
 * How Hearth works — the pipeline clustered into the SIX stages data actually
 * flows through (Connect → Understand → Featurize → Learn → Serve → Privacy),
 * with this instance's live numbers spliced in. Data: GET /api/methodology
 * (+ /api/entity-triage for the grouping numbers). Every value has a fallback so
 * a fresh install still reads as complete prose.
 */
import { useEffect, useState, type ReactNode } from "react";
import { Icon } from "../icons";
import FlowMap from "../components/FlowMap";

type M = Record<string, any>;

const B = ({ children }: { children: ReactNode }) =>
  <strong style={{ color: "var(--text)", fontVariantNumeric: "tabular-nums" }}>{children}</strong>;
/** bold lead-in label for a sub-topic inside a stage */
const L = ({ children }: { children: ReactNode }) =>
  <strong style={{ color: "var(--text)" }}>{children} </strong>;

const num = (x: unknown) => (typeof x === "number" ? x.toLocaleString() : null);
const pct = (x: unknown) => (typeof x === "number" ? `${Math.round(x * 100)}%` : null);
const kv = (o: unknown) =>
  o && typeof o === "object" ? Object.entries(o as object)
    .map(([k, v]) => `${k} ×${v}`).join(" · ") : null;

function ifv<T>(v: T | null | undefined, node: (v: T) => ReactNode, fallback: ReactNode): ReactNode {
  return v === null || v === undefined || v === "" ? fallback : node(v);
}

type Stage = { id: string; title: string; summary: ReactNode; body: ReactNode[] };

function buildStages(m: M, triage: M | null): Stage[] {
  const kept = triage?.kept_count ?? m.bindable_count;
  const groups = triage?.clusters?.length;
  return [
    {
      id: "connect", title: "Connecting to your home",
      summary: ifv(m.history_days, (d) => <>Recording for <B>{num(d)}</B> days — all on your own hardware.</>,
        "Reads only your local Home Assistant and InfluxDB — never the cloud."),
      body: [
        <><L>Local only.</L> Hearth reads two local sources — <B>Home Assistant</B> (the live WebSocket stream
          and its recorded history) and a bundled <B>InfluxDB</B> — and nothing leaves the box. No HA→InfluxDB
          integration is required; live data arrives over the WebSocket.{" "}
          {ifv(m.recording_since, (s) => <>Recording since <B>{new Date(s as string).toLocaleDateString()}</B>{" "}
            ({num(m.history_days)} days), <B>{num(m.events_24h)}</B> events in the last 24 h.</>, null)}</>,
        <><L>Warm start.</L> Every home gets a head start: at setup Hearth pulls roughly the last <B>10 days</B>{" "}
          from Home Assistant's own recorder, so a first model can train on day one instead of waiting a week. If
          you already push to an external InfluxDB bucket, it imports that longer history too.{" "}
          {ifv(m.imported_points, (p) => <>Imported <B>{num(p)}</B> points over <B>{num(m.import_span_days)}</B> days.</>, null)}
          {" "}Training recency-weights with a <B>{num(m.recency_half_life) ?? "21"}-day half-life</B>, so old data is
          kept but barely moves the current model.</>,
      ],
    },
    {
      id: "understand", title: "Understanding your home",
      summary: ifv(m.entity_total, (t) => <>Grouped <B>{num(t)}</B> entities, kept <B>{num(kept)}</B> for activity.</>,
        "Groups every entity and keeps only the ones that are about people."),
      body: [
        <><L>Grouping (the funnel).</L> Home Assistant exposes everything — phone batteries, sun forecasts, printer
          temperatures. Hearth groups your whole list and keeps only the activity-relevant groups: <B>with an AI
          key</B> it clusters from names and judges relevance; <B>without one</B> it groups by role with built-in
          heuristics. You saw this as the bubble cloud during setup, and can re-pick groups any time on the Sensors
          page.{" "}
          {ifv(m.entity_total, () => <>On your instance, <B>{num(kept)}</B> of <B>{num(m.entity_total)}</B> entities
            were kept{groups ? <> across <B>{num(groups)}</B> groups</> : null}
            {ifv(m.filtered_examples, (ex) => <> (set aside e.g. <code>{(ex as string[]).slice(0, 4).join(", ")}</code>)</>, null)}.</>,
            <>Run a Home Assistant rescan from the Sensors page to populate these numbers.</>)}{" "}
          Nothing is permanent — a set-aside entity can be re-admitted later if it proves useful.</>,
        <><L>Roles, not names.</L> Each kept sensor is mapped to a <B>role</B> — presence, bed, power, media, env,
          person… — and every feature is computed from the role, never the entity id. A model trained on
          <code> presence_sensor_sofa</code> only works in one house; learning on roles is what makes the method
          portable and lets you swap a sensor without retraining from scratch.{" "}
          {ifv(m.role_breakdown, (r) => <>Your roles: <B>{kv(r)}</B>.</>, null)}</>,
        <><L>Rooms &amp; coverage.</L> Hearth doesn't predict per room, but it tracks per-room coverage to drive the
          dashboard's live map — where it can see, and where it's blind. Room names are canonicalised so a rescan
          can't split one room into two spellings.{" "}
          {m.weakest_room && <>Thinnest coverage right now: <B>{m.weakest_room}</B> — a presence sensor there is the
            highest-leverage upgrade.</>}</>,
      ],
    },
    {
      id: "featurize", title: "Turning signals into features",
      summary: ifv(m.feature_count, (c) => <>Each <B>30-min</B> window becomes <B>{num(c)}</B> features.</>,
        "Resamples, windows, and summarises every sensor by its role."),
      body: [
        <><L>Normalise.</L> Sensors report at wildly different rates, so each is resampled to a common
          <B> 1-minute grid</B> (last value wins) and forward-filled for a role-specific number of minutes. After
          this, sampling rate no longer biases anything — only what a sensor <em>said</em> matters.</>,
        <><L>Window.</L> Hearth never classifies an instant — it classifies a <B>30-minute window</B>. The window
          end is shared across sensors, but the lookback is role-aware (presence ~<B>{num(m.window_presence) ?? "15"}</B> min,
          steps ~<B>{num(m.window_steps) ?? "180"}</B> min). Window length isn't latency: the realtime lane
          re-evaluates the moment a sensor changes.</>,
        <><L>Features.</L> Each role runs a small <B>recipe</B> (fraction active, mean/max, deltas, transitions) —
          aggregations only; the model learns thresholds itself, Hearth never hard-codes "CO₂ &gt; 1200 = cooking".{" "}
          {ifv(m.feature_count, () => <><B>{num(m.feature_count)}</B> features from <B>{num(m.sensor_count)}</B> sensors,
            version <code>{m.feature_set_version}</code>.</>, null)}</>,
        <><L>Dynamics &amp; time.</L> Two cross-cutting families ride on top: event dynamics (state-change count,
          dominant sensor, minutes of silence — 40 silent minutes at 23:30 screams "asleep") and a deliberately
          <B> coarse</B> part-of-day clock. Raw hour would let a tree memorise "19:00 = cooking" and stop reading
          sensors — the clock-crutch trap.</>,
        <><L>Composites, rules &amp; evidence.</L> Combination signals ("TV playing AND on the sofa AND lights low" =
          movie) are stored as a JSON <B>expression tree</B>, never code — safe to generate, editable on Activities.{" "}
          {ifv(m.rule_count, () => <><B>{num(m.composite_count)}</B> composites, <B>{num(m.rule_count)}</B> rules.</>, null)}
          {" "}And each sensor carries an <B>evidence tier</B>: direct (a bed sensor) outranks behavioural (a power
          spike) outranks ambient (room temperature) — an ambient-only guess gets its confidence capped so Hearth
          doesn't over-trust a coincidence.</>,
      ],
    },
    {
      id: "learn", title: "Learning your activities",
      summary: ifv(m.model_version, (v) => <>Live model <B>{v}</B>{m.model_accuracy ? <> · <B>{pct(m.model_accuracy)}</B> on held-out data</> : null}.</>,
        "Bootstraps from rules, then learns from your confirmations — honestly scored."),
      body: [
        <><L>Activities.</L> These are the states Hearth predicts — your vocabulary, not ours.{" "}
          {ifv(m.activity_list, (a) => <>Currently: <B>{(a as string[]).join(", ")}</B>.</>, null)}{" "}
          {ifv(m.hierarchy, (h: any) => Object.keys(h).length
            ? <>It predicts coarse-first then the fine activity ({Object.entries(h).map(([p, kids]) =>
              <span key={p}><B>{p}</B>→{(kids as string[]).join("/")} </span>)}), sharpening as labels grow.</>
            : null, null)}
          {ifv(m.silent_activities, (s) => (s as string[]).length
            ? <> Silent (never notified about): <B>{(s as string[]).join(", ")}</B>.</> : null, null)}</>,
        <><L>Cold-start &amp; active learning.</L> Day one there are no labels, so starter rules <B>bootstrap</B> them
          ("bed + night + home → sleeping"); your real confirmations then take priority. Hearth asks <em>sparingly</em>{" "}
          — only when genuinely unsure, up to <B>{num(m.ask_budget) ?? "8"}</B>/day, never below <B>{pct(m.ask_threshold) ?? "75%"}</B>{" "}
          confidence, and never about silent activities.{" "}
          {(m.confirmed_label_count != null) && <>So far <B>{num(m.bootstrap_label_count) ?? "0"}</B> bootstrap +{" "}
            <B>{num(m.confirmed_label_count) ?? "0"}</B> confirmed labels.</>}</>,
        <><L>The model.</L> A classic tabular classifier (<B>random forest</B> by default — robust on small data,
          CPU-only, and able to say which sensors drove a decision), one per hierarchy node. It stays marked
          <B> provisional</B> until enough confirmed labels exist, then becomes <B>validated</B> — no pretending a
          model trained mostly on its own bootstrap guesses is proven.{" "}
          {ifv(m.model_version, (v) => <>Your live model is <B>{v}</B>
            {m.model_trained_at && <>, trained <B>{new Date(m.model_trained_at).toLocaleString()}</B></>}
            {m.train_window_count && <> on <B>{num(m.train_window_count)}</B> windows</>}.</>,
            <>No model is live yet — the first training run promotes one once enough labels accumulate.</>)}</>,
        <><L>Honest evaluation &amp; the gate.</L> Accuracy is measured only on a <B>held-out time slice</B> the model
          never trained on (grading on training data would be a lie). A freshly trained model must clear a
          <B> promotion gate</B> — enough windows and classes, not worse than the one it replaces — or the previous
          model stays live. <B>Calibration</B> re-maps its numbers so "70%" really means 70%, and
          <B> transition smoothing</B> damps implausible single-window flicker.{" "}
          {ifv(m.worst_class, (w) => <>Most error-prone class: <B>{w}</B>.</>, null)}</>,
      ],
    },
    {
      id: "serve", title: "Serving & improving",
      summary: <>Predict → ask → confirm → retrain <B>{m.retrain_schedule ?? "weekly"}</B>.</>,
      body: [
        <><L>Serving.</L> Predictions run on two lanes: a <B>grid lane</B> every 5 minutes (the dashboard ribbon)
          and a <B>realtime lane</B> that re-evaluates the instant a sensor changes and fires a
          <code> hearth_activity_changed</code> event on Home Assistant's bus — so automations trigger with no
          polling lag.{" "}
          {ifv(m.predictions_24h, (p) => <><B>{num(p)}</B> predictions in the last 24 h.</>, null)}
          {ifv(m.current_states, (c: any) => <> Right now: {Object.entries(c).map(([n, s]: any) =>
            <span key={n}><B>{n}</B> {s.state}{s.confidence ? ` (${pct(s.confidence)})` : ""}. </span>)}</>, null)}</>,
        <><L>Notifications.</L> Admins get system alerts; every other member only ever gets their own training
          questions; silent activities never notify, and nobody is asked about someone else's state.</>,
        <><L>Discovery.</L> {m.discovery_schedule ?? "Weekly"}, Hearth clusters the windows it couldn't confidently
          explain — a tight, recurring cluster is probably an activity you never named ("weeknights 21:00, sofa + TV
          + low light"). It surfaces them on Patterns; naming one labels weeks of history in a click.{" "}
          {m.patterns_pending != null && <><B>{num(m.patterns_pending)}</B> waiting for you.</>}</>,
        <><L>The loop.</L> It's a cycle — predict → ask when unsure → you confirm → retrain on a rolling
          <B> {num(m.retrain_window_weeks) ?? "6"}-week</B> window → gate → promote. Whenever the <em>method</em>{" "}
          changes (a new feature, a different window) the feature-set version bumps and forces a clean retrain, so
          old and new definitions never mix in one model.</>,
      ],
    },
    {
      id: "privacy", title: "What Hearth never does",
      summary: "Raw history stays on your hardware. No cloud account. Delete anything.",
      body: [
        <>Raw sensor history never leaves your hardware. {m.llm_enabled
          ? <>The AI assistant (you have <B>{m.llm_model ?? "a model"}</B> enabled) is used only at setup and
            re-analysis, and sees entity <B>metadata and aggregate stats only</B> — per the data-sharing choice you
            made — never raw streams or a timeline of your life.</>
          : <>No AI key is set, so nothing is ever sent off-device.</>}{" "}
          Predictions run 100% locally, no cloud account is required, and you can delete any sensor, label, or model
          from the UI.</>,
      ],
    },
  ];
}

export default function Methodology() {
  const [m, setM] = useState<M | null>(null);
  const [triage, setTriage] = useState<M | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    fetch("/api/methodology").then((r) => r.json()).then(setM).catch(() => setErr(true));
    fetch("/api/entity-triage").then((r) => r.json()).then(setTriage).catch(() => {});
  }, []);

  if (err) return <p style={{ color: "var(--text-dim)" }}>Couldn't load methodology.</p>;
  if (!m) return <p style={{ color: "var(--text-dim)" }}>Loading…</p>;
  const stages = buildStages(m, triage);

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="sensors" size={22} />
        <h2 style={{ margin: 0 }}>How Hearth works</h2>
      </div>
      <p style={{ margin: 0, fontSize: 14, color: "var(--text-dim)" }}>
        The whole pipeline in six stages, following the data, with your instance's own numbers. Click a stage to
        expand it.
        {m.generated_at && <> <span style={{ opacity: 0.7 }}>As of {new Date(m.generated_at).toLocaleString()}.</span></>}
      </p>
      <div className="card" style={{ padding: 16 }}>
        <p style={{ margin: "0 0 8px", fontSize: 13, color: "var(--text-dim)" }}>
          Live data flow — dots move at your real throughput. Hover a stage for detail; click it to open that page.
        </p>
        <FlowMap />
      </div>
      {stages.map((s, i) => (
        <details key={s.id} id={s.id} open={i === 0}
                 style={{ border: "1px solid var(--border)", borderRadius: 12,
                          background: "var(--surface)", padding: "2px 4px" }}>
          <summary style={{ cursor: "pointer", listStyle: "none", padding: "12px 14px",
                            display: "flex", flexDirection: "column", gap: 3 }}>
            <strong style={{ fontSize: 14.5 }}>
              <span style={{ color: "var(--accent)" }}>{i + 1}</span>
              {" · "}{s.title}
            </strong>
            <span style={{ fontSize: 13, color: "var(--text-dim)" }}>{s.summary}</span>
          </summary>
          <div style={{ padding: "0 14px 14px", display: "flex", flexDirection: "column", gap: 10,
                        fontSize: 13.5, lineHeight: 1.55, color: "var(--text)" }}>
            {s.body.filter(Boolean).map((p, j) => <p key={j} style={{ margin: 0 }}>{p}</p>)}
          </div>
        </details>
      ))}
    </section>
  );
}

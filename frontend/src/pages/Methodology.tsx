/**
 * Methodology — explains, A→Z, what Hearth did under the hood, with live numbers
 * from THIS instance spliced into the prose. Content + injection points are
 * specced in docs/METHODOLOGY.md; data comes from GET /api/methodology. Every
 * value has a fallback so a fresh install still reads as complete prose.
 */
import { useEffect, useState, type ReactNode } from "react";
import { Icon } from "../icons";

type M = Record<string, any>;

const B = ({ children }: { children: ReactNode }) =>
  <strong style={{ color: "var(--text)", fontVariantNumeric: "tabular-nums" }}>{children}</strong>;

const num = (x: unknown) =>
  typeof x === "number" ? x.toLocaleString() : null;
const pct = (x: unknown) =>
  typeof x === "number" ? `${Math.round(x * 100)}%` : null;
const kv = (o: unknown) =>
  o && typeof o === "object" ? Object.entries(o as object)
    .map(([k, v]) => `${k} ×${v}`).join(" · ") : null;

/** value-or-fallback inline: render `node` when present, else a neutral phrase */
function ifv<T>(v: T | null | undefined, node: (v: T) => ReactNode, fallback: ReactNode): ReactNode {
  return v === null || v === undefined || v === "" ? fallback : node(v);
}

type Section = { id: string; title: string; summary: ReactNode; body: ReactNode[] };

function buildSections(m: M): Section[] {
  return [
    {
      id: "sources", title: "A · Connecting your home",
      summary: ifv(m.history_days, (d) => <>Recording for <B>{num(d)}</B> days, all on your own hardware.</>,
        "Reads only from your local Home Assistant + InfluxDB."),
      body: [
        <>Hearth never reaches into the cloud. It reads from two local sources: <B>Home Assistant</B> (the
          live event stream + recorded history) and <B>InfluxDB</B> (where those events are stored).</>,
        ifv(m.recording_since, (s) => <>Your instance has recorded since <B>{new Date(s as string).toLocaleDateString()}</B>
          {" "}({num(m.history_days)} days) and saw <B>{num(m.events_24h)}</B> sensor events in the last 24 hours.</>,
          <>Once a source is connected, this is where you'll see how long Hearth has been watching.</>),
      ],
    },
    {
      id: "funnel", title: "B · Taking inventory — the entity funnel",
      summary: ifv(m.entity_total, (t) => <>Of <B>{num(t)}</B> entities, <B>{num(m.bindable_count)}</B> became sensors.</>,
        "Most Home Assistant entities say nothing about people — Hearth keeps only those that do."),
      body: [
        <>Home Assistant exposes everything — phone batteries, sun forecasts, printer temperatures. Hearth runs
          your full list through a funnel: drop disabled entities → block diagnostics/infrastructure by name →
          a <B>physics gate</B> (a button has no state stream, so it can never be a sensor) → match to a role →
          optional LLM pruning → de-dupe.</>,
        ifv(m.entity_total, () => <>On your instance, <B>{num(m.entity_filtered)}</B> entities were set aside;{" "}
          {ifv(m.filtered_examples, (ex) => <>e.g. <code>{(ex as string[]).join(", ")}</code>.</>, "the rest became bound sensors.")}</>,
          <>Run a Home Assistant rescan from the Sensors page to populate these numbers.</>),
        <>This is a <B>cold-start prior</B>, not a claim that filtered entities are signal-free — anything can be
          re-admitted later if the data proves it carries signal.</>,
      ],
    },
    {
      id: "roles", title: "C · Roles — the portable abstraction",
      summary: ifv(m.role_count, (c) => <>Your sensors map to <B>{num(c)}</B> roles — features never use entity names.</>,
        "Every sensor gets a role; features are computed from the role, not the name."),
      body: [
        <>A model trained on entity names (<code>presence_sensor_sofa</code>) only works in one house. Hearth
          assigns each sensor a <B>role</B> — presence, bed, power, media, env, person… — and learns on the role.
          That's what makes the method portable and lets you swap a sensor without retraining from scratch.</>,
        ifv(m.role_breakdown, (r) => <>Your roles: <B>{kv(r)}</B>.</>, null),
      ],
    },
    {
      id: "rooms", title: "D · Rooms & coverage",
      summary: ifv(m.room_count, (c) => <>Sensors across <B>{num(c)}</B> rooms; weakest is <B>{m.weakest_room ?? "—"}</B>.</>,
        "Coverage per room tells you where Hearth can see well and where it's blind."),
      body: [
        <>Hearth doesn't predict per-room, but it uses room coverage to show where it can see (the dashboard
          bubble chart). Room names are canonicalised so a rescan can't split one room into two spellings.</>,
        ifv(m.room_list, (r) => <>Rooms: <B>{kv(r)}</B>.{" "}
          {m.weakest_room && <>The thinnest direct coverage is in <B>{m.weakest_room}</B> — a presence sensor there
            is the highest-leverage upgrade.</>}</>, null),
      ],
    },
    {
      id: "import", title: "E · Importing your history",
      summary: ifv(m.imported_points, (p) => <>Imported <B>{num(p)}</B> points over <B>{num(m.import_span_days)}</B> days.</>,
        "If you arrived with data, Hearth imports all of it to skip the cold-start week."),
      body: [
        <>Hearth probes the earliest timestamp in your database and backfills <B>all</B> of it, one month-sized
          chunk at a time so years of history never overflow memory.{" "}
          {m.pruned_note && <><B>{m.pruned_note}</B>.</>}</>,
        <>Honest caveat: training recency-weights data with a <B>{num(m.recency_half_life) ?? "21"}-day half-life</B>,
          so data older than a few months barely moves the current model — though it's all kept for discovery and
          future retraining.</>,
      ],
    },
    {
      id: "normalise", title: "F · Normalising the signal",
      summary: "Every sensor resampled to a 1-minute grid so chatty sensors can't drown out quiet ones.",
      body: [
        <>Sensors report at wildly different rates. Hearth resamples each to a common <B>1-minute grid</B> (last
          value wins), then forward-fills for a role-specific number of minutes. After this, sampling rate no
          longer biases anything — only what a sensor <em>said</em> matters.</>,
      ],
    },
    {
      id: "windowing", title: "G · Windowing — the unit of prediction",
      summary: <>Each prediction summarises a <B>30-minute</B> window; lookback is role-aware.</>,
      body: [
        <>Hearth never classifies an instant — it classifies a <B>30-minute window</B>. The window <em>end</em> is
          shared by every sensor, but the <em>lookback</em> is role-aware: presence looks back{" "}
          <B>{num(m.window_presence) ?? "15"}</B> min (recent matters), a step counter <B>{num(m.window_steps) ?? "180"}</B> min
          (it only means something over hours).</>,
        <>Window length isn't latency: the live lane re-evaluates the instant a sensor changes, so reaction is
          near-instant while each prediction still carries 30 minutes of context.</>,
        ifv(m.role_windows, (r) => <>Per-role windows (minutes): <B>{kv(r)}</B>.</>, null),
      ],
    },
    {
      id: "features", title: "H · Feature engineering",
      summary: ifv(m.feature_count, (c) => <>Each window produces <B>{num(c)}</B> features.</>,
        "Each role runs a small recipe turning its raw sub-series into a few numbers."),
      body: [
        <>For each window, every sensor's role runs a <B>recipe</B>: presence → fraction active + transitions;
          bed → mean/max pressure, occupied flag; env → mean, delta, max. These are aggregations only — the model
          learns thresholds itself; Hearth never hard-codes "CO₂ &gt; 1200 = cooking."</>,
        ifv(m.feature_count, () => <>Your set: <B>{num(m.feature_count)}</B> features from <B>{num(m.sensor_count)}</B> sensors,
          version <code>{m.feature_set_version}</code>.</>, null),
      ],
    },
    {
      id: "time", title: "I · Event dynamics & time",
      summary: <>Adds silence/activity counts and a deliberately <B>coarse</B> clock.</>,
      body: [
        <>Two cross-cutting families ride on top of the recipes: <B>event dynamics</B> (state-change count,
          dominant sensor, and minutes of silence — 40 silent minutes at 23:30 screams "asleep"), and <B>time</B>,
          encoded as a 4-bucket part-of-day, not raw hour. Raw hour lets a tree memorise "19:00 = cooking" and stop
          reading sensors — the clock-crutch failure. Yours is set to <B>{m.time_granularity ?? "coarse"}</B>.</>,
      ],
    },
    {
      id: "composites", title: "J · Composites & rules",
      summary: ifv(m.rule_count, (c) => <><B>{num(m.composite_count)}</B> composites, <B>{num(c)}</B> rules — stored as data.</>,
        "Combination signals expressed as data (an expression tree), never code."),
      body: [
        <>Some signals only mean something combined — "TV playing <em>and</em> on the sofa <em>and</em> lights low"
          = movie. Hearth stores these as <B>composites</B> and <B>rules</B> in a JSON expression tree, never as
          code — safe to generate, editable on the Activities page.</>,
        ifv(m.composite_names, (names) => (names as string[]).length
          ? <>Your composites: <B>{(names as string[]).join(", ")}</B>.</> : null, null),
      ],
    },
    {
      id: "tiers", title: "K · Evidence tiers",
      summary: ifv(m.tier_breakdown, (t: any) => <>Direct <B>{t[1]}</B> · behavioural <B>{t[2]}</B> · ambient <B>{t[3]}</B>.</>,
        "Direct evidence (a bed sensor) counts more than ambient (room temperature)."),
      body: [
        <>A bed sensor <em>directly</em> says someone's in bed (tier 1); a power spike is <em>behavioural</em>
          (tier 2); room temperature is <em>ambient</em> (tier 3). Tiers colour the coverage chart and <B>cap
          confidence</B> — a high-confidence guess resting only on weak ambient evidence is knocked down so Hearth
          doesn't over-trust a coincidence.</>,
      ],
    },
    {
      id: "activities", title: "L · The activities it predicts",
      summary: ifv(m.activity_count, (c) => <>Predicts <B>{num(c)}</B> activities{m.hierarchy && Object.keys(m.hierarchy).length ? ", hierarchically" : ""}.</>,
        "The set of states Hearth classifies, optionally hierarchical."),
      body: [
        ifv(m.activity_list, (a) => <>Your taxonomy: <B>{(a as string[]).join(", ")}</B>.</>, null),
        ifv(m.hierarchy, (h: any) => Object.keys(h).length
          ? <>Hierarchy: {Object.entries(h).map(([p, kids]) =>
            <span key={p}><B>{p}</B> → {(kids as string[]).join(", ")}. </span>)} Hearth predicts coarse-first,
            then the fine activity once there's enough labelled data — so it starts coarse and sharpens.</>
          : <>Activities are flat for now; they become hierarchical (e.g. home → cooking/movie) as data grows.</>,
          null),
        ifv(m.silent_activities, (s) => (s as string[]).length
          ? <>Silent (never notified about): <B>{(s as string[]).join(", ")}</B>.</> : null, null),
      ],
    },
    {
      id: "labels", title: "M · Cold-start labels & active learning",
      summary: ifv(m.confirmed_label_count, (c) => <><B>{num(c)}</B> of your confirmations now anchor the model.</>,
        "Rules bootstrap labels on day one; your confirmations take over."),
      body: [
        <>On day one you have no labels, so the starter rules <B>bootstrap</B> them ("bed + night + home → sleeping").
          Noisy, but enough to start; real confirmations then take priority.{" "}
          {(m.bootstrap_label_count != null || m.confirmed_label_count != null) &&
            <>Currently <B>{num(m.bootstrap_label_count) ?? "0"}</B> bootstrap + <B>{num(m.confirmed_label_count) ?? "0"}</B> confirmed.</>}</>,
        <>Hearth asks <em>sparingly</em>: only when genuinely unsure (low margin between its top two guesses),
          up to a daily budget of <B>{num(m.ask_budget) ?? "8"}</B>, never below confidence <B>{pct(m.ask_threshold) ?? "75%"}</B>,
          and <B>never for silent activities</B>.{" "}
          {m.questions_today != null && <>Asked today: <B>{num(m.questions_today)}</B>.</>}</>,
      ],
    },
    {
      id: "model", title: "N · The model",
      summary: ifv(m.model_version, (v) => <>Live model <B>{v}</B>{m.model_accuracy ? <> · <B>{pct(m.model_accuracy)}</B> accurate</> : null}.</>,
        "A Random Forest — interpretable, CPU-only, robust on small data."),
      body: [
        <>The classifier is a <B>Random Forest</B> — chosen over a neural net because it's robust on small
          datasets, needs no GPU, trains in seconds, and can tell you which sensors drove a decision. Hierarchical
          taxonomies get one forest per node (coarse root + a child per parent).</>,
        ifv(m.model_version, (v) => <>Your live model is <B>{v}</B>
          {m.model_trained_at && <>, trained <B>{new Date(m.model_trained_at).toLocaleString()}</B></>}
          {m.train_window_count && <> on <B>{num(m.train_window_count)}</B> windows</>}
          {m.n_nodes > 1 && <> across <B>{num(m.n_nodes)}</B> sub-models</>}.</>,
          <>No model is live yet — once enough labels accumulate, the first training run promotes one here.</>),
      ],
    },
    {
      id: "evaluation", title: "O · Honest evaluation",
      summary: ifv(m.model_accuracy, (a) => <>Scored <B>{pct(a)}</B> on a held-out time slice.</>,
        "Accuracy measured only on data the model never trained on."),
      body: [
        <>Accuracy is measured on a <B>held-out time slice</B> the model never saw — never on training data, or the
          score is a lie. Classes absent from training are excluded (you can't grade what wasn't taught). Training
          recency-weights windows with a <B>{num(m.recency_half_life) ?? "21"}-day half-life</B>.</>,
        ifv(m.per_class_f1, (f: any) => <>Per-class F1: <B>{Object.entries(f).map(([c, v]) =>
          `${c} ${pct(v as number)}`).join(" · ")}</B>.{" "}
          {m.worst_class && <>Most error-prone: <B>{m.worst_class}</B>.</>}</>, null),
      ],
    },
    {
      id: "calibration", title: "P · Calibration, smoothing & the gate",
      summary: ifv(m.calibration_status, (s) => <>Calibration <B>{s}</B>; transitions learned from your history.</>,
        "Confidence is re-calibrated, flicker is damped, bad models are blocked."),
      body: [
        <><B>Calibration</B> (isotonic, per class) re-maps the forest's numbers so a stated "70%" really hits 70% —
          important because the asking policy trusts those numbers.{" "}<B>Transition smoothing</B> learns your
          state-to-state matrix (you rarely jump sleeping → cooking) and damps implausible single-window flickers.</>,
        <>A freshly trained model must clear a <B>promotion gate</B> — enough windows, enough classes, not worse
          than the model it replaces — or the previous one stays live.{" "}
          {m.last_train_outcome && <>Last run: <B>{m.last_train_outcome}</B>.</>}</>,
      ],
    },
    {
      id: "serving", title: "Q · Serving predictions",
      summary: ifv(m.current_states, (c: any) => <>Now: {Object.entries(c).map(([n, s]: any) =>
        <span key={n}><B>{n}</B> {s.state}{s.confidence ? ` (${pct(s.confidence)})` : ""}. </span>)}</>,
        "Two lanes: a 5-minute grid and an instant event-driven lane."),
      body: [
        <>Predictions run on two lanes: a <B>grid lane</B> every 5 minutes (the ribbon), and a <B>realtime lane</B>
          that re-evaluates the instant a sensor changes and fires a <code>hearth_activity_changed</code> event on
          Home Assistant's bus when the state actually changes — so automations trigger with no polling lag.</>,
        ifv(m.predictions_24h, (p) => <><B>{num(p)}</B> predictions in the last 24 hours.</>, null),
      ],
    },
    {
      id: "notifications", title: "R · Notifications — who gets what",
      summary: "Admins get system alerts; everyone else only their own training questions.",
      body: [
        ifv(m.member_roles, (r: any) => <>Members: {Object.entries(r).map(([n, role]) =>
          <span key={n}><B>{n}</B> — {role as string}. </span>)}</>,
          <>Each member has a notification role; nobody is asked about someone else's state, and silent activities
            never notify.</>),
      ],
    },
    {
      id: "discovery", title: "U · Discovery — activities you never named",
      summary: ifv(m.patterns_pending, (p) => <><B>{num(p)}</B> unnamed pattern{p === 1 ? "" : "s"} waiting for you.</>,
        "Weekly clustering surfaces recurring behaviours you haven't labelled."),
      body: [
        <>Weekly, Hearth clusters the windows it couldn't confidently explain. A tight, recurring cluster is
          probably a real activity you haven't named ("weeknights 21:00, sofa + TV + low light"). It surfaces them
          on the Patterns page — naming one labels weeks of history in a click.{" "}
          {m.patterns_found != null && <><B>{num(m.patterns_found)}</B> found so far, <B>{num(m.patterns_pending)}</B> pending.</>}</>,
      ],
    },
    {
      id: "loop", title: "V · The self-improvement loop",
      summary: <>Predict → ask → confirm → <B>retrain {m.retrain_schedule ?? "weekly"}</B>.</>,
      body: [
        <>It's a cycle: predict → ask when unsure → you confirm → new labels stored → retrain on a rolling{" "}
          <B>{num(m.retrain_window_weeks) ?? "6"}</B>-week window → gate → promote. Discovery runs{" "}
          <B>{m.discovery_schedule ?? "weekly"}</B>, retraining <B>{m.retrain_schedule ?? "weekly"}</B>.</>,
        <>Whenever the <em>method</em> changes (a new feature, a different window), the feature-set version changes
          and forces a clean retrain so old and new definitions never mix in one model.</>,
      ],
    },
    {
      id: "privacy", title: "W · What Hearth never does",
      summary: "Raw history stays on your hardware. No cloud account. You can delete anything.",
      body: [
        <>Raw history never leaves your hardware. {m.llm_enabled
          ? <>LLM calls (you have <B>{m.llm_model ?? "a model"}</B> enabled) send entity <B>metadata and aggregate
            stats only</B>, never raw sensor streams.</>
          : <>No LLM is enabled, so nothing is sent off-device at all.</>}{" "}
          No cloud account is required, and you can delete any sensor, label, or model from the UI.</>,
      ],
    },
  ];
}

export default function Methodology() {
  const [m, setM] = useState<M | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    fetch("/api/methodology").then((r) => r.json()).then(setM).catch(() => setErr(true));
  }, []);

  if (err) return <p style={{ color: "var(--text-dim)" }}>Couldn't load methodology.</p>;
  if (!m) return <p style={{ color: "var(--text-dim)" }}>Loading…</p>;
  const sections = buildSections(m);

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="sensors" size={22} />
        <h2 style={{ margin: 0 }}>How Hearth works</h2>
      </div>
      <p style={{ margin: 0, fontSize: 14, color: "var(--text-dim)" }}>
        The whole pipeline, A→Z, with your instance's own numbers. Click any stage to expand it.
        {m.generated_at && <> <span style={{ opacity: 0.7 }}>As of {new Date(m.generated_at).toLocaleString()}.</span></>}
      </p>
      {sections.map((s) => (
        <details key={s.id} id={s.id}
                 style={{ border: "1px solid var(--border)", borderRadius: 12,
                          background: "var(--surface)", padding: "2px 4px" }}>
          <summary style={{ cursor: "pointer", listStyle: "none", padding: "12px 14px",
                            display: "flex", flexDirection: "column", gap: 3 }}>
            <strong style={{ fontSize: 14.5 }}>{s.title}</strong>
            <span style={{ fontSize: 13, color: "var(--text-dim)" }}>{s.summary}</span>
          </summary>
          <div style={{ padding: "0 14px 14px", display: "flex", flexDirection: "column", gap: 10,
                        fontSize: 13.5, lineHeight: 1.55, color: "var(--text)" }}>
            {s.body.filter(Boolean).map((p, i) => <p key={i} style={{ margin: 0 }}>{p}</p>)}
          </div>
        </details>
      ))}
    </section>
  );
}

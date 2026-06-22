# Hearth — the buddy: assessment & opportunities

Status: assessment, June 2026. What the ember buddy does today, where it's strong,
and the highest-value things it *could* do given signals Hearth already computes but
doesn't yet surface.

## 1. What the buddy is, today

**Backend**
- `domain/buddy.py :: buddy_state(repo, tsdb)` — a pure, priority-ordered phase
  resolver. First match wins, roughly: fast-track failed → live incident
  (`health.current_issue`) → triage awaiting → seed sub-phases → fast-track stages →
  integrate new sensors → re-map → stalled sensors → LLM error → retraining →
  what's-new → questions waiting → new sensors found → live (watch & predict) →
  collecting → waiting (with Influx/HA diagnostics). Returns
  `{phase, tone, title, detail, progress, cta, ack}`.
- `domain/health.py` — `record_issue / clear_issue / current_issue`. ONE issue slot
  (`system.issue`), 10-min TTL, components self-clear when they recover.
- `domain/insight.py :: model_insight(person, repo)` — deterministic model-health
  summary (gold accuracy, calibration ECE, beats-flat-baseline, drift, weakest slice).
  Served at `/api/buddy/insight`, but **not folded into the phase resolver**.
- The governor tick records a `system_heavy` issue under sustained load.

**Frontend** (`components/Buddy.tsx`) — ember orb, top-right on every page. Polls
`/api/buddy` (4 s during work, 15 s idle, 60 s when live), collapses to just the orb
in steady state, expands for setup/attention. `buddyBus` cheers give an instant ack
when you act (answer a question, approve sensors) before the next poll. Tones →
colours; a CTA button navigates; what's-new has a "Got it" ack.

## 2. What's genuinely good (keep)

- **One source of truth.** Buddy and dashboard read the same resolver — they can't
  disagree. This is the right architecture.
- **Honest degradation.** Any error → neutral "watching & predicting", never a scary
  false alarm.
- **Self-expiring, self-clearing issues.** A hiccup that recovered stops nagging;
  components clear their own issue the moment they work again.
- **Responsiveness.** The cheer/ack loop makes it feel alive without hammering the API.
- **Deterministic insight.** Model health is computed from real metrics, no LLM
  round-trip, honest about weaknesses (calibration, flat-baseline, drift).
- **Pure & testable** (`test_buddy.py`).

## 3. Weaknesses / gaps

### 3a. Only one issue can exist at a time
`record_issue` overwrites a single slot. If Influx drops *and* HA drops, the second
masks the first; whichever was written last wins, by recency not severity. Priority
between issue kinds is implicit (code order), not explicit.
→ **Fix:** store issues in a dict keyed by `kind`, each with its own TTL and an
explicit `severity` (critical/warn/info); the buddy surfaces the worst active one and
can show "+2 more" if several are live.

### 3b. The buddy narrates STATE, but rarely gives ADVICE
It reports phases and incidents well. But Hearth now computes several high-value
signals the user never proactively sees — they sit in settings pages or API endpoints:

1. **Foundational sensor demotions (highest value).** The 24 h verdict job can demote
   a sensor fact→feature→suspect (e.g. the bed sensor becomes unreliable). Today this
   only changes a Settings card silently. The user *relies* on these for the
   away/asleep facts — a demotion is exactly the kind of thing the buddy should say:
   *"Your bed sensor has become unreliable — I've stopped trusting it for 'asleep' and
   I'm using it as a hint."* (record an issue/event on `role_decision` change).
2. **Coverage blind-spots — your stated goal.** `coverage/advisor.py` already detects
   gaps ("no sensor covers the kitchen"). This is precisely the *"tell me to add a
   sensor in the kitchen"* vision, computed but never surfaced. The buddy is the
   natural home for a gentle, dismissible nudge: *"I'm blind in the kitchen — a motion
   sensor there would sharpen cooking vs. eating."*
3. **Model health nudges.** `model_insight` knows when confidence is miscalibrated,
   when a flat model does just as well, or when accuracy is low after enough
   spot-checks — none of it reaches the user unless they open the insight panel. A
   quiet nudge when health is poor (with a "Methodology" CTA) closes the loop.
4. **Drift → retrain CTA.** Drift is detected daily and shown in insight, but the
   buddy doesn't offer the one-click *"a retrain would recalibrate"* action.

### 3c. No memory / event timeline
The buddy is "now" only. There's no record of *what happened when* — sensor demoted,
model promoted, drift detected, pattern found, blind-spot resolved. A small append-only
**event log** (the buddy writes milestones to it) surfaced as an "Activity" strip would
give the system a narrative and the user real insight into its life and decisions.

### 3d. Nudges can't be snoozed
Only what's-new has an ack. A persistent non-critical nudge ("AI credits low",
"add a kitchen sensor") with no dismiss/snooze will nag. Each *advisory* (not
incident) should be dismissible per-kind with a cooldown.

### 3e. Stalled detection is all-or-nothing
`count_raw_events(3)==0` only fires when EVERY sensor goes quiet. A single dead sensor
among many is invisible here (drift/PSI may catch a stuck value, but it's not
surfaced as "sensor X went silent"). Per-binding silence detection would be valuable.

### 3f. The "live" detail is thin
It shows `alice: cooking` — good, but we now have **basis** (fact vs model) and
confidence. *"Alice: asleep (known)"* vs *"Alice: cooking (78%)"* would make the
glass-box ethos visible in the one line everyone sees.

## 4. A guiding principle worth stating
The buddy should own **system / model / sensor health + actionable advice**. Personal
*behaviour* commentary ("you slept less this week") belongs in the **opt-in weekly
digest**, not the always-on orb — surfacing personal habits unprompted, on every page,
risks feeling surveillant. Keeping that boundary is what keeps the buddy helpful rather
than creepy.

## 5. Recommended roadmap (by value / effort)

1. **Surface foundational demotions** — buddy nudge + event when `role_decision`
   changes. Low effort (the verdict job already runs; add a compare-and-record), very
   high value, directly protects the facts the user depends on.
2. **Surface coverage blind-spots** — a dismissible "add a sensor in X" nudge from the
   existing advisor. Low effort, realises a stated product goal.
3. **Explicit severity + multi-issue health** (3a) — small refactor, removes a real
   correctness gap (masked concurrent incidents).
4. **Snooze/dismiss for advisories** (3d) — needed before 1 & 2 ship, or they'll nag.
5. **Event timeline** (3c) — medium effort, turns the buddy into a system with memory.
6. **Model-health & drift nudges** (3b.3/3b.4) and **basis in the live line** (3f) —
   low effort polish.
7. **Per-sensor silence** (3e) — medium effort, needs a per-binding last-seen check.

## 5b. Roadmap status — IMPLEMENTED (June 2026)
Built as one coherent slice rather than ad-hoc nudges:
- **Shared primitives.** `domain/advisories.py` (standing, dismissible, severity-ranked,
  snooze with cooldown), `domain/events.py` (append-only timeline ring buffer), and
  `domain/health.py` upgraded to **keyed multi-issue + severity** (concurrent incidents
  no longer mask each other; back-compatible, migrates the old single slot).
- **Producers.** Foundational demotions/promotions are detected inline in
  `foundational.facts.run_verdicts` (advisory + timeline event on a role_decision
  change). A daily `advisory_scan.refresh_system_advisories` turns coverage blind-spots
  (`gaps_from_home`) and poor model health (`model_insight`: low accuracy / drift /
  miscalibration / flat-baseline) into advisories. Wired in the scheduler.
- **Buddy.** Folds the worst warn/critical advisory in above routine nudges, with a
  CTA + a **Dismiss** action (`ack_label`); info-level advisories stay passive. The
  live line now shows basis — "Alice: asleep (known)" vs "Bob: cooking (78%)".
- **Surface.** `GET /api/advisories` (+ events), `POST /api/advisories/dismiss`,
  `GET /api/events`; a new **Activity** page (nav) lists active advisories (dismiss)
  and the timeline.
- **Tests.** advisories/events/health, producers (demotion, coverage, model health),
  buddy advisory surfacing + dismissal — all green.
- **Deferred:** per-sensor silence (3e) — needs a per-binding last-seen query I didn't
  want to guess at; noted for a follow-up.

## 6. Note on consistency
Several of these (1, 2, 5) want a shared notion of an "advisory": a typed,
dismissible, optionally-actionable message with a severity and a cooldown. Worth
introducing one small `advisories` concept (settings-backed, like `health`) that the
buddy resolver folds in at the right priority — rather than bolting each nudge on
ad-hoc. That keeps the resolver readable and gives the event log a natural source.

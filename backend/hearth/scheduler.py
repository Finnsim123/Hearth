"""Job registry — all periodic work in one place (APScheduler).

| job              | cadence            | entrypoint                          |
|------------------|--------------------|--------------------------------------|
| window_builder   | every 5 min (cfg)  | features.pipeline.build_latest_windows |
| ingest           | long-running task  | domain.ingest.run_ingest             |
(training / discovery jobs land in Phase 2/4)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings
from .domain.features.pipeline import build_latest_windows
from .domain.ingest import run_ingest
from .domain.inference.predictor import predict_latest
from .domain.labeling.active import expire_stale_questions
from .domain.milestones import check_milestones
from .domain.training.trainer import train_person

log = logging.getLogger(__name__)


def build_scheduler(deps: dict) -> AsyncIOScheduler:
    """deps: {'repo': AppRepo, 'tsdb': TimeSeriesStore|None, 'events': EventSource|None}"""
    scheduler = AsyncIOScheduler(timezone="UTC")
    repo, tsdb, events = deps.get("repo"), deps.get("tsdb"), deps.get("events")

    # ── governor: sense load, gate heavy jobs, alert on sustained pressure ──
    from .domain.system import runtime as gov_runtime
    from .domain.system.governor import DISCOVERY, IMPORT, TRAINING, GovernorState, admit
    from .domain.system.vitals import heaviness_index

    def _governor_tick() -> None:
        try:
            v, state = gov_runtime.refresh()
        except Exception:
            return
        try:                                   # rolling history for the Vitals page
            hist = repo.get_setting("system.vitals.history")
            hist = hist if isinstance(hist, list) else []
            hist.append({"t": v.ts.isoformat(), "cpu": round(v.cpu_pct, 1),
                         "temp": v.temp_c, "mem": round(v.mem_pct, 1), "watts": v.watts,
                         "h": round(heaviness_index(v, gov_runtime.config()), 3),
                         "state": state.name.lower()})
            repo.set_setting("system.vitals.history", hist[-180:])
        except Exception:
            pass
        from .domain.health import clear_issue, record_issue
        if state >= GovernorState.HIGH:        # surface on the buddy (same channel)
            record_issue(repo, "system_heavy", "Hearth is running heavy",
                         f"System load is {state.name.lower()} — I've paused heavy work "
                         "and kept predictions live. It resumes automatically when load "
                         "eases.", cta={"label": "System", "href": "/system"})
        else:
            clear_issue(repo, "system_heavy")

    scheduler.add_job(_governor_tick, "interval", seconds=60,
                      id="governor", max_instances=1, coalesce=True)

    def _admit(kind: str) -> bool:
        """Gate a heavy job on the current governor state (inference is never gated)."""
        try:
            return admit(kind, gov_runtime.state())
        except Exception:
            return True

    def _influx_down(exc: Exception) -> bool:
        """Does this look like InfluxDB being unreachable / timing out (vs a real
        bug)? Match on the connectivity vocabulary urllib3/influxdb raise."""
        s = f"{type(exc).__name__} {exc}".lower()
        return any(k in s for k in (
            "timed out", "timeout", "connection", "max retries",
            "newconnectionerror", "protocolerror", "8086"))

    def _guard_influx(exc: Exception, what: str) -> None:
        """Surface a database-unreachable hiccup on the buddy (the user asked for
        these to be visible), or log a genuine bug normally."""
        from .domain.health import record_issue
        if _influx_down(exc):
            record_issue(repo, "influx_unreachable", "I can't reach your database",
                         "InfluxDB isn't responding — features and predictions are "
                         "paused until it's back. Check it's running and reachable.",
                         cta={"label": "Logs", "href": "/settings#logs"})
        else:
            log.exception("%s failed", what)

    if tsdb is not None:
        def _window_builder() -> None:
            from .domain.health import clear_issue
            try:
                build_latest_windows(tsdb, repo)
                clear_issue(repo, "influx_unreachable")
            except Exception as exc:                       # noqa: BLE001
                _guard_influx(exc, "window builder")

        scheduler.add_job(_window_builder, "interval",
                          seconds=settings.window_builder_interval,
                          id="window_builder", max_instances=1, coalesce=True)

    if tsdb is not None:
        async def _inference() -> None:
            from .domain.health import clear_issue
            try:
                await predict_latest(tsdb, repo, deps.get("models"),
                                     deps.get("publisher"), deps.get("notifier"))
                clear_issue(repo, "influx_unreachable")
            except Exception as exc:                       # noqa: BLE001
                _guard_influx(exc, "inference")

        scheduler.add_job(_inference, "interval", minutes=5,
                          id="inference", max_instances=1, coalesce=True)

        def _set_training(running: bool) -> None:
            repo.set_setting("training.status",
                             {"running": running, "at": datetime.now(timezone.utc).isoformat()})

        def _train_all() -> None:
            from .domain.health import clear_issue, record_issue
            if not _admit(TRAINING):
                log.info("weekly training deferred — system %s",
                         gov_runtime.state().name.lower())
                return
            _set_training(True)
            tried = ok = 0
            try:
                for person in repo.persons():
                    if not person.enabled:
                        continue
                    tried += 1
                    try:
                        train_person(person.id, tsdb, repo, deps.get("models"))
                        ok += 1
                    except Exception:
                        log.exception("weekly training failed for %s", person.id)
            finally:
                _set_training(False)
            if tried and ok == 0:       # every model failed → surface it
                record_issue(repo, "training_failed", "I couldn't train a model",
                             "The latest training run failed for everyone — check the logs.",
                             cta={"label": "Logs", "href": "/settings#logs"})
            elif ok:
                clear_issue(repo, "training_failed")

        scheduler.add_job(_train_all, "cron", day_of_week="sun", hour=3,
                          id="weekly_training", max_instances=1)

        async def _weekly_newsletter() -> None:
            # Sunday morning recap, after the overnight retrain so accuracy is
            # fresh. No-ops cleanly when SMTP is off or nobody opted in.
            from .domain.behaviour.newsletter_service import send_weekly
            try:
                await send_weekly(deps)
            except Exception:
                log.exception("weekly newsletter failed")

        scheduler.add_job(_weekly_newsletter, "cron", day_of_week="sun", hour=8,
                          id="weekly_newsletter", max_instances=1)

        def _first_train_if_ready() -> None:
            """Cold-start accelerator: a fresh no-history install shouldn't wait
            until Sunday for its first model. As soon as a person has enough
            feature windows, train + promote — then this becomes a no-op."""
            if not _admit(TRAINING):
                return
            from .domain.features.registry import active_feature_set_version
            from .domain.training.trainer import MIN_TRAIN_WINDOWS
            fset = active_feature_set_version(repo)
            now = datetime.now(timezone.utc)
            for person in repo.persons():
                if not person.enabled:
                    continue
                if any(m.promoted for m in repo.models(person.id)):
                    continue                                  # already live
                try:
                    feats = tsdb.read_features(person.id, fset, now - timedelta(weeks=8), now)
                    if len(feats) < MIN_TRAIN_WINDOWS:
                        continue
                    _set_training(True)
                    train_person(person.id, tsdb, repo, deps.get("models"))
                except Exception:
                    log.exception("first-train check failed for %s", person.id)
                finally:
                    _set_training(False)

        scheduler.add_job(_first_train_if_ready, "interval", minutes=30,
                          id="first_train", max_instances=1, coalesce=True)

        def _drift_check() -> None:
            from .domain.training.drift import run_drift_check
            try:
                run_drift_check(tsdb, repo, deps.get("models"))
            except Exception:
                log.exception("drift check failed")

        # daily: detect feature/regime drift (train window vs recent) before it
        # silently degrades predictions; flags features and can trigger a retrain
        scheduler.add_job(_drift_check, "interval", hours=24,
                          id="drift_check", max_instances=1, coalesce=True)

        def _foundational_verdicts() -> None:
            # score each bound foundational sensor (away/asleep) so only ones that
            # EARN it bypass the model; degraded ones auto-demote to a hint (§7a)
            from .domain.foundational.facts import run_verdicts
            try:
                run_verdicts(tsdb, repo)
            except Exception:
                log.exception("foundational verdict scoring failed")

        scheduler.add_job(_foundational_verdicts, "interval", hours=24,
                          id="foundational_verdicts", max_instances=1, coalesce=True)

        def _marker_timing() -> None:
            # learn each transition marker's lead/lag + reliability from history
            from .domain.markers import learn_marker_timing
            try:
                learn_marker_timing(repo, tsdb)
            except Exception:
                log.exception("marker timing learning failed")

        scheduler.add_job(_marker_timing, "interval", hours=24,
                          id="marker_timing", max_instances=1, coalesce=True)

        def _advisory_scan() -> None:
            # turn coverage blind-spots + poor model health into standing advisories
            # the buddy can surface (foundational demotions are produced inline above)
            from .domain.advisory_scan import refresh_system_advisories
            try:
                refresh_system_advisories(repo)
            except Exception:
                log.exception("advisory scan failed")

        scheduler.add_job(_advisory_scan, "interval", hours=24,
                          id="advisory_scan", max_instances=1, coalesce=True)

        def _discover_all() -> None:
            if not _admit(DISCOVERY):
                log.info("discovery deferred — system %s", gov_runtime.state().name.lower())
                return
            from .domain.discovery.clustering import run_discovery
            run_discovery(tsdb, repo)

        # Saturday: fresh pattern candidates waiting in the UI before Sunday's
        # retrain — name one and the very next training run learns from it.
        scheduler.add_job(_discover_all, "cron", day_of_week="sat", hour=4,
                          id="weekly_discovery", max_instances=1)
        scheduler.add_job(expire_stale_questions, "interval", hours=6,
                          args=[repo], id="question_expiry")

        if events is not None:
            async def _sync_inventory() -> None:
                if not _admit(IMPORT):
                    return
                from .domain.onboarding.inventory_sync import sync_inventory
                await sync_inventory(repo, events, use_llm=False)

            # daily: pick up new sensors / renamed entities / new HA areas
            scheduler.add_job(_sync_inventory, "interval", hours=24,
                              id="inventory_sync", max_instances=1, coalesce=True)

            async def _device_watch() -> None:
                # notice newly-added HA devices and offer to integrate them (push +
                # advisory). First scan just seeds the snapshot; later ones detect new.
                from .domain.onboarding.device_watch import scan_new_nodes
                try:
                    await scan_new_nodes(repo, events, deps.get("notifier"))
                except Exception:
                    log.exception("device watch failed")

            scheduler.add_job(_device_watch, "interval", hours=24,
                              id="device_watch", max_instances=1, coalesce=True)

    if tsdb is not None and deps.get("notifier") is not None:
        scheduler.add_job(check_milestones, "interval", minutes=30,
                          args=[repo, tsdb, deps["notifier"]], id="milestones",
                          max_instances=1, coalesce=True)

        async def _behaviour_digest() -> None:
            # opt-in weekly recap (descriptive, via the same notification channel).
            # No-op unless behaviour.digest.enabled is set.
            from .domain.behaviour.digest import run_weekly_digest
            try:
                await run_weekly_digest(repo, tsdb, deps["notifier"])
            except Exception:
                log.exception("weekly behaviour digest failed")

        # Monday 08:00 — a gentle start-of-week "here's how last week went".
        scheduler.add_job(_behaviour_digest, "cron", day_of_week="mon", hour=8,
                          id="behaviour_digest", max_instances=1)

    if tsdb is not None and events is not None:
        from .domain.inference.realtime import RealtimeSignal, realtime_loop
        signal = RealtimeSignal()
        deps["realtime_signal"] = signal

        async def _ingest_forever() -> None:
            while True:
                try:
                    await run_ingest(events, tsdb, repo, signal)
                    await asyncio.sleep(30)   # no bindings yet -> poll for some
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("ingest crashed — restarting in 10 s")
                    await asyncio.sleep(10)

        scheduler.add_job(_ingest_forever, id="ingest", next_run_time=None)
        # started as a one-shot task from main (long-running, not interval)
        deps["ingest_coro"] = _ingest_forever

        async def _realtime_forever() -> None:
            while True:
                try:
                    await realtime_loop(tsdb, repo, deps.get("models"), signal,
                                        deps.get("notifier"))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("realtime lane crashed — restarting in 10 s")
                    await asyncio.sleep(10)

        deps["realtime_coro"] = _realtime_forever
    return scheduler

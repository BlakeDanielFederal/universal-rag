"""In-process incremental-sync scheduler (APScheduler).

Reads each source's effective cron + strategy from config and runs `run_sync`
on that cadence. Per-source `schedule` / `strategy` options override the
`defaults.sync` values; sources whose effective strategy is not "incremental"
are not scheduled (they sync on demand via `urag sync`).

Run with `urag serve-scheduler` (blocks until interrupted).
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from universal_rag.config.schema import AppConfig
from universal_rag.ingestion import run_sync

log = structlog.get_logger("universal_rag.scheduler")


@dataclass(frozen=True, slots=True)
class JobSpec:
    project_id: str
    source_id: str
    cron: str

    @property
    def id(self) -> str:
        return f"{self.project_id}:{self.source_id}"


def build_jobs(config: AppConfig) -> list[JobSpec]:
    """Derive the scheduled jobs from config (pure; no scheduler involved)."""
    sync_defaults = config.defaults.sync
    jobs: list[JobSpec] = []
    for project in config.projects:
        for source in project.sources:
            strategy = source.opt("strategy", sync_defaults.strategy)
            if strategy != "incremental":
                continue
            cron = source.opt("schedule", sync_defaults.schedule)
            jobs.append(JobSpec(project.id, source.id, cron))
    return jobs


def _run_job(config: AppConfig, spec: JobSpec) -> None:
    """Job body — sync one source, logging the outcome; never raises."""
    try:
        for r in run_sync(spec.project_id, spec.source_id, config=config):
            log.info(
                "sync.done",
                source=r.source_id,
                status=r.status,
                documents=r.documents,
                chunks=r.chunks,
                skipped=r.skipped,
                deleted=r.deleted,
                error=r.error or None,
            )
    except Exception as exc:  # a bad run must not kill the scheduler
        log.error("sync.failed", source=spec.source_id, error=f"{type(exc).__name__}: {exc}")


class SyncScheduler:
    def __init__(self, config: AppConfig, scheduler: BaseScheduler | None = None) -> None:
        self.config = config
        self.scheduler: BaseScheduler = scheduler or BlockingScheduler()

    def register(self) -> list[JobSpec]:
        """Add a job per scheduled source; returns the specs registered."""
        specs = build_jobs(self.config)
        for spec in specs:
            self.scheduler.add_job(
                _run_job,
                CronTrigger.from_crontab(spec.cron),
                args=[self.config, spec],
                id=spec.id,
                replace_existing=True,
                max_instances=1,  # don't overlap a source with itself
                coalesce=True,  # collapse missed runs into one
            )
        return specs

    def start(self) -> None:
        specs = self.register()
        if not specs:
            log.warning("scheduler.no_jobs", reason="no incremental sources configured")
        for spec in specs:
            log.info("scheduler.scheduled", job=spec.id, cron=spec.cron)
        log.info("scheduler.starting", jobs=len(specs))
        self.scheduler.start()


__all__ = ["JobSpec", "SyncScheduler", "build_jobs"]

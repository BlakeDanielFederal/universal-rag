from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from universal_rag.config.schema import (
    AppConfig,
    Defaults,
    ProjectConfig,
    SourceConfig,
    SyncConfig,
)
from universal_rag.scheduler import SyncScheduler, build_jobs


def _config(
    sources: list[SourceConfig],
    *,
    default_cron: str = "0 * * * *",
    default_strategy: str = "incremental",
) -> AppConfig:
    defaults = Defaults(sync=SyncConfig(strategy=default_strategy, schedule=default_cron))
    project = ProjectConfig(id="p", name="P", description="", sources=sources)
    return AppConfig(defaults=defaults, projects=[project])


def test_build_jobs_uses_default_cron_and_skips_manual() -> None:
    sources = [
        SourceConfig(id="p-conf", provider="confluence", spaces=["S"]),
        SourceConfig(id="p-manual", provider="jira", projects=["J"], strategy="manual"),
    ]
    jobs = build_jobs(_config(sources))
    assert [j.source_id for j in jobs] == ["p-conf"]  # manual source not scheduled
    assert jobs[0].cron == "0 * * * *"
    assert jobs[0].id == "p:p-conf"


def test_build_jobs_per_source_schedule_override() -> None:
    sources = [SourceConfig(id="p-gh", provider="github", repos=["o/r"], schedule="*/15 * * * *")]
    jobs = build_jobs(_config(sources))
    assert jobs[0].cron == "*/15 * * * *"


def test_build_jobs_global_manual_strategy_skips_everything() -> None:
    sources = [SourceConfig(id="p-conf", provider="confluence", spaces=["S"])]
    assert build_jobs(_config(sources, default_strategy="manual")) == []


def test_register_adds_jobs_with_parsed_cron() -> None:
    sources = [
        SourceConfig(id="p-conf", provider="confluence", spaces=["S"]),
        SourceConfig(id="p-gh", provider="github", repos=["o/r"], schedule="*/30 * * * *"),
    ]
    scheduler = BackgroundScheduler()  # not started; CronTrigger parsing still validated
    specs = SyncScheduler(_config(sources), scheduler=scheduler).register()
    assert {s.id for s in specs} == {"p:p-conf", "p:p-gh"}
    assert {j.id for j in scheduler.get_jobs()} == {"p:p-conf", "p:p-gh"}

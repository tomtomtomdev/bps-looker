"""``bps`` command line: migrate the DB, seed crawl tasks, run the worker, show queue status.

    bps migrate
    bps seed dynamic [--domain 0000 ...] [--level prov ...] [--limit-vars N]
    bps work [--drain] [--max-tasks N] [--concurrency N] [--kind data ...]
    bps status

``seed dynamic`` enqueues one ``domains`` task: it fetches ``/domain`` and upserts every domain
row first, then fans out ``var_list`` only for the requested domains/levels — so the ``domain``
FK that ``var_list`` needs always exists. ``--limit-vars`` caps each ``var_list`` to the first N
variables (smoke runs). Seeding is idempotent: the same seed twice is one task.

Commands touching the DB refuse to run (exit 2) until ``bps migrate`` has brought it to head.
"""

import asyncio
import logging
from typing import Annotated, Any

import typer
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, func, select

from bps_fetcher import queue
from bps_fetcher.db.migrate import alembic_config, database_url, upgrade
from bps_fetcher.db.schema import DOMAIN_LEVELS, task
from bps_fetcher.handlers.domains import KIND as DOMAINS_KIND
from bps_fetcher.handlers.domains import domain_level
from bps_fetcher.redact import install_redaction, redact

log = logging.getLogger("bps_fetcher")

app = typer.Typer(
    help="Crawl the BPS WebAPI into Postgres.", no_args_is_help=True, add_completion=False
)
seed_app = typer.Typer(help="Enqueue crawl tasks.", no_args_is_help=True)
app.add_typer(seed_app, name="seed")

NOT_MIGRATED_EXIT = 2


def _setup_logging(secrets: list[str] | None = None) -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    logging.getLogger("alembic").setLevel(logging.WARNING)
    install_redaction(secrets or [])


def _head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config("postgresql://unused")).get_current_head()


def _engine() -> Engine:
    """Engine on ``DATABASE_URL``; exits with a hint unless the DB is migrated to head."""
    url = database_url()
    engine = create_engine(url)
    head = _head_revision()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current != head:
        engine.dispose()
        where = redact(engine.url.render_as_string(hide_password=True))
        typer.echo(
            f"Database {where} is not migrated (at {current or 'empty'}, head is {head}).\n"
            "Run `bps migrate` first.",
            err=True,
        )
        raise typer.Exit(NOT_MIGRATED_EXIT)
    return engine


@app.command()
def migrate() -> None:
    """Upgrade the database (DATABASE_URL) to the latest schema."""
    _setup_logging()
    upgrade(None, "head")
    typer.echo(f"Database migrated to {_head_revision()}.")


def _check_domain(value: list[str] | None) -> list[str] | None:
    for d in value or []:
        try:
            domain_level(d)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from None
    return value


def _check_level(value: list[str] | None) -> list[str] | None:
    for lv in value or []:
        if lv not in DOMAIN_LEVELS:
            raise typer.BadParameter(f"{lv!r} is not one of {', '.join(DOMAIN_LEVELS)}")
    return value


@seed_app.command()
def dynamic(
    domain: Annotated[
        list[str] | None,
        typer.Option(
            "--domain", "-d", help="Domain id (repeatable); default: all.", callback=_check_domain
        ),
    ] = None,
    level: Annotated[
        list[str] | None,
        typer.Option(
            help=f"Only domains at this level (repeatable): {', '.join(DOMAIN_LEVELS)}.",
            callback=_check_level,
        ),
    ] = None,
    limit_vars: Annotated[
        int | None, typer.Option(min=1, help="Only the first N variables of each domain.")
    ] = None,
) -> None:
    """Seed the dynamic-table crawl: domains → var_list → th_list → data."""
    _setup_logging()
    params: dict[str, Any] = {"type": "all"}
    if domain:
        params["domains"] = sorted(set(domain))
    if level:
        params["level"] = sorted(set(level))
    if limit_vars is not None:
        params["limit_vars"] = limit_vars
    engine = _engine()
    try:
        with engine.begin() as conn:
            task_id = queue.enqueue(conn, DOMAINS_KIND, params)
            if task_id is None:
                row = conn.execute(
                    select(task.c.id, task.c.status).where(
                        task.c.kind == DOMAINS_KIND,
                        task.c.params_hash == queue.params_hash(params),
                    )
                ).one()
                typer.echo(f"Already seeded: task {row.id} ({row.status}) {params}")
                return
    finally:
        engine.dispose()
    typer.echo(f"Seeded task {task_id}: {DOMAINS_KIND} {params}")


@app.command()
def work(
    drain: Annotated[bool, typer.Option(help="Stop once no task is due.")] = False,
    max_tasks: Annotated[int | None, typer.Option(min=1, help="Stop after N tasks.")] = None,
    concurrency: Annotated[
        int | None, typer.Option(min=1, help="Parallel slots (default: BPS_CONCURRENCY).")
    ] = None,
    kind: Annotated[
        list[str] | None, typer.Option(help="Only run tasks of this kind (repeatable).")
    ] = None,
) -> None:
    """Run the worker: claim due tasks and process them."""
    from bps_fetcher.client import BpsClient
    from bps_fetcher.settings import get_settings
    from bps_fetcher.worker import Stats, run_worker

    settings = get_settings()
    key = settings.api_key.get_secret_value()
    _setup_logging([key])
    engine = _engine()

    async def _go() -> Stats:
        async with BpsClient.from_settings(settings) as client:
            return await run_worker(
                engine,
                client=client,
                concurrency=concurrency or settings.concurrency,
                drain=drain,
                max_tasks=max_tasks,
                kinds=kind,
                secrets=[key],
            )

    try:
        stats = asyncio.run(_go())
    finally:
        engine.dispose()
    typer.echo(f"Worker stopped: {stats.done} done, {stats.failed} failed.")


@app.command()
def status() -> None:
    """Task counts by kind and status."""
    _setup_logging()
    engine = _engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(task.c.kind, task.c.status, func.count())
                .group_by(task.c.kind, task.c.status)
                .order_by(task.c.kind, task.c.status)
            ).all()
    finally:
        engine.dispose()
    if not rows:
        typer.echo("No tasks.")
        return
    width = max(len("kind"), *(len(r[0]) for r in rows))
    typer.echo(f"{'kind':<{width}}  {'status':<8}  count")
    for kind_, status_, n in rows:
        typer.echo(f"{kind_:<{width}}  {status_:<8}  {n}")


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()

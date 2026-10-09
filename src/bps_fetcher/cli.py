"""``bps`` command line: migrate the DB, seed crawl tasks, run the worker, show queue status.

    bps migrate
    bps seed dynamic [--domain 0000 ...] [--level prov ...] [--limit-vars N]
    bps seed indicators [--domain 0000 ...] [--run LABEL]
    bps seed trade [--from 2014] [--to YEAR] [--flow exp|imp ...] [--period annual|monthly ...]
                   [--batch-size 10] [--run LABEL]
    bps seed refresh [--domain 0000 ...] [--source indicators|trade|dynamic ...]
                     [--as-of ISO-DATETIME] [--dry-run]
    bps work [--drain] [--drain-wait SECONDS] [--max-tasks N] [--concurrency N] [--kind data ...]
    bps status

``seed dynamic`` enqueues one ``domains`` task: it fetches ``/domain`` and upserts every domain
row first, then fans out ``var_list`` only for the requested domains/levels — so the ``domain``
FK that ``var_list`` needs always exists. ``--limit-vars`` caps each ``var_list`` to the first N
variables (smoke runs). Seeding is idempotent: the same seed twice is one task.

``seed refresh`` re-runs what was crawled before once its refresh policy is due (indicators daily,
trade current + previous year weekly, variable re-list weekly, per-variable data probes by age —
see :mod:`bps_fetcher.refresh`); running it twice in a row schedules nothing new.

Commands touching the DB refuse to run (exit 2) until ``bps migrate`` has brought it to head.
"""

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Annotated, Any

import typer
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, func, select

from bps_fetcher import queue
from bps_fetcher import refresh as refresh_mod
from bps_fetcher.db.migrate import alembic_config, database_url, upgrade
from bps_fetcher.db.schema import DOMAIN_LEVELS, domain, task
from bps_fetcher.handlers.domains import KIND as DOMAINS_KIND
from bps_fetcher.handlers.domains import domain_level
from bps_fetcher.handlers.indicators import LEVELS as INDICATOR_LEVELS
from bps_fetcher.handlers.indicators import seed_indicators
from bps_fetcher.handlers.trade import seed_trade
from bps_fetcher.redact import install_redaction, redact
from bps_fetcher.trade import DEFAULT_BATCH_SIZE, EARLIEST_YEAR, EXPORT, IMPORT, MONTHLY, YEARLY
from bps_fetcher.worker import DEFAULT_DRAIN_WAIT

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


@seed_app.command()
def indicators(
    domain_: Annotated[
        list[str] | None,
        typer.Option(
            "--domain",
            "-d",
            help="Domain id (repeatable, pusat/prov only); default: every pusat/prov domain.",
            callback=_check_domain,
        ),
    ] = None,
    run: Annotated[
        str | None,
        typer.Option(help="Snapshot label (e.g. a date) so a repeat seed enqueues fresh tasks."),
    ] = None,
) -> None:
    """Seed strategic-indicator snapshots (one ``indicators`` task per domain)."""
    _setup_logging()
    engine = _engine()
    try:
        with engine.begin() as conn:
            known = conn.execute(
                select(func.count()).select_from(domain).where(domain.c.level.in_(INDICATOR_LEVELS))
            ).scalar_one()
            try:
                n = seed_indicators(conn, domain_, run=run)
            except ValueError as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(1) from None
    finally:
        engine.dispose()
    if not known:
        hint = "The domain table is empty: run `bps seed dynamic` and `bps work` first"
        if not domain_:
            typer.echo(f"{hint}; nothing seeded.", err=True)
            raise typer.Exit(1)
        typer.echo(f"Warning: {hint}, or these tasks will fail.", err=True)
    typer.echo(f"Seeded {n} indicators task(s)" + ("" if n else " (already seeded)") + ".")


_FLOW_NAMES = {"exp": EXPORT, "imp": IMPORT}
_PERIOD_NAMES = {"monthly": MONTHLY, "annual": YEARLY}


def _names(choices: dict[str, int]) -> Any:
    def check(value: list[str] | None) -> list[str] | None:
        for v in value or []:
            if v not in choices:
                raise typer.BadParameter(f"{v!r} is not one of {', '.join(choices)}")
        return value

    return check


@seed_app.command()
def trade(
    from_year: Annotated[
        int, typer.Option("--from", min=EARLIEST_YEAR, help="First year.")
    ] = EARLIEST_YEAR,
    to_year: Annotated[
        int | None, typer.Option("--to", help="Last year (default: the current year).")
    ] = None,
    flow: Annotated[
        list[str] | None,
        typer.Option(help="exp or imp (repeatable); default: both.", callback=_names(_FLOW_NAMES)),
    ] = None,
    period: Annotated[
        list[str] | None,
        typer.Option(
            help="annual or monthly (repeatable); default: both.",
            callback=_names(_PERIOD_NAMES),
        ),
    ] = None,
    batch_size: Annotated[
        int, typer.Option(min=1, help="HS chapters per request.")
    ] = DEFAULT_BATCH_SIZE,
    run: Annotated[
        str | None,
        typer.Option(help="Reload label (e.g. a date) so a repeat seed enqueues fresh tasks."),
    ] = None,
) -> None:
    """Seed foreign-trade loads: one ``trade`` task per flow x period x year x chapter batch."""
    _setup_logging()
    kwargs: dict[str, Any] = {"batch_size": batch_size, "run": run}
    if flow:
        kwargs["flows"] = [_FLOW_NAMES[f] for f in flow]
    if period:
        kwargs["period_types"] = [_PERIOD_NAMES[p] for p in period]
    last = date.today().year if to_year is None else to_year
    engine = _engine()
    try:
        with engine.begin() as conn:
            try:
                n = seed_trade(conn, from_year, last, **kwargs)
            except ValueError as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(1) from None
    finally:
        engine.dispose()
    typer.echo(f"Seeded {n} trade task(s) for {from_year}..{last}.")


def _check_source(value: list[str] | None) -> list[str] | None:
    for v in value or []:
        if v not in refresh_mod.SOURCES:
            raise typer.BadParameter(f"{v!r} is not one of {', '.join(refresh_mod.SOURCES)}")
    return value


def _parse_as_of(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(f"{value!r} is not an ISO date/time") from None
    return at if at.tzinfo is not None else at.astimezone()  # naive = local time


@seed_app.command("refresh")
def refresh_cmd(
    domain_: Annotated[
        list[str] | None,
        typer.Option(
            "--domain",
            "-d",
            help="Only these domains (repeatable; indicators + dynamic); default: all.",
            callback=_check_domain,
        ),
    ] = None,
    source: Annotated[
        list[str] | None,
        typer.Option(
            help=f"Only these sources (repeatable): {', '.join(refresh_mod.SOURCES)}.",
            callback=_check_source,
        ),
    ] = None,
    as_of: Annotated[
        str | None,
        typer.Option(
            help="Evaluate the policies as of this ISO date/time (default: now).",
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be scheduled; change nothing.")
    ] = False,
) -> None:
    """Re-schedule crawled tasks whose refresh policy is due (idempotent)."""
    _setup_logging()
    now = _parse_as_of(as_of) or datetime.now(UTC)
    engine = _engine()
    try:
        with engine.connect() as conn:
            txn = conn.begin()
            try:
                counts = refresh_mod.refresh(
                    conn, now=now, domains=domain_, sources=source or refresh_mod.SOURCES
                )
            except BaseException:
                txn.rollback()
                raise
            if dry_run:
                txn.rollback()
            else:
                txn.commit()
    finally:
        engine.dispose()
    width = max(len(p) for p in counts)
    for policy, n in counts.items():
        typer.echo(f"{policy:<{width}}  {n}")
    total = sum(counts.values())
    if dry_run:
        typer.echo(f"Dry run: {total} task(s) would be scheduled (as of {now.isoformat()}).")
    elif total:
        typer.echo(f"Scheduled {total} task(s); run `bps work --drain`.")
    else:
        typer.echo("Nothing to refresh.")


@app.command()
def work(
    drain: Annotated[bool, typer.Option(help="Stop once no task is due.")] = False,
    drain_wait: Annotated[
        float,
        typer.Option(
            min=0,
            help="With --drain: wait up to N seconds for pending tasks in retry backoff.",
        ),
    ] = DEFAULT_DRAIN_WAIT,
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
                drain_wait=drain_wait,
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
            waiting = conn.execute(
                select(func.count(), func.min(task.c.next_run_at)).where(
                    task.c.status == queue.PENDING, task.c.next_run_at > func.now()
                )
            ).one()
    finally:
        engine.dispose()
    if not rows:
        typer.echo("No tasks.")
        return
    width = max(len("kind"), *(len(r[0]) for r in rows))
    typer.echo(f"{'kind':<{width}}  {'status':<8}  count")
    for kind_, status_, n in rows:
        typer.echo(f"{kind_:<{width}}  {status_:<8}  {n}")
    if waiting[0]:
        typer.echo(
            f"{waiting[0]} pending task(s) not due yet (next at {waiting[1]:%Y-%m-%d %H:%M:%S %Z})."
        )


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()

"""Queue and data health for ``bps status`` (S20): task counts, dead tasks, table sizes and the
last successful run per source. Everything is read-only and cheap on a multi-million-row DB:
big tables report the planner's estimate (``pg_class.reltuples``) instead of ``count(*)``.

Parser report counts (S12: unmatched/ambiguous/non-numeric cells) are only logged, not
persisted, so they are not shown here.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Connection, case, func, select, text

from bps_fetcher import queue
from bps_fetcher.db.schema import metadata, task
from bps_fetcher.redact import redact

ALERT_EXIT: Final = 1  # ``bps status`` exit code when dead tasks exceed ``--max-dead``
ERROR_PREVIEW_LEN: Final = 200
DEFAULT_DEAD_LIMIT: Final = 10
# Tables whose estimate is below this are counted exactly; above it the estimate is shown.
EXACT_BELOW: Final = 100_000

# Task kinds per crawl source.
SOURCE_KINDS: Final[dict[str, tuple[str, ...]]] = {
    "dynamic": ("domains", "var_list", "th_list", "data"),
    "indicators": ("indicators",),
    "trade": ("trade",),
}


@dataclass(frozen=True, slots=True)
class TaskCount:
    kind: str
    status: str
    count: int


@dataclass(frozen=True, slots=True)
class DeadTask:
    id: int
    kind: str
    params: dict[str, Any]
    attempts: int
    updated_at: datetime
    last_error: str | None


@dataclass(frozen=True, slots=True)
class TableRows:
    table: str
    rows: int
    estimate: bool


@dataclass(frozen=True, slots=True)
class SourceRun:
    source: str
    last_success: datetime | None


@dataclass(slots=True)
class Status:
    tasks: list[TaskCount] = field(default_factory=list)
    dead_total: int = 0
    dead: list[DeadTask] = field(default_factory=list)
    pending_not_due: int = 0
    next_due_at: datetime | None = None
    pending_retrying: int = 0
    tables: list[TableRows] = field(default_factory=list)
    sources: list[SourceRun] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """A JSON-ready dict (datetimes as ISO strings)."""

        def conv(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, dict):
                return {k: conv(v) for k, v in value.items()}
            if isinstance(value, list):
                return [conv(v) for v in value]
            return value

        result: dict[str, Any] = conv(asdict(self))
        return result


def preview_error(error: str | None, limit: int = ERROR_PREVIEW_LEN) -> str | None:
    """Redacted, single-line, truncated error text."""
    if error is None:
        return None
    one_line = " ".join(redact(error).split())
    return one_line if len(one_line) <= limit else one_line[:limit] + "…"


def table_rows(conn: Connection, *, exact_below: int = EXACT_BELOW) -> list[TableRows]:
    """Rows per schema table: exact ``count(*)`` for small (or never-analyzed) tables, the
    ``pg_class.reltuples`` estimate for big ones."""
    names = sorted(t.name for t in metadata.sorted_tables)
    estimates: dict[str, int] = dict(
        conn.execute(
            text(
                "SELECT c.relname, c.reltuples::bigint FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = current_schema() AND c.relkind = 'r'"
                " AND c.relname = ANY(:names)"
            ),
            {"names": names},
        ).all()
    )
    out = []
    for name in names:
        est = estimates.get(name)
        if est is None:
            continue
        if est >= 0 and est >= exact_below:
            out.append(TableRows(name, int(est), estimate=True))
        else:
            n = conn.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one()
            out.append(TableRows(name, int(n), estimate=False))
    return out


def collect(conn: Connection, *, dead_limit: int = DEFAULT_DEAD_LIMIT) -> Status:
    """Gather the full status snapshot."""
    s = Status()
    s.tasks = [
        TaskCount(k, st, n)
        for k, st, n in conn.execute(
            select(task.c.kind, task.c.status, func.count())
            .group_by(task.c.kind, task.c.status)
            .order_by(task.c.kind, task.c.status)
        ).all()
    ]
    s.dead_total = sum(c.count for c in s.tasks if c.status == queue.DEAD)
    if s.dead_total:
        s.dead = [
            DeadTask(r.id, r.kind, r.params, r.attempts, r.updated_at, preview_error(r.last_error))
            for r in conn.execute(
                select(
                    task.c.id,
                    task.c.kind,
                    task.c.params,
                    task.c.attempts,
                    task.c.updated_at,
                    task.c.last_error,
                )
                .where(task.c.status == queue.DEAD)
                .order_by(task.c.updated_at.desc(), task.c.id.desc())
                .limit(dead_limit)
            ).all()
        ]
    pending = conn.execute(
        select(
            func.count().filter(task.c.next_run_at > func.now()),
            func.min(task.c.next_run_at).filter(task.c.next_run_at > func.now()),
            func.count().filter(task.c.last_error.is_not(None)),
        ).where(task.c.status == queue.PENDING)
    ).one()
    s.pending_not_due, s.next_due_at, s.pending_retrying = pending[0], pending[1], pending[2]

    source_of = case(
        *((task.c.kind.in_(kinds), source) for source, kinds in SOURCE_KINDS.items()),
        else_=None,
    ).label("source")
    last = dict(
        conn.execute(
            select(source_of, func.max(task.c.updated_at))
            .where(task.c.status == queue.DONE)
            .group_by(source_of)
        ).all()
    )
    s.sources = [SourceRun(src, last.get(src)) for src in SOURCE_KINDS]
    s.tables = table_rows(conn)
    return s


def _fmt_time(at: datetime | None) -> str:
    return "never" if at is None else f"{at:%Y-%m-%d %H:%M:%S %Z}".rstrip()


def render(s: Status) -> list[str]:
    """Plain-text report lines."""
    lines: list[str] = []
    if not s.tasks:
        lines.append("No tasks.")
    else:
        width = max(len("kind"), *(len(c.kind) for c in s.tasks))
        lines.append(f"{'kind':<{width}}  {'status':<8}  count")
        lines += [f"{c.kind:<{width}}  {c.status:<8}  {c.count}" for c in s.tasks]
    if s.pending_not_due:
        lines.append(
            f"{s.pending_not_due} pending task(s) not due yet (next at {_fmt_time(s.next_due_at)})."
        )
    if s.pending_retrying:
        lines.append(f"{s.pending_retrying} pending task(s) retrying after an error.")

    if s.dead_total:
        lines += ["", f"Dead tasks: {s.dead_total} (latest {len(s.dead)})"]
        for d in s.dead:
            lines.append(f"  #{d.id} {d.kind} {d.params} attempts={d.attempts}")
            lines.append(f"      {d.last_error or '(no error)'}")

    lines += ["", "Last successful run:"]
    sw = max(len(r.source) for r in s.sources)
    lines += [f"  {r.source:<{sw}}  {_fmt_time(r.last_success)}" for r in s.sources]

    lines += ["", "Rows per table (~ = estimate):"]
    tw = max((len(t.table) for t in s.tables), default=0)
    lines += [f"  {t.table:<{tw}}  {'~' if t.estimate else ' '}{t.rows:>12,}" for t in s.tables]
    return lines

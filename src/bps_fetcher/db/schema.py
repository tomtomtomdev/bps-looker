"""SQLAlchemy 2 Core table definitions — the source of truth Alembic migrations must match.

Later slices add tables here *and* in a new migration (``test_migrations_match_metadata`` checks).
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

DOMAIN_LEVELS = ("pusat", "prov", "kab")

# Task lifecycle (see bps_fetcher.queue): pending -> running -> done, or back to pending on
# failure (with backoff) until max attempts, then dead.
TASK_STATUSES = ("pending", "running", "done", "dead")

task = Table(
    "task",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("kind", Text, nullable=False),
    Column("params", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("params_hash", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("next_run_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_error", Text),
    Column("parent_id", BigInteger, ForeignKey("task.id", ondelete="SET NULL")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("kind", "params_hash"),
    Index("ix_task_status_next_run_at", "status", "next_run_at"),
    CheckConstraint("status IN ('pending', 'running', 'done', 'dead')", name="status"),
)

raw_response = Table(
    "raw_response",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("task_id", BigInteger, ForeignKey("task.id", ondelete="SET NULL"), index=True),
    Column("endpoint", Text, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("body", JSONB, nullable=False),
    Column("sha256", String(64), nullable=False),
)

domain = Table(
    "domain",
    metadata,
    Column("domain_id", String(4), primary_key=True),
    Column("name", Text, nullable=False),
    Column("url", Text),
    Column("level", Text, nullable=False),
    CheckConstraint("level IN ('pusat', 'prov', 'kab')", name="level"),
)

# Dynamic-table variable catalog, from ``/list?model=var`` (S9). ``decimal`` and ``last_update``
# are not in the list response — they are filled from data responses by the loader (S12).
variable = Table(
    "variable",
    metadata,
    Column(
        "domain_id",
        String(4),
        ForeignKey("domain.domain_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("var_id", Integer, primary_key=True),
    Column("title", Text, nullable=False),
    Column("unit", Text),
    Column("sub_id", Integer),
    Column("sub_name", Text),
    Column("subcsa_id", Integer),
    Column("subcsa_name", Text),
    Column("def", Text),
    Column("notes", Text),
    Column("decimal", Integer),
    Column("vertical", Integer),
    Column("last_update", DateTime(timezone=False)),
)

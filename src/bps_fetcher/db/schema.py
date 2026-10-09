"""SQLAlchemy 2 Core table definitions — the source of truth Alembic migrations must match.

Later slices add tables here *and* in a new migration (``test_migrations_match_metadata`` checks).
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
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

# Periods (``th``) a variable has data for, from ``/list?model=th`` (S10). ``th_id`` is BPS's
# period code (e.g. ``117``), ``label`` its year text (``"2017"``).
period = Table(
    "period",
    metadata,
    Column("domain_id", String(4), primary_key=True),
    Column("var_id", Integer, primary_key=True),
    Column("th_id", Integer, primary_key=True),
    Column("label", Text, nullable=False),
    ForeignKeyConstraint(
        ["domain_id", "var_id"],
        ["variable.domain_id", "variable.var_id"],
        ondelete="CASCADE",
    ),
)


def _var_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["domain_id", "var_id"],
        ["variable.domain_id", "variable.var_id"],
        ondelete="CASCADE",
    )


# Dimension labels of a variable, from its data responses (S12). ``val`` is BPS's item code;
# ``group_label`` is the vervar dimension's title (``labelvervar``).
dim_vervar = Table(
    "dim_vervar",
    metadata,
    Column("domain_id", String(4), primary_key=True),
    Column("var_id", Integer, primary_key=True),
    Column("val", Integer, primary_key=True),
    Column("label", Text, nullable=False),
    Column("group_label", Text),
    _var_fk(),
)

dim_turvar = Table(
    "dim_turvar",
    metadata,
    Column("domain_id", String(4), primary_key=True),
    Column("var_id", Integer, primary_key=True),
    Column("val", Integer, primary_key=True),
    Column("label", Text, nullable=False),
    _var_fk(),
)

dim_turth = Table(
    "dim_turth",
    metadata,
    Column("domain_id", String(4), primary_key=True),
    Column("var_id", Integer, primary_key=True),
    Column("val", Integer, primary_key=True),
    Column("label", Text, nullable=False),
    _var_fk(),
)

# One cell of a dynamic table (S12). ``last_update`` is the response's ``last_update`` the value
# came from (an older response never overwrites a newer one); ``fetched_at`` is when the row was
# last inserted/changed (re-loading an identical response leaves it alone).
observation = Table(
    "observation",
    metadata,
    Column("domain_id", String(4), primary_key=True),
    Column("var_id", Integer, primary_key=True),
    Column("vervar", Integer, primary_key=True),
    Column("turvar", Integer, primary_key=True),
    Column("th", Integer, primary_key=True),
    Column("turth", Integer, primary_key=True),
    Column("value", Numeric, nullable=False),
    Column("last_update", DateTime(timezone=False)),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    _var_fk(),
    Index("ix_observation_domain_id_var_id_th", "domain_id", "var_id", "th"),
)

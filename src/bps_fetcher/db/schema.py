"""SQLAlchemy 2 Core table definitions — the source of truth Alembic migrations must match.

Later slices add tables here *and* in a new migration (``test_migrations_match_metadata`` checks).
"""

from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ColumnElement,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
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


def _simple_tsvector(text_: ColumnElement[Any]) -> ColumnElement[Any]:
    return func.to_tsvector(text("'simple'::regconfig"), text_)


def _or_empty(col: ColumnElement[Any]) -> ColumnElement[Any]:
    return func.coalesce(col, text("''"))


# U2 search vector: title (weight A) + subject and category names (weight B), 'simple' config
# (no stemming/stop words — titles are Indonesian; queries use prefix matching instead). An
# expression GIN index, not a generated column: built CONCURRENTLY, it never rewrites or
# write-locks ``variable`` while a crawl runs. Queries must use this exact expression.
VARIABLE_SEARCH_VECTOR: ColumnElement[Any] = func.setweight(
    _simple_tsvector(variable.c.title), text("'A'")
).op("||")(
    func.setweight(
        _simple_tsvector(
            _or_empty(variable.c.sub_name)
            .op("||")(text("' '"))
            .op("||")(_or_empty(variable.c.subcsa_name))
        ),
        text("'B'"),
    )
)
Index("ix_variable_search_vector", VARIABLE_SEARCH_VECTOR, postgresql_using="gin")

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
    # bigint: regency vars list villages, whose 10-digit codes (3401010001) exceed int32 (S19).
    Column("val", BigInteger, primary_key=True),
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
    Column("vervar", BigInteger, primary_key=True),  # village codes, see dim_vervar
    Column("turvar", Integer, primary_key=True),
    Column("th", Integer, primary_key=True),
    Column("turth", Integer, primary_key=True),
    Column("value", Numeric, nullable=False),
    Column("last_update", DateTime(timezone=False)),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    _var_fk(),
    Index("ix_observation_domain_id_var_id_th", "domain_id", "var_id", "th"),
)

# Strategic indicators (S14), from ``/list?model=indicators`` (pusat/prov domains only). The API
# only serves each indicator's latest value, so every distinct ``(indicator_id, periode, title)``
# seen is kept as a row: a new period adds a row (history). ``first_seen``/``last_seen`` are when
# the row was first/last returned by the API.
indicator_snapshot = Table(
    "indicator_snapshot",
    metadata,
    Column(
        "domain_id",
        String(4),
        ForeignKey("domain.domain_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("indicator_id", Integer, primary_key=True),
    Column("periode", Text, primary_key=True),
    Column("title", Text, primary_key=True),
    Column("var", Integer),
    Column("subject_csa", Integer),
    Column("name", Text),
    Column("value", Numeric),
    Column("unit", Text),
    Column("category", Integer),
    Column("data_source", Text),
    Column("first_seen", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_seen", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# HS 2-digit chapters (S16), from the ``kodehs`` labels of trade responses. Descriptions differ
# by flow/year (2014 imports are Indonesian, exports English), so each row remembers where its
# text came from: a later year replaces it, and in the same year an export (``source_flow`` 1)
# beats an import (2).
hs_chapter = Table(
    "hs_chapter",
    metadata,
    Column("hs2", String(2), primary_key=True),
    Column("description", Text, nullable=False),
    Column("source_flow", SmallInteger, nullable=False),
    Column("source_year", SmallInteger, nullable=False),
    CheckConstraint("hs2 ~ '^[0-9]{2}$'", name="hs2"),
)

# Foreign trade (S16), from ``dataexim/``: ``flow`` 1 export / 2 import (API ``sumber``),
# ``period_type`` 1 monthly / 2 annual (API ``periode``). Primary-key columns can't be NULL, so
# annual rows use ``month = 0`` and a missing port (``pod: null``) is stored as ``''``.
# A task replaces all rows of its (flow, period_type, year, chapters) scope.
trade_flow = Table(
    "trade_flow",
    metadata,
    Column("flow", SmallInteger, primary_key=True),
    Column("period_type", SmallInteger, primary_key=True),
    Column("year", SmallInteger, primary_key=True),
    Column("month", SmallInteger, primary_key=True),
    Column("hs2", String(2), ForeignKey("hs_chapter.hs2"), primary_key=True),
    Column("port", Text, primary_key=True),
    Column("country", Text, primary_key=True),
    Column("value_usd", Numeric),
    Column("netweight_kg", Numeric),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("flow IN (1, 2)", name="flow"),
    CheckConstraint(
        "(period_type = 2 AND month = 0) OR (period_type = 1 AND month BETWEEN 1 AND 12)",
        name="period_month",
    ),
    Index("ix_trade_flow_hs2_year", "hs2", "year"),
    Index("ix_trade_flow_country_year", "country", "year"),
)

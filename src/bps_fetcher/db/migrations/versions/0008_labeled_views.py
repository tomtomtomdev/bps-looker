"""S17: labeled read views v_observation, v_trade, v_indicator_latest.

Plain (non-materialized) views, hand-written: they are not in ``schema.metadata`` and autogenerate
does not reflect views, so ``test_migrations_match_metadata`` ignores them.

- Every label is a LEFT JOIN on a unique key, so a row with a missing label is kept (label NULL)
  and Postgres removes joins whose columns a query doesn't use. Filters on the base table's key
  columns (``domain_id``/``var_id``/``th``, ``flow``/``period_type``/``year``/``hs2``...) push
  straight down to the base table's indexes.
- ``v_trade`` maps the S16 key sentinels back: annual ``month`` 0 -> NULL, port ``''`` -> NULL.
  (Filter on ``month``/``port`` there via the expressions, or on ``trade_flow`` for index use.)
- ``v_indicator_latest`` = per (domain, indicator) the snapshot row most recently returned by the
  API (``last_seen``, then ``first_seen``): ``periode`` is free text and can't be ordered.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-09 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V_OBSERVATION = """
CREATE VIEW v_observation AS
SELECT
    o.domain_id,
    d.name AS domain_name,
    d.level AS domain_level,
    o.var_id,
    v.title AS var_title,
    v.unit,
    v.sub_name AS subject,
    o.vervar,
    dv.label AS vervar_label,
    dv.group_label AS vervar_group,
    o.turvar,
    dt.label AS turvar_label,
    o.th,
    p.label AS year_label,
    CASE WHEN p.label ~ '^[0-9]{4}$' THEN p.label::integer END AS year,
    o.turth,
    dth.label AS turth_label,
    o.value,
    v.decimal,
    o.last_update,
    o.fetched_at
FROM observation o
LEFT JOIN domain d ON d.domain_id = o.domain_id
LEFT JOIN variable v ON v.domain_id = o.domain_id AND v.var_id = o.var_id
LEFT JOIN dim_vervar dv
    ON dv.domain_id = o.domain_id AND dv.var_id = o.var_id AND dv.val = o.vervar
LEFT JOIN dim_turvar dt
    ON dt.domain_id = o.domain_id AND dt.var_id = o.var_id AND dt.val = o.turvar
LEFT JOIN period p
    ON p.domain_id = o.domain_id AND p.var_id = o.var_id AND p.th_id = o.th
LEFT JOIN dim_turth dth
    ON dth.domain_id = o.domain_id AND dth.var_id = o.var_id AND dth.val = o.turth
"""

V_TRADE = """
CREATE VIEW v_trade AS
SELECT
    t.flow,
    CASE t.flow WHEN 1 THEN 'export' WHEN 2 THEN 'import' END AS flow_label,
    t.period_type,
    CASE t.period_type WHEN 1 THEN 'monthly' WHEN 2 THEN 'annual' END AS period_label,
    t.year,
    NULLIF(t.month, 0) AS month,
    t.hs2,
    h.description AS hs_description,
    NULLIF(t.port, '') AS port,
    t.country,
    t.value_usd,
    t.netweight_kg,
    t.fetched_at
FROM trade_flow t
LEFT JOIN hs_chapter h ON h.hs2 = t.hs2
"""

V_INDICATOR_LATEST = """
CREATE VIEW v_indicator_latest AS
SELECT DISTINCT ON (s.domain_id, s.indicator_id)
    s.domain_id,
    d.name AS domain_name,
    d.level AS domain_level,
    s.indicator_id,
    s.var,
    s.subject_csa,
    s.title,
    s.name,
    s.value,
    s.unit,
    s.periode,
    s.category,
    s.data_source,
    s.first_seen,
    s.last_seen
FROM indicator_snapshot s
LEFT JOIN domain d ON d.domain_id = s.domain_id
ORDER BY s.domain_id, s.indicator_id, s.last_seen DESC, s.first_seen DESC, s.periode DESC,
    s.title DESC
"""


def upgrade() -> None:
    op.execute(V_OBSERVATION)
    op.execute(V_TRADE)
    op.execute(V_INDICATOR_LATEST)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_indicator_latest")
    op.execute("DROP VIEW IF EXISTS v_trade")
    op.execute("DROP VIEW IF EXISTS v_observation")

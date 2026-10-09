"""U6: ``mv_trade_rollup`` — trade totals per period by HS chapter, country and port.

One row per ``(grp, flow, period_type, year, month, key)``: ``grp`` is the dimension the row
totals over — ``total`` (``key = ''``), ``hs2``, ``country`` or ``port`` (``key`` = its value;
``''`` keeps meaning "not stated" as in ``trade_flow``). A few hundred rows per period instead of
tens of thousands, so the trade dashboard's summary / breakdown / series queries read a small
index range (``bps_fetcher.api.trade``).

``REFRESH MATERIALIZED VIEW CONCURRENTLY`` (readers never blocked) needs the unique index; the
worker refreshes after trade tasks complete (``bps_fetcher.rollup``). Creating it only reads
``trade_flow`` (ACCESS SHARE), so it can run while a crawl writes.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-09 23:59:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MV = """
CREATE MATERIALIZED VIEW mv_trade_rollup AS
SELECT
    CASE GROUPING(hs2, country, port)
        WHEN 7 THEN 'total' WHEN 3 THEN 'hs2' WHEN 5 THEN 'country' WHEN 6 THEN 'port'
    END AS grp,
    flow,
    period_type,
    year,
    month,
    COALESCE(hs2, country, port, '') AS key,
    sum(value_usd) AS value_usd,
    sum(netweight_kg) AS netweight_kg,
    count(*) AS n_rows
FROM trade_flow
GROUP BY GROUPING SETS (
    (flow, period_type, year, month),
    (flow, period_type, year, month, hs2),
    (flow, period_type, year, month, country),
    (flow, period_type, year, month, port)
)
WITH DATA
"""


def upgrade() -> None:
    op.execute(_MV)
    op.execute(
        "CREATE UNIQUE INDEX ux_mv_trade_rollup "
        "ON mv_trade_rollup (grp, flow, period_type, year, month, key)"
    )
    op.execute(
        "CREATE INDEX ix_mv_trade_rollup_key "
        "ON mv_trade_rollup (grp, key, flow, period_type, year, month)"
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_trade_rollup")

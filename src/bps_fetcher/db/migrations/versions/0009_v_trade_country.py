"""S18: ``v_trade`` maps the new country sentinel back: ``''`` (``ctr: null``) -> NULL.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-09 22:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_V_TRADE = """
CREATE OR REPLACE VIEW v_trade AS
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
    {country} AS country,
    t.value_usd,
    t.netweight_kg,
    t.fetched_at
FROM trade_flow t
LEFT JOIN hs_chapter h ON h.hs2 = t.hs2
"""


def upgrade() -> None:
    op.execute(_V_TRADE.format(country="NULLIF(t.country, '')"))


def downgrade() -> None:
    op.execute(_V_TRADE.format(country="t.country"))

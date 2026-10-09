"""S16: hs_chapter + trade_flow (foreign trade; annual rows use month 0, missing port '').

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-09 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hs_chapter",
        sa.Column("hs2", sa.String(length=2), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_flow", sa.SmallInteger(), nullable=False),
        sa.Column("source_year", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("hs2 ~ '^[0-9]{2}$'", name=op.f("ck_hs_chapter_hs2")),
        sa.PrimaryKeyConstraint("hs2", name=op.f("pk_hs_chapter")),
    )
    op.create_table(
        "trade_flow",
        sa.Column("flow", sa.SmallInteger(), nullable=False),
        sa.Column("period_type", sa.SmallInteger(), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("hs2", sa.String(length=2), nullable=False),
        sa.Column("port", sa.Text(), nullable=False),
        sa.Column("country", sa.Text(), nullable=False),
        sa.Column("value_usd", sa.Numeric(), nullable=True),
        sa.Column("netweight_kg", sa.Numeric(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("flow IN (1, 2)", name=op.f("ck_trade_flow_flow")),
        sa.CheckConstraint(
            "(period_type = 2 AND month = 0) OR (period_type = 1 AND month BETWEEN 1 AND 12)",
            name=op.f("ck_trade_flow_period_month"),
        ),
        sa.ForeignKeyConstraint(
            ["hs2"], ["hs_chapter.hs2"], name=op.f("fk_trade_flow_hs2_hs_chapter")
        ),
        sa.PrimaryKeyConstraint(
            "flow",
            "period_type",
            "year",
            "month",
            "hs2",
            "port",
            "country",
            name=op.f("pk_trade_flow"),
        ),
    )
    op.create_index("ix_trade_flow_hs2_year", "trade_flow", ["hs2", "year"], unique=False)
    op.create_index("ix_trade_flow_country_year", "trade_flow", ["country", "year"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_trade_flow_country_year", table_name="trade_flow")
    op.drop_index("ix_trade_flow_hs2_year", table_name="trade_flow")
    op.drop_table("trade_flow")
    op.drop_table("hs_chapter")

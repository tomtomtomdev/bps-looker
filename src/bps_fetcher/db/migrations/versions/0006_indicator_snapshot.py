"""S14: indicator_snapshot (history of strategic indicators; FK to domain, cascade on delete).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-09 11:46:21.395520
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "indicator_snapshot",
        sa.Column("domain_id", sa.String(length=4), nullable=False),
        sa.Column("indicator_id", sa.Integer(), nullable=False),
        sa.Column("periode", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("var", sa.Integer(), nullable=True),
        sa.Column("subject_csa", sa.Integer(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("value", sa.Numeric(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("category", sa.Integer(), nullable=True),
        sa.Column("data_source", sa.Text(), nullable=True),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["domain.domain_id"],
            name=op.f("fk_indicator_snapshot_domain_id_domain"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "domain_id", "indicator_id", "periode", "title", name=op.f("pk_indicator_snapshot")
        ),
    )


def downgrade() -> None:
    op.drop_table("indicator_snapshot")

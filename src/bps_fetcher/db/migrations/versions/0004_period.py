"""S10: period (FK to variable, cascade on delete).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09 10:39:28.335544
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "period",
        sa.Column("domain_id", sa.String(length=4), nullable=False),
        sa.Column("var_id", sa.Integer(), nullable=False),
        sa.Column("th_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["domain_id", "var_id"],
            ["variable.domain_id", "variable.var_id"],
            name=op.f("fk_period_domain_id_variable"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("domain_id", "var_id", "th_id", name=op.f("pk_period")),
    )


def downgrade() -> None:
    op.drop_table("period")

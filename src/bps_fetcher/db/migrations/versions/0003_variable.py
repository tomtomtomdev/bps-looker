"""S9: variable catalog (FK to domain, cascade on delete).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09 10:35:51.294768
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "variable",
        sa.Column("domain_id", sa.String(length=4), nullable=False),
        sa.Column("var_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("sub_id", sa.Integer(), nullable=True),
        sa.Column("sub_name", sa.Text(), nullable=True),
        sa.Column("subcsa_id", sa.Integer(), nullable=True),
        sa.Column("subcsa_name", sa.Text(), nullable=True),
        sa.Column("def", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("decimal", sa.Integer(), nullable=True),
        sa.Column("vertical", sa.Integer(), nullable=True),
        sa.Column("last_update", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["domain.domain_id"],
            name=op.f("fk_variable_domain_id_domain"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("domain_id", "var_id", name=op.f("pk_variable")),
    )


def downgrade() -> None:
    op.drop_table("variable")

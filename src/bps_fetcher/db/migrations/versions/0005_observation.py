"""S12: dim_vervar, dim_turvar, dim_turth, observation (FKs to variable, cascade on delete).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09 10:49:41.910988
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dim_turth",
        sa.Column("domain_id", sa.String(length=4), nullable=False),
        sa.Column("var_id", sa.Integer(), nullable=False),
        sa.Column("val", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["domain_id", "var_id"],
            ["variable.domain_id", "variable.var_id"],
            name=op.f("fk_dim_turth_domain_id_variable"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("domain_id", "var_id", "val", name=op.f("pk_dim_turth")),
    )
    op.create_table(
        "dim_turvar",
        sa.Column("domain_id", sa.String(length=4), nullable=False),
        sa.Column("var_id", sa.Integer(), nullable=False),
        sa.Column("val", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["domain_id", "var_id"],
            ["variable.domain_id", "variable.var_id"],
            name=op.f("fk_dim_turvar_domain_id_variable"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("domain_id", "var_id", "val", name=op.f("pk_dim_turvar")),
    )
    op.create_table(
        "dim_vervar",
        sa.Column("domain_id", sa.String(length=4), nullable=False),
        sa.Column("var_id", sa.Integer(), nullable=False),
        sa.Column("val", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("group_label", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["domain_id", "var_id"],
            ["variable.domain_id", "variable.var_id"],
            name=op.f("fk_dim_vervar_domain_id_variable"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("domain_id", "var_id", "val", name=op.f("pk_dim_vervar")),
    )
    op.create_table(
        "observation",
        sa.Column("domain_id", sa.String(length=4), nullable=False),
        sa.Column("var_id", sa.Integer(), nullable=False),
        sa.Column("vervar", sa.Integer(), nullable=False),
        sa.Column("turvar", sa.Integer(), nullable=False),
        sa.Column("th", sa.Integer(), nullable=False),
        sa.Column("turth", sa.Integer(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("last_update", sa.DateTime(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["domain_id", "var_id"],
            ["variable.domain_id", "variable.var_id"],
            name=op.f("fk_observation_domain_id_variable"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "domain_id", "var_id", "vervar", "turvar", "th", "turth", name=op.f("pk_observation")
        ),
    )
    op.create_index(
        "ix_observation_domain_id_var_id_th",
        "observation",
        ["domain_id", "var_id", "th"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_observation_domain_id_var_id_th", table_name="observation")
    op.drop_table("observation")
    op.drop_table("dim_vervar")
    op.drop_table("dim_turvar")
    op.drop_table("dim_turth")

"""S5: raw_response, task, domain.

Revision ID: 0001
Revises:
Create Date: 2026-10-09 10:21:38.523629
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain",
        sa.Column("domain_id", sa.String(length=4), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("level", sa.Text(), nullable=False),
        sa.CheckConstraint("level IN ('pusat', 'prov', 'kab')", name=op.f("ck_domain_level")),
        sa.PrimaryKeyConstraint("domain_id", name=op.f("pk_domain")),
    )
    op.create_table(
        "task",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("params_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_run_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["task.id"], name=op.f("fk_task_parent_id_task"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task")),
        sa.UniqueConstraint("kind", "params_hash", name=op.f("uq_task_kind_params_hash")),
    )
    op.create_index("ix_task_status_next_run_at", "task", ["status", "next_run_at"], unique=False)
    op.create_table(
        "raw_response",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["task.id"], name=op.f("fk_raw_response_task_id_task"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_response")),
    )
    op.create_index(op.f("ix_raw_response_task_id"), "raw_response", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_raw_response_task_id"), table_name="raw_response")
    op.drop_table("raw_response")
    op.drop_index("ix_task_status_next_run_at", table_name="task")
    op.drop_table("task")
    op.drop_table("domain")

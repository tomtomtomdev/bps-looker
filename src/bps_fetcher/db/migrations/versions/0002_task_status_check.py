"""S6: constrain task.status to the queue's lifecycle values.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09 10:24:47.773452

Written by hand: Alembic autogenerate does not detect check constraints.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_task_status"), "task", "status IN ('pending', 'running', 'done', 'dead')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_task_status"), "task", type_="check")

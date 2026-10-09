"""U2: full-text search over the variable catalog — GIN expression index
``ix_variable_search_vector`` on ``schema.VARIABLE_SEARCH_VECTOR`` (title A + subject/category B,
'simple' config).

Built ``CONCURRENTLY`` (outside the migration transaction): no table rewrite and no write lock,
so it can run while a crawl writes ``variable``; it only waits for transactions already open.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-09 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of ``schema.VARIABLE_SEARCH_VECTOR`` (a later change needs a new migration).
_VECTOR = (
    "setweight(to_tsvector('simple'::regconfig, title), 'A') || "
    "setweight(to_tsvector('simple'::regconfig, "
    "coalesce(sub_name, '') || ' ' || coalesce(subcsa_name, '')), 'B')"
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_variable_search_vector")  # failed build
        op.create_index(
            "ix_variable_search_vector",
            "variable",
            [sa.text(f"({_VECTOR})")],
            postgresql_using="gin",
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_variable_search_vector", table_name="variable", postgresql_concurrently=True
        )

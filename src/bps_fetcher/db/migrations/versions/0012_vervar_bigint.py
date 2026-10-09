"""S19: vervar codes as bigint — regency vars list villages (10-digit codes such as 3401010001,
above int32) as their vertical dimension.

Widens ``dim_vervar.val`` and ``observation.vervar`` to ``bigint`` (rewrites both tables and their
primary keys; ~2 GB of observations took about a minute locally). ``v_observation`` selects
``o.vervar``, so it is dropped and recreated unchanged (definition as in ``0008``). Downgrade
narrows back to ``integer`` and fails if a stored code no longer fits.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-10 05:10:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V_OBSERVATION = """
CREATE VIEW v_observation AS
SELECT
    o.domain_id,
    d.name AS domain_name,
    d.level AS domain_level,
    o.var_id,
    v.title AS var_title,
    v.unit,
    v.sub_name AS subject,
    o.vervar,
    dv.label AS vervar_label,
    dv.group_label AS vervar_group,
    o.turvar,
    dt.label AS turvar_label,
    o.th,
    p.label AS year_label,
    CASE WHEN p.label ~ '^[0-9]{4}$' THEN p.label::integer END AS year,
    o.turth,
    dth.label AS turth_label,
    o.value,
    v.decimal,
    o.last_update,
    o.fetched_at
FROM observation o
LEFT JOIN domain d ON d.domain_id = o.domain_id
LEFT JOIN variable v ON v.domain_id = o.domain_id AND v.var_id = o.var_id
LEFT JOIN dim_vervar dv
    ON dv.domain_id = o.domain_id AND dv.var_id = o.var_id AND dv.val = o.vervar
LEFT JOIN dim_turvar dt
    ON dt.domain_id = o.domain_id AND dt.var_id = o.var_id AND dt.val = o.turvar
LEFT JOIN period p
    ON p.domain_id = o.domain_id AND p.var_id = o.var_id AND p.th_id = o.th
LEFT JOIN dim_turth dth
    ON dth.domain_id = o.domain_id AND dth.var_id = o.var_id AND dth.val = o.turth
"""


def _retype(sql_type: str) -> None:
    op.execute("DROP VIEW IF EXISTS v_observation")
    op.execute(f"ALTER TABLE dim_vervar ALTER COLUMN val TYPE {sql_type}")
    op.execute(f"ALTER TABLE observation ALTER COLUMN vervar TYPE {sql_type}")
    op.execute(V_OBSERVATION)


def upgrade() -> None:
    _retype("bigint")


def downgrade() -> None:
    _retype("integer")

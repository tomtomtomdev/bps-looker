"""S17: labeled read views ``v_observation``, ``v_trade``, ``v_indicator_latest`` (migration 0008;
DB tests need Postgres)."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, insert, text

from bps_fetcher.db.schema import (
    dim_turth,
    dim_turvar,
    dim_vervar,
    domain,
    hs_chapter,
    indicator_snapshot,
    observation,
    period,
    trade_flow,
    variable,
)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _rows(engine: Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings()]


@pytest.fixture
def seeded(db_engine: Engine) -> Engine:
    with db_engine.begin() as conn:
        conn.execute(
            insert(domain),
            [
                {"domain_id": "0000", "name": "Pusat", "level": "pusat"},
                {"domain_id": "3100", "name": "DKI Jakarta", "level": "prov"},
            ],
        )
        conn.execute(
            insert(variable),
            [
                {
                    "domain_id": "0000",
                    "var_id": 2263,
                    "title": "Inflasi bulanan",
                    "unit": "Persen",
                    "sub_name": "Inflasi",
                },
                {
                    "domain_id": "0000",
                    "var_id": 1804,
                    "title": "Korban",
                    "unit": None,
                    "sub_name": None,
                },
                {
                    "domain_id": "3100",
                    "var_id": 2263,
                    "title": "Inflasi DKI",
                    "unit": "%",
                    "sub_name": None,
                },
            ],
        )
        conn.execute(
            insert(period),
            [
                {"domain_id": "0000", "var_id": 2263, "th_id": 124, "label": "2024"},
                {"domain_id": "0000", "var_id": 1804, "th_id": 117, "label": "2017"},
            ],
        )
        conn.execute(
            insert(dim_vervar),
            [
                {
                    "domain_id": "0000",
                    "var_id": 2263,
                    "val": 1100,
                    "label": "Aceh",
                    "group_label": "Provinsi",
                },
            ],
        )
        conn.execute(
            insert(dim_turvar),
            [{"domain_id": "0000", "var_id": 2263, "val": 0, "label": "Tidak ada"}],
        )
        conn.execute(
            insert(dim_turth),
            [
                {"domain_id": "0000", "var_id": 2263, "val": 3, "label": "Maret"},
                {"domain_id": "0000", "var_id": 1804, "val": 0, "label": "Tahun"},
            ],
        )
        conn.execute(
            insert(observation),
            [
                # fully labeled
                {
                    "domain_id": "0000",
                    "var_id": 2263,
                    "vervar": 1100,
                    "turvar": 0,
                    "th": 124,
                    "turth": 3,
                    "value": Decimal("0.52"),
                },
                # missing vervar/turvar labels and missing year label (no period row for 125)
                {
                    "domain_id": "0000",
                    "var_id": 2263,
                    "vervar": 9999,
                    "turvar": 7,
                    "th": 125,
                    "turth": 4,
                    "value": Decimal("1.5"),
                },
                {
                    "domain_id": "0000",
                    "var_id": 1804,
                    "vervar": 1,
                    "turvar": 0,
                    "th": 117,
                    "turth": 0,
                    "value": Decimal("42"),
                },
                # other domain, same var id: must not mix labels across domains
                {
                    "domain_id": "3100",
                    "var_id": 2263,
                    "vervar": 1100,
                    "turvar": 0,
                    "th": 124,
                    "turth": 3,
                    "value": Decimal("9"),
                },
            ],
        )
    return db_engine


# --- v_observation -------------------------------------------------------------------------------


def test_v_observation_labels(seeded: Engine) -> None:
    rows = _rows(
        seeded,
        "SELECT * FROM v_observation WHERE domain_id = '0000' AND var_id = 2263 AND th = 124",
    )
    assert rows == [
        {
            "domain_id": "0000",
            "domain_name": "Pusat",
            "domain_level": "pusat",
            "var_id": 2263,
            "var_title": "Inflasi bulanan",
            "unit": "Persen",
            "subject": "Inflasi",
            "vervar": 1100,
            "vervar_label": "Aceh",
            "vervar_group": "Provinsi",
            "turvar": 0,
            "turvar_label": "Tidak ada",
            "th": 124,
            "year_label": "2024",
            "year": 2024,
            "turth": 3,
            "turth_label": "Maret",
            "value": Decimal("0.52"),
            "decimal": None,
            "last_update": None,
            "fetched_at": rows[0]["fetched_at"],
        }
    ]


def test_v_observation_keeps_rows_with_missing_labels(seeded: Engine) -> None:
    rows = _rows(
        seeded,
        "SELECT vervar_label, vervar_group, turvar_label, year_label, year, turth_label, value"
        " FROM v_observation WHERE domain_id = '0000' AND var_id = 2263 AND th = 125",
    )
    assert rows == [
        {
            "vervar_label": None,
            "vervar_group": None,
            "turvar_label": None,
            "year_label": None,
            "year": None,
            "turth_label": None,
            "value": Decimal("1.5"),
        }
    ]


def test_v_observation_returns_every_observation(seeded: Engine) -> None:
    total = _rows(seeded, "SELECT count(*) AS n FROM v_observation")[0]["n"]
    assert total == 4
    other = _rows(
        seeded,
        "SELECT var_title, unit, vervar_label, year_label, turth_label FROM v_observation"
        " WHERE domain_id = '3100'",
    )
    assert other == [
        {
            "var_title": "Inflasi DKI",
            "unit": "%",
            "vervar_label": None,
            "year_label": None,
            "turth_label": None,
        }
    ]


def test_v_observation_annual_var(seeded: Engine) -> None:
    rows = _rows(
        seeded,
        "SELECT unit, year, turth, turth_label, value FROM v_observation WHERE var_id = 1804",
    )
    assert rows == [
        {"unit": None, "year": 2017, "turth": 0, "turth_label": "Tahun", "value": Decimal("42")}
    ]


def _plan(engine: Engine, sql: str) -> str:
    with engine.connect() as conn:
        conn.execute(text("SET enable_seqscan = off"))
        return "\n".join(r[0] for r in conn.execute(text(f"EXPLAIN {sql}")))


def test_v_observation_filter_uses_observation_index(seeded: Engine) -> None:
    plan = _plan(
        seeded, "SELECT value FROM v_observation WHERE domain_id = '0000' AND var_id = 2263"
    )
    assert "Seq Scan on observation" not in plan
    assert "observation" in plan
    # Unused LEFT JOINs on unique keys are removed entirely.
    assert "dim_vervar" not in plan
    assert "period" not in plan


# --- v_trade -------------------------------------------------------------------------------------


@pytest.fixture
def trade_seeded(db_engine: Engine) -> Engine:
    with db_engine.begin() as conn:
        conn.execute(
            insert(hs_chapter),
            [
                {"hs2": "03", "description": "Fish", "source_flow": 1, "source_year": 2024},
                {"hs2": "99", "description": "Other", "source_flow": 1, "source_year": 2024},
            ],
        )
        base = {"hs2": "03", "country": "Japan", "netweight_kg": Decimal("10")}
        conn.execute(
            insert(trade_flow),
            [
                {
                    **base,
                    "flow": 1,
                    "period_type": 1,
                    "year": 2024,
                    "month": 3,
                    "port": "Tanjung Priok",
                    "value_usd": Decimal("100.5"),
                },
                {
                    **base,
                    "flow": 2,
                    "period_type": 2,
                    "year": 2024,
                    "month": 0,
                    "port": "",
                    "value_usd": None,
                },
            ],
        )
    return db_engine


def test_v_trade_labels(trade_seeded: Engine) -> None:
    rows = _rows(
        trade_seeded,
        "SELECT flow, flow_label, period_type, period_label, year, month, hs2, hs_description,"
        " port, country, value_usd, netweight_kg FROM v_trade ORDER BY flow",
    )
    assert rows == [
        {
            "flow": 1,
            "flow_label": "export",
            "period_type": 1,
            "period_label": "monthly",
            "year": 2024,
            "month": 3,
            "hs2": "03",
            "hs_description": "Fish",
            "port": "Tanjung Priok",
            "country": "Japan",
            "value_usd": Decimal("100.5"),
            "netweight_kg": Decimal("10"),
        },
        {
            "flow": 2,
            "flow_label": "import",
            "period_type": 2,
            "period_label": "annual",
            "year": 2024,
            "month": None,
            "hs2": "03",
            "hs_description": "Fish",
            "port": None,
            "country": "Japan",
            "value_usd": None,
            "netweight_kg": Decimal("10"),
        },
    ]


def test_v_trade_filter_uses_index(trade_seeded: Engine) -> None:
    plan = _plan(trade_seeded, "SELECT value_usd FROM v_trade WHERE hs2 = '03' AND year = 2024")
    assert "Seq Scan on trade_flow" not in plan
    assert "hs_chapter" not in plan  # unused label join removed


# --- v_indicator_latest --------------------------------------------------------------------------


def _snap(domain_id: str, indicator_id: int, periode: str, **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "domain_id": domain_id,
        "indicator_id": indicator_id,
        "periode": periode,
        "title": f"Ind {indicator_id}, {periode}",
        "var": 1,
        "subject_csa": None,
        "name": f"Ind {indicator_id}",
        "value": Decimal("1"),
        "unit": "Persen",
        "category": 1,
        "data_source": "BPS",
        "first_seen": T0,
        "last_seen": T0,
    }
    row.update(kw)
    return row


def test_v_indicator_latest(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        conn.execute(
            insert(domain),
            [
                {"domain_id": "0000", "name": "Pusat", "level": "pusat"},
                {"domain_id": "3100", "name": "DKI Jakarta", "level": "prov"},
            ],
        )
        conn.execute(
            insert(indicator_snapshot),
            [
                # indicator 3: August seen first, September replaces it later
                _snap("0000", 3, "Agustus 2026", value=Decimal("2.1"), last_seen=T0),
                _snap(
                    "0000",
                    3,
                    "September 2026",
                    value=Decimal("2.3"),
                    first_seen=T1,
                    last_seen=T1,
                ),
                # indicator 5: only one snapshot, never re-seen
                _snap("0000", 5, "Tahun 2025", value=None),
                # same indicator id in another domain is independent
                _snap("3100", 3, "Agustus 2026", value=Decimal("1.9")),
            ],
        )
    rows = _rows(
        db_engine,
        "SELECT domain_id, domain_name, indicator_id, periode, value FROM v_indicator_latest"
        " ORDER BY domain_id, indicator_id",
    )
    assert rows == [
        {
            "domain_id": "0000",
            "domain_name": "Pusat",
            "indicator_id": 3,
            "periode": "September 2026",
            "value": Decimal("2.3"),
        },
        {
            "domain_id": "0000",
            "domain_name": "Pusat",
            "indicator_id": 5,
            "periode": "Tahun 2025",
            "value": None,
        },
        {
            "domain_id": "3100",
            "domain_name": "DKI Jakarta",
            "indicator_id": 3,
            "periode": "Agustus 2026",
            "value": Decimal("1.9"),
        },
    ]
    cols = set(_rows(db_engine, "SELECT * FROM v_indicator_latest LIMIT 1")[0])
    assert {"title", "name", "unit", "var", "category", "data_source", "last_seen"} <= cols


def test_v_indicator_latest_filter_pushes_down(db_engine: Engine) -> None:
    plan = _plan(db_engine, "SELECT * FROM v_indicator_latest WHERE domain_id = '0000'")
    assert "Seq Scan on indicator_snapshot" not in plan

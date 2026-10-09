"""U2: ``GET /variables`` — full-text variable search (title + subject) with ranking, filters and
pagination; migration ``0010`` (GIN expression index ``ix_variable_search_vector``).
DB tests need Postgres."""

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, Text, cast, insert, select, text

from bps_fetcher.api import create_app, openapi_schema
from bps_fetcher.api.search import search_query, search_tsquery
from bps_fetcher.db.schema import VARIABLE_SEARCH_VECTOR, domain, variable
from bps_fetcher.settings import ApiSettings


@pytest.fixture
def api(db_engine: Engine) -> FastAPI:
    url = db_engine.url.render_as_string(hide_password=False)
    return create_app(ApiSettings(_env_file=None, database_url=url))


@pytest.fixture
async def client(api: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        api.router.lifespan_context(api),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c,
    ):
        yield c


def _var(
    domain_id: str,
    var_id: int,
    title: str,
    *,
    unit: str | None = "Persen",
    sub: tuple[int, str] | None = (3, "Inflasi"),
    csa: tuple[int, str] | None = (536, "Harga-Harga"),
) -> dict[str, Any]:
    return {
        "domain_id": domain_id,
        "var_id": var_id,
        "title": title,
        "unit": unit,
        "sub_id": sub[0] if sub else None,
        "sub_name": sub[1] if sub else None,
        "subcsa_id": csa[0] if csa else None,
        "subcsa_name": csa[1] if csa else None,
    }


@pytest.fixture
def seeded(db_engine: Engine) -> Engine:
    with db_engine.begin() as conn:
        conn.execute(
            insert(domain),
            [
                {"domain_id": "0000", "name": "Indonesia", "level": "pusat"},
                {"domain_id": "3100", "name": "DKI Jakarta", "level": "prov"},
                {"domain_id": "3171", "name": "Jakarta Selatan", "level": "kab"},
            ],
        )
        conn.execute(
            insert(variable),
            [
                _var("0000", 1, "Inflasi Bulanan (M-to-M) Indonesia"),
                _var(
                    "0000", 2, "Inflasi (2018=100) Menurut Kelompok dan Sub Kelompok 05 Kesehatan"
                ),
                _var("0000", 3, "Indeks Harga Konsumen", unit="Tidak Ada Satuan", sub=(4, "IHK")),
                _var("0000", 4, "Jumlah Penduduk", unit="Jiwa", sub=(12, "Kependudukan"), csa=None),
                _var(
                    "0000", 5, "Garis Kemiskinan", unit="Rupiah", sub=(23, "Kemiskinan"), csa=None
                ),
                _var("3100", 1, "Inflasi Jakarta"),
                _var("3171", 9, "Inflasi Kota", unit=None, sub=None, csa=None),
            ],
        )
    return db_engine


async def _search(client: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    r = await client.get("/variables", params=params)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def _keys(body: dict[str, Any]) -> list[tuple[str, int]]:
    return [(i["domain_id"], i["var_id"]) for i in body["items"]]


# --- tsquery builder ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("inflasi", "inflasi:*"),
        ("  Inflasi  BULANAN ", "inflasi:* & bulanan:*"),
        ("infl", "infl:*"),
        ("M-to-M (2018=100)", "m:* & to:* & 2018:* & 100:*"),
        ("kab/kota's & | ! :*", "kab:* & kota:* & s:*"),
        ("penduduk_usia", "penduduk:* & usia:*"),
        ("inflasi Inflasi", "inflasi:*"),
        ("", None),
        ("  &|!()  ", None),
    ],
)
def test_search_tsquery(q: str, expected: str | None) -> None:
    assert search_tsquery(q) == expected


# --- search -------------------------------------------------------------------------------------


async def test_search_matches_title_with_domain_unit_subject(
    client: httpx.AsyncClient, seeded: Engine
) -> None:
    body = await _search(client, q="inflasi", domain="0000")
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["page_size"] == 20
    first = body["items"][0]
    assert first == {
        "domain_id": "0000",
        "domain_name": "Indonesia",
        "domain_level": "pusat",
        "var_id": 1,
        "title": "Inflasi Bulanan (M-to-M) Indonesia",
        "unit": "Persen",
        "subject_id": 3,
        "subject": "Inflasi",
        "category": "Harga-Harga",
    }
    # Shorter title ranks first (rank normalised by document length).
    assert _keys(body) == [("0000", 1), ("0000", 2)]


async def test_search_is_case_insensitive_and_prefix(
    client: httpx.AsyncClient, seeded: Engine
) -> None:
    body = await _search(client, q="INFL bul")
    assert _keys(body) == [("0000", 1)]
    body = await _search(client, q="kemis")
    assert _keys(body) == [("0000", 5)]


async def test_search_matches_subject(client: httpx.AsyncClient, seeded: Engine) -> None:
    # "Harga" is in var 3's title and in the category (subcsa) of the inflation variables.
    body = await _search(client, q="harga", domain="0000")
    assert set(_keys(body)) == {("0000", 1), ("0000", 2), ("0000", 3)}
    assert _keys(body)[0] == ("0000", 3), "title match outranks a subject-only match"
    # "Kependudukan" only appears in the subject of var 4.
    body = await _search(client, q="kependudukan")
    assert _keys(body) == [("0000", 4)]


async def test_search_all_words_must_match(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _search(client, q="inflasi kesehatan")
    assert _keys(body) == [("0000", 2)]
    body = await _search(client, q="inflasi penduduk")
    assert body == {"items": [], "total": 0, "page": 1, "page_size": 20}


async def test_search_across_domains_and_by_level(
    client: httpx.AsyncClient, seeded: Engine
) -> None:
    body = await _search(client, q="inflasi")
    assert body["total"] == 4
    body = await _search(client, q="inflasi", level="prov")
    assert _keys(body) == [("3100", 1)]
    assert body["items"][0]["domain_name"] == "DKI Jakarta"
    body = await _search(client, q="inflasi", level="kab")
    item = body["items"][0]
    assert (item["unit"], item["subject_id"], item["subject"], item["category"]) == (
        None,
        None,
        None,
        None,
    )


async def test_search_by_subject(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _search(client, subject=3, domain="0000")
    assert _keys(body) == [("0000", 2), ("0000", 1)]  # empty q → by title
    body = await _search(client, q="inflasi", subject=12)
    assert body["total"] == 0


async def test_empty_query_lists_by_title(client: httpx.AsyncClient, seeded: Engine) -> None:
    body = await _search(client, domain="0000")
    titles = [i["title"] for i in body["items"]]
    assert titles == sorted(titles)
    assert body["total"] == 5
    body_blank = await _search(client, q="  ()  ", domain="0000")
    assert body_blank == body


async def test_pagination(client: httpx.AsyncClient, seeded: Engine) -> None:
    page1 = await _search(client, page_size=3)
    page2 = await _search(client, page_size=3, page=2)
    page3 = await _search(client, page_size=3, page=3)
    assert page1["total"] == page2["total"] == page3["total"] == 7
    assert [len(p["items"]) for p in (page1, page2, page3)] == [3, 3, 1]
    keys = _keys(page1) + _keys(page2) + _keys(page3)
    assert len(set(keys)) == 7
    beyond = await _search(client, page_size=3, page=9)
    assert beyond["items"] == []
    assert beyond["total"] == 7


async def test_pagination_of_ranked_results_is_stable(
    client: httpx.AsyncClient, seeded: Engine
) -> None:
    ranked = await _search(client, q="inflasi", page_size=100)
    pages = [await _search(client, q="inflasi", page_size=1, page=n) for n in range(1, 5)]
    assert [k for p in pages for k in _keys(p)] == _keys(ranked)


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"level": "city"},
        {"domain": "00"},
        {"subject": "x"},
        {"q": "x" * 201},
    ],
)
async def test_search_rejects_bad_params(client: httpx.AsyncClient, params: dict[str, Any]) -> None:
    r = await client.get("/variables", params=params)
    assert r.status_code == 422


# --- index --------------------------------------------------------------------------------------


def test_search_vector_weights_title_over_subject(seeded: Engine) -> None:
    with seeded.connect() as conn:
        vec = conn.execute(
            select(cast(VARIABLE_SEARCH_VECTOR, Text)).where(
                variable.c.domain_id == "0000", variable.c.var_id == 4
            )
        ).scalar_one()
    assert vec == "'jumlah':1A 'kependudukan':3B 'penduduk':2A"


def test_search_query_uses_the_gin_index(seeded: Engine) -> None:
    rows, count = search_query("inflasi bul", level="pusat")
    with seeded.connect() as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        for stmt in (rows, count):
            compiled = stmt.compile(seeded, compile_kwargs={"literal_binds": True})
            plan = "\n".join(conn.execute(text(f"EXPLAIN {compiled}")).scalars())
            assert "ix_variable_search_vector" in plan, plan


def test_index_expression_matches_schema(seeded: Engine) -> None:
    """Migration 0010 froze the expression; the query side uses ``schema``'s — same index."""
    with seeded.connect() as conn:
        indexdef = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_variable_search_vector'")
        ).scalar_one()
        conn.execute(text("CREATE TEMP TABLE probe (LIKE variable)"))
        expr = str(VARIABLE_SEARCH_VECTOR.compile(seeded)).replace("variable.", "")
        conn.execute(text(f"CREATE INDEX probe_ix ON probe USING gin (({expr}))"))
        probe = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'probe_ix'")
        ).scalar_one()
    assert probe.split(" USING gin ", 1)[1] == indexdef.split(" USING gin ", 1)[1]


async def test_search_is_fast_on_many_rows(client: httpx.AsyncClient, seeded: Engine) -> None:
    with seeded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO variable (domain_id, var_id, title, sub_id, sub_name) "
                "SELECT '3100', g, 'Variabel ' || g || ' penduduk menurut kecamatan', 12, "
                "'Kependudukan' FROM generate_series(100, 20100) g"
            )
        )
        conn.execute(text("ANALYZE variable"))
    t0 = time.perf_counter()
    body = await _search(client, q="inflasi")
    elapsed = time.perf_counter() - t0
    assert body["total"] == 4
    assert elapsed < 0.5


# --- OpenAPI ------------------------------------------------------------------------------------


def test_openapi_variables_operation() -> None:
    schema = openapi_schema()
    op = schema["paths"]["/variables"]["get"]
    assert op["operationId"] == "searchVariables"
    assert op["tags"] == ["variables"]
    names = {p["name"] for p in op["parameters"]}
    assert names == {"q", "domain", "level", "subject", "page", "page_size"}
    assert {"VariablePage", "VariableSummary"} <= schema["components"]["schemas"].keys()

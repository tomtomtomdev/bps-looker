"""S9: ``var_list`` handler (fake client serving ``var_0000_p1``; DB tests need Postgres)."""

import copy
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, insert, select

from bps_fetcher import queue, worker
from bps_fetcher.client import BpsApiError
from bps_fetcher.db.schema import domain, raw_response, task, variable
from bps_fetcher.handlers.variables import VarItem, var_list
from bps_fetcher.worker import Child, TaskContext

NOT_AVAILABLE = {"status": "OK", "data-availability": "not-available"}


class FakeClient:
    """Serves ``/list?model=var`` pages from ``pages`` (page number -> body); records calls."""

    def __init__(self, pages: dict[int, dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append((path, params))
        assert path == "list"
        assert params["model"] == "var"
        return self.pages[int(params["page"])]


@pytest.fixture
def var_body(fixture_body: Callable[[str], Any]) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(fixture_body("var_0000_p1"))
    # The recording is page 1 of 176; make it a complete single-page listing.
    body["data"][0]["pages"] = 1
    return body


def _page(n: int, pages: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "OK",
        "data-availability": "available",
        "data": [{"page": n, "pages": pages, "per_page": 10, "count": len(items)}, items],
    }


def _item(var_id: int, **extra: Any) -> dict[str, Any]:
    return {
        "var_id": var_id,
        "title": f"Var {var_id}",
        "sub_id": 1,
        "sub_name": "Subjek",
        "subcsa_id": 2,
        "subcsa_name": "CSA",
        "def": "",
        "notes": "",
        "vertical": 7,
        "unit": "Persen",
        "graph_id": 1,
        "graph_name": "bar",
        **extra,
    }


def _task(params: dict[str, Any]) -> queue.Task:
    return queue.Task(id=1, kind="var_list", params=params, attempts=0, parent_id=None)


def _seed_domain(engine: Engine, domain_id: str = "0000") -> None:
    with engine.begin() as conn:
        conn.execute(insert(domain).values(domain_id=domain_id, name="Pusat", level="pusat"))


def _rows(engine: Engine) -> dict[tuple[str, int], Any]:
    with engine.connect() as conn:
        return {(r.domain_id, r.var_id): r for r in conn.execute(select(variable)).all()}


async def _handle(
    engine: Engine, pages: dict[int, dict[str, Any]], params: dict[str, Any]
) -> tuple[FakeClient, worker.HandlerResult]:
    client = FakeClient(pages)
    with engine.begin() as conn:
        result = await var_list(TaskContext(_task(params), client, conn))
    return client, result


# --- pure ----------------------------------------------------------------------------------------


def test_var_item_parses_fixture_items_and_unescapes_notes(var_body: dict[str, Any]) -> None:
    items = [VarItem.model_validate(i) for i in var_body["data"][1]]
    assert len(items) == 10
    first = items[0]
    assert first.var_id == 70
    assert first.sub_id == 2
    assert first.subcsa_id == 565
    assert first.vertical == 1
    assert first.unit == "Tidak Ada Satuan"
    assert first.def_ is None  # "" -> NULL
    assert first.notes is not None
    assert first.notes.startswith("<p><br /></p><p>Sumber: BPS")
    assert "&lt;" not in first.notes


def test_var_item_ignores_unknown_fields() -> None:
    item = VarItem.model_validate(_item(5, brand_new_field={"x": 1}, graph_name="line"))
    assert item.var_id == 5
    assert not hasattr(item, "brand_new_field")


def test_var_item_unescapes_def_and_requires_var_id() -> None:
    item = VarItem.model_validate(_item(5, **{"def": "a &amp; b", "notes": "&quot;x&quot;"}))
    assert item.def_ == "a & b"
    assert item.notes == '"x"'
    bad = _item(5)
    del bad["var_id"]
    with pytest.raises(ValidationError):
        VarItem.model_validate(bad)


def test_registered_in_handlers() -> None:
    assert worker.HANDLERS["var_list"] is var_list


# --- handler + DB --------------------------------------------------------------------------------


async def test_handler_upserts_fixture_stores_raw_and_emits_th_list(
    db_engine: Engine, var_body: dict[str, Any]
) -> None:
    _seed_domain(db_engine)
    client, result = await _handle(db_engine, {1: var_body}, {"domain": "0000"})

    assert client.calls == [("list", {"model": "var", "domain": "0000", "page": 1})]
    rows = _rows(db_engine)
    assert len(rows) == 10
    r = rows[("0000", 70)]
    assert r.title.startswith("Persentase Penduduk Usia 5 tahun")
    assert (r.sub_id, r.sub_name, r.subcsa_id, r.subcsa_name) == (
        2,
        "Komunikasi",
        565,
        "Masyarakat Informasi",
    )
    assert r.unit == "Tidak Ada Satuan"
    assert r.vertical == 1
    assert r._mapping["def"] is None
    assert r.notes.startswith("<p><br /></p>")
    assert r.decimal is None
    assert r.last_update is None
    assert rows[("0000", 402)].vertical == 152

    (raw,) = result.raw
    assert raw.endpoint == "list"
    assert dict(raw.params) == {"model": "var", "domain": "0000", "page": 1}
    assert raw.body is var_body

    var_ids = [i["var_id"] for i in var_body["data"][1]]
    assert result.children == [Child("th_list", {"domain": "0000", "var": v}) for v in var_ids]


async def test_paginates_two_pages_storing_raw_per_page(db_engine: Engine) -> None:
    _seed_domain(db_engine, "3100")
    p1 = _page(1, 2, [_item(1), _item(2)])
    p2 = _page(2, 2, [_item(3)])
    client, result = await _handle(db_engine, {1: p1, 2: p2}, {"domain": "3100"})

    assert [c[1]["page"] for c in client.calls] == [1, 2]
    assert set(_rows(db_engine)) == {("3100", 1), ("3100", 2), ("3100", 3)}
    assert [(r.params["page"], r.body) for r in result.raw] == [(1, p1), (2, p2)]
    assert [c.params for c in result.children] == [
        {"domain": "3100", "var": 1},
        {"domain": "3100", "var": 2},
        {"domain": "3100", "var": 3},
    ]


async def test_upsert_idempotent_and_updates_changes(
    db_engine: Engine, var_body: dict[str, Any]
) -> None:
    _seed_domain(db_engine)
    await _handle(db_engine, {1: var_body}, {"domain": "0000"})
    before = _rows(db_engine)
    await _handle(db_engine, {1: var_body}, {"domain": "0000"})
    assert _rows(db_engine) == before

    # last_update / decimal belong to the data loader (S12): a re-list must not clobber them.
    with db_engine.begin() as conn:
        conn.execute(
            variable.update()
            .where(variable.c.var_id == 70)
            .values(decimal=2, last_update="2026-10-01 11:24:01")
        )
    changed = copy.deepcopy(var_body)
    changed["data"][1][0]["title"] = "Judul baru"
    changed["data"][1][0]["unit"] = "Persen"
    await _handle(db_engine, {1: changed}, {"domain": "0000"})

    after = _rows(db_engine)
    assert len(after) == 10
    r = after[("0000", 70)]
    assert (r.title, r.unit, r.decimal) == ("Judul baru", "Persen", 2)
    assert r.last_update is not None
    assert {k: v for k, v in after.items() if k[1] != 70} == {
        k: v for k, v in before.items() if k[1] != 70
    }


async def test_not_available_stores_raw_and_no_rows(db_engine: Engine) -> None:
    _seed_domain(db_engine)
    _, result = await _handle(db_engine, {1: NOT_AVAILABLE}, {"domain": "0000"})
    assert _rows(db_engine) == {}
    assert result.children == []
    assert [r.body for r in result.raw] == [NOT_AVAILABLE]


async def test_missing_domain_param_or_row_rejected(db_engine: Engine) -> None:
    with pytest.raises(ValueError, match="domain"):
        await _handle(db_engine, {}, {})
    with pytest.raises(LookupError, match="9999"):
        await _handle(db_engine, {1: _page(1, 1, [_item(1)])}, {"domain": "9999"})


async def test_malformed_page_raises(db_engine: Engine) -> None:
    _seed_domain(db_engine)
    with pytest.raises(BpsApiError):
        await _handle(db_engine, {1: {"status": "OK", "data": "nope"}}, {"domain": "0000"})


# --- end to end through the worker ---------------------------------------------------------------


async def test_end_to_end_through_worker(db_engine: Engine) -> None:
    _seed_domain(db_engine)
    with db_engine.begin() as conn:
        parent = queue.enqueue(conn, "var_list", {"domain": "0000"})
    p1 = _page(1, 2, [_item(10, notes="&lt;b&gt;x&lt;/b&gt;"), _item(11)])
    p2 = _page(2, 2, [_item(12)])
    client = FakeClient({1: p1, 2: p2})

    stats = await worker.run_worker(db_engine, client=client, kinds=["var_list"], idle_sleep=0.01)

    assert (stats.done, stats.failed) == (1, 0)
    rows = _rows(db_engine)
    assert set(rows) == {("0000", 10), ("0000", 11), ("0000", 12)}
    assert rows[("0000", 10)].notes == "<b>x</b>"
    with db_engine.connect() as conn:
        tasks = conn.execute(select(task).order_by(task.c.id)).all()
        raws = conn.execute(select(raw_response).order_by(raw_response.c.id)).all()
    (vl,) = [t for t in tasks if t.kind == "var_list"]
    assert vl.id == parent
    assert vl.status == "done"
    children = [t for t in tasks if t.kind == "th_list"]
    assert sorted(c.params["var"] for c in children) == [10, 11, 12]
    assert all(c.parent_id == parent and c.status == "pending" for c in children)
    assert all(c.params["domain"] == "0000" for c in children)
    assert [(r.task_id, r.endpoint, r.params["page"]) for r in raws] == [
        (parent, "list", 1),
        (parent, "list", 2),
    ]
    assert raws[0].body == p1


# --- S13: limit_vars -----------------------------------------------------------------------------


async def test_limit_vars_caps_rows_children_and_stops_paging(db_engine: Engine) -> None:
    _seed_domain(db_engine, "3100")
    pages = {
        1: _page(1, 3, [_item(1), _item(2), _item(3)]),
        2: _page(2, 3, [_item(4), _item(5), _item(6)]),
        3: _page(3, 3, [_item(7)]),
    }
    client, result = await _handle(db_engine, pages, {"domain": "3100", "limit_vars": 4})

    assert [c[1]["page"] for c in client.calls] == [1, 2]
    assert "limit_vars" not in client.calls[0][1]
    assert set(_rows(db_engine)) == {("3100", v) for v in (1, 2, 3, 4)}
    assert [c.params["var"] for c in result.children] == [1, 2, 3, 4]
    assert all("limit_vars" not in c.params for c in result.children)
    assert len(result.raw) == 2


async def test_limit_vars_larger_than_catalog_is_harmless(db_engine: Engine) -> None:
    _seed_domain(db_engine, "3100")
    pages = {1: _page(1, 1, [_item(1), _item(2)])}
    _, result = await _handle(db_engine, pages, {"domain": "3100", "limit_vars": 50})
    assert [c.params["var"] for c in result.children] == [1, 2]


@pytest.mark.parametrize("bad", [0, -3, "5", True])
async def test_bad_limit_vars_rejected(db_engine: Engine, bad: Any) -> None:
    _seed_domain(db_engine, "3100")
    with pytest.raises(ValueError, match="limit_vars"):
        await _handle(db_engine, {}, {"domain": "3100", "limit_vars": bad})

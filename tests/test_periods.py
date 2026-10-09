"""S10: ``th_list`` handler + data windows (fake client serving ``th_0000_1804``; DB tests need
Postgres)."""

import copy
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, insert, select

from bps_fetcher import queue, worker
from bps_fetcher.client import BpsApiError
from bps_fetcher.db.schema import domain, period, raw_response, task, variable
from bps_fetcher.handlers.periods import MAX_TH_PER_CALL, th_list, th_param, windows
from bps_fetcher.worker import Child, TaskContext

NOT_AVAILABLE = {"status": "OK", "data-availability": "not-available"}


class FakeClient:
    """Serves ``/list?model=th`` pages from ``pages`` (page number -> body); records calls."""

    def __init__(self, pages: dict[int, dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append((path, params))
        assert path == "list"
        assert params["model"] == "th"
        return self.pages[int(params["page"])]


def _page(n: int, pages: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "OK",
        "data-availability": "available",
        "data": [{"page": n, "pages": pages, "per_page": 10, "count": len(items)}, items],
    }


def _th(th_id: int, label: str | None = None) -> dict[str, Any]:
    return {"th_id": th_id, "th": label if label is not None else str(1900 + th_id)}


# --- windows (pure) ------------------------------------------------------------------------------


def test_max_th_per_call_is_api_limit(fixture_body: Callable[[str], Any]) -> None:
    assert MAX_TH_PER_CALL == 3
    assert "is 3" in fixture_body("error_data_too_many_th")["message"]


def test_windows_contiguous_range() -> None:
    assert windows(range(110, 127), 3) == [
        [110, 111, 112],
        [113, 114, 115],
        [116, 117, 118],
        [119, 120, 121],
        [122, 123, 124],
        [125, 126],
    ]


def test_windows_split_on_gaps() -> None:
    assert windows([110, 111, 113, 114, 115, 116, 120], 3) == [
        [110, 111],
        [113, 114, 115],
        [116],
        [120],
    ]


def test_windows_sorts_and_dedups() -> None:
    assert windows([119, 117, 118, 117, 113, 119], 3) == [[113], [117, 118, 119]]


def test_windows_edge_cases() -> None:
    assert windows([], 3) == []
    assert windows([5], 3) == [[5]]
    assert windows([1, 2, 3, 4], 1) == [[1], [2], [3], [4]]
    with pytest.raises(ValueError, match="size"):
        windows([1], 0)


def test_windows_never_exceed_size_and_cover_input() -> None:
    ids = [1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 20, 21, 30]
    ws = windows(ids, 3)
    assert [i for w in ws for i in w] == ids
    for w in ws:
        assert 1 <= len(w) <= 3
        assert w == list(range(w[0], w[-1] + 1))


def test_th_param_syntax() -> None:
    # docs/bps-webapi.md: single `117`, range `117:119`.
    assert th_param([117]) == "117"
    assert th_param([117, 118]) == "117:118"
    assert th_param([117, 118, 119]) == "117:119"
    with pytest.raises(ValueError, match="empty"):
        th_param([])
    with pytest.raises(ValueError, match="contiguous"):
        th_param([117, 119])  # not contiguous


def test_registered_in_handlers() -> None:
    assert worker.HANDLERS["th_list"] is th_list


# --- handler + DB --------------------------------------------------------------------------------


def _task(params: dict[str, Any]) -> queue.Task:
    return queue.Task(id=1, kind="th_list", params=params, attempts=0, parent_id=None)


def _seed_variable(engine: Engine, domain_id: str = "0000", var_id: int = 1804) -> None:
    with engine.begin() as conn:
        level = "pusat" if domain_id == "0000" else "prov"
        conn.execute(insert(domain).values(domain_id=domain_id, name="D", level=level))
        conn.execute(insert(variable).values(domain_id=domain_id, var_id=var_id, title="V"))


def _rows(engine: Engine) -> dict[tuple[str, int, int], str]:
    with engine.connect() as conn:
        return {
            (r.domain_id, r.var_id, r.th_id): r.label for r in conn.execute(select(period)).all()
        }


async def _handle(
    engine: Engine, pages: dict[int, dict[str, Any]], params: dict[str, Any]
) -> tuple[FakeClient, worker.HandlerResult]:
    client = FakeClient(pages)
    with engine.begin() as conn:
        result = await th_list(TaskContext(_task(params), client, conn))
    return client, result


async def test_handler_stores_fixture_periods_and_emits_data_windows(
    db_engine: Engine, fixture_body: Callable[[str], Any]
) -> None:
    body = fixture_body("th_0000_1804")
    _seed_variable(db_engine)
    client, result = await _handle(db_engine, {1: body}, {"domain": "0000", "var": 1804})

    assert client.calls == [("list", {"model": "th", "domain": "0000", "var": 1804, "page": 1})]
    assert _rows(db_engine) == {("0000", 1804, t): str(1900 + t) for t in range(113, 120)}

    (raw,) = result.raw
    assert raw.endpoint == "list"
    assert dict(raw.params) == {"model": "th", "domain": "0000", "var": 1804, "page": 1}
    assert raw.body is body

    assert result.children == [
        Child("data", {"domain": "0000", "var": 1804, "th": "113:115"}),
        Child("data", {"domain": "0000", "var": 1804, "th": "116:118"}),
        Child("data", {"domain": "0000", "var": 1804, "th": "119"}),
    ]
    # The recorded >3 error came from asking for all 7 years at once.
    assert "113:119" not in [c.params["th"] for c in result.children]


async def test_paginates_and_splits_gaps(db_engine: Engine) -> None:
    _seed_variable(db_engine, "3100", 5)
    p1 = _page(1, 2, [_th(126), _th(125), _th(123)])
    p2 = _page(2, 2, [_th(122), _th(121), _th(120), _th(119)])
    client, result = await _handle(db_engine, {1: p1, 2: p2}, {"domain": "3100", "var": 5})

    assert [c[1]["page"] for c in client.calls] == [1, 2]
    assert len(_rows(db_engine)) == 7
    assert [(r.params["page"], r.body) for r in result.raw] == [(1, p1), (2, p2)]
    assert [c.params["th"] for c in result.children] == ["119:121", "122:123", "125:126"]
    assert all(c.kind == "data" for c in result.children)


async def test_upsert_idempotent_and_updates_label(db_engine: Engine) -> None:
    _seed_variable(db_engine)
    body = _page(1, 1, [_th(117), _th(118)])
    await _handle(db_engine, {1: body}, {"domain": "0000", "var": 1804})
    before = _rows(db_engine)
    _, again = await _handle(db_engine, {1: body}, {"domain": "0000", "var": 1804})
    assert _rows(db_engine) == before
    assert [c.params["th"] for c in again.children] == ["117:118"]

    changed = copy.deepcopy(body)
    changed["data"][1][0]["th"] = "2017 (revisi)"
    await _handle(db_engine, {1: changed}, {"domain": "0000", "var": 1804})
    assert _rows(db_engine) == {("0000", 1804, 117): "2017 (revisi)", ("0000", 1804, 118): "2018"}


async def test_not_available_stores_raw_no_rows_no_children(db_engine: Engine) -> None:
    _seed_variable(db_engine)
    _, result = await _handle(db_engine, {1: NOT_AVAILABLE}, {"domain": "0000", "var": 1804})
    assert _rows(db_engine) == {}
    assert result.children == []
    assert [r.body for r in result.raw] == [NOT_AVAILABLE]


async def test_bad_params_or_missing_variable_rejected(db_engine: Engine) -> None:
    with pytest.raises(ValueError, match="domain"):
        await _handle(db_engine, {}, {"var": 1804})
    with pytest.raises(ValueError, match="var"):
        await _handle(db_engine, {}, {"domain": "0000"})
    with pytest.raises(LookupError, match="1804"):
        await _handle(db_engine, {1: _page(1, 1, [_th(117)])}, {"domain": "0000", "var": 1804})


async def test_malformed_page_raises(db_engine: Engine) -> None:
    _seed_variable(db_engine)
    with pytest.raises(BpsApiError):
        await _handle(
            db_engine, {1: {"status": "OK", "data": "nope"}}, {"domain": "0000", "var": 1804}
        )


# --- end to end through the worker ---------------------------------------------------------------


async def test_end_to_end_through_worker(
    db_engine: Engine, fixture_body: Callable[[str], Any]
) -> None:
    _seed_variable(db_engine)
    with db_engine.begin() as conn:
        parent = queue.enqueue(conn, "th_list", {"domain": "0000", "var": 1804})
    client = FakeClient({1: fixture_body("th_0000_1804")})

    stats = await worker.run_worker(db_engine, client=client, kinds=["th_list"], idle_sleep=0.01)

    assert (stats.done, stats.failed) == (1, 0)
    assert len(_rows(db_engine)) == 7
    with db_engine.connect() as conn:
        tasks = conn.execute(select(task).order_by(task.c.id)).all()
        raws = conn.execute(select(raw_response)).all()
    (tl,) = [t for t in tasks if t.kind == "th_list"]
    assert tl.id == parent
    assert tl.status == "done"
    data = [t for t in tasks if t.kind == "data"]
    assert [d.params for d in data] == [
        {"domain": "0000", "var": 1804, "th": "113:115"},
        {"domain": "0000", "var": 1804, "th": "116:118"},
        {"domain": "0000", "var": 1804, "th": "119"},
    ]
    assert all(d.parent_id == parent and d.status == "pending" for d in data)
    assert [(r.task_id, r.endpoint, r.params) for r in raws] == [
        (parent, "list", {"model": "th", "domain": "0000", "var": 1804, "page": 1})
    ]

    # Re-running th_list (e.g. a refresh) enqueues no duplicate data tasks.
    with db_engine.begin() as conn:
        conn.execute(task.update().where(task.c.id == parent).values(status="pending"))
    await worker.run_worker(db_engine, client=client, kinds=["th_list"], idle_sleep=0.01)
    with db_engine.connect() as conn:
        n_data = len(conn.execute(select(task).where(task.c.kind == "data")).all())
    assert n_data == 3

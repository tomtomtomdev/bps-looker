"""S8: ``domains`` handler (fake client serving ``domain_all``; DB tests skip without Postgres)."""

import copy
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, select

from bps_fetcher import queue, worker
from bps_fetcher.client import BpsApiError
from bps_fetcher.db.schema import domain, raw_response, task
from bps_fetcher.handlers.domains import (
    DomainRow,
    domain_level,
    domains,
    parse_domains,
)
from bps_fetcher.worker import Child, TaskContext


class FakeClient:
    """Returns a canned ``/domain`` body and records calls (never touches the network)."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append((path, params))
        assert path == "domain"
        return self.body


@pytest.fixture
def domain_body(fixture_body: Callable[[str], Any]) -> dict[str, Any]:
    body: dict[str, Any] = fixture_body("domain_all")
    return body


def _task(params: dict[str, Any]) -> queue.Task:
    return queue.Task(id=1, kind="domains", params=params, attempts=0, parent_id=None)


def _rows(engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        return {r.domain_id: r for r in conn.execute(select(domain)).all()}


async def _handle(engine: Engine, body: dict[str, Any], params: dict[str, Any]) -> Any:
    client = FakeClient(body)
    with engine.begin() as conn:
        result = await domains(TaskContext(_task(params), client, conn))
    return client, result


# --- pure ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain_id", "level"),
    [("0000", "pusat"), ("1100", "prov"), ("3100", "prov"), ("1101", "kab"), ("3171", "kab")],
)
def test_domain_level_from_id(domain_id: str, level: str) -> None:
    assert domain_level(domain_id) == level


@pytest.mark.parametrize("bad", ["", "123", "12345", "abcd", "11a0"])
def test_domain_level_rejects_malformed_ids(bad: str) -> None:
    with pytest.raises(ValueError, match="domain_id"):
        domain_level(bad)


def test_parse_domain_all_fixture(domain_body: dict[str, Any]) -> None:
    rows = parse_domains(domain_body)
    assert len(rows) == 549
    assert len({r.domain_id for r in rows}) == 549
    assert rows[0] == DomainRow("0000", "Pusat", "https://www.bps.go.id", "pusat")
    assert rows[1] == DomainRow("1100", "Aceh", "https://aceh.bps.go.id", "prov")
    levels = [r.level for r in rows]
    assert levels.count("pusat") == 1
    assert levels.count("prov") == 34  # the 2024 recording still lists 34 provinces
    assert levels.count("kab") == 549 - 1 - 34


def test_parse_not_available_is_empty() -> None:
    assert parse_domains({"status": "OK", "data-availability": "not-available"}) == []


def test_parse_malformed_body_raises() -> None:
    with pytest.raises(BpsApiError):
        parse_domains({"status": "OK", "data": "nope"})


# --- handler + DB --------------------------------------------------------------------------------


async def test_handler_writes_549_rows_raw_and_var_list_children(
    db_engine: Engine, domain_body: dict[str, Any]
) -> None:
    client, result = await _handle(db_engine, domain_body, {})

    assert client.calls == [("domain", {"type": "all"})]
    rows = _rows(db_engine)
    assert len(rows) == 549
    assert rows["0000"].level == "pusat"
    assert rows["1100"].level == "prov"
    assert rows["1101"].level == "kab"
    assert rows["1101"].name == "Simeulue"
    assert rows["1101"].url == "https://simeuluekab.bps.go.id"

    (raw,) = result.raw
    assert raw.endpoint == "domain"
    assert dict(raw.params) == {"type": "all"}
    assert raw.body is domain_body

    assert len(result.children) == 549
    assert result.children[0] == Child("var_list", {"domain": "0000"})
    assert {c.kind for c in result.children} == {"var_list"}


async def test_upsert_is_idempotent_and_updates_changed_names_and_urls(
    db_engine: Engine, domain_body: dict[str, Any]
) -> None:
    await _handle(db_engine, domain_body, {})
    before = _rows(db_engine)
    await _handle(db_engine, domain_body, {})
    assert _rows(db_engine) == before

    changed = copy.deepcopy(domain_body)
    item = next(i for i in changed["data"][1] if i["domain_id"] == "1101")
    item["domain_name"] = "Simeulue (baru)"
    item["domain_url"] = "https://simeulue.bps.go.id"
    await _handle(db_engine, changed, {})

    after = _rows(db_engine)
    assert len(after) == 549
    assert after["1101"].name == "Simeulue (baru)"
    assert after["1101"].url == "https://simeulue.bps.go.id"
    assert {k: v for k, v in after.items() if k != "1101"} == {
        k: v for k, v in before.items() if k != "1101"
    }


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("pusat", ["0000"]),
        (["pusat", "prov"], None),
        ("kab", None),
    ],
)
async def test_children_filterable_by_level_but_all_rows_stored(
    db_engine: Engine, domain_body: dict[str, Any], level: Any, expected: list[str] | None
) -> None:
    _, result = await _handle(db_engine, domain_body, {"level": level})

    assert len(_rows(db_engine)) == 549  # catalog is always complete
    wanted = {level} if isinstance(level, str) else set(level)
    ids = [c.params["domain"] for c in result.children]
    assert ids
    assert all(domain_level(i) in wanted for i in ids)
    all_ids = [i["domain_id"] for i in domain_body["data"][1]]
    assert len(ids) == sum(1 for i in all_ids if domain_level(i) in wanted)
    if expected is not None:
        assert ids == expected


async def test_unknown_level_filter_rejected(
    db_engine: Engine, domain_body: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="level"):
        await _handle(db_engine, domain_body, {"level": "desa"})
    assert _rows(db_engine) == {}


async def test_api_params_passed_through(db_engine: Engine, domain_body: dict[str, Any]) -> None:
    body = copy.deepcopy(domain_body)
    body["data"][1] = [i for i in body["data"][1] if i["domain_id"].startswith("11")]
    client, result = await _handle(db_engine, body, {"type": "kabbyprov", "prov": "1100"})
    assert client.calls == [("domain", {"type": "kabbyprov", "prov": "1100"})]
    assert dict(result.raw[0].params) == {"type": "kabbyprov", "prov": "1100"}
    assert set(_rows(db_engine)) == {i["domain_id"] for i in body["data"][1]}


def test_registered_in_handlers() -> None:
    assert worker.HANDLERS["domains"] is domains


# --- end to end through the worker ---------------------------------------------------------------


async def test_end_to_end_through_worker(db_engine: Engine, domain_body: dict[str, Any]) -> None:
    with db_engine.begin() as conn:
        parent = queue.enqueue(conn, "domains", {"level": "prov"})
    client = FakeClient(domain_body)

    stats = await worker.run_worker(db_engine, client=client, kinds=["domains"], idle_sleep=0.01)

    assert stats.done == 1
    assert stats.failed == 0
    assert len(_rows(db_engine)) == 549
    with db_engine.connect() as conn:
        tasks = conn.execute(select(task)).all()
        raws = conn.execute(select(raw_response)).all()
    by_kind: dict[str, list[Any]] = {}
    for t in tasks:
        by_kind.setdefault(t.kind, []).append(t)
    (dom,) = by_kind["domains"]
    assert dom.id == parent
    assert dom.status == "done"
    children = by_kind["var_list"]
    assert all(c.parent_id == parent and c.status == "pending" for c in children)
    assert {c.params["domain"] for c in children} == {
        i for i in _rows(db_engine) if domain_level(i) == "prov"
    }
    (raw,) = raws
    assert raw.task_id == parent
    assert raw.endpoint == "domain"
    assert raw.params == {"type": "all"}
    assert raw.body == domain_body


# --- S13: seed restrictions ----------------------------------------------------------------------


async def test_domains_param_restricts_children_but_all_rows_stored(
    db_engine: Engine, domain_body: dict[str, Any]
) -> None:
    _, result = await _handle(db_engine, domain_body, {"domains": ["0000", "3100"]})
    assert len(_rows(db_engine)) == 549
    assert result.children == [
        Child("var_list", {"domain": "0000"}),
        Child("var_list", {"domain": "3100"}),
    ]


async def test_domains_param_combines_with_level(
    db_engine: Engine, domain_body: dict[str, Any]
) -> None:
    params = {"domains": ["0000", "3100"], "level": "prov"}
    _, result = await _handle(db_engine, domain_body, params)
    assert result.children == [Child("var_list", {"domain": "3100"})]


async def test_unknown_requested_domain_fails(
    db_engine: Engine, domain_body: dict[str, Any]
) -> None:
    with pytest.raises(LookupError, match="9999"):
        await _handle(db_engine, domain_body, {"domains": ["0000", "9999"]})


async def test_limit_vars_passed_to_var_list_children(
    db_engine: Engine, domain_body: dict[str, Any]
) -> None:
    _, result = await _handle(db_engine, domain_body, {"domains": ["0000"], "limit_vars": 5})
    assert result.children == [Child("var_list", {"domain": "0000", "limit_vars": 5})]


@pytest.mark.parametrize("bad", [0, -1, "5", True])
async def test_bad_limit_vars_rejected(
    db_engine: Engine, domain_body: dict[str, Any], bad: Any
) -> None:
    with pytest.raises(ValueError, match="limit_vars"):
        await _handle(db_engine, domain_body, {"limit_vars": bad})

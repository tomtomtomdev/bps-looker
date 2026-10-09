"""S12: ``data`` handler — observation loader (fake client serving the data fixtures; DB tests need
Postgres)."""

import copy
import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, Table, insert, select, text

from bps_fetcher import queue, worker
from bps_fetcher.client import BpsApiError, BpsNullResponseError
from bps_fetcher.db.schema import (
    dim_turth,
    dim_turvar,
    dim_vervar,
    domain,
    observation,
    raw_response,
    task,
    variable,
)
from bps_fetcher.handlers import data as data_mod
from bps_fetcher.handlers.data import data
from bps_fetcher.parse_data import parse_data
from bps_fetcher.worker import TaskContext

Body = Callable[[str], Any]
NOT_AVAILABLE = {"status": "OK", "data-availability": "not-available"}
PARAMS_1804 = {"domain": "0000", "var": 1804, "th": "117:119"}


class FakeClient:
    """Answers ``list?model=data`` (and ``model=th``) through ``answer(params)``; records calls."""

    def __init__(self, answer: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.answer = answer
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append((path, params))
        assert path == "list"
        body = self.answer(params)
        if body.get("status") == "Error":  # what BpsClient does with an error envelope
            raise BpsApiError(body["message"])
        return body


def _task(params: dict[str, Any]) -> queue.Task:
    return queue.Task(id=1, kind="data", params=params, attempts=0, parent_id=None)


def _seed_variable(engine: Engine, domain_id: str = "0000", var_id: int = 1804) -> None:
    with engine.begin() as conn:
        conn.execute(insert(domain).values(domain_id=domain_id, name="D", level="pusat"))
        conn.execute(insert(variable).values(domain_id=domain_id, var_id=var_id, title="V"))


async def _handle(
    engine: Engine, body: dict[str, Any], params: dict[str, Any] = PARAMS_1804
) -> tuple[FakeClient, worker.HandlerResult]:
    client = FakeClient(lambda _: body)
    with engine.begin() as conn:
        result = await data(TaskContext(_task(params), client, conn))
    return client, result


def _obs(engine: Engine) -> dict[tuple[int, int, int, int], Decimal]:
    with engine.connect() as conn:
        rows = conn.execute(select(observation)).all()
    return {(r.vervar, r.turvar, r.th, r.turth): r.value for r in rows}


def _xmins(engine: Engine, table: Table) -> list[str]:
    """Row versions: unchanged ``xmin``s prove a re-run wrote nothing."""
    with engine.connect() as conn:
        return sorted(
            str(x) for x in conn.execute(text(f"SELECT xmin FROM {table.name}")).scalars()
        )


def _variable(engine: Engine, var_id: int = 1804) -> Any:
    with engine.connect() as conn:
        return conn.execute(select(variable).where(variable.c.var_id == var_id)).one()


def _bump(body: dict[str, Any], last_update: str, **values: Any) -> dict[str, Any]:
    out = copy.deepcopy(body)
    out["last_update"] = last_update
    out["datacontent"].update(values)
    return out


# --- registration / params -----------------------------------------------------------------------


def test_registered_in_handlers() -> None:
    assert worker.HANDLERS["data"] is data


async def test_bad_params_or_missing_variable_rejected(
    db_engine: Engine, fixture_body: Body
) -> None:
    body = fixture_body("data_0000_1804")
    with pytest.raises(ValueError, match="domain"):
        await _handle(db_engine, body, {"var": 1804, "th": "117"})
    with pytest.raises(ValueError, match="var"):
        await _handle(db_engine, body, {"domain": "0000", "var": "1804", "th": "117"})
    with pytest.raises(ValueError, match="th"):
        await _handle(db_engine, body, {"domain": "0000", "var": 1804})
    with pytest.raises(LookupError, match="1804"):
        await _handle(db_engine, body)


async def test_response_for_another_var_rejected(db_engine: Engine, fixture_body: Body) -> None:
    _seed_variable(db_engine, var_id=2263)
    with pytest.raises(ValueError, match="1804"):
        await _handle(db_engine, fixture_body("data_0000_1804"), {**PARAMS_1804, "var": 2263})


# --- loading -------------------------------------------------------------------------------------


async def test_loads_annual_fixture(db_engine: Engine, fixture_body: Body) -> None:
    body = fixture_body("data_0000_1804")
    _seed_variable(db_engine)
    client, result = await _handle(db_engine, body)

    params = {"model": "data", "domain": "0000", "var": 1804, "th": "117:119"}
    assert client.calls == [("list", params)]
    (raw,) = result.raw
    assert (raw.endpoint, dict(raw.params), raw.body) == ("list", params, body)
    assert result.children == []

    assert _obs(db_engine) == {
        (1, 0, 117, 0): Decimal(440),
        (2, 0, 117, 0): Decimal(1088),
        (3, 0, 117, 0): Decimal(3674369),
        (1, 0, 118, 0): Decimal(5248),
        (2, 0, 118, 0): Decimal(21218),
        (3, 0, 118, 0): Decimal(10417179),
        (1, 0, 119, 0): Decimal(492),
        (2, 0, 119, 0): Decimal(3362),
        (3, 0, 119, 0): Decimal(5188077),
    }
    with db_engine.connect() as conn:
        vervar = conn.execute(select(dim_vervar).order_by(dim_vervar.c.val)).all()
        turvar = conn.execute(select(dim_turvar)).all()
        turth = conn.execute(select(dim_turth)).all()
        lu = conn.execute(select(observation.c.last_update).distinct()).scalars().all()
    assert [(r.domain_id, r.var_id, r.val, r.label, r.group_label) for r in vervar] == [
        ("0000", 1804, 1, "Meninggal dan Hilang", "Jenis Korban"),
        ("0000", 1804, 2, "Terluka", "Jenis Korban"),
        ("0000", 1804, 3, "Menderita dan Mengungsi", "Jenis Korban"),
    ]
    assert [(r.val, r.label) for r in turvar] == [(0, "Tidak ada")]
    assert [(r.val, r.label) for r in turth] == [(0, "Tahun")]
    assert lu == [datetime(2020, 3, 26)]

    var = _variable(db_engine)
    assert (var.decimal, var.last_update) == (0, datetime(2020, 3, 26))
    assert var.title == "V"  # list-owned columns untouched


async def test_loads_monthly_fixture(db_engine: Engine, fixture_body: Body) -> None:
    body = fixture_body("data_0000_2263")
    parsed = parse_data(body)
    _seed_variable(db_engine, var_id=2263)
    await _handle(db_engine, body, {"domain": "0000", "var": 2263, "th": "124"})

    obs = _obs(db_engine)
    assert len(obs) == len(parsed.observations) > 400
    assert obs == {(o.vervar, o.turvar, o.th, o.turth): o.value for o in parsed.observations}
    assert {k[3] for k in obs} == set(range(1, 13))
    with db_engine.connect() as conn:
        turth = dict(conn.execute(select(dim_turth.c.val, dim_turth.c.label)).all())
        n_vervar = len(conn.execute(select(dim_vervar)).all())
    assert len(turth) == 13
    assert turth[13] == "Tahunan"  # listed even without values
    assert n_vervar == len(parsed.dims.vervar)
    var = _variable(db_engine, 2263)
    assert (var.decimal, var.last_update) == (2, datetime(2026, 10, 1, 11, 24, 1))


async def test_small_batches_load_everything(
    db_engine: Engine, fixture_body: Body, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(data_mod, "BATCH_SIZE", 4)
    _seed_variable(db_engine)
    with db_engine.begin() as conn:
        stats = data_mod.load(conn, "0000", parse_data(fixture_body("data_0000_1804")))
    assert stats.observations == 9
    assert len(_obs(db_engine)) == 9


async def test_rerun_same_response_changes_nothing(db_engine: Engine, fixture_body: Body) -> None:
    body = fixture_body("data_0000_1804")
    _seed_variable(db_engine)
    await _handle(db_engine, body)
    tables = (observation, dim_vervar, dim_turvar, dim_turth, variable)
    before = {t.name: _xmins(db_engine, t) for t in tables}

    with db_engine.begin() as conn:
        stats = data_mod.load(conn, "0000", parse_data(body))
    await _handle(db_engine, body)

    assert (stats.observations, stats.dims, stats.variable) == (0, 0, False)
    assert {t.name: _xmins(db_engine, t) for t in tables} == before


async def test_newer_last_update_replaces_values(db_engine: Engine, fixture_body: Body) -> None:
    body = fixture_body("data_0000_1804")
    _seed_variable(db_engine)
    await _handle(db_engine, body)

    newer = _bump(body, "2021-01-01 00:00:00", **{"1180401190": 500})
    newer["vervar"][0]["label"] = "Meninggal & Hilang"
    newer["var"][0]["decimal"] = 1
    with db_engine.begin() as conn:
        stats = data_mod.load(conn, "0000", parse_data(newer))

    obs = _obs(db_engine)
    assert obs[(1, 0, 119, 0)] == Decimal(500)
    assert obs[(1, 0, 118, 0)] == Decimal(5248)
    # Every row now carries the newer last_update, so all 9 were touched (8 marker-only).
    assert stats.observations == 9
    var = _variable(db_engine)
    assert (var.decimal, var.last_update) == (1, datetime(2021, 1, 1))
    with db_engine.connect() as conn:
        label = conn.execute(select(dim_vervar.c.label).where(dim_vervar.c.val == 1)).scalar()
    assert label == "Meninggal & Hilang"


async def test_older_last_update_does_not_regress(db_engine: Engine, fixture_body: Body) -> None:
    body = fixture_body("data_0000_1804")
    _seed_variable(db_engine)
    newer = _bump(body, "2021-01-01 00:00:00", **{"1180401190": 500})
    newer["vervar"][0]["label"] = "Meninggal & Hilang"
    newer["var"][0]["decimal"] = 1
    await _handle(db_engine, newer)

    older = _bump(body, "2020-03-26 00:00:00", **{"1180401200": 7})  # plus a cell not seen yet
    older["tahun"].append({"val": 120, "label": "2020"})
    with db_engine.begin() as conn:
        stats = data_mod.load(conn, "0000", parse_data(older))

    obs = _obs(db_engine)
    assert obs[(1, 0, 119, 0)] == Decimal(500)  # not regressed to 492
    assert obs[(1, 0, 120, 0)] == Decimal(7)  # new cells are still added
    assert stats.observations == 1
    assert stats.stale is True
    var = _variable(db_engine)
    assert (var.decimal, var.last_update) == (1, datetime(2021, 1, 1))
    with db_engine.connect() as conn:
        label = conn.execute(select(dim_vervar.c.label).where(dim_vervar.c.val == 1)).scalar()
    assert label == "Meninggal & Hilang"


async def test_equal_last_update_other_window_loads(db_engine: Engine, fixture_body: Body) -> None:
    """Windows of one variable share its last_update: the second window must still load."""
    body = fixture_body("data_0000_1804")
    _seed_variable(db_engine)
    first = copy.deepcopy(body)
    first["datacontent"] = {k: v for k, v in body["datacontent"].items() if k.endswith("1170")}
    await _handle(db_engine, first, {**PARAMS_1804, "th": "117"})
    assert len(_obs(db_engine)) == 3
    await _handle(db_engine, body)
    assert len(_obs(db_engine)) == 9


async def test_not_available_stores_raw_only(db_engine: Engine) -> None:
    _seed_variable(db_engine)
    _, result = await _handle(db_engine, NOT_AVAILABLE)
    assert [r.body for r in result.raw] == [NOT_AVAILABLE]
    assert _obs(db_engine) == {}
    var = _variable(db_engine)
    assert (var.decimal, var.last_update) == (None, None)


async def test_list_not_available_fixture_stores_raw_only(
    db_engine: Engine, fixture_body: Body
) -> None:
    body = fixture_body("data_list_not_available")
    _seed_variable(db_engine, var_id=698)
    _, result = await _handle(db_engine, body, {"domain": "0000", "var": 698, "th": "86:88"})
    assert [r.body for r in result.raw] == [body]
    assert result.children == []
    assert _obs(db_engine) == {}


class NullClient:
    """Answers ``null`` (BpsNullResponseError) for multi-period windows, a body otherwise."""

    def __init__(self, single: dict[str, Any] | None) -> None:
        self.single = single
        self.calls: list[dict[str, Any]] = []

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append(params)
        if ":" in str(params["th"]) or self.single is None:
            raise BpsNullResponseError("BPS returned JSON null from /v1/api/list")
        return self.single


async def test_null_response_on_window_splits_into_single_periods(db_engine: Engine) -> None:
    _seed_variable(db_engine, var_id=2096)
    params = {"domain": "0000", "var": 2096, "th": "118:120"}
    with db_engine.begin() as conn:
        result = await data(TaskContext(_task(params), NullClient(None), conn))
    assert result.raw == []
    assert result.children == [
        worker.Child("data", {"domain": "0000", "var": 2096, "th": str(th)})
        for th in (118, 119, 120)
    ]
    assert _obs(db_engine) == {}


async def test_null_response_on_single_period_fails_clearly(db_engine: Engine) -> None:
    _seed_variable(db_engine, var_id=2096)
    params = {"domain": "0000", "var": 2096, "th": "118"}
    with db_engine.begin() as conn, pytest.raises(BpsNullResponseError, match="null"):
        await data(TaskContext(_task(params), NullClient(None), conn))


async def test_null_fixture_is_json_null(fixture_body: Body) -> None:
    assert fixture_body("data_null_too_large") is None


async def test_report_buckets_logged(
    db_engine: Engine, fixture_body: Body, caplog: pytest.LogCaptureFixture
) -> None:
    body = copy.deepcopy(fixture_body("data_0000_1804"))
    body["datacontent"].update({"999999": 1, "1180401170": "-", "2180401170": None})
    _seed_variable(db_engine)
    with caplog.at_level(logging.WARNING, logger=data_mod.__name__):
        await _handle(db_engine, body)
    assert len(_obs(db_engine)) == 7
    (rec,) = [r for r in caplog.records if r.name == data_mod.__name__]
    assert rec.levelno == logging.WARNING
    msg = rec.getMessage()
    for part in ("0000/1804", "117:119", "1 unmatched", "0 ambiguous", "2 non-numeric"):
        assert part in msg


async def test_api_error_propagates(db_engine: Engine, fixture_body: Body) -> None:
    _seed_variable(db_engine)
    with pytest.raises(BpsApiError, match="maximum allowed"):
        await _handle(db_engine, fixture_body("error_data_too_many_th"))


# --- end to end through the worker ---------------------------------------------------------------


def _windowed(body: dict[str, Any], th: str) -> dict[str, Any]:
    """The 1804 fixture cut to the requested ``th`` (``a`` or ``a:b``); none → not-available."""
    lo, _, hi = th.partition(":")
    wanted = set(range(int(lo), int(hi or lo) + 1))
    tahun = [t for t in body["tahun"] if t["val"] in wanted]
    if not tahun:
        return NOT_AVAILABLE
    out = copy.deepcopy(body)
    out["tahun"] = tahun
    out["datacontent"] = {k: v for k, v in body["datacontent"].items() if int(k[-4:-1]) in wanted}
    return out


async def test_end_to_end_th_list_then_data(db_engine: Engine, fixture_body: Body) -> None:
    th_body, data_body = fixture_body("th_0000_1804"), fixture_body("data_0000_1804")
    err_body = fixture_body("error_data_too_many_th")
    _seed_variable(db_engine)
    with db_engine.begin() as conn:
        queue.enqueue(conn, "th_list", {"domain": "0000", "var": 1804})
        bad = queue.enqueue(conn, "data", {"domain": "0000", "var": 1804, "th": "113:119"})

    def answer(params: dict[str, Any]) -> dict[str, Any]:
        if params["model"] == "th":
            return th_body  # type: ignore[no-any-return]
        if params["th"] == "113:119":
            return err_body  # type: ignore[no-any-return]
        return _windowed(data_body, params["th"])

    client = FakeClient(answer)
    stats = await worker.run_worker(db_engine, client=client, idle_sleep=0.01)

    assert (stats.done, stats.failed) == (4, 1)  # th_list + 3 windows; the >3-years task fails
    assert len(_obs(db_engine)) == 9
    with db_engine.connect() as conn:
        tasks = conn.execute(select(task).order_by(task.c.id)).all()
        raws = conn.execute(select(raw_response.c.params)).scalars().all()
    windows = {t.params["th"]: t.status for t in tasks if t.kind == "data"}
    assert windows == {"113:115": "done", "116:118": "done", "119": "done", "113:119": "pending"}
    (failed,) = [t for t in tasks if t.id == bad]
    assert failed.attempts == 1
    assert "BpsApiError" in failed.last_error
    assert sorted(r["th"] for r in raws if r["model"] == "data") == ["113:115", "116:118", "119"]
    assert _variable(db_engine).last_update == datetime(2020, 3, 26)

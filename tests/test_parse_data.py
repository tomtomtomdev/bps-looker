"""S11: pure parsing of ``/list?model=data`` responses (no DB)."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from bps_fetcher.parse_data import Combo, DimItem, Observation, ParsedData, parse_data

Body = Callable[[str], Any]


def _body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": "OK",
        "data-availability": "available",
        "last_update": "2025-01-02 03:04:05",
        "var": [{"val": 7, "label": "V", "unit": "Orang", "decimal": 1}],
        "labelvervar": "Region",
        "vervar": [{"val": 1, "label": "A"}, {"val": 2, "label": "B"}],
        "turvar": [{"val": "0", "label": "Tidak ada"}],
        "tahun": [{"val": 120, "label": "2020"}, {"val": 121, "label": "2021"}],
        "turtahun": [{"val": 0, "label": "Tahun"}],
        "datacontent": {},
    }
    body.update(over)
    return body


# --- fixtures ------------------------------------------------------------------------------------


def test_annual_fixture(fixture_body: Body) -> None:
    body = fixture_body("data_0000_1804")
    parsed = parse_data(body)

    assert parsed.var_id == 1804
    assert parsed.decimal == 0
    assert parsed.last_update == datetime(2020, 3, 26)
    assert len(parsed.observations) == len(body["datacontent"]) == 9
    assert parsed.unmatched == {}
    assert parsed.ambiguous == {}
    assert parsed.non_numeric == {}
    assert Observation(1, 0, 117, 0, Decimal(440)) in parsed.observations
    assert Observation(3, 0, 118, 0, Decimal(10417179)) in parsed.observations
    # every key maps to exactly one combination, and all combinations are distinct
    assert len({(o.vervar, o.turvar, o.th, o.turth) for o in parsed.observations}) == 9

    dims = parsed.dims
    assert dims.vervar_label == "Jenis Korban"
    assert dims.vervar == (
        DimItem(1, "Meninggal dan Hilang", "Jenis Korban"),
        DimItem(2, "Terluka", "Jenis Korban"),
        DimItem(3, "Menderita dan Mengungsi", "Jenis Korban"),
    )
    assert dims.turvar == (DimItem(0, "Tidak ada"),)
    assert dims.tahun == (DimItem(117, "2017"), DimItem(118, "2018"), DimItem(119, "2019"))
    assert dims.turtahun == (DimItem(0, "Tahun"),)


def test_monthly_fixture(fixture_body: Body) -> None:
    body = fixture_body("data_0000_2263")
    parsed = parse_data(body)

    assert parsed.var_id == 2263
    assert parsed.decimal == 2
    assert parsed.last_update == datetime.strptime(body["last_update"], "%Y-%m-%d %H:%M:%S")
    assert len(parsed.observations) == len(body["datacontent"]) == 39 * 12
    assert (parsed.unmatched, parsed.ambiguous, parsed.non_numeric) == ({}, {}, {})
    assert {o.turth for o in parsed.observations} == set(range(1, 13))  # 13 listed, no values
    assert {o.th for o in parsed.observations} == {124}
    assert len({o.vervar for o in parsed.observations}) == 39
    assert len(parsed.dims.vervar) == 39
    assert parsed.dims.turtahun[-1] == DimItem(13, "Tahunan")
    assert all(isinstance(o.value, Decimal) for o in parsed.observations)
    # floats are converted via their repr: no binary noise
    assert Observation(9200, 0, 124, 11, Decimal("2.33")) in parsed.observations


def test_values_match_datacontent(fixture_body: Body) -> None:
    body = fixture_body("data_0000_2263")
    parsed = parse_data(body)
    by_combo = {(o.vervar, o.turvar, o.th, o.turth): o.value for o in parsed.observations}
    for key, raw in body["datacontent"].items():
        vervar, rest = int(key[:4]), key[4 + 4 :]  # vervar is 4 digits, var 2263
        assert rest[0] == "0"  # turvar
        th, turth = int(rest[1:4]), int(rest[4:])
        assert by_combo[(vervar, 0, th, turth)] == Decimal(str(raw))


# --- synthetic -----------------------------------------------------------------------------------


def test_unknown_key_is_reported_not_dropped() -> None:
    parsed = parse_data(_body(datacontent={"1701200": 5, "999999": 1}))
    assert parsed.observations == (Observation(1, 0, 120, 0, Decimal(5)),)
    assert parsed.unmatched == {"999999": 1}


def test_missing_combinations_are_fine() -> None:
    parsed = parse_data(_body(datacontent={"2701210": 3}))
    assert parsed.observations == (Observation(2, 0, 121, 0, Decimal(3)),)
    assert (parsed.unmatched, parsed.ambiguous, parsed.non_numeric) == ({}, {}, {})


def test_ambiguous_concatenation_is_detected() -> None:
    # vervar "1" + var "1" + turvar "11" == vervar "11" + var "1" + turvar "1"
    body = _body(
        var=[{"val": 1, "label": "V"}],
        vervar=[{"val": 1, "label": "a"}, {"val": 11, "label": "b"}],
        turvar=[{"val": 1, "label": "x"}, {"val": 11, "label": "y"}],
        tahun=[{"val": 120, "label": "2020"}],
        turtahun=[{"val": 0, "label": "Tahun"}],
        datacontent={"11111200": 9, "1111200": 1, "111111200": 2},
    )
    parsed = parse_data(body)

    assert parsed.ambiguous == {
        "11111200": (Combo(1, 11, 120, 0), Combo(11, 1, 120, 0)),
    }
    assert set(parsed.observations) == {
        Observation(1, 1, 120, 0, Decimal(1)),
        Observation(11, 11, 120, 0, Decimal(2)),
    }
    assert parsed.unmatched == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (5, Decimal(5)),
        (-1.5, Decimal("-1.5")),
        (0.1, Decimal("0.1")),
        ("12.50", Decimal("12.50")),
        (" 3 ", Decimal(3)),
    ],
)
def test_numeric_values(raw: Any, expected: Decimal) -> None:
    parsed = parse_data(_body(datacontent={"1701200": raw}))
    assert parsed.observations == (Observation(1, 0, 120, 0, expected),)


@pytest.mark.parametrize("raw", [None, "", "-", "…", "1,5", "NaN", "inf", True, [1], {"a": 1}])
def test_non_numeric_values_are_reported_and_skipped(raw: Any) -> None:
    parsed = parse_data(_body(datacontent={"1701200": raw, "2701200": 4}))
    assert parsed.observations == (Observation(2, 0, 120, 0, Decimal(4)),)
    assert parsed.non_numeric == {"1701200": raw}


def test_not_available_response_is_empty() -> None:
    parsed = parse_data({"status": "OK", "data-availability": "not-available"})
    assert parsed == ParsedData(var_id=None, decimal=None, last_update=None)
    assert parsed.observations == ()
    assert parsed.dims.vervar == ()


def test_missing_last_update_is_none_and_bad_one_raises() -> None:
    assert parse_data(_body(last_update="")).last_update is None
    assert parse_data(_body(last_update=None)).last_update is None
    with pytest.raises(ValueError, match="last_update"):
        parse_data(_body(last_update="yesterday"))


def test_blank_vervar_label_is_no_group() -> None:
    parsed = parse_data(_body(labelvervar="  "))
    assert parsed.dims.vervar_label is None
    assert parsed.dims.vervar[0] == DimItem(1, "A")


def test_key_uses_raw_value_text() -> None:
    # A val written with a leading zero keys as written but is stored as int.
    body = _body(turvar=[{"val": "01", "label": "x"}], datacontent={"17011200": 2})
    parsed = parse_data(body)
    assert parsed.observations == (Observation(1, 1, 120, 0, Decimal(2)),)


@pytest.mark.parametrize(
    "over",
    [
        {"var": []},
        {"vervar": [{"val": "abc", "label": "x"}]},
        {"tahun": [{"label": "2020"}]},
        {"datacontent": ["not", "a", "dict"]},
    ],
)
def test_malformed_response_raises(over: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        parse_data(_body(datacontent={"1701200": 1}) | over)

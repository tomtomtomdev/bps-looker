"""S15: pure trade helpers (bracket labels, chapter list, batching)."""

from collections.abc import Callable
from typing import Any

import pytest

from bps_fetcher.trade import (
    ALL_CHAPTERS,
    UNUSED_CHAPTERS,
    batch_chapters,
    parse_bracket,
    parse_hs,
    parse_month,
)


def test_parse_bracket_keeps_code_as_text() -> None:
    assert parse_bracket("[03] Fish, crustaceans and mollusca") == (
        "03",
        "Fish, crustaceans and mollusca",
    )
    assert parse_bracket("  [11] November ") == ("11", "November")


def test_parse_hs_keeps_leading_zeros() -> None:
    assert parse_hs("[03] Fish, ...") == ("03", "Fish, ...")
    assert parse_hs("[85] Electrical machinery; parts [thereof]") == (
        "85",
        "Electrical machinery; parts [thereof]",
    )


def test_parse_month_is_int() -> None:
    assert parse_month("[11] November") == (11, "November")
    assert parse_month("[01] Januari") == (1, "Januari")


@pytest.mark.parametrize(
    "text",
    ["", "03 Fish", "[03]", "[03] ", "[] Fish", "[ab] Fish", "Fish [03]", "[03]Fish"],
)
def test_parse_bracket_rejects_malformed(text: str) -> None:
    with pytest.raises(ValueError, match="bracket"):
        parse_bracket(text)


@pytest.mark.parametrize("text", ["[3] Fish", "[030] Fish"])
def test_parse_hs_needs_two_digits(text: str) -> None:
    with pytest.raises(ValueError, match="HS chapter"):
        parse_hs(text)


@pytest.mark.parametrize("text", ["[00] Nope", "[13] Tahunan"])
def test_parse_month_range(text: str) -> None:
    with pytest.raises(ValueError, match="month"):
        parse_month(text)


def test_chapter_list() -> None:
    assert ALL_CHAPTERS[0] == "01"
    assert "77" in UNUSED_CHAPTERS
    assert not set(UNUSED_CHAPTERS) & set(ALL_CHAPTERS)
    assert len(ALL_CHAPTERS) == 99 - len(UNUSED_CHAPTERS)
    assert sorted(ALL_CHAPTERS) == ALL_CHAPTERS
    assert all(len(c) == 2 and c.isdigit() for c in ALL_CHAPTERS)


def test_batch_chapters() -> None:
    assert batch_chapters(["01", "02", "03", "04", "05"], 2) == ["01;02", "03;04", "05"]
    assert batch_chapters(["03"], 10) == ["03"]
    assert batch_chapters([], 10) == []
    batches = batch_chapters(ALL_CHAPTERS, 10)
    assert ";".join(batches).split(";") == ALL_CHAPTERS
    assert all(len(b.split(";")) <= 10 for b in batches)


def test_batch_chapters_default_is_all() -> None:
    assert batch_chapters(size=50) == batch_chapters(ALL_CHAPTERS, 50)


def test_batch_chapters_rejects_bad_size() -> None:
    with pytest.raises(ValueError, match="size"):
        batch_chapters(["01"], 0)


def test_parses_fixture_rows(fixture_body: Callable[[str], Any]) -> None:
    rows = fixture_body("trade_exp_monthly_03_2024")["data"]
    assert {parse_hs(r["kodehs"])[0] for r in rows} == {"03"}
    assert {parse_month(r["bulan"])[0] for r in rows} == set(range(1, 13))

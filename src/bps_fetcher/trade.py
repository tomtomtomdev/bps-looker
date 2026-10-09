"""Pure helpers for the foreign-trade endpoint (``dataexim/``): labels, chapters, batches.

Trade rows carry bracketed labels: ``kodehs: "[03] Fish, ..."`` and (monthly) ``bulan:
"[11] November"``. :func:`parse_bracket` splits them as text; :func:`parse_hs` keeps the HS
chapter as a 2-digit string (leading zeros matter) and :func:`parse_month` returns an ``int``.
"""

import re
from collections.abc import Sequence

# ``sumber``: flow; ``periode``: period type (see docs/bps-webapi.md).
EXPORT = 1
IMPORT = 2
MONTHLY = 1
YEARLY = 2

# Chapter 77 is reserved in the HS nomenclature (API: unavailable). Indonesia does use the
# national chapters 98 (CKD motor vehicles) and 99 (software, digital products, parcels).
EARLIEST_YEAR = 2014  # 2013 and earlier: data-availability "unavailable" (verified 2026-10-09)
UNUSED_CHAPTERS: tuple[str, ...] = ("77",)
ALL_CHAPTERS: list[str] = [
    code for code in (f"{n:02d}" for n in range(1, 100)) if code not in UNUSED_CHAPTERS
]
DEFAULT_BATCH_SIZE = 10

_BRACKET = re.compile(r"\[(\d+)\] (\S.*)", re.DOTALL)


def parse_bracket(text: str) -> tuple[str, str]:
    """Split ``"[03] Fish, ..."`` into ``("03", "Fish, ...")``; code stays text."""
    match = _BRACKET.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"not a '[code] label' bracket: {text!r}")
    return match.group(1), match.group(2)


def parse_hs(text: str) -> tuple[str, str]:
    """``kodehs`` → (2-digit HS chapter as str, description)."""
    code, label = parse_bracket(text)
    if len(code) != 2:
        raise ValueError(f"HS chapter must be 2 digits: {text!r}")
    return code, label


def parse_month(text: str) -> tuple[int, str]:
    """``bulan`` → (month 1-12, name)."""
    code, label = parse_bracket(text)
    month = int(code)
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range 1-12: {text!r}")
    return month, label


def batch_chapters(
    chapters: Sequence[str] = ALL_CHAPTERS, size: int = DEFAULT_BATCH_SIZE
) -> list[str]:
    """Group chapters into ``kodehs`` values of at most ``size`` codes joined by ``;``."""
    if size < 1:
        raise ValueError(f"batch size must be >= 1, got {size}")
    return [";".join(chapters[i : i + size]) for i in range(0, len(chapters), size)]

"""Parse a ``/list?model=data`` response into observations + dimension labels. Pure, no I/O.

``datacontent`` keys are the plain concatenation ``vervar + var + turvar + th + turth`` (e.g.
``"1" "1804" "0" "117" "0"`` → ``"1180401170"``). The parts have variable length, so keys are
never split: every combination of the listed dimension values is turned into its key, and each
``datacontent`` key is looked up in that map. Combinations without a value are fine (monthly
vars list ``turtahun`` 13 = ``Tahunan`` but only have 1-12).

Nothing is silently dropped — every ``datacontent`` entry ends up in exactly one of:

- ``observations`` — key maps to exactly one combination, value numeric (as :class:`Decimal`);
- ``unmatched`` — key matches no combination;
- ``ambiguous`` — naive concatenation collides (e.g. vervar ``1`` + var ``1`` + turvar ``11``
  vs vervar ``11`` + var ``1`` + turvar ``1``), so the value can't be placed;
- ``non_numeric`` — value is ``None``, blank, ``"-"``, a bool, NaN/inf or otherwise not a number.

Numbers are taken as written: ints and numeric strings exactly, floats through ``repr`` (so
``2.33`` stays ``2.33``). Decimal commas (``"1,5"``) are not guessed at.

The vervar dimension's title (``labelvervar``) is kept as each vervar item's ``group_label``.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from itertools import product
from typing import Any, NamedTuple

LAST_UPDATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class Combo(NamedTuple):
    """One cell's coordinates: ``(vervar, turvar, th, turth)``."""

    vervar: int
    turvar: int
    th: int
    turth: int


class Observation(NamedTuple):
    vervar: int
    turvar: int
    th: int
    turth: int
    value: Decimal


@dataclass(frozen=True, slots=True)
class DimItem:
    val: int
    label: str
    group_label: str | None = None


@dataclass(frozen=True, slots=True)
class Dims:
    vervar: tuple[DimItem, ...] = ()
    turvar: tuple[DimItem, ...] = ()
    tahun: tuple[DimItem, ...] = ()
    turtahun: tuple[DimItem, ...] = ()
    vervar_label: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedData:
    var_id: int | None
    decimal: int | None
    last_update: datetime | None
    observations: tuple[Observation, ...] = ()
    dims: Dims = field(default_factory=Dims)
    unmatched: dict[str, Any] = field(default_factory=dict)
    ambiguous: dict[str, tuple[Combo, ...]] = field(default_factory=dict)
    non_numeric: dict[str, Any] = field(default_factory=dict)


def _int(raw: Any, what: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | str):
        raise ValueError(f"{what}: expected an integer id, got {raw!r}")
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{what}: expected an integer id, got {raw!r}") from None


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _dim(body: Mapping[str, Any], name: str) -> list[tuple[str, DimItem]]:
    """``[(key text, item)]`` for one dimension list (missing → empty)."""
    items = body.get(name) or []
    if not isinstance(items, list):
        raise ValueError(f"{name}: expected a list, got {type(items).__name__}")
    group = _text(body.get("labelvervar")) if name == "vervar" else None
    out = []
    for item in items:
        if not isinstance(item, Mapping) or "val" not in item:
            raise ValueError(f"{name}: malformed item {item!r}")
        raw = item["val"]
        val = _int(raw, name)
        out.append((str(raw), DimItem(val, str(item.get("label") or ""), group)))
    return out


def _var(body: Mapping[str, Any]) -> tuple[int | None, str | None, int | None]:
    """``(var_id, key text, decimal)`` from ``var[0]``; all ``None`` when ``var`` is absent."""
    var = body.get("var")
    if var is None:
        return None, None, None
    if not isinstance(var, list) or not var or not isinstance(var[0], Mapping):
        raise ValueError(f"var: expected a non-empty list, got {var!r}")
    first = var[0]
    if "val" not in first:
        raise ValueError(f"var: missing val in {first!r}")
    decimal = first.get("decimal")
    return (
        _int(first["val"], "var"),
        str(first["val"]),
        None if decimal is None else _int(decimal, "var.decimal"),
    )


def _last_update(raw: Any) -> datetime | None:
    text = _text(raw)
    if text is None:
        return None
    try:
        return datetime.strptime(text, LAST_UPDATE_FORMAT)
    except ValueError:
        raise ValueError(f"last_update: unexpected format {raw!r}") from None


def to_decimal(raw: Any) -> Decimal | None:
    """The value as a finite :class:`Decimal`, or ``None`` if it isn't a number."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        return Decimal(repr(raw)) if math.isfinite(raw) else None
    if isinstance(raw, str):
        try:
            value = Decimal(raw.strip())
        except InvalidOperation:
            return None
        return value if value.is_finite() else None
    return None


def parse_data(body: Mapping[str, Any]) -> ParsedData:
    """Parse one data response body. Raises :class:`ValueError` on a malformed shape."""
    var_id, var_key, decimal = _var(body)
    vervar, turvar, tahun, turtahun = (
        _dim(body, name) for name in ("vervar", "turvar", "tahun", "turtahun")
    )
    dims = Dims(
        vervar=tuple(i for _, i in vervar),
        turvar=tuple(i for _, i in turvar),
        tahun=tuple(i for _, i in tahun),
        turtahun=tuple(i for _, i in turtahun),
        vervar_label=_text(body.get("labelvervar")),
    )

    content = body.get("datacontent") or {}
    if not isinstance(content, Mapping):
        raise ValueError(f"datacontent: expected an object, got {type(content).__name__}")
    if content and var_key is None:
        raise ValueError("datacontent present but no var")

    combos: dict[str, set[Combo]] = {}
    for (vv_k, vv), (tv_k, tv), (th_k, th), (tt_k, tt) in product(vervar, turvar, tahun, turtahun):
        key = f"{vv_k}{var_key}{tv_k}{th_k}{tt_k}"
        combos.setdefault(key, set()).add(Combo(vv.val, tv.val, th.val, tt.val))

    observations: list[Observation] = []
    unmatched: dict[str, Any] = {}
    ambiguous: dict[str, tuple[Combo, ...]] = {}
    non_numeric: dict[str, Any] = {}
    for key, raw in content.items():
        found = combos.get(key)
        if not found:
            unmatched[key] = raw
        elif len(found) > 1:
            ambiguous[key] = tuple(sorted(found))
        elif (value := to_decimal(raw)) is None:
            non_numeric[key] = raw
        else:
            observations.append(Observation(*next(iter(found)), value))

    return ParsedData(
        var_id=var_id,
        decimal=decimal,
        last_update=_last_update(body.get("last_update")),
        observations=tuple(observations),
        dims=dims,
        unmatched=unmatched,
        ambiguous=ambiguous,
        non_numeric=non_numeric,
    )

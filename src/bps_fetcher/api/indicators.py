"""Strategic indicators (U5): the latest snapshot per indicator, its change vs the previous
periode, and its history.

``indicator_snapshot`` keeps one row per ``(domain, indicator, periode, title)`` as the crawler saw
them (S14). ``periode`` is free text (``September 2026``, ``Triwulan II 2026``,
``Semester 1 (Maret) 2026``) and can't be ordered, so **time order is sighting order**:

- A periode's point is its most recently seen row (a periode re-published under a revised title
  keeps one point, with the newer value); its ``first_seen`` is the earliest sighting of any of
  its rows.
- Periodes are ordered by their last sighting (``last_seen``), then ``first_seen``, then the
  text — the same ordering ``v_indicator_latest`` uses, so the last point is the latest row.
- **Previous** = the point just before the latest one; ``change`` = latest - previous when both
  values are numeric (non-numeric values are stored NULL, S14), ``change_pct`` additionally needs
  a non-zero previous value.

A domain has a few dozen indicators and one new periode per indicator per release, so the whole
domain's history is read and folded here in Python.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, RowMapping, select, table, tuple_
from sqlalchemy.sql import column

from bps_fetcher.db.schema import domain, indicator_snapshot, variable

# v_indicator_latest (migration 0008) — a view, so not in ``schema.metadata``.
v_indicator_latest = table(
    "v_indicator_latest",
    *(
        column(c)
        for c in (
            "domain_id", "indicator_id", "var", "subject_csa", "title", "name", "value", "unit",
            "periode", "category", "data_source", "first_seen", "last_seen",
        )
    ),
)  # fmt: skip

# ", <text with a 4-digit year>" at the end of a title: the periode BPS appends to it.
_PERIOD_SUFFIX = re.compile(r",[^,]*\b(?:19|20)\d{2}\b[^,]*$")


def indicator_label(title: str) -> str:
    """The title without its trailing period (``Inflasi Year on Year, September 2026`` →
    ``Inflasi Year on Year``): stable across periodes, short enough for a tile."""
    return _PERIOD_SUFFIX.sub("", title).strip() or title


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


@dataclass
class _Point:
    periode: str
    title: str
    value: Decimal | None
    first_seen: datetime
    last_seen: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "periode": self.periode,
            "title": self.title,
            "value": _float(self.value),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


def fold_history(rows: Iterable[RowMapping | dict[str, Any]]) -> list[_Point]:
    """Snapshot rows of one indicator → one point per periode, oldest first (module doc)."""
    by_periode: dict[str, _Point] = {}
    for r in rows:
        p = by_periode.get(r["periode"])
        if p is None:
            by_periode[r["periode"]] = _Point(
                r["periode"], r["title"], r["value"], r["first_seen"], r["last_seen"]
            )
            continue
        if (r["last_seen"], r["first_seen"], r["title"]) > (p.last_seen, p.first_seen, p.title):
            p.title, p.value, p.last_seen = r["title"], r["value"], r["last_seen"]
        p.first_seen = min(p.first_seen, r["first_seen"])
    return sorted(by_periode.values(), key=lambda p: (p.last_seen, p.first_seen, p.periode))


def change(latest: Decimal | None, previous: Decimal | None) -> tuple[float | None, float | None]:
    """``(latest - previous, % change)``; ``None`` when unknown (pct also when previous is 0)."""
    if latest is None or previous is None:
        return None, None
    diff = latest - previous
    pct = None if previous == 0 else float(diff / abs(previous) * 100)
    return float(diff), pct


def domain_row(conn: Connection, domain_id: str) -> dict[str, Any] | None:
    row = (
        conn.execute(
            select(domain.c.domain_id, domain.c.name, domain.c.url, domain.c.level).where(
                domain.c.domain_id == domain_id
            )
        )
        .mappings()
        .first()
    )
    return None if row is None else dict(row)


def _variables(
    conn: Connection, domain_id: str, var_ids: Iterable[int | None]
) -> dict[int, dict[str, Any]]:
    ids = sorted({v for v in var_ids if v is not None})
    if not ids:
        return {}
    rows = conn.execute(
        select(variable.c.domain_id, variable.c.var_id, variable.c.title).where(
            tuple_(variable.c.domain_id, variable.c.var_id).in_([(domain_id, v) for v in ids])
        )
    ).mappings()
    return {r["var_id"]: dict(r) for r in rows}


def _history_rows(
    conn: Connection, domain_id: str, indicator_id: int | None = None
) -> dict[int, list[RowMapping]]:
    s = indicator_snapshot.c
    query = select(s.indicator_id, s.periode, s.title, s.value, s.first_seen, s.last_seen).where(
        s.domain_id == domain_id
    )
    if indicator_id is not None:
        query = query.where(s.indicator_id == indicator_id)
    out: dict[int, list[RowMapping]] = {}
    for r in conn.execute(query).mappings():
        out.setdefault(r["indicator_id"], []).append(r)
    return out


def _latest_rows(
    conn: Connection, domain_id: str, indicator_id: int | None = None
) -> Sequence[RowMapping]:
    v = v_indicator_latest.c
    query = select(v_indicator_latest).where(v.domain_id == domain_id).order_by(v.indicator_id)
    if indicator_id is not None:
        query = query.where(v.indicator_id == indicator_id)
    return conn.execute(query).mappings().all()


def _indicator(
    latest: RowMapping, points: list[_Point], variables: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    previous = points[-2] if len(points) > 1 else None
    diff, pct = change(latest["value"], previous.value if previous else None)
    return {
        "domain_id": latest["domain_id"],
        "indicator_id": latest["indicator_id"],
        "title": latest["title"],
        "label": indicator_label(latest["title"]),
        "name": latest["name"],
        "value": _float(latest["value"]),
        "unit": latest["unit"],
        "periode": latest["periode"],
        "category": latest["category"],
        "subject_csa": latest["subject_csa"],
        "data_source": latest["data_source"],
        "first_seen": latest["first_seen"],
        "last_seen": latest["last_seen"],
        "var": latest["var"],
        "variable": variables.get(latest["var"]) if latest["var"] is not None else None,
        "previous": (
            {"periode": previous.periode, "value": _float(previous.value)} if previous else None
        ),
        "change": diff,
        "change_pct": pct,
    }


def load_indicators(conn: Connection, domain_id: str) -> list[dict[str, Any]]:
    """Every indicator of the domain: its latest snapshot + change vs the previous periode."""
    latest = _latest_rows(conn, domain_id)
    history = _history_rows(conn, domain_id)
    variables = _variables(conn, domain_id, (r["var"] for r in latest))
    return [
        _indicator(r, fold_history(history.get(r["indicator_id"], [])), variables) for r in latest
    ]


def load_history(conn: Connection, domain_id: str, indicator_id: int) -> dict[str, Any] | None:
    """One indicator's latest snapshot plus every periode's point, oldest first."""
    latest = _latest_rows(conn, domain_id, indicator_id)
    if not latest:
        return None
    points = fold_history(_history_rows(conn, domain_id, indicator_id).get(indicator_id, []))
    variables = _variables(conn, domain_id, [latest[0]["var"]])
    return {
        **_indicator(latest[0], points, variables),
        "points": [p.as_dict() for p in points],
    }

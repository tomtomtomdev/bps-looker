"""Variable detail + time series (U3).

**Period → date rules.** An observation's time is ``th`` (a year: ``period.label``, e.g.
``2024``) plus ``turth`` (the sub-period). ``turth`` *values* are not standard across variables
(months are 1-12 with 13 = ``Tahunan`` for most monthly vars, but quarters come as 31-34, 213-216
or 321-324, annual as 0 ``Tahun``, 13/35/63/191 ``Tahunan`` or 217 ``Jumlah``), so the
*label* decides first and the value is only a fallback (``classify_turth``):

- month name (``Januari`` … ``Desember``) → ``month``: period ``YYYY-MM``, date ``YYYY-MM-01``;
- ``Triwulan I``-``IV`` (or ``Kuartal``/``Quarter``) → ``quarter``: ``YYYY-Qn``, date = first day
  of the quarter (Q2 → ``YYYY-04-01``);
- ``Semester 1``/``2`` (or ``I``/``II``) → ``semester``: ``YYYY-Sn``, date = the reference month
  in the label when there is one (``Semester 1 (Maret)`` → ``YYYY-03-01``), else the first day of
  the half (``YYYY-01-01`` / ``YYYY-07-01``);
- ``Tahun``/``Tahunan``/``Jumlah``/``Total`` → ``year``: ``YYYY``, date ``YYYY-01-01``;
- unknown label: value 1-12 → month, 13 → year; anything else (e.g. weekly ``Januari_II``)
  → ``other``: period ``"<year label> <turth label>"``, no date.

When the year label isn't 4 digits (e.g. ``2019/2020``), the date is ``None`` too; points still
sort by (year or ``th``, sub-period order, ``th``, ``turth``).
"""

import itertools
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy import Connection, Select, and_, exists, select

from bps_fetcher.db.schema import (
    dim_turth,
    dim_turvar,
    dim_vervar,
    domain,
    observation,
    period,
    variable,
)

Freq = Literal["month", "quarter", "semester", "year", "other"]

MAX_SERIES = 20
"""Most series (vervar x turvar combinations) one ``/series`` request returns."""

MONTHS = {
    name: i + 1
    for i, name in enumerate(
        [
            "januari",
            "februari",
            "maret",
            "april",
            "mei",
            "juni",
            "juli",
            "agustus",
            "september",
            "oktober",
            "november",
            "desember",
        ]
    )
}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
_ANNUAL = {"tahun", "tahunan", "jumlah", "total"}
_QUARTER = re.compile(r"^(?:triwulan|kuartal|quarter)\s+(iv|iii|ii|i|[1-4])$")
_SEMESTER = re.compile(r"^semester\s+(ii|i|[12])(?:\s*\((\w+)\))?$")
_YEAR = re.compile(r"^\d{4}$")


def classify_turth(val: int, label: str | None) -> tuple[Freq, int]:
    """``(freq, n)``: month 1-12, quarter 1-4, semester 1-2, year/other 0."""
    text = " ".join((label or "").lower().split())
    if text in MONTHS:
        return "month", MONTHS[text]
    if text in _ANNUAL:
        return "year", 0
    if m := _QUARTER.match(text):
        n = m[1]
        return "quarter", _ROMAN.get(n) or int(n)
    if m := _SEMESTER.match(text):
        n = m[1]
        return "semester", _ROMAN.get(n) or int(n)
    if 1 <= val <= 12:
        return "month", val
    if val == 13:
        return "year", 0
    return "other", 0


def _semester_month(n: int, label: str | None) -> int:
    m = _SEMESTER.match(" ".join((label or "").lower().split()))
    if m and m[2] in MONTHS:
        return MONTHS[m[2]]
    return 1 if n == 1 else 7


@dataclass(frozen=True)
class Period:
    freq: Freq
    period: str
    date: date | None
    sort_key: tuple[int, int]


def resolve_period(th: int, year_label: str | None, turth: int, turth_label: str | None) -> Period:
    """One observation's period string, date and sort key (see the module docstring)."""
    freq, n = classify_turth(turth, turth_label)
    year = int(year_label) if year_label and _YEAR.match(year_label) else None
    month = {
        "month": n,
        "quarter": (n - 1) * 3 + 1,
        "semester": _semester_month(n, turth_label) if freq == "semester" else 1,
        "year": 1,
        "other": 0,
    }[freq]
    sort_key = (year if year is not None else th, month)
    if year is None or freq == "other":
        base = year_label if year_label else f"th{th}"
        if freq == "year":
            return Period(freq, base, None, sort_key)
        return Period(freq, f"{base} {turth_label or turth}", None, sort_key)
    text = {
        "month": f"{year}-{n:02d}",
        "quarter": f"{year}-Q{n}",
        "semester": f"{year}-S{n}",
        "year": f"{year}",
    }[freq]
    return Period(freq, text, date(year, month, 1), sort_key)


# --- queries ------------------------------------------------------------------------------------


def _key(table: Any, domain_id: str, var_id: int) -> Any:
    return and_(table.c.domain_id == domain_id, table.c.var_id == var_id)


def variable_row(conn: Connection, domain_id: str, var_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        select(
            variable.c.domain_id,
            domain.c.name.label("domain_name"),
            domain.c.level.label("domain_level"),
            variable.c.var_id,
            variable.c.title,
            variable.c.unit,
            variable.c.sub_id.label("subject_id"),
            variable.c.sub_name.label("subject"),
            variable.c.subcsa_name.label("category"),
            variable.c["def"].label("definition"),
            variable.c.notes,
            variable.c.decimal,
            variable.c.last_update,
        )
        .join(domain, domain.c.domain_id == variable.c.domain_id)
        .where(_key(variable, domain_id, var_id))
    ).first()
    return dict(row._mapping) if row else None


def dimensions(conn: Connection, domain_id: str, var_id: int) -> dict[str, list[dict[str, Any]]]:
    """Dimension members (ordered by value) + periods; turths tell their freq and whether any
    observation uses them (one cheap EXISTS per turth on the observation PK prefix)."""
    vervars = conn.execute(
        select(dim_vervar.c.val, dim_vervar.c.label, dim_vervar.c.group_label)
        .where(_key(dim_vervar, domain_id, var_id))
        .order_by(dim_vervar.c.val)
    )
    turvars = conn.execute(
        select(dim_turvar.c.val, dim_turvar.c.label)
        .where(_key(dim_turvar, domain_id, var_id))
        .order_by(dim_turvar.c.val)
    )
    has_data = (
        exists()
        .where(_key(observation, domain_id, var_id), observation.c.turth == dim_turth.c.val)
        .label("has_data")
    )
    turths = conn.execute(
        select(dim_turth.c.val, dim_turth.c.label, has_data)
        .where(_key(dim_turth, domain_id, var_id))
        .order_by(dim_turth.c.val)
    )
    periods = conn.execute(
        select(period.c.th_id.label("th"), period.c.label)
        .where(_key(period, domain_id, var_id))
        .order_by(period.c.th_id)
    )
    return {
        "vervars": [dict(r._mapping) for r in vervars],
        "turvars": [dict(r._mapping) for r in turvars],
        "turths": [{**r._mapping, "freq": classify_turth(r.val, r.label)[0]} for r in turths],
        "periods": [dict(r._mapping) for r in periods],
    }


def series_query(
    domain_id: str,
    var_id: int,
    *,
    vervars: Sequence[int],
    turvars: Sequence[int],
    turths: Sequence[int] | None = None,
) -> Select[Any]:
    """Raw observations for the given members — the filter is a prefix of the observation PK
    (domain_id, var_id, vervar, turvar, th, turth), so it's one index range scan per member."""
    stmt = select(
        observation.c.vervar,
        observation.c.turvar,
        observation.c.th,
        observation.c.turth,
        observation.c.value,
    ).where(
        _key(observation, domain_id, var_id),
        observation.c.vervar.in_(list(vervars)),
        observation.c.turvar.in_(list(turvars)),
    )
    if turths is not None:
        stmt = stmt.where(observation.c.turth.in_(list(turths)))
    return stmt


def _dedup(values: Sequence[int]) -> list[int]:
    return list(dict.fromkeys(values))


def _labels(conn: Connection, key: Any, label: Any, domain_id: str, var_id: int) -> dict[int, str]:
    stmt = select(key, label).where(_key(key.table, domain_id, var_id))
    return dict(conn.execute(stmt).all())


def load_series(
    conn: Connection,
    domain_id: str,
    var_id: int,
    *,
    vervars: Sequence[int] | None,
    turvars: Sequence[int] | None,
    turths: Sequence[int] | None,
) -> tuple[list[dict[str, Any]], bool]:
    """``(series, truncated)``: one series per requested vervar x turvar (in request order; all
    members by value when omitted), at most ``MAX_SERIES``; points sorted by time."""
    vervar_labels = _labels(conn, dim_vervar.c.val, dim_vervar.c.label, domain_id, var_id)
    turvar_labels = _labels(conn, dim_turvar.c.val, dim_turvar.c.label, domain_id, var_id)
    turth_labels = _labels(conn, dim_turth.c.val, dim_turth.c.label, domain_id, var_id)
    year_labels = _labels(conn, period.c.th_id, period.c.label, domain_id, var_id)
    vv = _dedup(vervars) if vervars else sorted(vervar_labels)
    tv = _dedup(turvars) if turvars else sorted(turvar_labels)
    combos = list(itertools.product(vv, tv))
    truncated = len(combos) > MAX_SERIES
    combos = combos[:MAX_SERIES]
    if not combos:
        return [], truncated

    points: dict[tuple[int, int], list[tuple[Any, ...]]] = {c: [] for c in combos}
    stmt = series_query(
        domain_id,
        var_id,
        vervars=_dedup([c[0] for c in combos]),
        turvars=_dedup([c[1] for c in combos]),
        turths=_dedup(turths) if turths else None,
    )
    for r in conn.execute(stmt):
        bucket = points.get((r.vervar, r.turvar))
        if bucket is None:
            continue
        p = resolve_period(r.th, year_labels.get(r.th), r.turth, turth_labels.get(r.turth))
        bucket.append((p.sort_key, r.th, r.turth, p, r.value))

    series = []
    for (v, t), raw in points.items():
        raw.sort(key=lambda x: (x[0], x[1], x[2]))
        series.append(
            {
                "vervar": v,
                "vervar_label": vervar_labels.get(v),
                "turvar": t,
                "turvar_label": turvar_labels.get(t),
                "points": [
                    {
                        "period": p.period,
                        "date": p.date,
                        "th": th,
                        "turth": turth,
                        "value": float(value),
                    }
                    for _, th, turth, p, value in raw
                ],
            }
        )
    return series, truncated


# --- cross-section (U4) -------------------------------------------------------------------------

NATIONAL_VERVAR = 9999
"""BPS's vervar code for the national aggregate (``INDONESIA``)."""

_TAG = re.compile(r"<[^>]*>")


def is_national(val: int, label: str | None) -> bool:
    """The national aggregate member: code 9999 or a label that is just ``Indonesia`` (labels
    may carry HTML such as ``<b>INDONESIA</b>``)."""
    text = _TAG.sub("", label or "").strip().lower()
    return val == NATIONAL_VERVAR or text == "indonesia"


def cross_section_query(
    domain_id: str, var_id: int, *, turvar: int, th: int, turth: int
) -> Select[Any]:
    """Every region's value for one period — ``(domain_id, var_id, th)`` is the prefix of
    ``ix_observation_domain_id_var_id_th``."""
    return select(observation.c.vervar, observation.c.value).where(
        _key(observation, domain_id, var_id),
        observation.c.th == th,
        observation.c.turvar == turvar,
        observation.c.turth == turth,
    )


def _period_dict(
    th: int, turth: int, year_label: str | None, turth_label: str | None
) -> dict[str, Any]:
    p = resolve_period(th, year_label, turth, turth_label)
    year = year_label or f"th{th}"
    label = year if p.freq == "year" else f"{turth_label or turth} {year}"
    return {"th": th, "turth": turth, "period": p.period, "date": p.date, "label": label}


def load_cross_section(
    conn: Connection,
    domain_id: str,
    var_id: int,
    *,
    th: int | None,
    turvar: int | None,
    turth: int | None,
    freq: Freq | None,
) -> dict[str, Any]:
    """One value per vervar member for one period (default: the latest with data), ranked by
    value (desc, members without a value last); the national aggregate comes separately.
    ``periods`` lists every (th, turth) with data for the turvar (and ``freq``), in time order."""
    vervar_rows = conn.execute(
        select(dim_vervar.c.val, dim_vervar.c.label).where(_key(dim_vervar, domain_id, var_id))
    ).all()
    turvar_labels = _labels(conn, dim_turvar.c.val, dim_turvar.c.label, domain_id, var_id)
    turth_labels = _labels(conn, dim_turth.c.val, dim_turth.c.label, domain_id, var_id)
    year_labels = _labels(conn, period.c.th_id, period.c.label, domain_id, var_id)
    if turvar is None:
        turvar = min(turvar_labels, default=None)

    periods: list[dict[str, Any]] = []
    if turvar is not None:
        turths = [
            t for t, lb in turth_labels.items() if freq is None or classify_turth(t, lb)[0] == freq
        ]
        pairs = conn.execute(
            select(observation.c.th, observation.c.turth)
            .where(
                _key(observation, domain_id, var_id),
                observation.c.turvar == turvar,
                observation.c.turth.in_(turths),
            )
            .distinct()
        ).all()
        keyed = []
        for p_th, p_turth in pairs:
            year, tl = year_labels.get(p_th), turth_labels.get(p_turth)
            key = resolve_period(p_th, year, p_turth, tl).sort_key
            keyed.append(((key, p_th, p_turth), _period_dict(p_th, p_turth, year, tl)))
        periods = [p for _, p in sorted(keyed, key=lambda x: x[0])]

    chosen = [
        p
        for p in periods
        if (th is None or p["th"] == th) and (turth is None or p["turth"] == turth)
    ]
    current: dict[str, Any] | None = chosen[-1] if chosen else None
    if current is None and th is not None and turth is not None:
        current = _period_dict(th, turth, year_labels.get(th), turth_labels.get(turth))

    values: dict[int, float] = {}
    if current is not None and turvar is not None:
        stmt = cross_section_query(
            domain_id, var_id, turvar=turvar, th=current["th"], turth=current["turth"]
        )
        values = {r.vervar: float(r.value) for r in conn.execute(stmt)}

    national = None
    regions = []
    for val, label in vervar_rows:
        row = {"vervar": val, "label": label, "value": values.get(val)}
        if is_national(val, label):
            national = national or row
        else:
            regions.append(row)
    regions.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0.0), r["vervar"]))
    return {
        "turvar": turvar,
        "turvar_label": turvar_labels.get(turvar) if turvar is not None else None,
        "period": current,
        "periods": periods,
        "regions": regions,
        "national": national,
    }

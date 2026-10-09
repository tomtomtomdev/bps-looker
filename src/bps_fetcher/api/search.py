"""Variable search (U2): Postgres full-text over the variable catalog (migration 0010).

Design: the vector (``schema.VARIABLE_SEARCH_VECTOR``, a GIN expression index) uses the
``'simple'`` config — BPS titles are Indonesian, which Postgres has no stemmer for, and
``simple`` keeps every word (no English stop words/stemming). Instead every query word is a
*prefix* (``infl`` → ``infl:*``), all words must match (AND), and results are ranked by
``ts_rank`` (title weight A > subject/category B, normalised by document length so a short
exact title beats a long one). No ``unaccent``: titles are plain ASCII.
"""

import re
from typing import Any

from sqlalchemy import ColumnElement, Select, func, literal_column, select

from bps_fetcher.db.schema import VARIABLE_SEARCH_VECTOR, domain, variable

# Letters/digits only: tsquery operators (& | ! : * ( ) <->) and quotes can never reach Postgres.
_WORD = re.compile(r"[^\W_]+")


def search_tsquery(q: str | None) -> str | None:
    """User text → a ``to_tsquery('simple', …)`` string (``None`` when it has no words)."""
    words = list(dict.fromkeys(w.lower() for w in _WORD.findall(q or "")))
    if not words:
        return None
    return " & ".join(f"{w}:*" for w in words)


def search_query(
    q: str | None,
    *,
    domain_id: str | None = None,
    level: str | None = None,
    subject_id: int | None = None,
) -> tuple[Select[Any], Select[int]]:
    """``(rows, count)`` selects; rows are ordered (rank, then title) but not paginated."""
    rows = select(
        variable.c.domain_id,
        domain.c.name.label("domain_name"),
        domain.c.level.label("domain_level"),
        variable.c.var_id,
        variable.c.title,
        variable.c.unit,
        variable.c.sub_id.label("subject_id"),
        variable.c.sub_name.label("subject"),
        variable.c.subcsa_name.label("category"),
    ).join(domain, domain.c.domain_id == variable.c.domain_id)
    count = (
        select(func.count())
        .select_from(variable)
        .join(domain, domain.c.domain_id == variable.c.domain_id)
    )

    conditions = []
    if domain_id is not None:
        conditions.append(variable.c.domain_id == domain_id)
    if level is not None:
        conditions.append(domain.c.level == level)
    if subject_id is not None:
        conditions.append(variable.c.sub_id == subject_id)

    tsquery = search_tsquery(q)
    order: list[ColumnElement[Any]] = [variable.c.title, variable.c.domain_id, variable.c.var_id]
    if tsquery is not None:
        query = func.to_tsquery(literal_column("'simple'::regconfig"), tsquery)
        conditions.append(VARIABLE_SEARCH_VECTOR.bool_op("@@")(query))
        # Normalisation 1: rank / (1 + log(document length)).
        order.insert(0, func.ts_rank(VARIABLE_SEARCH_VECTOR, query, 1).desc())

    rows = rows.where(*conditions).order_by(*order)
    count = count.where(*conditions)
    return rows, count

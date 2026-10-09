"""Request dependencies: a read-only DB connection per request."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Connection, Engine


def get_engine(request: Request) -> Engine:
    engine: Engine = request.app.state.engine
    return engine


def get_conn(engine: Annotated[Engine, Depends(get_engine)]) -> Iterator[Connection]:
    """One pooled connection per request; the engine runs every transaction READ ONLY."""
    with engine.connect() as conn:
        yield conn


Conn = Annotated[Connection, Depends(get_conn)]

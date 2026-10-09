import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> dict[str, Any]:
    """Return a recorded fixture (``name``, ``request``, ``status_code``, ``body``...)."""
    data: dict[str, Any] = json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return data


@pytest.fixture
def fixture_body() -> Callable[[str], Any]:
    """Factory returning the recorded response body of a fixture by name."""
    return lambda name: read_fixture(name)["body"]

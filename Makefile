.PHONY: lint format typecheck test live build check migrate serve openapi

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

test:
	uv run pytest -m "not live"

live:
	uv run pytest -m live

build:
	uv build

check: lint typecheck test build

migrate:
	uv run alembic upgrade head

serve:
	uv run bps serve

# Regenerate web/openapi.json (committed; tests fail when it drifts from the API).
openapi:
	uv run bps openapi

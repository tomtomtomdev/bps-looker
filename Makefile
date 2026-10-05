.PHONY: lint format typecheck test live build check

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

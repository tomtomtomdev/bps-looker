.PHONY: lint format typecheck test live build check migrate serve openapi \
	web-install web-check web-api web-dev e2e

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

check: lint typecheck test build web-check

migrate:
	uv run alembic upgrade head

serve:
	uv run bps serve

# Regenerate web/openapi.json (committed; tests fail when it drifts from the API).
openapi:
	uv run bps openapi

# --- web/ (Next.js UI; pnpm, Node from web/.node-version) ---
PNPM := pnpm --dir web

web-install:
	$(PNPM) install --frozen-lockfile

# Generated client up to date vs web/openapi.json, then lint, typecheck, unit tests, build.
web-check:
	$(PNPM) check:api
	$(PNPM) lint
	$(PNPM) typecheck
	$(PNPM) test
	$(PNPM) build

# Regenerate web/openapi.json from the API, then the TS client (web/src/lib/api/schema.d.ts).
web-api: openapi
	$(PNPM) gen:api

web-dev:
	$(PNPM) dev

# Playwright e2e against `pnpm start` (needs a build; specs from U2 on, browsers via
# `pnpm --dir web exec playwright install chromium`).
e2e:
	$(PNPM) e2e

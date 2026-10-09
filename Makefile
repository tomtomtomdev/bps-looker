.PHONY: lint format typecheck test live build check migrate serve openapi \
	web-install web-check web-api web-dev e2e seed-fixtures e2e-stack

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

# Playwright e2e (web/e2e/) against a production build on :3000 (`pnpm start`); the read API is
# mocked by route interception (fast, no Python/Postgres). Browsers:
# `pnpm --dir web exec playwright install chromium`.
e2e:
	$(PNPM) build
	$(PNPM) e2e

# Load the recorded BPS fixtures (tests/fixtures) into DATABASE_URL — no API calls. Use a
# throwaway DB: it adds tasks + rows next to whatever is there.
seed-fixtures:
	uv run bps seed-fixtures tests/fixtures

# Stack smoke (web/e2e-stack/): compose `ui` profile (db + api + web, production images) seeded
# with the fixtures, then Playwright against http://localhost:$${BPS_WEB_PORT:-3000}. Needs Docker
# (CI runs it; not available on the dev Mac — see README "UI stack e2e without Docker").
# Leaves the stack running; `docker compose --profile ui down -v` removes it (and its DB volume!).
COMPOSE_UI := docker compose --profile ui
e2e-stack:
	$(COMPOSE_UI) build
	$(COMPOSE_UI) up -d --wait db
	$(COMPOSE_UI) run --rm -v $(CURDIR)/tests/fixtures:/fixtures:ro app bps seed-fixtures /fixtures
	$(COMPOSE_UI) up -d --wait api web
	E2E_BASE_URL=http://localhost:$${BPS_WEB_PORT:-3000} $(PNPM) e2e:stack

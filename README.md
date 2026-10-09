# bps-looker

Crawls the [BPS (Statistics Indonesia) WebAPI](docs/bps-webapi.md) into Postgres: **dynamic
tables**, **strategic indicators** and **foreign trade**, then keeps them fresh on a schedule.
Package `bps_fetcher`, command `bps`. Build plan and progress notes: [`docs/plan.md`](docs/plan.md).

## Deploy with Docker Compose

### Prerequisites

- Docker Engine with the Compose plugin (v2.24+, for the optional `env_file`).
- A BPS WebAPI key from <https://webapi.bps.go.id/developer/>.
- Disk: the national crawl alone is ~1.1 GB of Postgres data; provinces + regencies are much
  larger (see the S19 notes in `docs/plan.md`).

### Configure

```sh
cp .env.example .env
$EDITOR .env          # set BPS_API_KEY; on a server also set a strong POSTGRES_PASSWORD (letters/digits)
```

`.env` is read at runtime only — it is excluded from the image (`.dockerignore`) and from git.
Compose sets `DATABASE_URL` to the `db` service, so any `DATABASE_URL` in `.env` (for local dev)
is ignored inside containers.

### Start

```sh
docker compose up -d                  # db + scheduler (the crawler)
docker compose --profile ui up -d     # + api + web (the UI): http://localhost:3000
docker compose logs -f scheduler
```

| Service     | What it does |
|-------------|--------------|
| `db`        | Postgres 16, data in the `pgdata` volume, published on `127.0.0.1:5432` only. |
| `scheduler` | Loop: `bps seed refresh` → `bps work --drain --drain-wait 300` → `bps status`, then sleeps `BPS_SCHEDULE_INTERVAL` (default 3600 s). |
| `api`       | `ui` profile. Read-only HTTP API (`bps serve --workers $BPS_API_WORKERS`, default 2 uvicorn workers) on `127.0.0.1:8000` (`BPS_API_PORT`). Skips migrate-on-start (the scheduler migrates). |
| `web`       | `ui` profile. Next.js UI (standalone production image) on `127.0.0.1:3000` (`BPS_WEB_PORT`); proxies `/api/*` to the `api` service. See [Web UI in production](#web-ui-in-production). |
| `app`       | One-off commands (`tools` profile, not started by `up`): `docker compose run --rm app bps …`. |

Every container runs `python -m bps_fetcher.entrypoint` first: it migrates the database to head
(retrying while Postgres starts; exit 3 if it never comes up), then `exec`s the command. Set
`BPS_MIGRATE_ON_START=0` to skip it. The image is `python:3.12-slim` + a uv-built venv, running
as a non-root user, with no secrets inside.

**Why a shell loop and not cron/supercronic?** The work is one serial pipeline, and the queue
already decides what is due (refresh policies are daily/weekly and idempotent, retries have
backoff), so the scheduler only needs "run it again later". A plain `sleep` loop in the same image
needs no extra binary or crontab, never overlaps itself (the next run starts only after the drain
finishes, which matters for multi-hour crawls), and a failing step just waits for the next turn.
`restart: unless-stopped` covers crashes and reboots; `init: true` makes `docker compose stop`
prompt. Running an extra `bps work` next to it is safe (tasks are claimed `FOR UPDATE SKIP LOCKED`).

### Initial seeding

Seeds only enqueue tasks; the scheduler's next `bps work --drain` picks them up (or run a worker
yourself to start right away). Seeding the same thing twice is a no-op.

```sh
run() { docker compose run --rm app "$@"; }

run bps seed dynamic --domain 0000        # national dynamic tables (~9.4k requests, ~1.5 h at 2 rps)
run bps seed indicators                    # strategic indicators, every national/province domain
run bps seed trade --from 2014             # exports + imports, annual + monthly, 2014 → this year
run bps seed dynamic --level prov          # all province domains

run bps work --drain                       # optional: work now instead of waiting for the scheduler
```

The **full regency crawl** (`bps seed dynamic --level kab`) is meant to run on the server, where
it can take many hours; its measured cost and the tuned `BPS_RPS` / `BPS_CONCURRENCY` are in the
S19 notes of `docs/plan.md`. Afterwards the scheduler keeps everything fresh incrementally.

### Monitoring

```sh
docker compose run --rm app bps status                 # counts by kind/status, dead tasks, rows, last runs
docker compose run --rm app bps status --json
docker compose run --rm app bps status --max-dead 5    # exit 1 (+ "ALERT" on stderr) if dead > 5
```

`bps status --max-dead N` exits non-zero for alerting — wire it into a host cron or uptime check,
e.g. `docker compose run --rm -T app bps status --max-dead 0 || notify "bps: dead tasks"`. The
scheduler also logs `scheduler: status ALERT` each run while the threshold (`BPS_MAX_DEAD`) is
exceeded.

### Read API

`bps serve` runs a read-only FastAPI app (uvicorn) over the database — the backend of the web UI.
It needs only `DATABASE_URL` (no `BPS_API_KEY`) and opens every transaction `READ ONLY`.

```sh
uv run bps serve --host 127.0.0.1 --port 8000     # local; compose runs it as the `api` service
curl -s localhost:8000/health                      # {"status":"ok","database":"ok","revision":"0009"}; 503 if the DB is down
curl -s 'localhost:8000/domains?level=prov'        # level: pusat | prov | kab
```

Interactive docs at `/docs`, schema at `/openapi.json`. CORS allows only the origins in
`BPS_WEB_ORIGIN` (comma-separated, default `http://localhost:3000`), GET only.
The schema is committed as `web/openapi.json` (input of the web client generator): after changing
an endpoint run `make openapi` (= `uv run bps openapi`) — a test fails while it is out of date.

### Backups

Logical dump (works while running; restore into any Postgres 16):

```sh
docker compose exec -T db pg_dump -U bps -Fc bps > bps-$(date +%F).dump
docker compose exec -T db pg_restore -U bps -d bps --clean --if-exists < bps-2026-10-09.dump
```

Or copy the raw volume (stop the stack first; the volume is `<project>_pgdata`, e.g.
`bps-looker_pgdata` — see `docker volume ls`):

```sh
docker compose stop
docker run --rm -v bps-looker_pgdata:/data:ro -v "$PWD":/backup alpine \
  tar czf /backup/pgdata-$(date +%F).tgz -C /data .
docker compose start
```

`docker compose down -v` **deletes** the volume — use plain `down` to keep the data.

### Web UI in production

```sh
docker compose --profile ui up -d --build     # api + web (+ db, scheduler); waits on healthchecks
open http://localhost:3000
```

**How the browser reaches the API.** The `web` image is built with
`NEXT_PUBLIC_API_URL=/api`, so the browser only ever calls **same-origin** `/api/*`. The Next
server proxies those requests (route handler `web/src/app/api/[...path]/route.ts`, GET/HEAD only,
no cookies forwarded) to `BPS_API_INTERNAL_URL`, which is read **at runtime** (compose:
`http://api:8000`). So one image works under any hostname, the API needs no public port and no
CORS, and changing where the API lives needs no rebuild. (`NEXT_PUBLIC_*` values are inlined at
build time; a relative path keeps that harmless.)

| Env var                | Default (image / compose) | Used by |
|------------------------|---------------------------|---------|
| `BPS_API_INTERNAL_URL` | `http://api:8000`         | web (runtime): where `/api/*` is proxied to. |
| `NEXT_PUBLIC_API_URL`  | `/api`                    | web (**build** arg): API base URL as the browser sees it. Set an absolute URL only to make the browser call the API directly (then add the web origin to `BPS_WEB_ORIGIN`). |
| `BPS_API_WORKERS`      | `2`                       | api: uvicorn worker processes (`bps serve --workers`). |
| `BPS_WEB_PORT` / `BPS_API_PORT` | `3000` / `8000`  | compose: host ports, bound to `127.0.0.1` only. |

**Reverse proxy.** Put TLS + the public hostname in front of `web` only, e.g. Caddy
`bps.example.org { reverse_proxy 127.0.0.1:3000 }` or nginx `location / { proxy_pass
http://127.0.0.1:3000; proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto $scheme; }`.
`/api/*` rides along on the same origin; there is nothing else to route. Keep port 8000
unpublished (or on 127.0.0.1, as compose does) unless you want the raw API (`/docs`) reachable.

**Images.** `web/Dockerfile`: multi-stage `node:26.11.1-alpine`, `next build` with
`output: "standalone"` (`NEXT_STANDALONE=1`), runtime = `server.js` + traced `node_modules` +
static assets only, user `node`, healthcheck on `/`. The api reuses the main Python image.

**Vercel** (not set up): `web/` deploys as a plain Next.js app if the read API is reachable from
Vercel's servers — set `BPS_API_INTERNAL_URL` to the API's public URL in the project's
environment; nothing else changes.

## Local development

No Docker needed: Python 3.12 via [uv](https://docs.astral.sh/uv/) and a local Postgres 16.

```sh
brew install uv postgresql@16
brew services start postgresql@16
/opt/homebrew/opt/postgresql@16/bin/psql postgres -c "CREATE ROLE bps LOGIN PASSWORD 'bps'"
/opt/homebrew/opt/postgresql@16/bin/createdb -O bps bps          # dev DB (make migrate)
/opt/homebrew/opt/postgresql@16/bin/createdb -O bps bps_test     # tests (wiped by fixtures)

uv sync
cp .env.example .env      # BPS_API_KEY; DATABASE_URL defaults to localhost/bps
make migrate              # or: uv run bps migrate
uv run bps --help
```

| Command        | Runs |
|----------------|------|
| `make check`   | ruff + mypy --strict + pytest (no live API) + `uv build` + `make web-check` — must pass before every commit. |
| `make test`    | pytest only; DB tests use `TEST_DATABASE_URL` (default local `bps_test`) and skip if Postgres is down. |
| `make live`    | tests marked `live` against the real API (needs `BPS_API_KEY`). |
| `make format`  | ruff fix + format. |
| `make serve`   | `bps serve` (read API on 127.0.0.1:8000 against `DATABASE_URL`). |
| `make openapi` | regenerate `web/openapi.json` from the API (committed). |
| `make seed-fixtures` | `bps seed-fixtures tests/fixtures`: load the recorded BPS fixtures into `DATABASE_URL` without calling BPS (use a throwaway DB). |
| `make web-api` | `make openapi` + regenerate the TS client `web/src/lib/api/schema.d.ts` (committed). |

CI (GitHub Actions) runs the Python checks against a Postgres service, the web checks + mocked
Playwright e2e in a `web` job, the compose stack (`bps --help`, migrate-on-start against a fresh
DB, scheduler loop) in a `docker` job, and the UI stack smoke in an `e2e-stack` job (below).

### Web UI (`web/`)

Next.js (App Router) + TypeScript (strict) + Tailwind + shadcn/ui + TanStack Query, managed with
pnpm. Node version in `web/.node-version`, pnpm in `packageManager` (`web/package.json`).

```sh
brew install node pnpm                # Node 26, pnpm 12
make web-install                      # = pnpm --dir web install --frozen-lockfile
make serve                            # read API on :8000 (other terminal)
cd web && pnpm dev                    # http://localhost:3000
```

| Env var               | Default                 | Used by |
|-----------------------|-------------------------|---------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | web: read API base URL as seen from the **browser**; inlined at build time. The Docker image builds with `/api` (same-origin proxy). |
| `BPS_WEB_ORIGIN`      | `http://localhost:3000` | api: CORS origins (comma-separated) — must include the web UI's origin. |
| `BPS_WEB_PORT`        | `3000`                  | compose: host port of the `web` service. |

| Command (in `web/`) | Runs |
|---------------------|------|
| `pnpm dev`          | dev server on :3000. |
| `pnpm test`         | Vitest + Testing Library (jsdom); `pnpm test:watch` to watch. |
| `pnpm lint` / `pnpm typecheck` / `pnpm build` | ESLint (Next config), `next typegen` + `tsc`, production build. |
| `pnpm gen:api`      | regenerate `src/lib/api/schema.d.ts` from `openapi.json` (`openapi-typescript`); `pnpm check:api` fails when it is stale. |
| `pnpm e2e`          | Playwright (`e2e/`) against `pnpm start`, read API **mocked** by route interception (`make e2e`; browsers: `pnpm exec playwright install chromium`). |
| `pnpm e2e:stack`    | Playwright stack smoke (`e2e-stack/`, `playwright.stack.config.ts`) against a running stack at `E2E_BASE_URL` (default `http://localhost:3000`); nothing mocked. |

The API client is `openapi-fetch` over the generated types (`src/lib/api/client.ts`): after changing
an endpoint run `make web-api` and commit both `web/openapi.json` and `schema.d.ts`.
In development the browser calls `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`) directly
(CORS via `BPS_WEB_ORIGIN`); the production image uses the same-origin `/api` proxy instead.

#### UI stack e2e

`make e2e-stack` builds the images, starts `db`, seeds it with `bps seed-fixtures` (the recorded
responses in `tests/fixtures` run through the real handlers + worker with a fake client: all
domains, inflation y-on-y for 38 provinces 2024, 16 national indicators, chapter-03 exports 2024;
no BPS calls, deterministic), starts `api` + `web` and runs `pnpm e2e:stack` (Explorer search →
chart → map tab, Indicators tiles → history, Trade summary + breakdown + monthly charts). It needs
Docker; CI's `e2e-stack` job runs the same steps (Playwright report uploaded on failure).

Without Docker (e.g. the dev Mac), the same smoke against local processes:

```sh
createdb -O bps bps_e2e                              # throwaway DB
export DATABASE_URL=postgresql+psycopg://bps:bps@localhost:5432/bps_e2e
uv run bps migrate && make seed-fixtures
uv run bps serve --port 8001 &
cd web && NEXT_STANDALONE=1 NEXT_PUBLIC_API_URL=/api pnpm build \
  && cp -r public .next/standalone/ && cp -r .next/static .next/standalone/.next/
(cd .next/standalone && BPS_API_INTERNAL_URL=http://127.0.0.1:8001 PORT=3001 node server.js &)
E2E_BASE_URL=http://127.0.0.1:3001 pnpm e2e:stack
```

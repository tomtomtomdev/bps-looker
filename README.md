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
docker compose up -d          # db + scheduler
docker compose logs -f scheduler
```

| Service     | What it does |
|-------------|--------------|
| `db`        | Postgres 16, data in the `pgdata` volume, published on `127.0.0.1:5432` only. |
| `scheduler` | Loop: `bps seed refresh` → `bps work --drain --drain-wait 300` → `bps status`, then sleeps `BPS_SCHEDULE_INTERVAL` (default 3600 s). |
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
| `make check`   | ruff + mypy --strict + pytest (no live API) + `uv build` — must pass before every commit. |
| `make test`    | pytest only; DB tests use `TEST_DATABASE_URL` (default local `bps_test`) and skip if Postgres is down. |
| `make live`    | tests marked `live` against the real API (needs `BPS_API_KEY`). |
| `make format`  | ruff fix + format. |

CI (GitHub Actions) runs `make check` against a Postgres service and builds/runs the compose stack
(`bps --help`, migrate-on-start against a fresh DB, scheduler loop).

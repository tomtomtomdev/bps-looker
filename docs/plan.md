# BPS fetcher — build plan

Crawl three BPS WebAPI sources into Postgres: **dynamic tables**, **strategic indicators**, **foreign trade**.
API reference: [`bps-webapi.md`](bps-webapi.md).

## How every slice runs

Each slice is executed by **one subagent task**, one slice at a time, in order (slices depend on each other).
The main session is the orchestrator: it hands the subagent the slice spec below, then reviews the result
(diff, `make check` output, pushed commit) before starting the next slice.

Inside the subagent:
1. **Red** – write failing tests first (unit with recorded fixtures; DB tests against Postgres) and run them to see them fail.
2. **Green** – implement the minimum to pass.
3. **Build/test** – `make check` must pass: `ruff` + `mypy` + `pytest` + `uv build`.
4. **Progress** – tick the slice in the table below and add a short note (date, anything learned).
5. **Commit & push** – one commit per slice, message `S<n>: <title>`, pushed to `origin main`.

The subagent reports back: files changed, test count, `make check` result, commit SHA, and any surprises or follow-ups.

Live API tests are marked `@pytest.mark.live` and only run with `make live` (needs `BPS_API_KEY`). `make check` never calls the real API.

**Environment:** no Docker locally. DB tests use a local Postgres 16 via `DATABASE_URL` (Homebrew `postgresql@16`, set up in S5) and are skipped when it isn't reachable.
GitHub Actions CI (from S0) runs `make check` with a Postgres service container, plus `docker build`.
Repo: https://github.com/tomtomtomdev/bps-looker (public — `.env` must never be committed).

## Current status — 2026-10-09

- **Done:** S0 (scaffold, CI green), S1 (settings + key redaction), S2 (HTTP client), S3 (pagination), S4 (fixture recorder; `make live` green), S5 (DB schema + migrations), S6 (task queue), S7 (worker runner), S8 (domains), S9 (variable catalog), S10 (periods + data windows). Plan includes backend S0–S21 and UI U0–U7.
- **Next:** S11 — Dynamic data parser.
- **Local DB:** Postgres 16 installed via Homebrew (`/opt/homebrew/opt/postgresql@16/bin`, not on PATH); role `bps`/`bps` owns `bps` (dev, `make migrate`) and `bps_test` (pytest, wiped by fixtures).
- **Resume:** start a subagent for the next ☐ slice with its spec from this file, review its result, repeat.

## Progress

| # | Slice | Status | Notes |
|---|---|---|---|
| S0 | Repo scaffold | ☑ | 2026-10-05: uv + hatchling, src layout; ruff/mypy --strict/pytest (`live` marker excluded by default); Dockerfile + compose + CI (Postgres 16 service, docker build). Docker/compose only verified in CI. Note: `make live` exits 5 until live tests exist. |
| S1 | Settings + key redaction | ☑ | 2026-10-09: `settings.py` (`Settings` via pydantic-settings; env `BPS_API_KEY` as SecretStr, `DATABASE_URL`, `BPS_CONCURRENCY`=4, `BPS_RPS`=2.0, `BPS_USER_AGENT`; cached `get_settings()` raises `MissingApiKeyError`), `redact.py` (`redact()` masks `key=` / `/key/<x>` + known secrets; `RedactingFilter` scrubs msg, args, traceback; `install_redaction()` on root handlers). pydantic mypy plugin enabled. CI: checkout@v7, setup-uv@v10.2.0 (no floating major tags since v8, so pinned exact), runners pinned to ubuntu-24.04. |
| S2 | HTTP client | ☑ | 2026-10-09: `client.py` `BpsClient.get(path, **params)` (httpx + tenacity + aiolimiter; `from_settings()`, async ctx manager). Errors: `BpsApiError(message)`, `BpsAuthError` ("re-check your key"/"not allowed", not retried), `BpsTransientError` after `max_attempts` (5xx, non-JSON/WAF HTML, transport errors/timeouts). 4xx JSON → `BpsApiError`. Limiter is strict `1 token / (1/rps)` (no burst). Backoff `wait` injectable (`wait_none()` in tests). Surprise: httpx logs full request URL incl. `key=` at INFO — client attaches a `RedactingFilter` to the `httpx`/`httpcore` loggers. |
| S3 | Pagination | ☑ | 2026-10-09: `paginate.py` `paginate(client, model, **params)` async generator: `GET list?model=…&page=N` from `page` (default 1) to `data[0].pages`; yields `data[1]` items; stops on `data-availability: not-available` or missing `pages`. `model="domain"` hits `/domain` (own path, single call, no `page`). Malformed `data` → `BpsApiError`. Client typed via a `get` Protocol so tests use a fake client. Domain response meta shape not yet verified live — check in S4 fixture. |
| S4 | Fixture recorder | ☑ | 2026-10-09: `recorder.py` (`FixtureSpec`, `FIXTURES` catalog, `record()` scrubs key from URL/params/body and refuses to write if it survives) + `scripts/record_fixture.py [NAME…]` (1 call/s). 13 fixtures in `tests/fixtures/` (largest `trade_exp_monthly_03_2024` 979 KB, 4590 rows — not trimmed). `test_api_facts.py` checks assumptions on fixtures; 2 live tests. Learned: `/domain` meta is `{page:1, pages:1, total:549}` (has `pages`; S3 test fixed, code unchanged). Bad key → JSON `status: Error` "…Please re-check your key" (matches `_AUTH_MARKERS`) — but some key strings (e.g. `000…0x`) trip the WAF instead (403 HTML → `BpsTransientError`; recorded as `error_waf_block`). Monthly var 2263: `turtahun` lists 1–13 (13 = `Tahunan`) but values only for 1–12 (39 regions × 12 = 468). `>3` th error: "The maximum allowed number of years … is 3. You provided 7…". |
| S5 | DB schema + migrations | ☑ | 2026-10-09: `db/schema.py` (Core `metadata` with naming convention; `task` (+`created_at`/`updated_at`, `uq_task_kind_params_hash`, index `(status, next_run_at)`, self-FK `parent_id`), `raw_response` (FK `task_id` SET NULL), `domain` (check `level IN pusat/prov/kab`)). Alembic inside the package (`db/migrations`, revision `0001`; new ones `--rev-id 000N`); `db/migrate.py` `alembic_config/upgrade/downgrade(url)` — URL from arg else `DATABASE_URL` via new `DatabaseSettings` (no API key needed); root `alembic.ini` has no URL; `make migrate`. Test fixtures in `conftest.py`: `db_url` (TEST_DATABASE_URL, skip if unreachable), `empty_db`, `db_engine` (migrated to head, tables truncated after each test; DB reset to empty at session end). `test_migrations_match_metadata` fails if schema.py and migrations drift. Deps: sqlalchemy 2.1.4, alembic 1.20.0, psycopg 3.3.6. |
| S6 | Task queue | ☑ | 2026-10-09: `queue.py` (Core; every fn takes a `Connection`, runs in caller's txn): `params_hash` = sha256 of canonical JSON (sorted keys, compact, UTF-8); `enqueue` → `ON CONFLICT (kind, params_hash) DO NOTHING`, returns id or `None`; `claim(conn, n, kinds=)` = `FOR UPDATE SKIP LOCKED` on due `pending` rows → `running`, returns `Task`s; `complete`; `fail` → attempts+1, `next_run_at = now() + 30s·2^(a-1)` (cap 6 h), redacted/truncated `last_error`, `dead` at `max_attempts` (5); `requeue_stale(older_than)` resets committed orphaned `running` rows. Statuses `pending/running/done/dead` enforced by `ck_task_status` (migration `0002`, hand-written: autogenerate ignores check constraints, so `test_migrations_match_metadata` won't catch check drift). S7 pattern: one txn, claim → handler in `begin_nested()` savepoint → `complete`, or `fail` after savepoint rollback; a crashed worker's txn rolls back so the task is `pending` again (tested). |
| S7 | Worker runner | ☑ | 2026-10-09: `worker.py`: handler contract `async def h(ctx: TaskContext) -> HandlerResult` (`ctx.params`, `ctx.client` (any `get(path, **params)`), `ctx.conn` for the handler's own DB writes); result = `raw: [RawResponse(endpoint, params, body)]` + `children: [Child(kind, params)]`. `HANDLERS` registry (empty until S8). `run_one` = one txn per task on its own connection: claim(1) → savepoint[handler → `store_raw` (sha256 = `queue.canonical_sha256(body)`) → enqueue children] → `complete`, else `fail` with redacted `Type: msg` (unknown kind fails too). `run_worker(engine, client=, concurrency, drain, max_tasks, kinds, secrets)` runs N slots (TaskGroup); drain stops when nothing due *and* no slot busy (in-flight children counted); cancel → rollback → task `pending`, attempts unchanged (tested). Stored endpoint/params redacted, `key` param dropped; body not scrubbed. DB calls are sync and briefly block the loop (fine next to rate-limited HTTP). Entry point `python -m bps_fetcher.worker [--drain] [--max-tasks N] [--concurrency] [--kind]` (calls `install_redaction`); typer CLI comes in S13. |
| S8 | Domains | ☑ | 2026-10-09: `handlers/domains.py` (`@register("domains")`; `worker.register` decorator, `bps_fetcher.handlers` imported at the bottom of `worker.py`; `python -m bps_fetcher.worker` re-imports itself so `__main__` sees the registry). Params: `type` (default `all`) + `prov` passed to `/domain`; `level` (str or list) filters which domains get a `var_list` child `{"domain": id}` — all fetched domains are always stored. Upsert `ON CONFLICT DO UPDATE … WHERE IS DISTINCT FROM` (no-op rewrite skipped). Malformed id → `ValueError`; not-available → 0 rows. Fixture: 1 pusat, **34 prov** (not 38 — new Papua provinces apparently not separate domains yet; unchecked), 514 kab. No migration needed. S9 must register `var_list` taking `params["domain"]`. |
| S9 | Variable catalog | ☑ | 2026-10-09: `handlers/variables.py` (`@register("var_list")`, params `{"domain": id}`): pages `/list?model=var` via new `paginate.paginate_pages()` (yields `(request_params, body)` per page; `paginate()` now built on it; `_items` → public `items_of`), one raw response per page. `VarItem` pydantic model (`extra="ignore"`, `def` aliased `def_`): `notes`/`def` HTML-unescaped, `""` → NULL. Upsert `variable` (migration `0003`; PK `(domain_id, var_id)`, FK → `domain` ON DELETE CASCADE; extra `subcsa_name` column) only touches list-owned columns — `decimal`/`last_update` left for S12; unchanged rows skipped. Children: `th_list {"domain", "var": int}`. FK ⇒ the domain row must exist: missing → `LookupError` (S13 `seed dynamic` must run/seed `domains` first). Fixture is page 1 of 176 (1758 national vars). `test_db_schema` uses a growing `ALL_TABLES`. |
| S10 | Periods + data windows | ☑ | 2026-10-09: `handlers/periods.py` (`@register("th_list")`, params `{"domain", "var": int}`): pages `/list?model=th` (raw per page), `ThItem` (`th` aliased `label`), upsert `period` (migration `0004`; PK `(domain_id, var_id, th_id)`, composite FK → `variable` ON DELETE CASCADE; missing variable row → `LookupError`). `windows(ids, 3)` sorts/dedups, splits on gaps, chunks from the lowest id (so a new latest year only changes the last window — existing `data` tasks stay idempotent). `th_param`: single `"117"` or range `"117:119"` (per bps-webapi.md); `;` lists unused. Children: `data {"domain", "var": int, "th": str}` — S12 handler registers `data`. Follow-up: vars with sparse `th_id`s could pack gaps with `a;b;c` to save calls (not done; spec says gaps split). |
| S11 | Dynamic data parser | ☐ | |
| S12 | Observation loader | ☐ | |
| S13 | CLI + national end-to-end | ☐ | |
| S14 | Strategic indicators | ☐ | |
| S15 | Trade: discovery | ☐ | |
| S16 | Trade: fetch + load | ☐ | |
| S17 | Labeled views | ☐ | |
| S18 | Incremental refresh | ☐ | |
| S19 | Provinces + regencies rollout | ☐ | |
| S20 | Status / monitoring | ☐ | |
| S21 | Scheduled deployment | ☐ | |
| U0 | UI: read API scaffold | ☐ | |
| U1 | UI: web scaffold | ☐ | |
| U2 | UI: variable search | ☐ | |
| U3 | UI: variable detail + chart | ☐ | |
| U4 | UI: ranking + map | ☐ | |
| U5 | UI: indicators dashboard | ☐ | |
| U6 | UI: trade dashboard | ☐ | |
| U7 | UI: deploy + e2e | ☐ | |

---

## Verified API facts the design depends on (2026-10-05)

- Browser-like `User-Agent` required (WAF blocks curl/python defaults with an HTML page).
- Errors arrive as HTTP 200 with `"status":"Error"` + `message`.
- List endpoints are 10 per page; `perpage` is ignored for `var` and `th`.
- `model=data` requires `th`; **max 3 periods per call** (`th=124:126`).
- `datacontent` key = `vervar + var + turvar + th + turth` concatenated.
- Data response has `last_update` (e.g. `2026-10-01 11:24:01`) → change detection.
- Monthly vars use `turtahun` 1–12; 13 = `Tahunan` is listed in the dimension but had no values (var 2263, 2024 — verified 2026-10-09); `turth` filter param seems ignored — fetch all and filter locally.
- `/domain` meta is `{"page": 1, "pages": 1, "total": 549}` (no `per_page`/`count`) (verified 2026-10-09).
- Bad key → HTTP 200 JSON auth error, but odd key strings can trigger a 403 WAF HTML page instead (verified 2026-10-09).
- Indicators: national 16, DKI Jakarta 28; the API returns only the **latest value** per indicator → we must keep history ourselves.
- Trade: param is lowercase **`tahun`** (docs say `Tahun`). No pagination; whole result in one response. Data available **from 2015** (2013 and earlier: unavailable; 2014 unchecked). Monthly rows have `bulan: "[11] November"`; `kodehs: "[03] Fish, ..."`. 10 chapters monthly for one year ≈ 14k rows / 2.2 MB / 10 s. Full HS codes (`jenishs=2`) returned nothing for the codes tried — use 2-digit chapters.

## Size estimate

- Dynamic: ~125k vars across 549 domains. Per var: ~1 `th` call + ⌈years/3⌉ data calls (avg ~3) ≈ **~500k calls**, plus ~13k list pages. At 4 concurrent × ~1 s ≈ 1.5 days first load; national alone (1.75k vars) ≈ 30 min.
- Indicators: 35 domains × 1–3 pages ≈ 100 calls per snapshot.
- Trade: 2 flows × ~12 years × ~10 batches (10 chapters each), monthly ≈ **~240 calls**, ~2–3M rows.

## Data model (target)

```
raw_response(id, task_id, endpoint, params jsonb, fetched_at, body jsonb, sha256)
task(id, kind, params jsonb, params_hash, status, attempts, next_run_at, last_error, parent_id)
     unique(kind, params_hash)

domain(domain_id pk, name, url, level)                -- level: pusat/prov/kab
variable(domain_id, var_id, title, unit, sub_id, sub_name, subcsa_id, def, notes,
         decimal, vertical, last_update, pk(domain_id, var_id))
period(domain_id, var_id, th_id, label)
dim_vervar(domain_id, var_id, val, label, group_label)
dim_turvar(domain_id, var_id, val, label)
dim_turth(domain_id, var_id, val, label)
observation(domain_id, var_id, vervar, turvar, th, turth, value numeric, fetched_at,
            pk(domain_id, var_id, vervar, turvar, th, turth))

indicator_snapshot(domain_id, indicator_id, var, title, name, value, unit, periode,
                   category, data_source, first_seen, last_seen,
                   pk(domain_id, indicator_id, periode, title))

trade_flow(flow, period_type, year, month, hs2, port, country, value_usd, netweight_kg,
           fetched_at, pk(flow, period_type, year, month, hs2, port, country))
hs_chapter(hs2 pk, description)
```

---

## Slices

### S0 — Repo scaffold
- **Tests first:** `test_smoke.py` imports package `bps_fetcher` and checks `__version__`.
- **Build:** `uv init` (Python 3.12), `src/bps_fetcher/`, `pyproject` with ruff/mypy/pytest config, `Makefile` (`check`, `test`, `live`, `lint`), `docker-compose.yml` (Postgres 16 + app), `Dockerfile`, GitHub Actions workflow (`make check` with Postgres service + `docker build`). `.gitignore` already covers `.env`.
- **Done when:** `make check` green locally; pushed; CI green.

### S1 — Settings + key redaction
- **Tests first:** settings load `BPS_API_KEY` from env / `.env`; missing key raises clear error; `redact(url)` removes `key=...` and `/key/<x>/`; log records never contain the key.
- **Build:** `pydantic-settings` `Settings` (key, db url, concurrency, rps, user agent); logging filter for redaction.

### S2 — HTTP client
- **Tests first (respx):** sends UA + key; `/v1/api/` base; `status: Error` → `BpsApiError(message)`; key error → `BpsAuthError` (not retried); WAF HTML / 5xx / timeout → retried with backoff then `BpsTransientError`; rate limiter caps requests/sec.
- **Build:** async `BpsClient.get(path, **params) -> dict` on `httpx` + `tenacity` + `aiolimiter`.

### S3 — Pagination
- **Tests first:** iterates `page=1..pages`; stops on `not-available`; yields items from `data[1]`; handles `pages` missing (domain endpoint).
- **Build:** `async def paginate(client, model, **params)`.

### S4 — Fixture recorder
- **Tests first:** recorder strips key from stored URL and body; writes `tests/fixtures/<name>.json`.
- **Build:** `scripts/record_fixture.py` (live). Record: `domain_all`, `var_0000_p1`, `th_0000_1804`, `data_0000_1804`, `data_0000_2263` (monthly), `indicators_0000_p1/p2`, `trade_exp_annual_03_2024`, `trade_exp_monthly_03_2024`, plus error samples (bad key, missing th, >3 years).

### S5 — DB schema + migrations
- **Setup:** (done) Homebrew `postgresql@16` with `bps` and `bps_test` databases; tests read `TEST_DATABASE_URL`.
- **Tests first (local Postgres; CI service container):** `alembic upgrade head` from empty creates all tables; downgrade works; unique constraints enforced.
- **Build:** SQLAlchemy 2 Core table definitions + Alembic migration for `raw_response`, `task`, `domain`.

### S6 — Task queue
- **Tests first:** `enqueue` is idempotent on `(kind, params_hash)`; `claim(n)` uses `FOR UPDATE SKIP LOCKED` so two concurrent claimers never get the same task; `complete`; `fail` increments attempts and sets `next_run_at` with backoff; tasks over max attempts → `dead`.
- **Build:** `queue.py`.

### S7 — Worker runner
- **Tests first:** with a fake handler registry: claims task → calls handler → stores raw response → enqueues returned child tasks → marks done, all in one transaction; handler exception → `fail`; stops when queue empty (`--drain`) or after N tasks.
- **Build:** `worker.py` with `HANDLERS: dict[kind, handler]`, async concurrency = settings.

### S8 — Domains
- **Tests first:** handler `domains` parses `domain_all` fixture → 549 rows with level derived from id (`0000` pusat, `xx00` prov, else kab); upsert idempotent; emits `var_list` child per domain (filterable by level).
- **Build:** `handlers/domains.py`, migration for `domain`.

### S9 — Variable catalog
- **Tests first:** `var_list(domain)` paginates and upserts `variable`; emits one `th_list` per var; HTML-escaped notes are unescaped; extra unknown fields ignored (pydantic `extra=ignore`).
- **Build:** `handlers/variables.py`, migration for `variable`.

### S10 — Periods + data windows
- **Tests first:** `th_list` stores periods; `windows([110..126], 3)` → contiguous chunks of ≤3 sorted th_ids (gaps split chunks); emits one `data` task per window as `th=a:b` or `a;b`.
- **Build:** `handlers/periods.py`, migration for `period`.

### S11 — Dynamic data parser
- **Tests first (pure, no DB):** using fixtures `data_0000_1804` (annual, vervar) and `data_0000_2263` (monthly, 39 regions): every `datacontent` key maps to exactly one `(vervar, turvar, th, turth)`; total count matches; values numeric; dimension label lists extracted; unknown key → reported, not silently dropped.
- **Build:** `parse_data(resp) -> ParsedData(observations, dims, last_update)` by generating keys from dimension combinations.

### S12 — Observation loader
- **Tests first:** upsert observations + dims idempotently; re-running the same response writes 0 changed rows; `variable.last_update` updated; a newer `last_update` replaces values.
- **Build:** `handlers/data.py`, migrations for `dim_*` and `observation` (index on `(domain_id, var_id, th)`).

### S13 — CLI + national end-to-end
- **Tests first:** `bps seed dynamic --domain 0000 --limit-vars 5` + `bps work --drain` against respx-mocked API (fixtures) fills all tables; CLI `--help` works. `live` test: real run for 3 national vars.
- **Build:** `typer` CLI: `seed`, `work`, `status` (stub).
- **Manual:** full national crawl (~30 min); record counts in Notes.

### S14 — Strategic indicators
- **Tests first:** parse both indicator pages; snapshot upsert keeps `first_seen`, bumps `last_seen`; new `periode` creates a new row (history); non-pusat/prov domain skipped.
- **Build:** `handlers/indicators.py`, `bps seed indicators [--domain]`, migration for `indicator_snapshot`.

### S15 — Trade: discovery
- **Tests first:** `parse_bracket("[03] Fish, ...") -> ("03", "Fish, ...")`; `parse_bracket("[11] November") -> (11, ...)`; chapter list 01–99 excluding unused (77); batches of N chapters joined by `;`.
- **Build:** `trade.py` helpers; live script to find the earliest available year (check 2014) and confirm whether `jenishs=2` works with any code format → write findings to Notes and `bps-webapi.md`.

### S16 — Trade: fetch + load
- **Tests first:** handler `trade(flow, period_type, year, chapters)` parses annual + monthly fixtures into `trade_flow` rows (month null for annual); `hs_chapter` upserted; reload of same year replaces rows for that (flow, period_type, year, chapters) atomically; `unavailable` → done with 0 rows.
- **Build:** `handlers/trade.py`, `bps seed trade --from 2015 --to <current>`, migrations.

### S17 — Labeled views
- **Tests first:** on seeded rows, `v_observation` returns domain name, variable title, unit, vervar/turvar labels, year, month/period label, value; `v_trade` and `v_indicator_latest` return expected rows.
- **Build:** SQL views in a migration (dbt optional later).

### S18 — Incremental refresh
- **Tests first:** `bps seed refresh` schedules: indicators daily; trade current + previous year weekly; var re-list weekly; data tasks only for vars whose `last_update` changed or whose latest `th` is new; completed tasks become re-runnable via `next_run_at`.
- **Build:** refresh policies in `refresh.py`.

### S19 — Provinces + regencies rollout
- **Tests first:** seed by level (`--level prov|kab`) and by province (`kabbyprov`); concurrency/rps from settings respected (limiter test).
- **Manual:** run provinces, then regencies; record durations, error rates, and any new API errors in Notes; tune rps.

### S20 — Status / monitoring
- **Tests first:** `bps status` prints task counts by kind/status, dead tasks with last error, rows per table, last successful run per source.
- **Build:** status queries; exit code non-zero when dead tasks exceed a threshold (for alerting).

### S21 — Scheduled deployment
- **Tests first:** `docker compose run app bps --help` in CI; migration runs on container start.
- **Build:** compose service with scheduled `refresh` + `work --drain` (cron or GitHub Actions schedule); README with setup.

---

## UI (option C: custom web app)

Layout: `web/` (Next.js app) next to the Python package; read API lives in `src/bps_fetcher/api/`.
UI slices start after S17 (labeled views) and can interleave with S18–S21.
`make check` grows to include `web` checks from U1: `pnpm lint` + `pnpm typecheck` + `pnpm test` (Vitest) + `pnpm build`; Playwright e2e runs in `make e2e` and in CI.

### U0 — Read API scaffold
- **Tests first (pytest + httpx `ASGITransport`):** `GET /health` → 200; `GET /domains?level=prov` returns seeded domains; OpenAPI schema generated; CORS allows the web origin from settings.
- **Build:** FastAPI app (`bps serve`), DB session dependency, pydantic response models, `uvicorn` in compose.

### U1 — Web scaffold
- **Tests first (Vitest + Testing Library):** home page renders app shell (header, nav: Explorer / Indicators / Trade); typed API client generated from OpenAPI (`openapi-typescript`) compiles.
- **Build:** Next.js (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query, `pnpm`; `web` service in compose; CI job for web; `make check` extended.

### U2 — Variable search
- **API tests first:** `GET /variables?q=inflasi&domain=0000&page=` uses Postgres full-text (title + subject) with ranking and pagination; filters by subject and domain level.
- **Web tests first:** search box debounces, shows results with unit + subject, empty and error states; Playwright: type "inflasi" → results → click → variable page URL.
- **Build:** `tsvector` index migration, `/variables` endpoint, Explorer search page.

### U3 — Variable detail + time-series chart
- **API tests first:** `GET /variables/{domain}/{var}` returns metadata + dimensions (vervar/turvar/turth labels); `GET /variables/{domain}/{var}/series?vervar=&turvar=&turth=` returns `[{period, value}]` sorted, monthly periods resolved to dates.
- **Web tests first:** dimension pickers (multi-select regions/categories) update URL state; ECharts line chart renders one series per selection; table view toggle; CSV download; notes rendered as sanitized HTML.
- **Build:** endpoints + Explorer variable page with ECharts.

### U4 — Region ranking + map
- **Data:** Indonesia province + regency GeoJSON (BPS/BIG boundaries, simplified) stored in `web/public/geo/`, mapped by BPS domain/MFD code; test that every province domain has a shape.
- **API tests first:** `GET /variables/{domain}/{var}/cross-section?th=&turvar=&turth=` returns value per vervar region with region code.
- **Web tests first:** ranking bar chart sorted desc; choropleth colors by value with legend; period slider; clicking a region opens its series.
- **Build:** cross-section endpoint + map/ranking tab.

### U5 — Indicators dashboard
- **API tests first:** `GET /indicators?domain=` latest snapshot per indicator; `GET /indicators/{domain}/{id}/history` from `indicator_snapshot`.
- **Web tests first:** KPI tiles (value, unit, periode, change vs previous snapshot); province selector; tile click → history sparkline/chart and link to the underlying variable in Explorer.
- **Build:** endpoints + Indicators page.

### U6 — Trade dashboard
- **API tests first:** `GET /trade/summary?flow=&year=&month=` totals; `GET /trade/breakdown?by=country|port|hs2&flow=&from=&to=&top=` with "others" bucket; `GET /trade/series?hs2=&country=` monthly series. Queries use indexes/materialized view (perf test on seeded 1M rows < 500 ms).
- **Web tests first:** export/import toggle, year/month range, top-N bar charts (country, port, chapter), monthly trend line, trade balance; filters sync to URL.
- **Build:** endpoints (+ materialized view migration if needed) + Trade page.

### U7 — UI deploy + e2e
- **Tests first:** Playwright smoke across Explorer, Indicators, Trade against compose stack seeded with fixtures (CI).
- **Build:** production Dockerfiles for api + web, compose profile `ui`, README section; optional Vercel config for `web`.

### UI stack

| Concern | Choice |
|---|---|
| Read API | FastAPI, pydantic v2, uvicorn (reuses SQLAlchemy + `v_*` views) |
| Frontend | Next.js (App Router) + TypeScript, `pnpm` |
| UI kit | Tailwind CSS + shadcn/ui |
| Data fetching | TanStack Query, `openapi-typescript` generated client |
| Charts / maps | Apache ECharts (line, bar, geo choropleth) + Indonesia GeoJSON |
| Tests | Vitest + Testing Library (unit), Playwright (e2e), pytest for API |

---

## Tech stack

| Concern | Choice |
|---|---|
| Language / deps | Python 3.12, `uv` |
| HTTP | `httpx` (async), `tenacity`, `aiolimiter` |
| Models / config | `pydantic` v2, `pydantic-settings` |
| DB | PostgreSQL 16, SQLAlchemy 2 Core, Alembic, `psycopg` 3 |
| Queue | Postgres `task` table (`FOR UPDATE SKIP LOCKED`) — no extra infra |
| CLI | `typer` |
| Tests | `pytest`, `pytest-asyncio`, `respx`; Postgres from local Homebrew / CI service |
| Quality | `ruff`, `mypy --strict` |
| Runtime | Docker Compose (postgres + app) |
| BI (later) | Looker Studio on the `v_*` views; BigQuery sync only if needed |

## Open questions

- BI target decided: custom web app (option C, slices U0–U7). Looker Studio / BigQuery not planned.

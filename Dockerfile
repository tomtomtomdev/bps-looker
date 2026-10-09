# syntax=docker/dockerfile:1
# Two stages: uv builds a self-contained venv (project installed non-editable, so src/ is not
# needed at runtime); the runtime image is python:3.12-slim + that venv, run as a non-root user.
# No secrets in the image: BPS_API_KEY / DATABASE_URL come from the environment (.env via compose).

FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --no-editable

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim

RUN groupadd --system --gid 1000 bps \
 && useradd --system --uid 1000 --gid bps --home-dir /app --no-create-home bps

COPY --from=build --chown=bps:bps /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app
USER bps

# Migrate the DB (DATABASE_URL) to head, then exec the command (BPS_MIGRATE_ON_START=0 skips).
ENTRYPOINT ["python", "-m", "bps_fetcher.entrypoint"]
CMD ["bps", "--help"]

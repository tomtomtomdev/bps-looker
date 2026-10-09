# syntax=docker/dockerfile:1
# Simple image for the web UI: install, `next build`, `next start` (production Dockerfile: U7).
# NEXT_PUBLIC_API_URL is inlined into the client bundle at build time, so it is a build arg:
# the URL the *browser* uses to reach the read API.

FROM node:26.11.1-slim

RUN npm install --global pnpm@12.10.1

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

COPY . .

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

RUN chown -R node:node /app/.next
USER node
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
EXPOSE 3000
CMD ["pnpm", "start"]

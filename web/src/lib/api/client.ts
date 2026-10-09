import createClient, { type ClientOptions } from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Health = Schemas["Health"];
export type Domain = Schemas["Domain"];

export const DEFAULT_API_URL = "http://localhost:8000";

/** Read API base URL: `NEXT_PUBLIC_API_URL` (inlined at build time) or the local `bps serve`. */
export function apiBaseUrl(): string {
  const url = process.env.NEXT_PUBLIC_API_URL?.trim() || DEFAULT_API_URL;
  return url.replace(/\/+$/, "");
}

/**
 * Typed client for the read API, generated from `web/openapi.json` (`pnpm gen:api`).
 * Looks up `globalThis.fetch` per request (not at creation), so tests can stub it.
 */
export function createApiClient(options: ClientOptions = {}) {
  return createClient<paths>({
    baseUrl: apiBaseUrl(),
    fetch: (request) => globalThis.fetch(request),
    ...options,
  });
}

export type ApiClient = ReturnType<typeof createApiClient>;

export const api = createApiClient();

export type VariableSummary = Schemas["VariableSummary"];
export type VariablePage = Schemas["VariablePage"];
export type DomainLevel = Domain["level"];
export type VariableDetail = Schemas["VariableDetail"];
export type TurthMember = Schemas["TurthMember"];
export type Freq = TurthMember["freq"];
export type SeriesResponse = Schemas["SeriesResponse"];
export type Series = Schemas["Series"];
export type SeriesPoint = Schemas["SeriesPoint"];
export type CrossSection = Schemas["CrossSection"];
export type CrossSectionRegion = Schemas["CrossSectionRegion"];
export type CrossSectionPeriod = Schemas["CrossSectionPeriod"];
export type Indicator = Schemas["Indicator"];
export type IndicatorList = Schemas["IndicatorList"];
export type IndicatorHistory = Schemas["IndicatorHistory"];
export type IndicatorPoint = Schemas["IndicatorPoint"];

/**
 * Same-origin proxy to the read API: the browser calls `/api/<path>` on the web server, which
 * forwards to `BPS_API_INTERNAL_URL` (read per request, at runtime — so one image works behind
 * any API host; in compose it is `http://api:8000`). The production image is built with
 * `NEXT_PUBLIC_API_URL=/api`, so the browser never needs to know where the API lives and no CORS
 * is involved. GET/HEAD only (the API is read-only); cookies and auth headers are not forwarded.
 */

export const DEFAULT_UPSTREAM_URL = "http://localhost:8000";
const TIMEOUT_MS = 30_000;
const FORWARD_REQUEST = ["accept", "accept-language", "if-none-match", "if-modified-since"];
const FORWARD_RESPONSE = [
  "content-type",
  "cache-control",
  "etag",
  "last-modified",
  "content-disposition",
];

/** Upstream API base URL, read from the server environment at request time. */
export function apiUpstreamUrl(): string {
  const url = process.env.BPS_API_INTERNAL_URL?.trim() || DEFAULT_UPSTREAM_URL;
  return url.replace(/\/+$/, "");
}

type ProxyOptions = {
  upstream?: string;
  fetch?: (input: string, init: RequestInit) => Promise<Response>;
};

export async function proxyToApi(
  request: Request,
  path: string[],
  { upstream = apiUpstreamUrl(), fetch = globalThis.fetch }: ProxyOptions = {},
): Promise<Response> {
  const { search } = new URL(request.url);
  const target = `${upstream}/${path.map(encodeURIComponent).join("/")}${search}`;
  const headers = new Headers();
  for (const name of FORWARD_REQUEST) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  let res: Response;
  try {
    res = await fetch(target, {
      method: request.method,
      headers,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    return Response.json({ detail: "Read API unreachable" }, { status: 502 });
  }
  const out = new Headers();
  for (const name of FORWARD_RESPONSE) {
    const value = res.headers.get(name);
    if (value !== null) out.set(name, value);
  }
  return new Response(request.method === "HEAD" ? null : res.body, {
    status: res.status,
    statusText: res.statusText,
    headers: out,
  });
}

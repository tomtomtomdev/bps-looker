// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import { apiUpstreamUrl, DEFAULT_UPSTREAM_URL, proxyToApi } from "@/lib/api/proxy";

describe("apiUpstreamUrl", () => {
  it("defaults to the local API", () => {
    vi.stubEnv("BPS_API_INTERNAL_URL", "");
    expect(apiUpstreamUrl()).toBe(DEFAULT_UPSTREAM_URL);
    expect(DEFAULT_UPSTREAM_URL).toBe("http://localhost:8000");
  });

  it("reads BPS_API_INTERNAL_URL at request time, without a trailing slash", () => {
    vi.stubEnv("BPS_API_INTERNAL_URL", "http://api:8000/");
    expect(apiUpstreamUrl()).toBe("http://api:8000");
  });
});

describe("proxyToApi", () => {
  it("forwards path + query and passes the response through", async () => {
    const upstream = vi.fn(
      async () =>
        new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { "content-type": "application/json", "set-cookie": "x=1" },
        }),
    );
    const res = await proxyToApi(
      new Request("http://web.test/api/variables?q=inflasi&page=2", {
        headers: { accept: "application/json", cookie: "secret=1" },
      }),
      ["variables"],
      { upstream: "http://api:8000", fetch: upstream },
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ items: [] });
    expect(res.headers.get("content-type")).toBe("application/json");
    expect(res.headers.get("set-cookie")).toBeNull();
    const [url, init] = upstream.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://api:8000/variables?q=inflasi&page=2");
    const sent = new Headers(init.headers);
    expect(sent.get("accept")).toBe("application/json");
    expect(sent.get("cookie")).toBeNull();
  });

  it("encodes path segments", async () => {
    const upstream = vi.fn(async () => new Response("{}"));
    await proxyToApi(new Request("http://web.test/api/x"), ["variables", "0000", "a b"], {
      upstream: "http://api:8000",
      fetch: upstream,
    });
    expect((upstream.mock.calls[0] as unknown as [string])[0]).toBe(
      "http://api:8000/variables/0000/a%20b",
    );
  });

  it("keeps upstream error statuses (e.g. 503 health)", async () => {
    const body = { status: "error", database: "unreachable", revision: null };
    const res = await proxyToApi(new Request("http://web.test/api/health"), ["health"], {
      upstream: "http://api:8000",
      fetch: async () => Response.json(body, { status: 503 }),
    });
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual(body);
  });

  it("answers 502 when the API is unreachable", async () => {
    const res = await proxyToApi(new Request("http://web.test/api/health"), ["health"], {
      upstream: "http://api:8000",
      fetch: async () => {
        throw new TypeError("fetch failed");
      },
    });
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ detail: "Read API unreachable" });
  });
});

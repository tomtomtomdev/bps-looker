import { describe, expect, expectTypeOf, it, vi } from "vitest";

import { apiBaseUrl, createApiClient, DEFAULT_API_URL } from "@/lib/api/client";
import type { components, operations, paths } from "@/lib/api/schema";

describe("apiBaseUrl", () => {
  it("defaults to the local API", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");
    expect(apiBaseUrl()).toBe(DEFAULT_API_URL);
    expect(DEFAULT_API_URL).toBe("http://localhost:8000");
  });

  it("uses NEXT_PUBLIC_API_URL without a trailing slash", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.example.test/");
    expect(apiBaseUrl()).toBe("https://api.example.test");
  });
});

describe("generated schema", () => {
  it("types the endpoints", () => {
    expectTypeOf<keyof paths>().toEqualTypeOf<"/health" | "/domains" | "/variables">();
    expectTypeOf<components["schemas"]["Domain"]["level"]>().toEqualTypeOf<
      "pusat" | "prov" | "kab"
    >();
    expectTypeOf<keyof operations>().toEqualTypeOf<
      "getHealth" | "listDomains" | "searchVariables"
    >();
    expectTypeOf<
      NonNullable<operations["searchVariables"]["parameters"]["query"]>
    >().toEqualTypeOf<{
      q?: string | null;
      domain?: string | null;
      level?: "pusat" | "prov" | "kab" | null;
      subject?: number | null;
      page?: number;
      page_size?: number;
    }>();
    expectTypeOf<components["schemas"]["VariablePage"]["items"]>().toEqualTypeOf<
      components["schemas"]["VariableSummary"][]
    >();
  });
});

describe("createApiClient", () => {
  it("sends typed query params and parses the JSON body", async () => {
    const fetchMock = vi.fn(async (request: Request) => {
      void request;
      return Response.json([
        { domain_id: "1100", name: "Aceh", url: null, level: "prov" },
      ]);
    });
    const client = createApiClient({ baseUrl: "http://api.test", fetch: fetchMock });

    const { data, error } = await client.GET("/domains", {
      params: { query: { level: "prov" } },
    });

    expect(error).toBeUndefined();
    expect(data?.[0]?.name).toBe("Aceh");
    expect(fetchMock.mock.calls[0][0].url).toBe("http://api.test/domains?level=prov");
  });
});

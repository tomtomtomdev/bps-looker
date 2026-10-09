import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HealthBadge } from "@/components/health-badge";
import { renderWithQuery } from "@/test/render";

function stubFetch(response: () => Response | Promise<Response>) {
  const fetchMock = vi.fn(async (input: Request | string | URL) => {
    void input;
    return response();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("HealthBadge", () => {
  it("calls GET /health on the API base URL and shows the DB revision", async () => {
    const fetchMock = stubFetch(() =>
      Response.json({ status: "ok", database: "ok", revision: "0009" }),
    );
    renderWithQuery(<HealthBadge />);

    expect(screen.getByText(/checking/i)).toBeInTheDocument();
    expect(await screen.findByText("API ok · rev 0009")).toBeInTheDocument();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const request = fetchMock.mock.calls[0][0] as Request;
    expect(request.method).toBe("GET");
    expect(request.url).toBe("http://localhost:8000/health");
  });

  it("shows the database as unreachable on a 503", async () => {
    stubFetch(() => Response.json({ status: "error", database: "unreachable" }, { status: 503 }));
    renderWithQuery(<HealthBadge />);
    expect(await screen.findByText("API up · DB unreachable")).toBeInTheDocument();
  });

  it("shows the API as down when the request fails", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    renderWithQuery(<HealthBadge />);
    expect(await screen.findByText("API down")).toBeInTheDocument();
  });
});

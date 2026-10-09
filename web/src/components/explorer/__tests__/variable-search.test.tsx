import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SEARCH_DEBOUNCE_MS, VariableSearch } from "@/components/explorer/variable-search";
import type { VariablePage, VariableSummary } from "@/lib/api/client";
import { renderWithQuery } from "@/test/render";

const nav = vi.hoisted(() => ({
  search: "",
  replace: vi.fn<(href: string, options?: { scroll?: boolean }) => void>(),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ replace: nav.replace, push: vi.fn(), prefetch: vi.fn() }),
}));

function variable(overrides: Partial<VariableSummary> = {}): VariableSummary {
  return {
    domain_id: "0000",
    domain_name: "Indonesia",
    domain_level: "pusat",
    var_id: 1,
    title: "Inflasi Bulanan (M-to-M)",
    unit: "Persen",
    subject_id: 3,
    subject: "Inflasi",
    category: "Harga-Harga",
    ...overrides,
  };
}

function page(items: VariableSummary[], extra: Partial<VariablePage> = {}): VariablePage {
  return { items, total: items.length, page: 1, page_size: 20, ...extra };
}

/** Stub fetch; `respond` gets the parsed request URL. Returns the list of requested URLs. */
function stubApi(respond: (url: URL) => Response | Promise<Response>) {
  const urls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      urls.push(url);
      return respond(url);
    }),
  );
  return urls;
}

const searched = (urls: URL[]) => urls.map((u) => u.searchParams.get("q"));

describe("VariableSearch", () => {
  beforeEach(() => {
    nav.search = "";
    nav.replace.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("debounces typing into one search request and writes ?q= to the URL", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const urls = stubApi((url) =>
      Response.json(url.searchParams.get("q") ? page([variable()]) : page([])),
    );
    renderWithQuery(<VariableSearch />);

    await user.type(screen.getByRole("searchbox", { name: /search variables/i }), "inflasi");
    // Still inside the debounce window: no request for the typed text yet.
    expect(searched(urls).filter(Boolean)).toEqual([]);
    expect(nav.replace).not.toHaveBeenCalled();

    await act(() => vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS));
    expect(await screen.findByRole("link", { name: /Inflasi Bulanan/ })).toBeInTheDocument();
    expect(searched(urls).filter(Boolean)).toEqual(["inflasi"]);
    expect(nav.replace).toHaveBeenLastCalledWith("/explorer?q=inflasi", { scroll: false });

    const request = urls.at(-1)!;
    expect(request.origin + request.pathname).toBe("http://localhost:8000/variables");
    expect(request.searchParams.get("page")).toBe("1");
  });

  it("shows each result with its domain, unit and subject, linking to the variable page", async () => {
    nav.search = "q=inflasi";
    stubApi(() =>
      Response.json(
        page([
          variable(),
          variable({
            domain_id: "3100",
            domain_name: "DKI Jakarta",
            domain_level: "prov",
            var_id: 77,
            title: "Inflasi Jakarta",
            unit: null,
            subject: null,
            category: null,
          }),
        ]),
      ),
    );
    renderWithQuery(<VariableSearch />);

    expect(screen.getByRole("searchbox", { name: /search variables/i })).toHaveValue("inflasi");
    const list = await screen.findByRole("list", { name: "Search results" });
    const [first, second] = within(list).getAllByRole("listitem");
    expect(within(first).getByRole("link", { name: "Inflasi Bulanan (M-to-M)" })).toHaveAttribute(
      "href",
      "/explorer/0000/1",
    );
    expect(first).toHaveTextContent("Indonesia");
    expect(first).toHaveTextContent("Persen");
    expect(first).toHaveTextContent("Inflasi · Harga-Harga");
    expect(within(second).getByRole("link", { name: "Inflasi Jakarta" })).toHaveAttribute(
      "href",
      "/explorer/3100/77",
    );
    expect(second).toHaveTextContent("DKI Jakarta");
    expect(screen.getByText("2 variables")).toBeInTheDocument();
  });

  it("shows an empty state when nothing matches", async () => {
    nav.search = "q=zzz";
    stubApi(() => Response.json(page([])));
    renderWithQuery(<VariableSearch />);
    expect(await screen.findByText("No variables match “zzz”.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Search results" })).not.toBeInTheDocument();
  });

  it("shows an error state with a retry", async () => {
    nav.search = "q=inflasi";
    let fail = true;
    stubApi(() =>
      fail
        ? Promise.reject(new TypeError("Failed to fetch"))
        : Response.json(page([variable()])),
    );
    renderWithQuery(<VariableSearch />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load variables");
    fail = false;
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("link", { name: /Inflasi Bulanan/ })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an error state on an API error response", async () => {
    nav.search = "q=inflasi";
    stubApi(() => Response.json({ detail: "boom" }, { status: 500 }));
    renderWithQuery(<VariableSearch />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load variables");
  });

  it("paginates, keeping the page in the URL", async () => {
    nav.search = "q=inflasi&page=2";
    const urls = stubApi((url) => {
      const n = Number(url.searchParams.get("page"));
      return Response.json(
        page([variable({ var_id: n, title: `Inflasi page ${n}` })], {
          total: 45,
          page: n,
        }),
      );
    });
    renderWithQuery(<VariableSearch />);

    expect(await screen.findByText("Inflasi page 2")).toBeInTheDocument();
    expect(screen.getByText("45 variables · page 2 of 3")).toBeInTheDocument();
    expect(urls[0].searchParams.get("page")).toBe("2");
    expect(urls[0].searchParams.get("page_size")).toBe("20");

    const pager = screen.getByRole("navigation", { name: "Pagination" });
    fireEvent.click(within(pager).getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Inflasi page 3")).toBeInTheDocument();
    expect(nav.replace).toHaveBeenLastCalledWith("/explorer?q=inflasi&page=3", {
      scroll: false,
    });
    expect(within(pager).getByRole("button", { name: "Next" })).toBeDisabled();

    fireEvent.click(within(pager).getByRole("button", { name: "Previous" }));
    fireEvent.click(await within(pager).findByRole("button", { name: "Previous" }));
    expect(await screen.findByText("Inflasi page 1")).toBeInTheDocument();
    expect(nav.replace).toHaveBeenLastCalledWith("/explorer?q=inflasi", { scroll: false });
    expect(within(pager).getByRole("button", { name: "Previous" })).toBeDisabled();
  });

  it("goes back to page 1 on a new search", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    nav.search = "q=inflasi&page=3";
    const urls = stubApi(() => Response.json(page([variable()], { total: 60 })));
    renderWithQuery(<VariableSearch />);
    await screen.findByRole("list", { name: "Search results" });

    const box = screen.getByRole("searchbox", { name: /search variables/i });
    await user.clear(box);
    await user.type(box, "penduduk");
    await act(() => vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS));

    const last = urls.at(-1)!;
    expect(last.searchParams.get("q")).toBe("penduduk");
    expect(last.searchParams.get("page")).toBe("1");
    expect(nav.replace).toHaveBeenLastCalledWith("/explorer?q=penduduk", { scroll: false });
  });

  it("filters by domain level from the URL and the level picker", async () => {
    nav.search = "q=inflasi&level=prov";
    const urls = stubApi(() => Response.json(page([variable()])));
    renderWithQuery(<VariableSearch />);
    await screen.findByRole("list", { name: "Search results" });
    expect(urls[0].searchParams.get("level")).toBe("prov");

    const picker = screen.getByRole("combobox", { name: "Level" });
    expect(picker).toHaveValue("prov");
    fireEvent.change(picker, { target: { value: "" } });
    await vi.waitFor(() => expect(urls.at(-1)!.searchParams.has("level")).toBe(false));
    expect(nav.replace).toHaveBeenLastCalledWith("/explorer?q=inflasi", { scroll: false });
  });

  it("lists every variable by title when the search is empty", async () => {
    const urls = stubApi(() => Response.json(page([variable()], { total: 1234 })));
    renderWithQuery(<VariableSearch />);
    expect(await screen.findByText(/1,234 variables/)).toBeInTheDocument();
    expect(urls[0].searchParams.has("q")).toBe(false);
  });
});

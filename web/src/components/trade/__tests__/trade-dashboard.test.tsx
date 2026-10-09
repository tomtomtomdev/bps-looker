import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TradeDashboard } from "@/components/trade/trade-dashboard";
import type { TradeBreakdown, TradePeriods, TradeSeriesResponse, TradeSummary } from "@/lib/api/client";
import { renderWithQuery } from "@/test/render";

const nav = vi.hoisted(() => ({
  search: "",
  replace: vi.fn<(href: string, options?: { scroll?: boolean }) => void>(),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ replace: nav.replace, push: vi.fn(), prefetch: vi.fn() }),
}));

// ECharts needs a canvas: the chart is a probe exposing its option, with a button per bar.
vi.mock("@/components/explorer/echart", () => ({
  EChart: ({
    option,
    label,
    onClick,
  }: {
    option: { series: { data: unknown[] }[] };
    label: string;
    onClick?: (p: { data?: unknown }) => void;
  }) => (
    <div role="figure" aria-label={label} data-option={JSON.stringify(option)}>
      {onClick
        ? (option.series[0].data as { key: string }[]).map((d) => (
            <button key={d.key} type="button" onClick={() => onClick({ data: d })}>
              pick {d.key}
            </button>
          ))
        : null}
    </div>
  ),
}));

const PERIODS: TradePeriods = {
  items: [
    { flow: "export", year: 2024, annual: true, months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] },
    { flow: "export", year: 2025, annual: false, months: [1, 2, 3] },
    { flow: "import", year: 2024, annual: true, months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] },
    { flow: "import", year: 2025, annual: false, months: [1, 2] },
  ],
  latest_year: 2025,
  latest_month: "2025-03",
};

function range(url: URL) {
  const from = url.searchParams.get("from") ?? "2025";
  const to = url.searchParams.get("to") ?? from;
  return { start: from, end: to, granularity: from.includes("-") ? "month" : "year" } as const;
}

function summary(url: URL): TradeSummary {
  const partial = (url.searchParams.get("from") ?? "").startsWith("202");
  const periods = partial ? [{ year: 2025, basis: "monthly" as const, months: 3 }] : [];
  return {
    range: range(url),
    items: [
      { flow: "export", value_usd: 266_500_000_000, netweight_kg: 600_000_000_000, periods },
      { flow: "import", value_usd: 233_700_000_000, netweight_kg: 200_000_000_000, periods },
    ],
    balance_usd: 32_800_000_000,
  };
}

const NAMES = {
  hs2: [["27", "Mineral fuels"], ["15", "Animal or vegetable fats"]],
  country: [["CHINA", "CHINA"], ["UNITED STATES", "UNITED STATES"]],
  port: [["TANJUNG PRIOK", "TANJUNG PRIOK"], ["", null]],
} as const;

function breakdown(url: URL): TradeBreakdown {
  const by = url.searchParams.get("by") as TradeBreakdown["by"];
  const flow = (url.searchParams.get("flow") ?? "export") as TradeBreakdown["flow"];
  return {
    by,
    flow,
    top: Number(url.searchParams.get("top") ?? 10),
    range: range(url),
    total: { flow, value_usd: 100, netweight_kg: 10, periods: [] },
    items: NAMES[by].map(([key, label], i) => ({ key, label, value_usd: 60 - i * 30, netweight_kg: 5, share: 0.6 - i * 0.3 })),
    others: { count: 40, value_usd: 10, netweight_kg: 1, share: 0.1 },
  };
}

function series(url: URL, empty: boolean): TradeSeriesResponse {
  const points = empty
    ? []
    : [
        { period: "2025-01", date: "2025-01-01", value_usd: 20, netweight_kg: 1 },
        { period: "2025-02", date: "2025-02-01", value_usd: 22, netweight_kg: 1 },
      ];
  return {
    hs2: url.searchParams.get("hs2"),
    hs2_label: url.searchParams.get("hs2") ? "Mineral fuels" : null,
    country: url.searchParams.get("country"),
    range: null,
    series: [
      { flow: "export", points },
      { flow: "import", points: points.map((p) => ({ ...p, value_usd: 15 })) },
    ],
    balance: points.map((p) => ({ period: p.period, date: p.date, value_usd: 5 })),
  };
}

type Mode = { periods?: "ok" | "empty" | "error"; breakdown?: "ok" | "error"; series?: "ok" | "empty" };

function stubApi(mode: Mode = {}) {
  const urls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      urls.push(url);
      switch (url.pathname) {
        case "/trade/periods":
          if (mode.periods === "error") return Response.json({ detail: "boom" }, { status: 500 });
          return Response.json(
            mode.periods === "empty" ? { items: [], latest_year: null, latest_month: null } : PERIODS,
          );
        case "/trade/summary":
          return Response.json(summary(url));
        case "/trade/breakdown":
          if (mode.breakdown === "error") return Response.json({ detail: "boom" }, { status: 500 });
          return Response.json(breakdown(url));
        case "/trade/series":
          return Response.json(series(url, mode.series === "empty"));
      }
      return Response.json({ detail: "Not Found" }, { status: 404 });
    }),
  );
  return urls;
}

const requests = (urls: URL[], path: string) => urls.filter((u) => u.pathname === path);
const lastHref = () => nav.replace.mock.calls.at(-1)?.[0];

describe("TradeDashboard", () => {
  beforeEach(() => {
    nav.search = "";
    nav.replace.mockReset();
  });

  it("shows totals, top-N charts, trend and balance for the latest year", async () => {
    const urls = stubApi();
    renderWithQuery(<TradeDashboard />);
    expect(screen.getByRole("status")).toHaveTextContent(/Loading trade data/);

    const exports = await screen.findByRole("group", { name: "Exports total" });
    await waitFor(() => expect(exports).toHaveTextContent("US$266.5B"));
    expect(screen.getByRole("group", { name: "Imports total" })).toHaveTextContent("US$233.7B");
    expect(screen.getByRole("group", { name: "Trade balance" })).toHaveTextContent("US$32.8B");
    expect(screen.getByText(/2025: 3 months of monthly figures/)).toBeInTheDocument();
    expect(requests(urls, "/trade/summary")[0].searchParams.get("from")).toBe("2025");

    for (const name of [/Top countries/, /Top ports/, /Top HS chapters/]) {
      expect(await screen.findByRole("figure", { name })).toBeInTheDocument();
    }
    const chapters = screen.getByRole("figure", { name: /Top HS chapters/ });
    expect(chapters.dataset.option).toContain("27 Mineral fuels");
    expect(screen.getAllByText(/Others \(40\)/)).toHaveLength(3);
    const breakdowns = requests(urls, "/trade/breakdown");
    expect(breakdowns.map((u) => u.searchParams.get("by")).sort()).toEqual(["country", "hs2", "port"]);
    expect(breakdowns.every((u) => u.searchParams.get("flow") === "export")).toBe(true);
    expect(breakdowns.every((u) => u.searchParams.get("top") === "10")).toBe(true);

    expect(await screen.findByRole("figure", { name: /Monthly exports/ })).toBeInTheDocument();
    expect(screen.getByRole("figure", { name: /Trade balance by month/ })).toBeInTheDocument();
    const s = requests(urls, "/trade/series")[0];
    expect(s.searchParams.has("flow")).toBe(false); // both flows, for the balance
  });

  it("export/import toggle refetches the breakdowns and syncs the URL", async () => {
    const urls = stubApi();
    renderWithQuery(<TradeDashboard />);
    const toggle = await screen.findByRole("group", { name: "Flow" });
    expect(within(toggle).getByRole("button", { name: "Exports" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(within(toggle).getByRole("button", { name: "Imports" }));
    expect(lastHref()).toBe("/trade?flow=import");
    await waitFor(() =>
      expect(requests(urls, "/trade/breakdown").filter((u) => u.searchParams.get("flow") === "import")).toHaveLength(3),
    );
    expect(await screen.findByRole("figure", { name: /Monthly imports/ })).toBeInTheDocument();
  });

  it("year and month ranges", async () => {
    const urls = stubApi();
    renderWithQuery(<TradeDashboard />);
    const from = await screen.findByRole("combobox", { name: "From" });
    expect(from).toHaveValue("2025");

    fireEvent.change(from, { target: { value: "2024" } });
    expect(lastHref()).toBe("/trade?from=2024&to=2025");
    await waitFor(() =>
      expect(requests(urls, "/trade/summary").at(-1)!.searchParams.get("from")).toBe("2024"),
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Period" }), { target: { value: "month" } });
    expect(lastHref()).toBe("/trade?from=2024-01&to=2025-03");
    await screen.findByRole("heading", { name: "Jan 2024 – Mar 2025" });
    expect(screen.queryByText(/months of monthly figures/)).toBeNull();
    const to = screen.getByRole("combobox", { name: "To" });
    expect(to).toHaveValue("2025-03");
    expect(within(to).getAllByRole("option")).toHaveLength(15);

    fireEvent.change(to, { target: { value: "2024-06" } });
    expect(lastHref()).toBe("/trade?from=2024-01&to=2024-06");

    fireEvent.change(screen.getByRole("combobox", { name: "Top" }), { target: { value: "5" } });
    expect(lastHref()).toBe("/trade?from=2024-01&to=2024-06&top=5");
    await waitFor(() =>
      expect(requests(urls, "/trade/breakdown").some((u) => u.searchParams.get("top") === "5")).toBe(true),
    );
  });

  it("clicking a chapter or country filters the monthly charts; chips clear it", async () => {
    const urls = stubApi();
    renderWithQuery(<TradeDashboard />);
    const chapters = await screen.findByRole("figure", { name: /Top HS chapters/ });
    fireEvent.click(within(chapters).getByRole("button", { name: "pick 27" }));
    expect(lastHref()).toBe("/trade?hs2=27");
    await waitFor(() =>
      expect(requests(urls, "/trade/series").at(-1)!.searchParams.get("hs2")).toBe("27"),
    );
    expect(await screen.findByRole("figure", { name: /Monthly exports · 27 Mineral fuels/ })).toBeInTheDocument();

    fireEvent.click(within(screen.getByRole("figure", { name: /Top countries/ })).getByRole("button", { name: "pick CHINA" }));
    expect(lastHref()).toBe("/trade?hs2=27&country=CHINA");

    fireEvent.click(screen.getByRole("button", { name: "Clear chapter filter" }));
    expect(lastHref()).toBe("/trade?country=CHINA");
    // Ports aren't a series filter: no click handler.
    expect(within(screen.getByRole("figure", { name: /Top ports/ })).queryByRole("button")).toBeNull();
  });

  it("restores the state from the URL", async () => {
    nav.search = "flow=import&from=2024-02&to=2024-04&country=CHINA";
    const urls = stubApi();
    renderWithQuery(<TradeDashboard />);
    expect(await screen.findByRole("combobox", { name: "From" })).toHaveValue("2024-02");
    expect(screen.getByRole("combobox", { name: "Period" })).toHaveValue("month");
    expect(screen.getByRole("button", { name: "Imports" })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(requests(urls, "/trade/series").length).toBeGreaterThan(0));
    const s = requests(urls, "/trade/series")[0];
    expect([s.searchParams.get("country"), s.searchParams.get("from"), s.searchParams.get("to")]).toEqual([
      "CHINA",
      "2024-02",
      "2024-04",
    ]);
    expect(screen.getByRole("button", { name: "Clear country filter" })).toBeInTheDocument();
  });

  it("no monthly figures: says so instead of empty charts", async () => {
    stubApi({ series: "empty" });
    renderWithQuery(<TradeDashboard />);
    expect(await screen.findAllByText(/No monthly figures for this range/)).toHaveLength(2);
  });

  it("no trade data at all", async () => {
    stubApi({ periods: "empty" });
    renderWithQuery(<TradeDashboard />);
    expect(await screen.findByText(/No trade data yet/)).toBeInTheDocument();
  });

  it("errors: periods and breakdowns, with retry", async () => {
    stubApi({ periods: "error" });
    renderWithQuery(<TradeDashboard />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Could not load the trade data/);

    stubApi({ breakdown: "error" });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("group", { name: "Exports total" })).toBeInTheDocument();
    expect(await screen.findAllByText(/Could not load this breakdown/)).toHaveLength(3);
  });
});

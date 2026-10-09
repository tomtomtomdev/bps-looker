import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IndicatorsDashboard } from "@/components/indicators/indicators-dashboard";
import type { Indicator, IndicatorHistory } from "@/lib/api/client";
import type { HistoryOption } from "@/lib/indicators/indicators";
import { indicator } from "@/test/fixtures";
import { renderWithQuery } from "@/test/render";

const nav = vi.hoisted(() => ({
  search: "",
  replace: vi.fn<(href: string, options?: { scroll?: boolean }) => void>(),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ replace: nav.replace, push: vi.fn(), prefetch: vi.fn() }),
}));

// ECharts needs a canvas: the chart is a probe exposing its option.
vi.mock("@/components/explorer/echart", () => ({
  EChart: ({ option, label }: { option: object; label: string }) => (
    <div role="figure" aria-label={label} data-option={JSON.stringify(option)} />
  ),
}));

const NATIONAL: Indicator[] = [
  indicator(),
  indicator({
    indicator_id: 20,
    title: "Nilai Neraca Perdagangan, Agustus 2026",
    label: "Nilai Neraca Perdagangan",
    value: 3552.2,
    unit: "Juta US$",
    periode: "Agustus 2026",
    var: 498,
    variable: null,
    previous: { periode: "Juli 2026", value: 4170.1 },
    change: -617.9,
    change_pct: -14.82,
  }),
  indicator({
    indicator_id: 7,
    title: "Pertumbuhan Ekonomi, Triwulan II 2026",
    label: "Pertumbuhan Ekonomi",
    value: 5.29,
    periode: "Triwulan II 2026",
    var: 104,
    variable: null,
    previous: null,
    change: null,
    change_pct: null,
  }),
];

const ACEH: Indicator[] = [
  indicator({ domain_id: "1100", value: 1.5, variable: { domain_id: "1100", var_id: 2263, title: "Inflasi Aceh" } }),
];

const DOMAINS = [
  { domain_id: "1100", name: "Aceh", url: null, level: "prov" },
  { domain_id: "3100", name: "DKI Jakarta", url: null, level: "prov" },
];

function history(item: Indicator): IndicatorHistory {
  return {
    ...item,
    points: [
      { periode: "Agustus 2026", title: "x", value: 3.1, first_seen: "2026-10-04T00:00:00Z", last_seen: "2026-10-06T00:00:00Z" },
      { periode: item.periode, title: item.title, value: item.value, first_seen: item.first_seen, last_seen: item.last_seen },
    ],
  };
}

type Mode = "ok" | "empty" | "error";

function stubApi(mode: Mode = "ok") {
  const urls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      urls.push(url);
      if (url.pathname === "/domains") return Response.json(DOMAINS);
      const match = url.pathname.match(/^\/indicators\/(\d{4})\/(\d+)\/history$/);
      if (match) {
        const list = match[1] === "1100" ? ACEH : NATIONAL;
        const item = list.find((i) => i.indicator_id === Number(match[2]));
        return item ? Response.json(history(item)) : Response.json({ detail: "Indicator not found" }, { status: 404 });
      }
      if (url.pathname === "/indicators") {
        if (mode === "error") return Response.json({ detail: "boom" }, { status: 500 });
        const domain = url.searchParams.get("domain") ?? "0000";
        const items = mode === "empty" ? [] : domain === "1100" ? ACEH : NATIONAL;
        const name = domain === "1100" ? "Aceh" : "Indonesia";
        return Response.json({
          domain: { domain_id: domain, name, url: null, level: domain === "0000" ? "pusat" : "prov" },
          items,
        });
      }
      return Response.json({ detail: "Not Found" }, { status: 404 });
    }),
  );
  return urls;
}

const listUrls = (urls: URL[]) => urls.filter((u) => u.pathname === "/indicators");
const tile = (name: RegExp) => screen.getByRole("button", { name });

describe("IndicatorsDashboard", () => {
  beforeEach(() => {
    nav.search = "";
    nav.replace.mockReset();
  });

  it("shows a KPI tile per national indicator: value, unit, periode, change", async () => {
    const urls = stubApi();
    renderWithQuery(<IndicatorsDashboard />);
    expect(screen.getByRole("status")).toHaveTextContent(/Loading indicators/);

    const inflation = await screen.findByRole("button", { name: /Inflasi Year on Year/ });
    expect(listUrls(urls)[0].searchParams.get("domain")).toBe("0000");
    expect(inflation).toHaveTextContent("3.28");
    expect(inflation).toHaveTextContent("Persen");
    expect(inflation).toHaveTextContent("September 2026");
    expect(inflation).toHaveTextContent("+0.18 (+5.8%) vs Agustus 2026");
    expect(within(inflation).getByTestId("change")).toHaveAttribute("data-direction", "up");
    expect(inflation).toHaveAttribute("aria-pressed", "false");

    const trade = tile(/Neraca Perdagangan/);
    expect(trade).toHaveTextContent("3,552.2");
    expect(trade).toHaveTextContent("−617.9 (−14.8%) vs Juli 2026");
    expect(within(trade).getByTestId("change")).toHaveAttribute("data-direction", "down");

    const growth = tile(/Pertumbuhan Ekonomi/);
    expect(growth).toHaveTextContent("First recorded periode");
    expect(within(growth).getByTestId("change")).not.toHaveAttribute("data-direction");
  });

  it("lists national + provinces and switches domain via the URL", async () => {
    const urls = stubApi();
    renderWithQuery(<IndicatorsDashboard />);
    const select = await screen.findByRole("combobox", { name: "Region" });
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(3));
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Indonesia (national)",
      "Aceh",
      "DKI Jakarta",
    ]);
    expect(urls.find((u) => u.pathname === "/domains")?.searchParams.get("level")).toBe("prov");

    fireEvent.change(select, { target: { value: "1100" } });
    expect(nav.replace).toHaveBeenLastCalledWith("/indicators?domain=1100", { scroll: false });
    await waitFor(() => expect(tile(/Inflasi/)).toHaveTextContent("1.5"));
    expect(listUrls(urls).at(-1)!.searchParams.get("domain")).toBe("1100");
  });

  it("reads the domain from the URL", async () => {
    nav.search = "domain=1100";
    const urls = stubApi();
    renderWithQuery(<IndicatorsDashboard />);
    await screen.findByRole("button", { name: /Inflasi/ });
    expect(listUrls(urls)[0].searchParams.get("domain")).toBe("1100");
    expect(screen.getByRole("heading", { name: /Aceh/ })).toBeInTheDocument();
  });

  it("opens a tile's history chart and its Explorer link", async () => {
    const urls = stubApi();
    renderWithQuery(<IndicatorsDashboard />);
    fireEvent.click(await screen.findByRole("button", { name: /Inflasi Year on Year/ }));
    expect(nav.replace).toHaveBeenLastCalledWith("/indicators?indicator=3", { scroll: false });
    expect(tile(/Inflasi Year on Year/)).toHaveAttribute("aria-pressed", "true");

    const figure = await screen.findByRole("figure", { name: /Inflasi Year on Year history/ });
    expect(urls.some((u) => u.pathname === "/indicators/0000/3/history")).toBe(true);
    const option = JSON.parse(figure.getAttribute("data-option")!) as HistoryOption;
    expect(option.xAxis.data).toEqual(["Agustus 2026", "September 2026"]);
    expect(option.series[0].data).toEqual([3.1, 3.28]);
    const region = screen.getByRole("region", { name: /Inflasi Year on Year/ });
    expect(within(region).getByRole("link", { name: /Open in Explorer/ })).toHaveAttribute(
      "href",
      "/explorer/0000/2263",
    );
    expect(region).toHaveTextContent("Pada September 2026 terjadi inflasi");

    // Clicking the open tile again closes it.
    fireEvent.click(tile(/Inflasi Year on Year/));
    expect(nav.replace).toHaveBeenLastCalledWith("/indicators", { scroll: false });
    expect(screen.queryByRole("region", { name: /Inflasi Year on Year/ })).not.toBeInTheDocument();
  });

  it("says when the underlying variable isn't crawled", async () => {
    nav.search = "indicator=20";
    stubApi();
    renderWithQuery(<IndicatorsDashboard />);
    const region = await screen.findByRole("region", { name: /Neraca Perdagangan/ });
    expect(within(region).queryByRole("link", { name: /Open in Explorer/ })).not.toBeInTheDocument();
    expect(region).toHaveTextContent("Variable 498 is not crawled yet");
  });

  it("shows an empty state for a domain without indicators", async () => {
    nav.search = "domain=3100";
    stubApi("empty");
    renderWithQuery(<IndicatorsDashboard />);
    expect(await screen.findByText(/No indicators recorded for/)).toBeInTheDocument();
  });

  it("shows an error with retry", async () => {
    const urls = stubApi("error");
    renderWithQuery(<IndicatorsDashboard />);
    const alert = await screen.findByText(/Could not load the indicators/);
    const before = listUrls(urls).length;
    fireEvent.click(within(alert.closest("[role=alert]")! as HTMLElement).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(listUrls(urls).length).toBeGreaterThan(before));
  });
});

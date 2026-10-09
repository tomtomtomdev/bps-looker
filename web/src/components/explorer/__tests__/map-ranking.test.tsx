import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VariableExplorer } from "@/components/explorer/variable-explorer";
import type { CrossSection } from "@/lib/api/client";
import type { MapOption, RankingOption } from "@/lib/explorer/cross-section";
import { variableDetail } from "@/test/fixtures";
import { renderWithQuery } from "@/test/render";

const nav = vi.hoisted(() => ({
  search: "",
  replace: vi.fn<(href: string, options?: { scroll?: boolean }) => void>(),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ replace: nav.replace, push: vi.fn(), prefetch: vi.fn() }),
}));

const registered = vi.hoisted(() => [] as string[]);

// ECharts needs a canvas: each chart is a probe exposing its option, with one button per data
// item that "clicks" it.
vi.mock("@/components/explorer/echart", () => ({
  registerMap: (name: string) => registered.push(name),
  EChart: ({
    option,
    label,
    onClick,
  }: {
    option: { series: { data: { vervar: number }[] }[] };
    label: string;
    onClick?: (p: { data?: unknown }) => void;
  }) => (
    <div role="figure" aria-label={label} data-option={JSON.stringify(option)}>
      {option.series[0].data.map((d) => (
        <button key={d.vervar} type="button" onClick={() => onClick?.({ data: d })}>
          pick {d.vervar}
        </button>
      ))}
    </div>
  ),
}));

const PERIODS = [
  { th: 124, turth: 1, period: "2024-01", date: "2024-01-01", label: "Januari 2024" },
  { th: 124, turth: 2, period: "2024-02", date: "2024-02-01", label: "Februari 2024" },
  { th: 124, turth: 3, period: "2024-03", date: "2024-03-01", label: "Maret 2024" },
];

function crossSection(url: URL): CrossSection {
  const th = Number(url.searchParams.get("th") ?? 124);
  const turth = Number(url.searchParams.get("turth") ?? 3);
  const period = PERIODS.find((p) => p.th === th && p.turth === turth) ?? null;
  return {
    turvar: 0,
    turvar_label: "Tidak ada",
    period,
    periods: PERIODS,
    regions: [
      { vervar: 3100, label: "PROV DKI JAKARTA", value: 3 + turth / 10 },
      { vervar: 1100, label: "PROV ACEH", value: 1 + turth / 10 },
      { vervar: 9200, label: "PROV PAPUA BARAT DAYA", value: 0.5 + turth / 10 },
      { vervar: 9400, label: "PROV PAPUA", value: null },
    ],
    national: { vervar: 9999, label: "INDONESIA", value: 2 },
  };
}

const GEO = {
  type: "FeatureCollection",
  features: [1100, 3100, 9200].map((c) => ({
    type: "Feature",
    properties: { code: String(c), name: `P${c}` },
    geometry: { type: "Polygon", coordinates: [] },
  })),
};

function stubApi() {
  const urls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === "string" ? input : input.url, "http://localhost:3000");
      urls.push(url);
      if (url.pathname.startsWith("/geo/")) return Response.json(GEO);
      if (url.pathname.endsWith("/cross-section")) return Response.json(crossSection(url));
      if (url.pathname.endsWith("/series")) {
        return Response.json({ series: [], truncated: false, max_series: 20 });
      }
      return Response.json(
        variableDetail({
          vervars: [
            { val: 1100, label: "PROV ACEH", group_label: "38 Provinsi" },
            { val: 3100, label: "PROV DKI JAKARTA", group_label: "38 Provinsi" },
            { val: 9200, label: "PROV PAPUA BARAT DAYA", group_label: "38 Provinsi" },
            { val: 9999, label: "INDONESIA", group_label: "38 Provinsi" },
          ],
        }),
      );
    }),
  );
  return urls;
}

const option = <T,>(name: RegExp) =>
  JSON.parse(screen.getByRole("figure", { name }).getAttribute("data-option")!) as T;

const crossUrls = (urls: URL[]) => urls.filter((u) => u.pathname.endsWith("/cross-section"));

const render = () => renderWithQuery(<VariableExplorer domain="0000" varId={2263} />);

describe("Map / ranking tab", () => {
  beforeEach(() => {
    nav.search = "";
    nav.replace.mockReset();
    registered.length = 0;
  });

  it("switches from the time series tab, URL updated", async () => {
    stubApi();
    render();
    const tabs = await screen.findByRole("tablist", { name: "Data view" });
    expect(within(tabs).getByRole("tab", { name: "Time series" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(within(tabs).getByRole("tab", { name: "Map / ranking" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/explorer/0000/2263?tab=map", { scroll: false });
    expect(await screen.findByRole("figure", { name: /ranking/ })).toBeInTheDocument();
  });

  it("ranks regions descending and colours the 38-province map by value", async () => {
    nav.search = "tab=map";
    const urls = stubApi();
    render();

    await screen.findByRole("figure", { name: /ranking/ });
    const [request] = crossUrls(urls);
    expect(request.pathname).toBe("/variables/0000/2263/cross-section");
    expect(request.searchParams.get("turvar")).toBe("0");
    expect(request.searchParams.get("freq")).toBe("month");
    expect(request.searchParams.has("th")).toBe(false);

    const ranking = option<RankingOption>(/ranking/);
    expect(ranking.yAxis.data).toEqual(["DKI JAKARTA", "ACEH", "PAPUA BARAT DAYA"]);
    expect(ranking.series[0].data.map((d) => d.value)).toEqual([3.3, 1.3, 0.8]);
    expect(ranking.series[0].markLine?.data).toEqual([{ xAxis: 2, name: "Indonesia" }]);

    await screen.findByRole("figure", { name: /map/ });
    const map = option<MapOption>(/map/);
    expect(map.series[0].map).toBe("provinces-38");
    expect(map.series[0].data.map((d) => [d.name, d.value])).toEqual([
      ["1100", 1.3],
      ["3100", 3.3],
      ["9200", 0.8],
    ]);
    expect(map.visualMap).toMatchObject({ min: 0.8, max: 3.3 });
    expect(registered).toEqual(["provinces-38"]);
    expect(urls.some((u) => u.pathname === "/geo/provinces-38.json")).toBe(true);
    expect(screen.getByText(/INDONESIA/)).toHaveTextContent("2.00");
  });

  it("moves through periods with the slider", async () => {
    nav.search = "tab=map";
    const urls = stubApi();
    render();
    const slider = await screen.findByRole("slider", { name: "Period" });
    expect(slider).toHaveValue("2");
    expect(screen.getByText("Maret 2024")).toBeInTheDocument();

    fireEvent.change(slider, { target: { value: "0" } });
    expect(nav.replace).toHaveBeenLastCalledWith("/explorer/0000/2263?tab=map&th=124&turth=1", {
      scroll: false,
    });
    await waitFor(() => expect(crossUrls(urls).at(-1)!.searchParams.get("turth")).toBe("1"));
    expect(await screen.findByText("Januari 2024")).toBeInTheDocument();
    await waitFor(() =>
      expect(option<RankingOption>(/ranking/).series[0].data.map((d) => d.value)).toEqual([3.1, 1.1, 0.6]),
    );
  });

  it("clicking a region (bar or map) opens its time series", async () => {
    nav.search = "tab=map&th=124&turth=2";
    stubApi();
    render();
    const ranking = await screen.findByRole("figure", { name: /ranking/ });
    fireEvent.click(within(ranking).getByRole("button", { name: "pick 1100" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/explorer/0000/2263?vervar=1100", { scroll: false });
    expect(await screen.findByRole("tab", { name: "Time series" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("group", { name: /Region/ })).toBeInTheDocument();
  });

  it("clicking a map region works the same way", async () => {
    nav.search = "tab=map";
    stubApi();
    render();
    const map = await screen.findByRole("figure", { name: /map/ });
    fireEvent.click(within(map).getByRole("button", { name: "pick 3100" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/explorer/0000/2263?vervar=3100", { scroll: false });
  });
});

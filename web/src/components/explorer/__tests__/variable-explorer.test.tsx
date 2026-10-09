import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VariableExplorer } from "@/components/explorer/variable-explorer";
import type { Series, SeriesResponse } from "@/lib/api/client";
import type { ChartOption } from "@/lib/explorer/series";
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

// ECharts needs a canvas: the chart is replaced by a probe exposing the option it was given.
vi.mock("@/components/explorer/echart", () => ({
  EChart: ({ option, label }: { option: ChartOption; label: string }) => (
    <div data-testid="echart" aria-label={label} data-option={JSON.stringify(option)} />
  ),
}));

const LABELS: Record<number, string> = { 9999: "INDONESIA", 1100: "PROV ACEH", 3100: "PROV DKI JAKARTA" };

function seriesFor(url: URL): SeriesResponse {
  const vervars = url.searchParams.getAll("vervar").map(Number);
  const series: Series[] = vervars.map((v) => ({
    vervar: v,
    vervar_label: LABELS[v] ?? null,
    turvar: 0,
    turvar_label: "Tidak ada",
    points: [
      { period: "2024-01", date: "2024-01-01", th: 124, turth: 1, value: v / 1000 },
      { period: "2024-02", date: "2024-02-01", th: 124, turth: 2, value: v / 1000 + 1 },
    ],
  }));
  return { series, truncated: false, max_series: 20 };
}

type Responder = (url: URL) => Response | Promise<Response>;

function stubApi({ detail, series }: { detail?: Responder; series?: Responder } = {}) {
  const urls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      urls.push(url);
      if (url.pathname.endsWith("/series")) {
        return series ? series(url) : Response.json(seriesFor(url));
      }
      return detail
        ? detail(url)
        : Response.json(
            variableDetail({
              definition: "<p>Inflasi adalah <b>kenaikan</b> harga</p>",
              notes: '<p>Catatan</p><script>window.hacked = 1</script><img src=x onerror="alert(1)">',
            }),
          );
    }),
  );
  return urls;
}

const seriesUrls = (urls: URL[]) => urls.filter((u) => u.pathname.endsWith("/series"));

function chartOption(): ChartOption {
  return JSON.parse(screen.getByTestId("echart").getAttribute("data-option")!) as ChartOption;
}

const render = () => renderWithQuery(<VariableExplorer domain="0000" varId={2263} />);

describe("VariableExplorer", () => {
  beforeEach(() => {
    nav.search = "";
    nav.replace.mockReset();
  });

  it("shows metadata, sanitized notes and a chart of the default selection", async () => {
    const urls = stubApi();
    render();
    expect(screen.getByRole("status")).toHaveTextContent("Loading variable");

    expect(
      await screen.findByRole("heading", { level: 1, name: "Inflasi Tahunan (Y-on-Y)" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Persen")).toBeInTheDocument();
    expect(screen.getByText("Indonesia")).toBeInTheDocument();
    expect(screen.getByText(/Updated 1 Oct 2026/)).toBeInTheDocument();

    const notes = screen.getByRole("region", { name: "Notes" });
    expect(notes).toHaveTextContent("Catatan");
    expect(notes.querySelector("script, img")).toBeNull();
    expect(notes.innerHTML).not.toContain("onerror");
    expect(screen.getByRole("region", { name: "Definition" }).querySelector("b")).toHaveTextContent(
      "kenaikan",
    );

    await screen.findByTestId("echart");
    const option = chartOption();
    expect(option.series.map((s) => s.name)).toEqual(["INDONESIA"]);
    expect(option.series[0].data).toEqual([
      ["2024-01-01", 9.999],
      ["2024-02-01", 10.999],
    ]);

    const [request] = seriesUrls(urls);
    expect(request.pathname).toBe("/variables/0000/2263/series");
    expect(request.searchParams.getAll("vervar")).toEqual(["9999"]);
    expect(request.searchParams.getAll("turvar")).toEqual(["0"]);
    expect(request.searchParams.getAll("turth")).toEqual(["1", "2", "3"]);
    // Defaults aren't written to the URL.
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("adds a region: one chart series per selection, URL updated", async () => {
    const urls = stubApi();
    render();
    const regions = await screen.findByRole("group", { name: /Region/ });
    expect(within(regions).getByRole("checkbox", { name: "INDONESIA" })).toBeChecked();

    fireEvent.click(within(regions).getByRole("checkbox", { name: "PROV ACEH" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/explorer/0000/2263?vervar=9999,1100", {
      scroll: false,
    });
    await waitFor(() => expect(chartOption().series.map((s) => s.name)).toEqual(["INDONESIA", "PROV ACEH"]));
    expect(seriesUrls(urls).at(-1)!.searchParams.getAll("vervar")).toEqual(["9999", "1100"]);
  });

  it("filters a long region list", async () => {
    const base = variableDetail();
    const more = Array.from({ length: 10 }, (_, i) => ({
      val: 5100 + i,
      label: `PROV LAIN ${i}`,
      group_label: null,
    }));
    stubApi({
      detail: () => Response.json(variableDetail({ vervars: [...base.vervars, ...more] })),
    });
    render();
    const regions = await screen.findByRole("group", { name: /Region/ });
    fireEvent.change(within(regions).getByRole("searchbox", { name: "Filter regions" }), {
      target: { value: "aceh" },
    });
    expect(within(regions).getAllByRole("checkbox")).toHaveLength(1);
    expect(within(regions).getByRole("checkbox", { name: "PROV ACEH" })).toBeInTheDocument();
  });

  it("restores the selection from the URL and switches the period type", async () => {
    nav.search = "vervar=3100&freq=month";
    const urls = stubApi({
      detail: () =>
        Response.json(
          variableDetail({
            turths: [
              { val: 1, label: "Januari", freq: "month", has_data: true },
              { val: 13, label: "Tahunan", freq: "year", has_data: true },
            ],
          }),
        ),
    });
    render();
    await screen.findByTestId("echart");
    expect(chartOption().series.map((s) => s.name)).toEqual(["PROV DKI JAKARTA"]);

    const freq = screen.getByRole("combobox", { name: "Period type" });
    expect(freq).toHaveValue("month");
    fireEvent.change(freq, { target: { value: "year" } });
    expect(nav.replace).toHaveBeenLastCalledWith("/explorer/0000/2263?vervar=3100&freq=year", {
      scroll: false,
    });
    await waitFor(() =>
      expect(seriesUrls(urls).at(-1)!.searchParams.getAll("turth")).toEqual(["13"]),
    );
  });

  it("toggles to a table view and offers a CSV download", async () => {
    stubApi();
    const createObjectURL = vi.fn((blob: Blob) => {
      void blob;
      return "blob:csv";
    });
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    render();
    await screen.findByTestId("echart");

    fireEvent.click(screen.getByRole("radio", { name: "Table" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/explorer/0000/2263?view=table", {
      scroll: false,
    });
    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows[0]).toHaveTextContent("PeriodINDONESIA");
    expect(rows[1]).toHaveTextContent("2024-0211.00");
    expect(screen.queryByTestId("echart")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Download CSV/ }));
    expect(createObjectURL).toHaveBeenCalledOnce();
    const blob = createObjectURL.mock.calls[0][0];
    expect(await blob.text()).toContain("2024-01,2024-01-01,9999,INDONESIA,0,Tidak ada,9.999");
  });

  it("shows an empty state when the selection has no data", async () => {
    stubApi({ series: () => Response.json({ series: [], truncated: false, max_series: 20 }) });
    render();
    expect(await screen.findByText("No data for this selection.")).toBeInTheDocument();
  });

  it("shows not found for an unknown variable", async () => {
    stubApi({ detail: () => Response.json({ detail: "Variable not found" }, { status: 404 }) });
    render();
    expect(await screen.findByRole("heading", { name: "Variable not found" })).toBeInTheDocument();
  });

  it("shows an error state with a retry", async () => {
    let fail = true;
    stubApi({
      series: (url) =>
        fail ? Response.json({ detail: "boom" }, { status: 500 }) : Response.json(seriesFor(url)),
    });
    render();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load the series");
    fail = false;
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("echart")).toBeInTheDocument();
  });
});

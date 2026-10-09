import { describe, expect, it } from "vitest";

import type { Series } from "@/lib/api/client";
import {
  MAX_CHART_SERIES,
  SERIES_COLORS,
  buildChartOption,
  freqsOf,
  parseSelection,
  resolveSelection,
  selectionQueryString,
  seriesName,
  tableRows,
  toCsv,
  turthsFor,
} from "@/lib/explorer/series";
import { variableDetail as detail } from "@/test/fixtures";

const series = (vervar: number, label: string, values: [string, string | null, number][]): Series => ({
  vervar,
  vervar_label: label,
  turvar: 0,
  turvar_label: "Tidak ada",
  points: values.map(([period, date, value], i) => ({ period, date, th: 124, turth: i + 1, value })),
});

describe("URL selection", () => {
  it("round-trips through the query string, omitting defaults", () => {
    const sel = parseSelection(new URLSearchParams("vervar=9999,1100&turvar=0&freq=month&view=table"));
    expect(sel).toEqual({ vervars: [9999, 1100], turvars: [0], freq: "month", view: "table" });
    expect(selectionQueryString(sel)).toBe("?vervar=9999,1100&turvar=0&freq=month&view=table");
    expect(parseSelection(new URLSearchParams(""))).toEqual({
      vervars: null,
      turvars: null,
      freq: null,
      view: "chart",
    });
    expect(selectionQueryString(parseSelection(new URLSearchParams("")))).toBe("");
  });

  it("ignores junk", () => {
    expect(parseSelection(new URLSearchParams("vervar=x,,3&freq=weekly&view=pie"))).toEqual({
      vervars: [3],
      turvars: null,
      freq: null,
      view: "chart",
    });
  });
});

describe("resolveSelection", () => {
  it("defaults to the national member, the first category and the finest period type with data", () => {
    const d = detail();
    expect(freqsOf(d)).toEqual(["month"]);
    expect(resolveSelection(parseSelection(new URLSearchParams()), d)).toEqual({
      vervars: [9999],
      turvars: [0],
      freq: "month",
      view: "chart",
    });
  });

  it("falls back to the first member and keeps only known members", () => {
    const d = detail({ vervars: [{ val: 5, label: "A", group_label: null }, { val: 6, label: "B", group_label: null }] });
    expect(resolveSelection(parseSelection(new URLSearchParams("vervar=6,77")), d).vervars).toEqual([6]);
    expect(resolveSelection(parseSelection(new URLSearchParams("vervar=77")), d).vervars).toEqual([5]);
  });

  it("caps the number of charted series", () => {
    const vervars = Array.from({ length: 12 }, (_, i) => ({ val: i, label: `R${i}`, group_label: null }));
    const all = vervars.map((v) => v.val).join(",");
    const r = resolveSelection(parseSelection(new URLSearchParams(`vervar=${all}`)), detail({ vervars }));
    expect(r.vervars).toHaveLength(MAX_CHART_SERIES);
  });

  it("maps a period type to its turth values", () => {
    expect(turthsFor(detail(), "month")).toEqual([1, 2, 3]);
    expect(turthsFor(detail(), "year")).toEqual([13]);
    expect(turthsFor(detail(), null)).toBeUndefined();
  });
});

describe("chart option", () => {
  const a = series(9999, "INDONESIA", [["2024-01", "2024-01-01", 2.57], ["2024-02", "2024-02-01", 2.75]]);
  const b = series(1100, "PROV ACEH", [["2024-01", "2024-01-01", 1.5]]);

  it("draws one time-axis line per series in fixed colors", () => {
    const option = buildChartOption([a, b], { unit: "Persen", decimals: 2 });
    expect(option.xAxis).toMatchObject({ type: "time" });
    expect(option.yAxis).toMatchObject({ name: "Persen" });
    expect(option.series).toHaveLength(2);
    expect(option.series[0]).toMatchObject({
      type: "line",
      name: "INDONESIA",
      data: [["2024-01-01", 2.57], ["2024-02-01", 2.75]],
      itemStyle: { color: SERIES_COLORS[0] },
    });
    expect(option.series[1]).toMatchObject({ name: "PROV ACEH", itemStyle: { color: SERIES_COLORS[1] } });
    expect(option.legend).toMatchObject({ show: true });
  });

  it("hides the legend for one series and uses a category axis when dates are missing", () => {
    const odd = series(1, "X", [["2024 Januari_I", null, 1], ["2024 Januari_II", null, 2]]);
    const option = buildChartOption([odd], { unit: null, decimals: null });
    expect(option.legend).toMatchObject({ show: false });
    expect(option.xAxis).toMatchObject({ type: "category", data: ["2024 Januari_I", "2024 Januari_II"] });
    expect(option.series[0].data).toEqual([1, 2]);
  });

  it("names series by vervar, adding the category when several are charted", () => {
    expect(seriesName(a, false)).toBe("INDONESIA");
    expect(seriesName(a, true)).toBe("INDONESIA · Tidak ada");
    expect(seriesName({ ...a, vervar_label: null }, false)).toBe("9999");
  });
});

describe("table + CSV", () => {
  const a = series(9999, "INDONESIA", [["2024-01", "2024-01-01", 2.57], ["2024-02", "2024-02-01", 2.75]]);
  const b = series(1100, "PROV, ACEH \"x\"", [["2024-01", "2024-01-01", 1.5]]);

  it("pivots series into rows, latest period first", () => {
    expect(tableRows([a, b])).toEqual([
      { period: "2024-02", values: [2.75, null] },
      { period: "2024-01", values: [2.57, 1.5] },
    ]);
  });

  it("writes long-format CSV with RFC 4180 quoting", () => {
    expect(toCsv([a, b]).split("\r\n")).toEqual([
      "period,date,vervar,vervar_label,turvar,turvar_label,value",
      "2024-01,2024-01-01,9999,INDONESIA,0,Tidak ada,2.57",
      "2024-02,2024-02-01,9999,INDONESIA,0,Tidak ada,2.75",
      '2024-01,2024-01-01,1100,"PROV, ACEH ""x""",0,Tidak ada,1.5',
      "",
    ]);
  });
});

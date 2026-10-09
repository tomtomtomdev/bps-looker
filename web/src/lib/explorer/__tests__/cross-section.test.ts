import { describe, expect, it } from "vitest";

import type { CrossSectionRegion } from "@/lib/api/client";
import {
  RANK_COLOR,
  SEQUENTIAL,
  buildMapOption,
  buildRankingOption,
  rankedRegions,
  regionLabel,
} from "@/lib/explorer/cross-section";

const REGIONS: CrossSectionRegion[] = [
  { vervar: 1100, label: "PROV ACEH", value: 1.5 },
  { vervar: 3100, label: "PROV DKI JAKARTA", value: 3.25 },
  { vervar: 1101, label: "<b>Simeulue</b>", value: 9 },
  { vervar: 9200, label: "PROV PAPUA BARAT DAYA", value: null },
  { vervar: 5, label: "Lainnya", value: 100 },
];

describe("regionLabel", () => {
  it("strips HTML and the PROV prefix", () => {
    expect(regionLabel({ vervar: 1100, label: "<b>ACEH</b>" })).toBe("ACEH");
    expect(regionLabel({ vervar: 1100, label: "PROV ACEH" })).toBe("ACEH");
    expect(regionLabel({ vervar: 42, label: null })).toBe("42");
  });
});

describe("rankedRegions", () => {
  it("keeps regions of the level with a value, highest first", () => {
    expect(rankedRegions(REGIONS, "province").map((r) => r.vervar)).toEqual([3100, 1100]);
    expect(rankedRegions(REGIONS, "regency").map((r) => r.vervar)).toEqual([1101]);
    // No region level (category vervars): every member with a value.
    expect(rankedRegions(REGIONS, null).map((r) => r.vervar)).toEqual([5, 1101, 3100, 1100]);
  });
});

describe("buildRankingOption", () => {
  it("draws one bar per region, sorted descending from the top, with the national line", () => {
    const rows = rankedRegions(REGIONS, "province");
    const option = buildRankingOption(rows, { unit: "Persen", decimals: 2, national: 2.5 });
    expect(option.yAxis).toMatchObject({ type: "category", inverse: true, data: ["DKI JAKARTA", "ACEH"] });
    const [bars] = option.series;
    expect(bars.type).toBe("bar");
    expect(bars.data).toEqual([
      { value: 3.25, vervar: 3100, itemStyle: { color: RANK_COLOR } },
      { value: 1.5, vervar: 1100, itemStyle: { color: RANK_COLOR } },
    ]);
    expect(bars.markLine?.data).toEqual([{ xAxis: 2.5, name: "Indonesia" }]);
    expect(option.xAxis).toMatchObject({ type: "value" });
    const tooltip = option.tooltip as { valueFormatter: (v: number) => string };
    expect(tooltip.valueFormatter(3.25)).toBe("3.25 Persen");
  });

  it("omits the national line when there is none", () => {
    const option = buildRankingOption(rankedRegions(REGIONS, "province"), {
      unit: null,
      decimals: null,
      national: null,
    });
    expect(option.series[0].markLine).toBeUndefined();
  });
});

describe("buildMapOption", () => {
  const keys = new Map([
    [1100, "1100"],
    [3100, "3100"],
    [9200, "9200"],
  ]);

  it("colours regions by value with a continuous legend", () => {
    const option = buildMapOption(REGIONS, {
      map: "provinces-38",
      keys,
      level: "province",
      unit: "Persen",
      decimals: 2,
    });
    const [series] = option.series;
    expect(series).toMatchObject({ type: "map", map: "provinces-38", nameProperty: "code" });
    // Regions of the level, keyed by shape code; no value → null (drawn as "no data").
    expect(series.data).toEqual([
      { name: "1100", value: 1.5, vervar: 1100, label: "ACEH" },
      { name: "3100", value: 3.25, vervar: 3100, label: "DKI JAKARTA" },
      { name: "9200", value: null, vervar: 9200, label: "PAPUA BARAT DAYA" },
    ]);
    expect(option.visualMap).toMatchObject({
      type: "continuous",
      min: 1.5,
      max: 3.25,
      inRange: { color: SEQUENTIAL },
      calculable: false,
    });
    expect(option.visualMap.text).toEqual(["3.25 Persen", "1.50"]);
  });

  it("maps new regency codes to their shape, preferring a code with a value", () => {
    const regencyKeys = new Map([
      [9401, "9401"],
      [9501, "9401"],
      [9402, "9402"],
    ]);
    const option = buildMapOption(
      [
        { vervar: 9401, label: "Merauke", value: null },
        { vervar: 9501, label: "Merauke", value: 7 },
        { vervar: 9402, label: "Jayawijaya", value: 3 },
        { vervar: 9999, label: "Unmapped", value: 1 },
      ],
      { map: "regencies", keys: regencyKeys, level: "regency", unit: null, decimals: null },
    );
    expect(option.series[0].data).toEqual([
      { name: "9401", value: 7, vervar: 9501, label: "Merauke" },
      { name: "9402", value: 3, vervar: 9402, label: "Jayawijaya" },
    ]);
  });
});

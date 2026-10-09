import type { CrossSectionRegion } from "@/lib/api/client";
import { regionLevel, type GeoName } from "@/lib/geo/regions";
import { formatValue, type RegionLevel } from "@/lib/explorer/series";

/**
 * Map / ranking tab — pure helpers: region filtering and the ECharts options for the ranking bar
 * chart and the choropleth (kept free of React/ECharts so they're unit-tested directly).
 */

/** Ranking bars: the first categorical slot (one series, one hue). */
export const RANK_COLOR = "#2a78d6";
/** Choropleth: the validated sequential blue ramp, light → dark (palette steps 150…700; the
 * lightest step is skipped so low values stay distinct from the no-data grey). */
export const SEQUENTIAL = ["#b7d3f6", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
const NO_DATA = "#f0efec";
const BORDER = "#ffffff";
const MUTED = "#898781";
const INK = "#52514e";
const GRID = "#e1e0d9";
const AXIS = "#c3c2b7";

type Region = Pick<CrossSectionRegion, "vervar" | "label">;
export type Ranked = CrossSectionRegion & { value: number };

/** Display name: HTML stripped (`<b>ACEH</b>`), no `PROV ` prefix. */
export function regionLabel(r: Region): string {
  const text = (r.label ?? "").replace(/<[^>]*>/g, "").trim().replace(/^PROV\.?\s+/i, "");
  return text || String(r.vervar);
}

/** Regions of one level (all members when `level` is null) that have a value, highest first. */
export function rankedRegions(regions: CrossSectionRegion[], level: RegionLevel | null): Ranked[] {
  return regions
    .filter((r): r is Ranked => r.value !== null && r.value !== undefined)
    .filter((r) => level === null || regionLevel(r.vervar) === level)
    .sort((a, b) => b.value - a.value || a.vervar - b.vervar);
}

type BarItem = { value: number; vervar: number; itemStyle: { color: string } };

export type RankingOption = {
  animation: boolean;
  aria: { enabled: boolean };
  grid: Record<string, unknown>;
  tooltip: Record<string, unknown>;
  xAxis: Record<string, unknown>;
  yAxis: { type: "category"; inverse: boolean; data: string[] } & Record<string, unknown>;
  series: {
    type: "bar";
    data: BarItem[];
    barMaxWidth: number;
    itemStyle: Record<string, unknown>;
    markLine?: { data: { xAxis: number; name: string }[] } & Record<string, unknown>;
  }[];
};

/** Horizontal bars sorted descending (highest at the top), the national value as a line. */
export function buildRankingOption(
  rows: Ranked[],
  {
    unit,
    decimals,
    national,
  }: { unit: string | null | undefined; decimals: number | null | undefined; national: number | null },
): RankingOption {
  const format = (v: unknown) => formatValue(typeof v === "number" ? v : null, decimals ?? null);
  const withUnit = (v: unknown) => `${format(v)}${unit ? ` ${unit}` : ""}`;
  return {
    animation: false,
    aria: { enabled: false },
    grid: { left: 8, right: 24, top: 28, bottom: 8, containLabel: true },
    // The unit is in the tooltip (an axis name would be clipped at the chart edge).
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: withUnit },
    xAxis: {
      type: "value",
      position: "top",
      splitLine: { lineStyle: { color: GRID } },
      axisLabel: { color: MUTED },
    },
    yAxis: {
      type: "category",
      inverse: true,
      data: rows.map(regionLabel),
      axisLine: { lineStyle: { color: AXIS } },
      axisTick: { show: false },
      axisLabel: { color: INK, fontSize: 11 },
    },
    series: [
      {
        type: "bar",
        data: rows.map((r) => ({ value: r.value, vervar: r.vervar, itemStyle: { color: RANK_COLOR } })),
        barMaxWidth: 14,
        itemStyle: { borderRadius: [0, 4, 4, 0] },
        ...(national === null
          ? {}
          : {
              markLine: {
                symbol: "none",
                silent: true,
                lineStyle: { color: INK, type: "dashed", width: 1 },
                label: { formatter: `Indonesia ${format(national)}`, color: INK, position: "end" },
                data: [{ xAxis: national, name: "Indonesia" }],
              },
            }),
      },
    ],
  };
}

type MapItem = { name: string; value: number | null; vervar: number; label: string };

export type MapOption = {
  animation: boolean;
  aria: { enabled: boolean };
  tooltip: Record<string, unknown>;
  visualMap: {
    type: "continuous";
    min: number;
    max: number;
    text: [string, string];
    inRange: { color: string[] };
    calculable: boolean;
  } & Record<string, unknown>;
  series: ({ type: "map"; map: GeoName; nameProperty: string; data: MapItem[] } & Record<
    string,
    unknown
  >)[];
};

/** Choropleth of one level: data keyed by shape code (`keys`: vervar → feature code), a
 * continuous sequential legend; regions without a value are drawn in the no-data grey. */
export function buildMapOption(
  regions: CrossSectionRegion[],
  {
    map,
    keys,
    level,
    unit,
    decimals,
  }: {
    map: GeoName;
    keys: Map<number, string>;
    level: RegionLevel;
    unit: string | null | undefined;
    decimals: number | null | undefined;
  },
): MapOption {
  const byShape = new Map<string, MapItem>();
  for (const r of regions) {
    const name = keys.get(r.vervar);
    if (!name || regionLevel(r.vervar) !== level) continue;
    const value = r.value ?? null;
    const seen = byShape.get(name);
    // Old and new codes of one Papua regency share a shape: keep the one with a value.
    if (seen && (value === null || seen.value !== null)) continue;
    byShape.set(name, { name, value, vervar: r.vervar, label: regionLabel(r) });
  }
  const data = [...byShape.values()].sort((a, b) => a.name.localeCompare(b.name));
  const values = data.map((d) => d.value).filter((v): v is number => v !== null);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;
  const fmt = (v: number) => formatValue(v, decimals ?? null);
  return {
    animation: false,
    aria: { enabled: false },
    tooltip: {
      trigger: "item",
      transitionDuration: 0,
      formatter: (p: { data?: MapItem; name: string }) =>
        p.data
          ? `${p.data.label}: ${p.data.value === null ? "no data" : fmt(p.data.value)}${unit && p.data.value !== null ? ` ${unit}` : ""}`
          : `${p.name}: no data`,
    },
    visualMap: {
      type: "continuous",
      min,
      max: max === min ? min + 1 : max,
      text: [`${fmt(max)}${unit ? ` ${unit}` : ""}`, fmt(min)],
      inRange: { color: SEQUENTIAL },
      calculable: false,
      orient: "horizontal",
      left: 8,
      bottom: 8,
      itemHeight: 160,
      textStyle: { color: INK },
    },
    series: [
      {
        type: "map",
        map,
        nameProperty: "code",
        roam: true,
        scaleLimit: { min: 1, max: 12 },
        selectedMode: false,
        itemStyle: { areaColor: NO_DATA, borderColor: BORDER, borderWidth: level === "regency" ? 0.3 : 0.8 },
        emphasis: { label: { show: false }, itemStyle: { areaColor: "#eda100" } },
        data,
      },
    ],
  };
}

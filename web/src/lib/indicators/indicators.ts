import type { Indicator, IndicatorPoint } from "@/lib/api/client";
import { SERIES_COLORS, formatValue } from "@/lib/explorer/series";

/**
 * Indicators dashboard — pure helpers: URL state, change wording, Explorer link, history chart
 * option. Kept free of React/ECharts so they're unit-tested directly.
 */

export const NATIONAL_DOMAIN = "0000";

/** `?domain=` (omitted = national) and `?indicator=` (the tile whose history is open). */
export type DashboardState = { domain: string; indicator: number | null };

export function parseDashboardState(params: URLSearchParams): DashboardState {
  const domain = params.get("domain") ?? "";
  const indicator = Number(params.get("indicator") ?? "");
  return {
    domain: /^\d{4}$/.test(domain) ? domain : NATIONAL_DOMAIN,
    indicator: params.has("indicator") && Number.isInteger(indicator) && indicator >= 0 ? indicator : null,
  };
}

export function dashboardQueryString({ domain, indicator }: DashboardState): string {
  const parts: string[] = [];
  if (domain !== NATIONAL_DOMAIN) parts.push(`domain=${domain}`);
  if (indicator !== null) parts.push(`indicator=${indicator}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

export type Direction = "up" | "down" | "flat";

/** Sign of the change vs the previous periode (`null` = unknown). Whether "up" is good depends on
 * the indicator (inflation vs growth), so the UI only shows the direction, in neutral colours. */
export function changeDirection(item: Pick<Indicator, "change">): Direction | null {
  if (item.change === null) return null;
  return item.change > 0 ? "up" : item.change < 0 ? "down" : "flat";
}

const signed = (text: string, value: number) => (value > 0 ? `+${text}` : text.replace("-", "−"));

const pctFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

/** `+0.18 (+5.8%) vs Agustus 2026`, or why there is no change. */
export function describeChange(item: Indicator): string {
  const previous = item.previous;
  if (!previous) return "First recorded periode";
  if (item.change === null) return `${previous.periode}: no numeric value`;
  if (item.change === 0) return `No change vs ${previous.periode}`;
  const abs = signed(formatValue(item.change, null), item.change);
  const pct =
    item.change_pct === null ? "" : ` (${signed(pctFormat.format(item.change_pct), item.change_pct)}%)`;
  return `${abs}${pct} vs ${previous.periode}`;
}

/** Explorer page of the indicator's underlying variable, when it is crawled. */
export function explorerHref(item: Pick<Indicator, "variable">): string | null {
  const v = item.variable;
  return v ? `/explorer/${v.domain_id}/${v.var_id}` : null;
}

const MUTED = "#898781";
const GRID = "#e1e0d9";
const AXIS = "#c3c2b7";

export type HistoryOption = {
  color: string[];
  animation: boolean;
  aria: { enabled: boolean };
  grid: Record<string, number | boolean>;
  tooltip: { trigger: "axis"; valueFormatter: (v: unknown) => string };
  xAxis: { type: "category"; data: string[]; axisLine: object; axisLabel: object };
  yAxis: { type: "value"; name: string; nameTextStyle: object; scale: boolean; splitLine: object; axisLabel: object };
  series: {
    type: "line";
    name: string;
    data: (number | null)[];
    showSymbol: boolean;
    symbolSize: number;
    lineStyle: { width: number };
    connectNulls: boolean;
  }[];
};

/**
 * One line over the recorded periodes (category axis: periode is free text such as
 * `Triwulan II 2026`; the API already returns them oldest first).
 */
export function buildHistoryOption(
  points: IndicatorPoint[],
  { unit, name }: { unit: string | null | undefined; name: string },
): HistoryOption {
  const format = (v: unknown) => formatValue(typeof v === "number" ? v : null, null);
  return {
    color: [SERIES_COLORS[0]],
    animation: false,
    aria: { enabled: false },
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis", valueFormatter: format },
    xAxis: {
      type: "category",
      data: points.map((p) => p.periode),
      axisLine: { lineStyle: { color: AXIS } },
      axisLabel: { color: MUTED, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      name: unit ?? "",
      nameTextStyle: { color: MUTED, align: "left" },
      scale: true,
      splitLine: { lineStyle: { color: GRID } },
      axisLabel: { color: MUTED },
    },
    series: [
      {
        type: "line",
        name,
        data: points.map((p) => p.value),
        showSymbol: points.length <= 36,
        symbolSize: 8,
        lineStyle: { width: 2 },
        connectNulls: false,
      },
    ],
  };
}

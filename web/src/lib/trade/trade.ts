import type {
  TradeBreakdown,
  TradeBreakdownItem,
  TradeBy,
  TradeFlow,
  TradePeriods,
  TradeRange,
  TradeSeriesResponse,
  TradeTotal,
} from "@/lib/api/client";
import { SERIES_COLORS } from "@/lib/explorer/series";

/**
 * Trade dashboard — pure helpers: URL state, period ranges, number formatting and the ECharts
 * options (kept free of React/ECharts so they're unit-tested directly).
 *
 * Ranges follow the API (`from`/`to`, `YYYY` or `YYYY-MM`): two years = whole years (BPS annual
 * figures, else the sum of the year's months), otherwise months.
 */

export const DEFAULT_TOP = 10;
export const TOP_CHOICES = [5, 10, 20] as const;
export const FLOW_LABELS: Record<TradeFlow, string> = { export: "Exports", import: "Imports" };
export const FLOW_COLORS: Record<TradeFlow, string> = { export: SERIES_COLORS[0], import: SERIES_COLORS[1] };
const SURPLUS = SERIES_COLORS[2];
const DEFICIT = SERIES_COLORS[7];
const MUTED = "#898781";
const GRID = "#e1e0d9";
const AXIS = "#c3c2b7";

/** `?flow=&from=&to=&top=&hs2=&country=` — defaults (export, latest year, top 10) omitted. */
export type TradeState = {
  flow: TradeFlow;
  from: string | null;
  to: string | null;
  top: number;
  hs2: string | null;
  country: string | null;
};

const PERIOD = /^\d{4}(-(0[1-9]|1[0-2]))?$/;

export function parseTradeState(params: URLSearchParams): TradeState {
  const period = (name: string) => {
    const v = params.get(name);
    return v && PERIOD.test(v) ? v : null;
  };
  const top = Number(params.get("top"));
  const hs2 = params.get("hs2");
  const country = params.get("country")?.trim();
  return {
    flow: params.get("flow") === "import" ? "import" : "export",
    from: period("from"),
    to: period("to"),
    top: (TOP_CHOICES as readonly number[]).includes(top) ? top : DEFAULT_TOP,
    hs2: hs2 && /^\d{2}$/.test(hs2) ? hs2 : null,
    country: country || null,
  };
}

export function tradeQueryString(s: TradeState): string {
  const params = new URLSearchParams();
  if (s.flow !== "export") params.set("flow", s.flow);
  if (s.from) params.set("from", s.from);
  if (s.to) params.set("to", s.to);
  if (s.top !== DEFAULT_TOP) params.set("top", String(s.top));
  if (s.hs2) params.set("hs2", s.hs2);
  if (s.country) params.set("country", s.country);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

// --- ranges --------------------------------------------------------------------------------------

export type Range = { from: string; to: string };
export type Granularity = "year" | "month";

/** The range to show: the URL's (one end alone means both), else the latest year with data. */
export function effectiveRange(s: Pick<TradeState, "from" | "to">, periods: TradePeriods): Range | null {
  const from = s.from ?? s.to;
  const to = s.to ?? s.from;
  if (from && to) return { from, to };
  return periods.latest_year === null || periods.latest_year === undefined
    ? null
    : { from: String(periods.latest_year), to: String(periods.latest_year) };
}

export function granularityOf(r: Range): Granularity {
  return r.from.includes("-") || r.to.includes("-") ? "month" : "year";
}

export function yearChoices(periods: TradePeriods): number[] {
  return [...new Set(periods.items.map((i) => i.year))].sort((a, b) => a - b);
}

export function monthChoices(periods: TradePeriods): string[] {
  const months = new Set<string>();
  for (const item of periods.items) {
    for (const m of item.months) months.add(`${item.year}-${String(m).padStart(2, "0")}`);
  }
  return [...months].sort();
}

/** Same span at the other granularity, snapped to months that have data. */
export function switchGranularity(r: Range, to: Granularity, periods: TradePeriods): Range {
  const year = (p: string) => p.slice(0, 4);
  if (to === "year") return { from: year(r.from), to: year(r.to) };
  const lo = r.from.includes("-") ? r.from : `${r.from}-01`;
  const hi = r.to.includes("-") ? r.to : `${r.to}-12`;
  const months = monthChoices(periods);
  return {
    from: months.find((m) => m >= lo) ?? lo,
    to: [...months].reverse().find((m) => m <= hi) ?? hi,
  };
}

/** Move one end; the other follows when it would be crossed. */
export function setRangeEnd(r: Range, end: "from" | "to", value: string): Range {
  if (end === "from") return { from: value, to: r.to < value ? value : r.to };
  return { from: r.from > value ? value : r.from, to: value };
}

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function monthLabel(period: string): string {
  const [y, m] = period.split("-");
  return m ? `${MONTH_NAMES[Number(m) - 1]} ${y}` : y;
}

export function describeRange(r: TradeRange | null | undefined): string {
  if (!r) return "";
  if (r.start === r.end) return monthLabel(r.start);
  return r.granularity === "year" ? `${r.start}–${r.end}` : `${monthLabel(r.start)} – ${monthLabel(r.end)}`;
}

/** Partial years in the range: summed from fewer than 12 months of monthly figures, or BPS
 * annual figures that are year to date (monthly data covers fewer than 12 months). */
export function basisNote(total: TradeTotal | null | undefined): string | null {
  const notes = (total?.periods ?? []).flatMap((p) => {
    const months = p.months ?? 0;
    if (p.basis === "monthly" && months < 12) {
      return [`${p.year}: ${months} month${months === 1 ? "" : "s"} of monthly figures (no annual figures yet)`];
    }
    if (p.basis === "annual" && months > 0 && months < 12) {
      return [`${p.year}: year to date (monthly figures through ${MONTH_NAMES[months - 1]})`];
    }
    return [];
  });
  return notes.length ? notes.join("; ") : null;
}

// --- formatting ----------------------------------------------------------------------------------

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  const text = `US$${compact.format(Math.abs(value))}`;
  return value < 0 ? `−${text}` : text;
}

const precise = new Intl.NumberFormat("en-US", { notation: "compact", maximumSignificantDigits: 4 });

/** Headline figures: 4 significant digits (`US$1.002T`, `US$266.5B`). */
export function formatUsdPrecise(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  const text = `US$${precise.format(Math.abs(value))}`;
  return value < 0 ? `−${text}` : text;
}

export function formatTonnes(kg: number | null | undefined): string {
  if (kg === null || kg === undefined) return "–";
  return `${compact.format(kg / 1000)} t`;
}

const pct = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 });

export function formatShare(share: number | null | undefined): string {
  return share === null || share === undefined ? "–" : pct.format(share);
}

export function breakdownLabel(item: Pick<TradeBreakdownItem, "key" | "label">, by: TradeBy): string {
  if (by === "hs2") return item.label ? `${item.key} ${item.label}` : item.key;
  return item.label || "Not stated";
}

// --- chart options -------------------------------------------------------------------------------

const MAX_LABEL = 30;
const truncate = (text: string) => (text.length > MAX_LABEL ? `${text.slice(0, MAX_LABEL - 1)}…` : text);
const usdFormatter = (v: unknown) => formatUsd(typeof v === "number" ? v : null);

type BarDatum = { value: number | null; key: string; name: string; share: number | null };

export type BreakdownOption = {
  animation: boolean;
  aria: { enabled: boolean };
  grid: Record<string, unknown>;
  tooltip: Record<string, unknown>;
  xAxis: Record<string, unknown>;
  yAxis: { type: "category"; inverse: boolean; data: string[] } & Record<string, unknown>;
  series: {
    type: "bar";
    data: BarDatum[];
    barMaxWidth: number;
    itemStyle: { color: string };
    label: Record<string, unknown>;
  }[];
};

/** Horizontal bars, largest at the top; each datum keeps its key (click → filter). */
export function buildBreakdownOption(b: TradeBreakdown, color: string): BreakdownOption {
  const data = b.items.map((item) => ({
    value: item.value_usd ?? null,
    key: item.key,
    name: breakdownLabel(item, b.by),
    share: item.share ?? null,
  }));
  return {
    animation: false,
    aria: { enabled: false },
    // Values are printed at the bar ends (narrow cards: no room for an axis of ticks).
    grid: { left: 8, right: 56, top: 4, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "item",
      formatter: (p: { data: BarDatum }) =>
        `${p.data.name}<br/><b>${formatUsd(p.data.value)}</b> · ${formatShare(p.data.share)}`,
    },
    xAxis: { type: "value", show: false },
    yAxis: {
      type: "category",
      inverse: true,
      data: data.map((d) => truncate(d.name)),
      axisLine: { lineStyle: { color: AXIS } },
      axisTick: { show: false },
      axisLabel: { color: "#52514e" },
    },
    series: [
      {
        type: "bar",
        data,
        barMaxWidth: 18,
        itemStyle: { color },
        label: {
          show: true,
          position: "right",
          color: MUTED,
          formatter: (p: { value: number | null }) => formatUsd(p.value).replace("US$", ""),
        },
      },
    ],
  };
}

type TimeAxisOption = {
  animation: boolean;
  aria: { enabled: boolean };
  grid: Record<string, unknown>;
  tooltip: Record<string, unknown>;
  legend: Record<string, unknown>;
  xAxis: { type: "time" } & Record<string, unknown>;
  yAxis: Record<string, unknown>;
  series: {
    type: "line" | "bar";
    name: string;
    data: ([string, number | null] | { value: [string, number]; itemStyle: { color: string } })[];
    [k: string]: unknown;
  }[];
};

function timeAxisOption(series: TimeAxisOption["series"], legend: boolean): TimeAxisOption {
  return {
    animation: false,
    aria: { enabled: false },
    grid: { left: 8, right: 16, top: legend ? 40 : 24, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis", valueFormatter: usdFormatter },
    legend: { show: legend, top: 0, textStyle: { color: "#52514e" } },
    xAxis: { type: "time", axisLine: { lineStyle: { color: AXIS } }, axisLabel: { color: MUTED, hideOverlap: true } },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: GRID } },
      axisLabel: { color: MUTED, formatter: (v: number) => formatUsd(v).replace("US$", "") },
    },
    series,
  };
}

function line(name: string, color: string, data: [string, number | null][]) {
  return {
    type: "line" as const,
    name,
    data,
    itemStyle: { color },
    lineStyle: { width: 2 },
    showSymbol: data.length <= 24,
    symbolSize: 6,
    connectNulls: false,
  };
}

function flowPoints(resp: TradeSeriesResponse, flow: TradeFlow): [string, number | null][] {
  const s = resp.series.find((x) => x.flow === flow);
  return (s?.points ?? []).map((p) => [p.date, p.value_usd ?? null]);
}

/** The selected flow's monthly values. */
export function buildTrendOption(resp: TradeSeriesResponse, flow: TradeFlow): TimeAxisOption {
  return timeAxisOption([line(FLOW_LABELS[flow], FLOW_COLORS[flow], flowPoints(resp, flow))], false);
}

/** Monthly balance bars (exports − imports; surplus / deficit hues) with both flows as lines. */
export function buildBalanceOption(resp: TradeSeriesResponse): TimeAxisOption {
  const bars = {
    type: "bar" as const,
    name: "Balance",
    data: resp.balance.map((p) => ({
      value: [p.date, p.value_usd] as [string, number],
      itemStyle: { color: p.value_usd < 0 ? DEFICIT : SURPLUS },
    })),
    itemStyle: { color: SURPLUS },
    barMaxWidth: 14,
  };
  return timeAxisOption(
    [
      bars,
      line(FLOW_LABELS.export, FLOW_COLORS.export, flowPoints(resp, "export")),
      line(FLOW_LABELS.import, FLOW_COLORS.import, flowPoints(resp, "import")),
    ],
    true,
  );
}

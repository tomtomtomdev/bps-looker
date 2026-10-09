import type { Freq, Series, VariableDetail } from "@/lib/api/client";

/**
 * Explorer variable page — pure helpers: URL state, default selection, ECharts option, table
 * pivot and CSV. Kept free of React/ECharts so they're unit-tested directly.
 */

/** Most series charted at once: the categorical palette has 8 validated hues (never cycled). */
export const MAX_CHART_SERIES = 8;

/** Categorical slots in fixed order (dataviz reference palette, light surface; CVD-validated). */
export const SERIES_COLORS = [
  "#2a78d6",
  "#eb6834",
  "#1baf7a",
  "#eda100",
  "#e87ba4",
  "#008300",
  "#4a3aa7",
  "#e34948",
] as const;

export const FREQ_ORDER: Freq[] = ["month", "quarter", "semester", "year", "other"];
export const FREQ_LABELS: Record<Freq, string> = {
  month: "Monthly",
  quarter: "Quarterly",
  semester: "Semester",
  year: "Annual",
  other: "Other",
};

export type View = "chart" | "table";

/** What the URL says (`null` = not chosen: the default applies). */
export type Selection = {
  vervars: number[] | null;
  turvars: number[] | null;
  freq: Freq | null;
  view: View;
};

/** A selection with defaults filled in, valid for one variable. */
export type Resolved = { vervars: number[]; turvars: number[]; freq: Freq | null; view: View };

function parseInts(value: string | null): number[] | null {
  if (!value) return null;
  const ints = value
    .split(",")
    .filter((s) => /^\d+$/.test(s.trim()))
    .map(Number);
  return ints.length ? [...new Set(ints)] : null;
}

function isFreq(value: string | null): value is Freq {
  return FREQ_ORDER.includes(value as Freq);
}

/** `?vervar=9999,1100&turvar=0&freq=month&view=table` → selection. */
export function parseSelection(params: URLSearchParams): Selection {
  const freq = params.get("freq");
  return {
    vervars: parseInts(params.get("vervar")),
    turvars: parseInts(params.get("turvar")),
    freq: isFreq(freq) ? freq : null,
    view: params.get("view") === "table" ? "table" : "chart",
  };
}

/** The shareable query string of a selection (unchosen parts and the chart view omitted). */
export function selectionQueryString(sel: Selection): string {
  const parts: string[] = [];
  if (sel.vervars?.length) parts.push(`vervar=${sel.vervars.join(",")}`);
  if (sel.turvars?.length) parts.push(`turvar=${sel.turvars.join(",")}`);
  if (sel.freq) parts.push(`freq=${sel.freq}`);
  if (sel.view !== "chart") parts.push(`view=${sel.view}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** Period types that have data, finest first. */
export function freqsOf(detail: VariableDetail): Freq[] {
  const present = new Set(detail.turths.filter((t) => t.has_data).map((t) => t.freq));
  return FREQ_ORDER.filter((f) => present.has(f));
}

/** turth values of a period type (`undefined` = no filter). */
export function turthsFor(detail: VariableDetail, freq: Freq | null): number[] | undefined {
  if (!freq) return undefined;
  return detail.turths.filter((t) => t.freq === freq).map((t) => t.val);
}

function defaultVervar(detail: VariableDetail): number[] {
  const national = detail.vervars.find(
    (v) => v.val === 9999 || v.label.trim().toLowerCase() === "indonesia",
  );
  const first = national ?? detail.vervars[0];
  return first ? [first.val] : [];
}

/** Fill in defaults (the national member, the first category, the finest period type with
 * data), drop members the variable doesn't have, and cap to `MAX_CHART_SERIES` series. */
export function resolveSelection(sel: Selection, detail: VariableDetail): Resolved {
  const knownTurvars = new Set(detail.turvars.map((t) => t.val));
  let turvars = (sel.turvars ?? []).filter((t) => knownTurvars.has(t));
  if (!turvars.length) turvars = detail.turvars.slice(0, 1).map((t) => t.val);
  turvars = turvars.slice(0, MAX_CHART_SERIES);

  const knownVervars = new Set(detail.vervars.map((v) => v.val));
  let vervars = (sel.vervars ?? []).filter((v) => knownVervars.has(v));
  if (!vervars.length) vervars = defaultVervar(detail);
  vervars = vervars.slice(0, Math.max(1, Math.floor(MAX_CHART_SERIES / Math.max(1, turvars.length))));

  const freqs = freqsOf(detail);
  const freq = sel.freq && freqs.includes(sel.freq) ? sel.freq : (freqs[0] ?? null);
  return { vervars, turvars, freq, view: sel.view };
}

/** Legend/column name: the vervar label, plus the category when several are charted. */
export function seriesName(s: Series, withCategory: boolean): string {
  const name = s.vervar_label ?? String(s.vervar);
  return withCategory ? `${name} · ${s.turvar_label ?? s.turvar}` : name;
}

export function formatValue(value: number | null | undefined, decimals: number | null): string {
  if (value === null || value === undefined) return "–";
  const digits = decimals ?? undefined;
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits ?? 4,
  }).format(value);
}

function withCategory(series: Series[]): boolean {
  return new Set(series.map((s) => s.turvar)).size > 1;
}

/** Periods across all series, in time order (each series is already sorted by the API). */
function periodsOf(series: Series[]): string[] {
  const order = new Map<string, [string, number, number]>();
  for (const s of series) {
    for (const p of s.points) {
      if (!order.has(p.period)) order.set(p.period, [p.date ?? "", p.th, p.turth]);
    }
  }
  return [...order.entries()]
    .sort(([, a], [, b]) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] - b[1] || a[2] - b[2]))
    .map(([period]) => period);
}

type LineSeries = {
  type: "line";
  name: string;
  data: (number | null | [string, number])[];
  itemStyle: { color: string };
  lineStyle: { width: number };
  showSymbol: boolean;
  symbolSize: number;
  connectNulls: boolean;
};

export type ChartOption = {
  color: string[];
  animation: boolean;
  aria: { enabled: boolean };
  grid: Record<string, unknown>;
  tooltip: Record<string, unknown>;
  legend: { show: boolean } & Record<string, unknown>;
  xAxis: Record<string, unknown>;
  yAxis: Record<string, unknown>;
  dataZoom: Record<string, unknown>[];
  series: LineSeries[];
};

const MUTED = "#898781";
const GRID = "#e1e0d9";
const AXIS = "#c3c2b7";

/**
 * ECharts option: one line per series, colours by series position in fixed palette order.
 * A time axis when every point has a date; otherwise a category axis of period labels.
 */
export function buildChartOption(
  series: Series[],
  { unit, decimals }: { unit: string | null | undefined; decimals: number | null | undefined },
): ChartOption {
  const timed = series.every((s) => s.points.every((p) => p.date));
  const periods = timed ? [] : periodsOf(series);
  const named = withCategory(series);
  const pointCount = Math.max(0, ...series.map((s) => s.points.length));
  const format = (v: unknown) => formatValue(typeof v === "number" ? v : null, decimals ?? null);
  return {
    color: [...SERIES_COLORS],
    animation: false,
    aria: { enabled: false },
    grid: { left: 8, right: 16, top: 40, bottom: pointCount > 36 ? 56 : 24, containLabel: true },
    tooltip: { trigger: "axis", valueFormatter: format },
    legend: { show: series.length > 1, type: "scroll", top: 0, textStyle: { color: "#52514e" } },
    xAxis: timed
      ? {
          type: "time",
          axisLine: { lineStyle: { color: AXIS } },
          axisLabel: { color: MUTED, hideOverlap: true },
        }
      : {
          type: "category",
          data: periods,
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
    dataZoom: pointCount > 36 ? [{ type: "inside" }, { type: "slider", height: 20, bottom: 8 }] : [],
    series: series.map((s, i) => {
      const byPeriod = new Map(s.points.map((p) => [p.period, p.value]));
      return {
        type: "line",
        name: seriesName(s, named),
        data: timed
          ? s.points.map((p) => [p.date as string, p.value] as [string, number])
          : periods.map((period) => byPeriod.get(period) ?? null),
        itemStyle: { color: SERIES_COLORS[i % SERIES_COLORS.length] },
        lineStyle: { width: 2 },
        showSymbol: pointCount <= 36,
        symbolSize: 8,
        connectNulls: false,
      };
    }),
  };
}

export type TableRow = { period: string; values: (number | null)[] };

/** One row per period (latest first), one value column per series. */
export function tableRows(series: Series[]): TableRow[] {
  const lookups = series.map((s) => new Map(s.points.map((p) => [p.period, p.value])));
  return periodsOf(series)
    .reverse()
    .map((period) => ({ period, values: lookups.map((m) => m.get(period) ?? null) }));
}

function csvField(value: string | number | null): string {
  const text = value === null ? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/** Long-format CSV (one row per point), RFC 4180 quoting, CRLF line ends. */
export function toCsv(series: Series[]): string {
  const header = ["period", "date", "vervar", "vervar_label", "turvar", "turvar_label", "value"];
  const lines = [header.join(",")];
  for (const s of series) {
    for (const p of s.points) {
      lines.push(
        [p.period, p.date, s.vervar, s.vervar_label, s.turvar, s.turvar_label, p.value]
          .map(csvField)
          .join(","),
      );
    }
  }
  return `${lines.join("\r\n")}\r\n`;
}

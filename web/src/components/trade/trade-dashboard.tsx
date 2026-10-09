"use client";

import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { EChart, type ChartClick } from "@/components/explorer/echart";
import { Button } from "@/components/ui/button";
import {
  api,
  type TradeBreakdown,
  type TradeBy,
  type TradeFlow,
  type TradePeriods,
  type TradeSeriesResponse,
  type TradeSummary,
} from "@/lib/api/client";
import {
  FLOW_COLORS,
  FLOW_LABELS,
  TOP_CHOICES,
  basisNote,
  buildBalanceOption,
  buildBreakdownOption,
  buildTrendOption,
  describeRange,
  effectiveRange,
  formatShare,
  formatTonnes,
  formatUsd,
  formatUsdPrecise,
  granularityOf,
  monthChoices,
  monthLabel,
  parseTradeState,
  setRangeEnd,
  switchGranularity,
  tradeQueryString,
  yearChoices,
  type Granularity,
  type Range,
  type TradeState,
} from "@/lib/trade/trade";

async function fetchPeriods(): Promise<TradePeriods> {
  const { data, error } = await api.GET("/trade/periods");
  if (!data) throw new Error(`GET /trade/periods failed: ${JSON.stringify(error)}`);
  return data;
}

async function fetchSummary({ from, to }: Range): Promise<TradeSummary> {
  const { data, error } = await api.GET("/trade/summary", { params: { query: { from, to } } });
  if (!data) throw new Error(`GET /trade/summary failed: ${JSON.stringify(error)}`);
  return data;
}

async function fetchBreakdown(by: TradeBy, flow: TradeFlow, { from, to }: Range, top: number): Promise<TradeBreakdown> {
  const { data, error } = await api.GET("/trade/breakdown", { params: { query: { by, flow, from, to, top } } });
  if (!data) throw new Error(`GET /trade/breakdown failed: ${JSON.stringify(error)}`);
  return data;
}

async function fetchSeries(
  { from, to }: Range,
  hs2: string | null,
  country: string | null,
): Promise<TradeSeriesResponse> {
  const query = { from, to, ...(hs2 ? { hs2 } : {}), ...(country ? { country } : {}) };
  const { data, error } = await api.GET("/trade/series", { params: { query } });
  if (!data) throw new Error(`GET /trade/series failed: ${JSON.stringify(error)}`);
  return data;
}

function ErrorBox({ message, retry }: { message: string; retry: () => unknown }) {
  return (
    <div role="alert" className="flex items-center gap-3 rounded-md border p-4 text-sm">
      <span>{message}</span>
      <Button variant="outline" size="sm" onClick={() => void retry()}>
        Retry
      </Button>
    </div>
  );
}

const SELECT =
  "h-9 rounded-md border border-input bg-transparent px-2 text-sm text-foreground disabled:opacity-50";

/**
 * Trade dashboard: export/import totals and balance (`GET /trade/summary`), top-N countries,
 * ports and HS chapters (`GET /trade/breakdown`), monthly trend and trade balance
 * (`GET /trade/series`) over a year or month range. Clicking a chapter or country filters the
 * monthly charts. State lives in the URL (`?flow=&from=&to=&top=&hs2=&country=`).
 */
export function TradeDashboard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [state, setState] = useState<TradeState>(() => parseTradeState(searchParams));

  const update = (patch: Partial<TradeState>) => {
    const next = { ...state, ...patch };
    setState(next);
    router.replace(`/trade${tradeQueryString(next)}`, { scroll: false });
  };

  const periods = useQuery({ queryKey: ["trade-periods"], queryFn: fetchPeriods, staleTime: Infinity });

  if (periods.isPending) {
    return (
      <p className="text-sm text-muted-foreground" role="status">
        Loading trade data…
      </p>
    );
  }
  if (periods.isError) {
    return <ErrorBox message="Could not load the trade data — is the API running?" retry={periods.refetch} />;
  }
  const range = effectiveRange(state, periods.data);
  if (!range) {
    return (
      <p className="text-sm text-muted-foreground">
        No trade data yet — run <code>bps seed trade</code> and the worker.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <Filters
        state={state}
        range={range}
        periods={periods.data}
        onChange={(patch) => update(patch)}
      />
      <SummaryTiles range={range} flow={state.flow} />
      <div className="grid gap-4 lg:grid-cols-3">
        <BreakdownCard
          by="country"
          title="Top countries"
          flow={state.flow}
          range={range}
          top={state.top}
          onPick={(key) => update({ country: key })}
        />
        <BreakdownCard by="port" title="Top ports" flow={state.flow} range={range} top={state.top} />
        <BreakdownCard
          by="hs2"
          title="Top HS chapters"
          flow={state.flow}
          range={range}
          top={state.top}
          onPick={(key) => update({ hs2: key })}
        />
      </div>
      <MonthlyCharts
        range={range}
        flow={state.flow}
        hs2={state.hs2}
        country={state.country}
        onClear={(patch) => update(patch)}
      />
    </div>
  );
}

function Filters({
  state,
  range,
  periods,
  onChange,
}: {
  state: TradeState;
  range: Range;
  periods: TradePeriods;
  onChange: (patch: Partial<TradeState>) => void;
}) {
  const granularity = granularityOf(range);
  const choices =
    granularity === "year" ? yearChoices(periods).map(String) : monthChoices(periods);
  // A URL value without data still shows as selected.
  const options = [...new Set([...choices, range.from, range.to])].sort();
  const label = (v: string) => (granularity === "year" ? v : monthLabel(v));
  const setRange = (r: Range) => onChange({ from: r.from, to: r.to });

  return (
    <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
      <div role="group" aria-label="Flow" className="inline-flex rounded-md border p-0.5">
        {(["export", "import"] as const).map((flow) => (
          <button
            key={flow}
            type="button"
            aria-pressed={state.flow === flow}
            onClick={() => onChange({ flow })}
            className="rounded px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground aria-pressed:bg-accent aria-pressed:font-medium aria-pressed:text-foreground"
          >
            {FLOW_LABELS[flow]}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        Period
        <select
          aria-label="Period"
          value={granularity}
          onChange={(e) => setRange(switchGranularity(range, e.target.value as Granularity, periods))}
          className={SELECT}
        >
          <option value="year">Years</option>
          <option value="month">Months</option>
        </select>
      </label>
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        From
        <select
          aria-label="From"
          value={range.from}
          onChange={(e) => setRange(setRangeEnd(range, "from", e.target.value))}
          className={SELECT}
        >
          {options.map((v) => (
            <option key={v} value={v}>
              {label(v)}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        To
        <select
          aria-label="To"
          value={range.to}
          onChange={(e) => setRange(setRangeEnd(range, "to", e.target.value))}
          className={SELECT}
        >
          {options.map((v) => (
            <option key={v} value={v}>
              {label(v)}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        Top
        <select
          aria-label="Top"
          value={state.top}
          onChange={(e) => onChange({ top: Number(e.target.value) })}
          className={SELECT}
        >
          {TOP_CHOICES.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function Tile({
  label,
  value,
  detail,
  highlight,
}: {
  label: string;
  value: string;
  detail: string;
  highlight?: boolean;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      data-highlight={highlight || undefined}
      className="flex flex-col gap-1 rounded-lg border bg-card p-4 data-highlight:border-[#2a78d6] data-highlight:ring-1 data-highlight:ring-[#2a78d6]"
    >
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-2xl font-semibold tabular-nums">{value}</span>
      <span className="text-xs text-muted-foreground tabular-nums">{detail}</span>
    </div>
  );
}

function SummaryTiles({ range, flow }: { range: Range; flow: TradeFlow }) {
  const summary = useQuery({
    queryKey: ["trade-summary", range.from, range.to],
    queryFn: () => fetchSummary(range),
  });
  if (summary.isPending) {
    return (
      <p className="text-sm text-muted-foreground" role="status">
        Loading totals…
      </p>
    );
  }
  if (summary.isError) return <ErrorBox message="Could not load the totals." retry={summary.refetch} />;
  const totals = Object.fromEntries(summary.data.items.map((t) => [t.flow, t]));
  const balance = summary.data.balance_usd ?? null;
  // Partial-year notes only make sense for whole-year ranges (month ranges are explicit).
  const note = summary.data.range?.granularity === "year" ? basisNote(totals[flow]) : null;
  return (
    <section aria-labelledby="trade-totals-heading" className="space-y-3">
      <h2 id="trade-totals-heading" className="text-lg font-medium">
        {describeRange(summary.data.range)}
      </h2>
      <div className="grid gap-3 sm:grid-cols-3">
        {(["export", "import"] as const).map((f) => (
          <Tile
            key={f}
            label={`${FLOW_LABELS[f]} total`}
            value={formatUsdPrecise(totals[f]?.value_usd)}
            detail={`Net weight ${formatTonnes(totals[f]?.netweight_kg)}`}
            highlight={f === flow}
          />
        ))}
        <Tile
          label="Trade balance"
          value={formatUsdPrecise(balance)}
          detail={balance === null ? "Exports − imports" : balance >= 0 ? "Surplus (exports − imports)" : "Deficit (exports − imports)"}
        />
      </div>
      {note ? <p className="text-xs text-muted-foreground">{note}</p> : null}
    </section>
  );
}

function BreakdownCard({
  by,
  title,
  flow,
  range,
  top,
  onPick,
}: {
  by: TradeBy;
  title: string;
  flow: TradeFlow;
  range: Range;
  top: number;
  onPick?: (key: string) => void;
}) {
  const breakdown = useQuery({
    queryKey: ["trade-breakdown", by, flow, range.from, range.to, top],
    queryFn: () => fetchBreakdown(by, flow, range, top),
  });
  const headingId = `trade-${by}-heading`;
  return (
    <section aria-labelledby={headingId} className="space-y-2 rounded-lg border p-4">
      <h2 id={headingId} className="font-medium">
        {title}
      </h2>
      {breakdown.isPending ? (
        <p className="text-sm text-muted-foreground" role="status">
          Loading…
        </p>
      ) : breakdown.isError ? (
        <ErrorBox message="Could not load this breakdown." retry={breakdown.refetch} />
      ) : breakdown.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No data for this range.</p>
      ) : (
        <BreakdownChart
          breakdown={breakdown.data}
          label={`${title} by ${flow} value`}
          onPick={onPick}
        />
      )}
    </section>
  );
}

function BreakdownChart({
  breakdown,
  label,
  onPick,
}: {
  breakdown: TradeBreakdown;
  label: string;
  onPick?: (key: string) => void;
}) {
  const option = useMemo(
    () => buildBreakdownOption(breakdown, FLOW_COLORS[breakdown.flow]),
    [breakdown],
  );
  const onClick = onPick
    ? (p: ChartClick) => {
        const key = (p.data as { key?: string } | undefined)?.key;
        if (key) onPick(key);
      }
    : undefined;
  const others = breakdown.others;
  return (
    <div className="space-y-1">
      <EChart
        option={option}
        label={label}
        onClick={onClick}
        className="w-full"
        style={{ height: 40 + breakdown.items.length * 28 }}
      />
      <p className="text-xs text-muted-foreground tabular-nums">
        {others ? `Others (${others.count}): ${formatUsd(others.value_usd)} · ${formatShare(others.share)} · ` : ""}
        Total {formatUsd(breakdown.total.value_usd)}
        {onClick ? " · click a bar to filter the monthly charts" : ""}
      </p>
    </div>
  );
}

function MonthlyCharts({
  range,
  flow,
  hs2,
  country,
  onClear,
}: {
  range: Range;
  flow: TradeFlow;
  hs2: string | null;
  country: string | null;
  onClear: (patch: Partial<TradeState>) => void;
}) {
  const series = useQuery({
    queryKey: ["trade-series", range.from, range.to, hs2, country],
    queryFn: () => fetchSeries(range, hs2, country),
  });
  const chapter = hs2 ? `${hs2}${series.data?.hs2_label ? ` ${series.data.hs2_label}` : ""}` : null;
  const suffix = [chapter, country].filter(Boolean).map((s) => ` · ${s}`).join("");

  return (
    <section aria-labelledby="trade-monthly-heading" className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="trade-monthly-heading" className="text-lg font-medium">
          Monthly
        </h2>
        {chapter ? (
          <FilterChip label={`Chapter ${chapter}`} clearLabel="Clear chapter filter" onClear={() => onClear({ hs2: null })} />
        ) : null}
        {country ? (
          <FilterChip label={`Country ${country}`} clearLabel="Clear country filter" onClear={() => onClear({ country: null })} />
        ) : null}
      </div>
      {series.isPending ? (
        <p className="text-sm text-muted-foreground" role="status">
          Loading monthly figures…
        </p>
      ) : series.isError ? (
        <ErrorBox message="Could not load the monthly figures." retry={series.refetch} />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <TrendCard data={series.data} flow={flow} label={`Monthly ${flow}s${suffix}`} />
          <BalanceCard data={series.data} label={`Trade balance by month${suffix}`} />
        </div>
      )}
    </section>
  );
}

function FilterChip({ label, clearLabel, onClear }: { label: string; clearLabel: string; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border bg-accent/60 py-0.5 pr-1 pl-3 text-sm">
      {label}
      <button
        type="button"
        aria-label={clearLabel}
        onClick={onClear}
        className="rounded-full p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        <X aria-hidden className="size-3.5" />
      </button>
    </span>
  );
}

const EMPTY_MONTHLY = "No monthly figures for this range.";

function TrendCard({ data, flow, label }: { data: TradeSeriesResponse; flow: TradeFlow; label: string }) {
  const option = useMemo(() => buildTrendOption(data, flow), [data, flow]);
  const empty = !data.series.find((s) => s.flow === flow)?.points.length;
  return (
    <div className="space-y-2 rounded-lg border p-4">
      <h3 className="font-medium">{FLOW_LABELS[flow]} per month</h3>
      {empty ? (
        <p className="text-sm text-muted-foreground">{EMPTY_MONTHLY}</p>
      ) : (
        <EChart option={option} label={label} className="h-64 w-full" />
      )}
    </div>
  );
}

function BalanceCard({ data, label }: { data: TradeSeriesResponse; label: string }) {
  const option = useMemo(() => buildBalanceOption(data), [data]);
  return (
    <div className="space-y-2 rounded-lg border p-4">
      <h3 className="font-medium">Trade balance (exports − imports)</h3>
      {data.balance.length === 0 ? (
        <p className="text-sm text-muted-foreground">{EMPTY_MONTHLY}</p>
      ) : (
        <EChart option={option} label={label} className="h-64 w-full" />
      )}
    </div>
  );
}

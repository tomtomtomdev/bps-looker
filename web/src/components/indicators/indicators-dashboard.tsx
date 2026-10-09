"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowRight, ArrowUp, Minus } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { EChart } from "@/components/explorer/echart";
import { Button } from "@/components/ui/button";
import { api, type Domain, type Indicator, type IndicatorHistory, type IndicatorList } from "@/lib/api/client";
import { formatValue } from "@/lib/explorer/series";
import {
  NATIONAL_DOMAIN,
  buildHistoryOption,
  changeDirection,
  dashboardQueryString,
  describeChange,
  explorerHref,
  parseDashboardState,
  type DashboardState,
  type Direction,
} from "@/lib/indicators/indicators";

async function fetchIndicators(domain: string): Promise<IndicatorList> {
  const { data, error } = await api.GET("/indicators", { params: { query: { domain } } });
  if (!data) throw new Error(`GET /indicators failed: ${JSON.stringify(error)}`);
  return data;
}

async function fetchProvinces(): Promise<Domain[]> {
  const { data, error } = await api.GET("/domains", { params: { query: { level: "prov" } } });
  if (!data) throw new Error(`GET /domains failed: ${JSON.stringify(error)}`);
  return data;
}

async function fetchHistory(domain: string, id: number): Promise<IndicatorHistory> {
  const { data, error } = await api.GET("/indicators/{domain}/{indicator_id}/history", {
    params: { path: { domain, indicator_id: id } },
  });
  if (!data) throw new Error(`GET history failed: ${JSON.stringify(error)}`);
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

/**
 * Indicators dashboard: BPS strategic indicators of the national domain or a province
 * (`GET /indicators?domain=`) as KPI tiles with the change vs the previous periode; a tile opens
 * its history (`GET /indicators/{domain}/{id}/history`) and a link to its variable in the
 * Explorer. State lives in the URL (`?domain=&indicator=`).
 */
export function IndicatorsDashboard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [state, setState] = useState<DashboardState>(() => parseDashboardState(searchParams));

  const update = (patch: Partial<DashboardState>) => {
    const next = { ...state, ...patch };
    setState(next);
    router.replace(`/indicators${dashboardQueryString(next)}`, { scroll: false });
  };

  const provinces = useQuery({ queryKey: ["domains", "prov"], queryFn: fetchProvinces, staleTime: Infinity });
  const list = useQuery({
    queryKey: ["indicators", state.domain],
    queryFn: () => fetchIndicators(state.domain),
  });
  const open = list.data?.items.find((i) => i.indicator_id === state.indicator) ?? null;

  return (
    <div className="space-y-6">
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        Region
        <select
          aria-label="Region"
          value={state.domain}
          onChange={(e) => update({ domain: e.target.value, indicator: null })}
          className="h-9 max-w-72 rounded-md border border-input bg-transparent px-2 text-sm text-foreground"
        >
          <option value={NATIONAL_DOMAIN}>Indonesia (national)</option>
          {(provinces.data ?? []).map((d) => (
            <option key={d.domain_id} value={d.domain_id}>
              {d.name}
            </option>
          ))}
        </select>
      </label>

      {list.isPending ? (
        <p className="text-sm text-muted-foreground" role="status">
          Loading indicators…
        </p>
      ) : list.isError ? (
        <ErrorBox message="Could not load the indicators — is the API running?" retry={list.refetch} />
      ) : (
        <section aria-labelledby="indicators-heading" className="space-y-4" aria-busy={list.isFetching}>
          <h2 id="indicators-heading" className="text-lg font-medium">
            {list.data.domain.level === "pusat" ? "Indonesia (national)" : list.data.domain.name}{" "}
            <span className="text-sm font-normal text-muted-foreground">
              · {list.data.items.length} indicator{list.data.items.length === 1 ? "" : "s"}
            </span>
          </h2>
          {open ? <HistoryPanel item={open} /> : null}
          {list.data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No indicators recorded for {list.data.domain.name} yet — run{" "}
              <code>bps seed indicators</code> and the worker.
            </p>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {list.data.items.map((item) => (
                <li key={item.indicator_id}>
                  <IndicatorTile
                    item={item}
                    pressed={item.indicator_id === state.indicator}
                    onClick={() =>
                      update({ indicator: item.indicator_id === state.indicator ? null : item.indicator_id })
                    }
                  />
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

const DIRECTION_ICON: Record<Direction, typeof ArrowUp> = { up: ArrowUp, down: ArrowDown, flat: Minus };
const DIRECTION_TEXT: Record<Direction, string> = { up: "Up", down: "Down", flat: "Unchanged" };

function IndicatorTile({ item, pressed, onClick }: { item: Indicator; pressed: boolean; onClick: () => void }) {
  const direction = changeDirection(item);
  const Icon = direction ? DIRECTION_ICON[direction] : null;
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      title={item.title}
      className="flex h-full w-full flex-col gap-2 rounded-lg border bg-card p-4 text-left transition-colors hover:bg-accent/60 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none aria-pressed:border-[#2a78d6] aria-pressed:ring-1 aria-pressed:ring-[#2a78d6]"
    >
      <span className="line-clamp-2 text-sm font-medium">{item.label}</span>
      <span className="flex flex-wrap items-baseline gap-x-1.5">
        <span className="text-2xl font-semibold tabular-nums">{formatValue(item.value, null)}</span>
        {item.unit && item.unit !== "Tidak Ada Satuan" ? (
          <span className="text-sm text-muted-foreground">{item.unit}</span>
        ) : null}
      </span>
      <span className="text-xs text-muted-foreground">{item.periode}</span>
      {/* Neutral colours: whether "up" is good depends on the indicator (inflation vs growth). */}
      <span
        data-testid="change"
        data-direction={direction ?? undefined}
        className="mt-auto flex items-center gap-1 text-xs text-muted-foreground tabular-nums"
      >
        {Icon && direction ? (
          <>
            <Icon aria-hidden className="size-3.5 text-foreground" />
            <span className="sr-only">{DIRECTION_TEXT[direction]}:</span>
          </>
        ) : null}
        {describeChange(item)}
      </span>
    </button>
  );
}

function HistoryPanel({ item }: { item: Indicator }) {
  const history = useQuery({
    queryKey: ["indicator-history", item.domain_id, item.indicator_id],
    queryFn: () => fetchHistory(item.domain_id, item.indicator_id),
  });
  const headingId = `indicator-${item.indicator_id}-heading`;
  const ref = useRef<HTMLElement>(null);
  // The panel sits above the tiles: bring it into view when a (lower) tile opens it.
  useEffect(() => {
    ref.current?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }, [item.domain_id, item.indicator_id]);
  const href = explorerHref(item);

  return (
    <section ref={ref} aria-labelledby={headingId} className="space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id={headingId} className="text-lg font-medium">
            {item.label}
          </h2>
          <p className="text-sm text-muted-foreground">
            {item.title}
            {item.data_source ? ` · Source: ${item.data_source}` : ""}
          </p>
        </div>
        {href ? (
          <Button asChild variant="outline" size="sm">
            <Link href={href}>
              Open in Explorer <ArrowRight aria-hidden />
            </Link>
          </Button>
        ) : item.var !== null ? (
          <p className="text-sm text-muted-foreground">Variable {item.var} is not crawled yet.</p>
        ) : null}
      </div>
      {item.name && item.name !== item.title ? <p className="text-sm whitespace-pre-line">{item.name}</p> : null}
      {history.isPending ? (
        <p className="text-sm text-muted-foreground" role="status">
          Loading history…
        </p>
      ) : history.isError ? (
        <ErrorBox message="Could not load the history." retry={history.refetch} />
      ) : (
        <HistoryChart history={history.data} />
      )}
    </section>
  );
}

function HistoryChart({ history }: { history: IndicatorHistory }) {
  const option = useMemo(
    () => buildHistoryOption(history.points, { unit: history.unit, name: history.label }),
    [history],
  );
  return (
    <div className="space-y-1">
      <EChart option={option} label={`${history.label} history`} className="h-64 w-full" />
      <p className="text-xs text-muted-foreground">
        {history.points.length} periode{history.points.length === 1 ? "" : "s"} recorded by the crawler
        {history.points.length === 1 ? " — the history grows with each new BPS release." : "."}
      </p>
    </div>
  );
}

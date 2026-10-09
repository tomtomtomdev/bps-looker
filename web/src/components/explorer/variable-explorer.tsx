"use client";

import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { EChart } from "@/components/explorer/echart";
import { MapRanking } from "@/components/explorer/map-ranking";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, type Series, type SeriesResponse, type VariableDetail } from "@/lib/api/client";
import {
  FREQ_LABELS,
  MAX_CHART_SERIES,
  buildChartOption,
  formatValue,
  freqsOf,
  parseSelection,
  resolveSelection,
  selectionQueryString,
  seriesName,
  tableRows,
  toCsv,
  turthsFor,
  type Selection,
  type Tab,
  type View,
} from "@/lib/explorer/series";
import { sanitizeHtml } from "@/lib/sanitize";

class NotFoundError extends Error {}

async function fetchVariable(domain: string, varId: number): Promise<VariableDetail> {
  const { data, error, response } = await api.GET("/variables/{domain}/{var}", {
    params: { path: { domain, var: varId } },
  });
  if (response.status === 404) throw new NotFoundError("Variable not found");
  if (!data) throw new Error(`GET /variables/${domain}/${varId} failed: ${JSON.stringify(error)}`);
  return data;
}

type SeriesRequest = { vervars: number[]; turvars: number[]; turths: number[] | undefined };

async function fetchSeries(
  domain: string,
  varId: number,
  { vervars, turvars, turths }: SeriesRequest,
): Promise<SeriesResponse> {
  const { data, error } = await api.GET("/variables/{domain}/{var}/series", {
    params: {
      path: { domain, var: varId },
      query: { vervar: vervars, turvar: turvars, turth: turths },
    },
  });
  if (!data) throw new Error(`GET series failed: ${JSON.stringify(error)}`);
  return data;
}

const updatedFormat = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" });

function downloadCsv(series: Series[], filename: string) {
  const blob = new Blob([`﻿${toCsv(series)}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * Explorer variable page: metadata, dimension pickers and a time-series chart/table of
 * `GET /variables/{domain}/{var}/series`. The selection lives in the URL
 * (`?vervar=&turvar=&freq=&view=`); unchosen parts use defaults and aren't written.
 */
export function VariableExplorer({ domain, varId }: { domain: string; varId: number }) {
  const detail = useQuery({
    queryKey: ["variable", domain, varId],
    queryFn: () => fetchVariable(domain, varId),
    retry: (count, error) => !(error instanceof NotFoundError) && count < 3,
  });

  if (detail.isPending) {
    return (
      <p className="text-sm text-muted-foreground" role="status">
        Loading variable…
      </p>
    );
  }
  if (detail.isError) {
    if (detail.error instanceof NotFoundError) {
      return (
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">Variable not found</h1>
          <p className="text-muted-foreground">
            No variable {varId} in domain {domain} — it may not be crawled yet.
          </p>
        </div>
      );
    }
    return (
      <ErrorBox message="Could not load the variable — is the API running?" retry={detail.refetch} />
    );
  }
  return <VariableView detail={detail.data} />;
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

function VariableView({ detail }: { detail: VariableDetail }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [selection, setSelection] = useState<Selection>(() => parseSelection(searchParams));
  const resolved = resolveSelection(selection, detail);
  const path = `/explorer/${detail.domain_id}/${detail.var_id}`;

  const update = (patch: Partial<Selection>) => {
    const next = { ...selection, ...patch };
    setSelection(next);
    router.replace(`${path}${selectionQueryString(next)}`, { scroll: false });
  };

  const request: SeriesRequest = {
    vervars: resolved.vervars,
    turvars: resolved.turvars,
    turths: turthsFor(detail, resolved.freq),
  };
  const series = useQuery({
    queryKey: ["series", detail.domain_id, detail.var_id, request],
    queryFn: () => fetchSeries(detail.domain_id, detail.var_id, request),
    placeholderData: keepPreviousData,
    enabled: resolved.tab === "series",
  });

  const freqs = freqsOf(detail);
  const vervarMax = Math.max(1, Math.floor(MAX_CHART_SERIES / resolved.turvars.length));
  const turvarMax = Math.max(1, Math.floor(MAX_CHART_SERIES / resolved.vervars.length));

  return (
    <div className="space-y-8">
      <VariableHeader detail={detail} />

      <section aria-label="Data" className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Tabs value={resolved.tab} onChange={(tab) => update({ tab })} />
          {freqs.length ? (
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              Period type
              <select
                aria-label="Period type"
                value={resolved.freq ?? ""}
                onChange={(e) =>
                  update({ freq: e.target.value as Selection["freq"], th: null, turth: null })
                }
                className="h-9 rounded-md border border-input bg-transparent px-2 text-sm text-foreground"
              >
                {freqs.map((f) => (
                  <option key={f} value={f}>
                    {FREQ_LABELS[f]}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {resolved.tab === "series" ? (
            <>
              <ViewToggle value={resolved.view} onChange={(view) => update({ view })} />
              <Button
                variant="outline"
                size="sm"
                disabled={!series.data?.series.some((s) => s.points.length)}
                onClick={() =>
                  series.data &&
                  downloadCsv(series.data.series, `bps-${detail.domain_id}-${detail.var_id}.csv`)
                }
              >
                <Download aria-hidden />
                Download CSV
              </Button>
            </>
          ) : null}
        </div>

        {resolved.tab === "series" ? (
          <div
            role="tabpanel"
            id="panel-series"
            aria-labelledby="tab-series"
            className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]"
          >
            <div className="space-y-4">
              <MemberPicker
                legend="Region / breakdown"
                filterLabel="Filter regions"
                members={detail.vervars}
                selected={resolved.vervars}
                max={vervarMax}
                onChange={(vervars) => update({ vervars })}
              />
              {detail.turvars.length > 1 ? (
                <MemberPicker
                  legend="Category"
                  filterLabel="Filter categories"
                  members={detail.turvars}
                  selected={resolved.turvars}
                  max={turvarMax}
                  onChange={(turvars) => update({ turvars })}
                />
              ) : null}
            </div>
            <SeriesPanel
              query={series}
              view={resolved.view}
              unit={detail.unit ?? null}
              decimals={detail.decimal ?? null}
              title={detail.title}
            />
          </div>
        ) : (
          <div role="tabpanel" id="panel-map" aria-labelledby="tab-map">
            <MapRanking
              detail={detail}
              selection={selection}
              turvar={resolved.turvars[0]}
              freq={resolved.freq}
              update={update}
            />
          </div>
        )}
      </section>

      <HtmlNote title="Definition" html={detail.definition} />
      <HtmlNote title="Notes" html={detail.notes} />
    </div>
  );
}

function VariableHeader({ detail }: { detail: VariableDetail }) {
  const updated = detail.last_update ? new Date(detail.last_update) : null;
  return (
    <header className="space-y-3">
      <h1 className="text-2xl font-semibold tracking-tight">{detail.title}</h1>
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <Badge variant="secondary">{detail.domain_name}</Badge>
        {detail.unit ? <Badge variant="outline">{detail.unit}</Badge> : null}
        {detail.subject || detail.category ? (
          <span>{[detail.subject, detail.category].filter(Boolean).join(" · ")}</span>
        ) : null}
        {updated && !Number.isNaN(updated.getTime()) ? (
          <span>· Updated {updatedFormat.format(updated)}</span>
        ) : null}
        <span>
          · var {detail.var_id}, domain {detail.domain_id}
        </span>
      </div>
    </header>
  );
}

const TABS: { value: Tab; label: string }[] = [
  { value: "series", label: "Time series" },
  { value: "map", label: "Map / ranking" },
];

function Tabs({ value, onChange }: { value: Tab; onChange: (tab: Tab) => void }) {
  return (
    <div role="tablist" aria-label="Data view" className="mr-auto flex gap-1 border-b">
      {TABS.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          id={`tab-${t.value}`}
          aria-controls={`panel-${t.value}`}
          aria-selected={value === t.value}
          tabIndex={value === t.value ? 0 : -1}
          onClick={() => onChange(t.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
              const next = TABS[(TABS.findIndex((x) => x.value === value) + 1) % TABS.length];
              onChange(next.value);
              document.getElementById(`tab-${next.value}`)?.focus();
            }
          }}
          className="-mb-px border-b-2 border-transparent px-3 py-2 text-sm font-medium text-muted-foreground hover:text-foreground aria-selected:border-foreground aria-selected:text-foreground"
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function ViewToggle({ value, onChange }: { value: View; onChange: (view: View) => void }) {
  return (
    <div role="radiogroup" aria-label="View" className="flex rounded-md border p-0.5 text-sm">
      {(["chart", "table"] as const).map((v) => (
        <label
          key={v}
          className="relative cursor-pointer rounded px-3 py-1 has-checked:bg-secondary has-checked:font-medium has-focus-visible:ring-2 has-focus-visible:ring-ring/50"
        >
          <input
            type="radio"
            name="view"
            value={v}
            checked={value === v}
            onChange={() => onChange(v)}
            className="absolute inset-0 cursor-pointer appearance-none rounded opacity-0"
          />
          {v === "chart" ? "Chart" : "Table"}
        </label>
      ))}
    </div>
  );
}

type Member = { val: number; label: string; group_label?: string | null };

function MemberPicker({
  legend,
  filterLabel,
  members,
  selected,
  max,
  onChange,
}: {
  legend: string;
  filterLabel: string;
  members: Member[];
  selected: number[];
  max: number;
  onChange: (vals: number[]) => void;
}) {
  const [filter, setFilter] = useState("");
  const chosen = new Set(selected);
  const visible = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return f ? members.filter((m) => m.label.toLowerCase().includes(f)) : members;
  }, [filter, members]);
  const full = selected.length >= max;
  const group = members.find((m) => m.group_label)?.group_label;

  const toggle = (val: number) => {
    if (chosen.has(val)) {
      if (selected.length > 1) onChange(selected.filter((v) => v !== val));
    } else if (!full) {
      onChange([...selected, val]);
    }
  };

  return (
    <fieldset className="space-y-2 rounded-lg border p-3">
      <legend className="px-1 text-sm font-medium">{legend}</legend>
      <p className="text-xs text-muted-foreground">
        {group ? `${group} · ` : ""}
        {selected.length} of {members.length} selected
        {full && members.length > max ? ` (max ${max})` : ""}
      </p>
      {members.length > 8 ? (
        <Input
          type="search"
          aria-label={filterLabel}
          placeholder="Filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="h-8"
        />
      ) : null}
      <ul className="max-h-72 space-y-0.5 overflow-y-auto text-sm">
        {visible.map((m) => (
          <li key={m.val}>
            <label className="flex cursor-pointer items-start gap-2 rounded px-1 py-0.5 hover:bg-accent has-disabled:cursor-default has-disabled:opacity-50">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={chosen.has(m.val)}
                disabled={!chosen.has(m.val) && full}
                onChange={() => toggle(m.val)}
              />
              <span>{m.label}</span>
            </label>
          </li>
        ))}
        {visible.length === 0 ? (
          <li className="px-1 text-muted-foreground">No match.</li>
        ) : null}
      </ul>
    </fieldset>
  );
}

function SeriesPanel({
  query,
  view,
  unit,
  decimals,
  title,
}: {
  query: UseQueryResult<SeriesResponse>;
  view: View;
  unit: string | null;
  decimals: number | null;
  title: string;
}) {
  const data = query.data;
  const series = useMemo(() => data?.series ?? [], [data]);
  const option = useMemo(() => buildChartOption(series, { unit, decimals }), [series, unit, decimals]);

  if (query.isError && !data) {
    return <ErrorBox message="Could not load the series — is the API running?" retry={query.refetch} />;
  }
  if (query.isPending) {
    return (
      <p className="text-sm text-muted-foreground" role="status">
        Loading series…
      </p>
    );
  }
  if (!series.some((s) => s.points.length)) {
    return (
      <p className="text-sm text-muted-foreground" role="status">
        No data for this selection.
      </p>
    );
  }

  const withCategory = new Set(series.map((s) => s.turvar)).size > 1;
  return (
    <div className="min-w-0 space-y-2" aria-busy={query.isFetching}>
      {data?.truncated ? (
        <p className="text-xs text-muted-foreground" role="status">
          Showing the first {data.max_series} series.
        </p>
      ) : null}
      {view === "chart" ? (
        <EChart option={option} label={`${title} — line chart: ${series.map((s) => seriesName(s, withCategory)).join(", ")}`} className="h-96 w-full" />
      ) : (
        <div className="max-h-[32rem] overflow-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-background">
              <tr className="border-b">
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  Period
                </th>
                {series.map((s) => (
                  <th
                    key={`${s.vervar}/${s.turvar}`}
                    scope="col"
                    className="px-3 py-2 text-right font-medium"
                  >
                    {seriesName(s, withCategory)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableRows(series).map((row) => (
                <tr key={row.period} className="border-b last:border-0">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    {row.period}
                  </th>
                  {row.values.map((v, i) => (
                    <td key={i} className="px-3 py-1.5 text-right tabular-nums">
                      {formatValue(v, decimals)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function HtmlNote({ title, html }: { title: string; html: string | null | undefined }) {
  const clean = sanitizeHtml(html);
  if (!clean) return null;
  return (
    <section aria-label={title} className="space-y-2">
      <h2 className="text-lg font-semibold">{title}</h2>
      <div
        className="prose-sm max-w-3xl space-y-2 text-sm text-muted-foreground [&_a]:underline"
        dangerouslySetInnerHTML={{ __html: clean }}
      />
    </section>
  );
}

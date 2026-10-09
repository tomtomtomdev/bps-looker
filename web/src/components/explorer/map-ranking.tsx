"use client";

import { useMemo } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { EChart, registerMap, type ChartClick } from "@/components/explorer/echart";
import { Button } from "@/components/ui/button";
import { api, type CrossSection, type Freq, type VariableDetail } from "@/lib/api/client";
import {
  buildMapOption,
  buildRankingOption,
  rankedRegions,
  regionLabel,
} from "@/lib/explorer/cross-section";
import { formatValue, type RegionLevel, type Selection } from "@/lib/explorer/series";
import {
  featureKeys,
  geoNameForData,
  geoUrl,
  mapLevels,
  pickLevel,
  type GeoCollection,
  type GeoName,
} from "@/lib/geo/regions";

type CrossSectionRequest = {
  turvar: number | undefined;
  freq: Freq | null;
  th: number | null;
  turth: number | null;
};

async function fetchCrossSection(
  domain: string,
  varId: number,
  { turvar, freq, th, turth }: CrossSectionRequest,
): Promise<CrossSection> {
  const { data, error } = await api.GET("/variables/{domain}/{var}/cross-section", {
    params: {
      path: { domain, var: varId },
      query: {
        turvar,
        freq: freq ?? undefined,
        th: th ?? undefined,
        turth: turth ?? undefined,
      },
    },
  });
  if (!data) throw new Error(`GET cross-section failed: ${JSON.stringify(error)}`);
  return data;
}

async function fetchGeo(name: GeoName): Promise<{ name: GeoName; keys: Map<number, string> }> {
  const response = await globalThis.fetch(geoUrl(name));
  if (!response.ok) throw new Error(`GET ${geoUrl(name)} failed: ${response.status}`);
  const geo = (await response.json()) as GeoCollection;
  registerMap(name, geo);
  return { name, keys: featureKeys(geo) };
}

const LEVEL_LABELS: Record<RegionLevel, string> = { province: "Provinces", regency: "Regencies" };

const vervarOf = (p: ChartClick): number | null => {
  const data = p.data as { vervar?: unknown } | undefined;
  return typeof data?.vervar === "number" ? data.vervar : null;
};

/**
 * Map / ranking tab: one period's value per region (`GET …/cross-section`), as a choropleth
 * (provinces or regencies, when the vervars are BPS region codes) and a ranking bar chart, with a
 * period slider. Clicking a region opens its time series.
 */
export function MapRanking({
  detail,
  selection,
  turvar,
  freq,
  update,
}: {
  detail: VariableDetail;
  selection: Selection;
  turvar: number | undefined;
  freq: Freq | null;
  update: (patch: Partial<Selection>) => void;
}) {
  const vals = detail.vervars.map((v) => v.val);
  const levels = mapLevels(vals);
  const level = pickLevel(selection.level, levels);

  const request: CrossSectionRequest = { turvar, freq, th: selection.th, turth: selection.turth };
  const query = useQuery({
    queryKey: ["cross-section", detail.domain_id, detail.var_id, request],
    queryFn: () => fetchCrossSection(detail.domain_id, detail.var_id, request),
    placeholderData: keepPreviousData,
  });
  // Shapes follow the codes that have values: a 38-province variable shows pre-2022 years (old
  // Papua codes only) on the 34-province map.
  const geoName = level && query.data ? geoNameForData(level, query.data.regions) : null;
  const geo = useQuery({
    queryKey: ["geo", geoName],
    queryFn: () => fetchGeo(geoName as GeoName),
    enabled: geoName !== null,
    staleTime: Infinity,
  });

  const data = query.data;
  const unit = detail.unit ?? null;
  const decimals = detail.decimal ?? null;
  const open = (p: ChartClick) => {
    const vervar = vervarOf(p);
    if (vervar === null) return;
    update({ tab: "series", vervars: [vervar], th: null, turth: null, level: null });
  };

  if (query.isError && !data) {
    return (
      <div role="alert" className="flex items-center gap-3 rounded-md border p-4 text-sm">
        <span>Could not load the regions — is the API running?</span>
        <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
          Retry
        </Button>
      </div>
    );
  }
  if (!data) {
    return (
      <p className="text-sm text-muted-foreground" role="status">
        Loading regions…
      </p>
    );
  }

  const periods = data.periods;
  const index = data.period
    ? periods.findIndex((p) => p.th === data.period?.th && p.turth === data.period?.turth)
    : -1;
  const periodLabel = data.period?.label ?? "No period";

  return (
    <div className="space-y-4" aria-busy={query.isFetching}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        {levels.length > 1 && level ? (
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            Regions
            <select
              aria-label="Region level"
              value={level}
              onChange={(e) => update({ level: e.target.value as RegionLevel })}
              className="h-9 rounded-md border border-input bg-transparent px-2 text-sm text-foreground"
            >
              {levels.map((l) => (
                <option key={l} value={l}>
                  {LEVEL_LABELS[l]}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {detail.turvars.length > 1 ? (
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            Category
            <select
              aria-label="Category"
              value={turvar}
              onChange={(e) => update({ turvars: [Number(e.target.value)], th: null, turth: null })}
              className="h-9 max-w-64 rounded-md border border-input bg-transparent px-2 text-sm text-foreground"
            >
              {detail.turvars.map((t) => (
                <option key={t.val} value={t.val}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {periods.length > 1 ? (
          <label className="flex min-w-64 flex-1 items-center gap-3 text-sm text-muted-foreground">
            <span>Period</span>
            <input
              type="range"
              aria-label="Period"
              aria-valuetext={periodLabel}
              min={0}
              max={periods.length - 1}
              step={1}
              value={index < 0 ? periods.length - 1 : index}
              onChange={(e) => {
                const p = periods[Number(e.target.value)];
                if (p) update({ th: p.th, turth: p.turth });
              }}
              className="flex-1 accent-[#2a78d6]"
            />
          </label>
        ) : null}
        <output className="text-sm font-medium tabular-nums">{periodLabel}</output>
      </div>

      {data.national ? (
        <p className="text-sm text-muted-foreground">
          {regionLabel(data.national)}:{" "}
          <span className="font-medium text-foreground tabular-nums">
            {formatValue(data.national.value, decimals)}
            {unit && data.national.value !== null ? ` ${unit}` : ""}
          </span>
        </p>
      ) : null}

      <RegionCharts
        title={detail.title}
        data={data}
        level={level}
        shapes={geo.data}
        shapesFailed={geo.isError}
        unit={unit}
        decimals={decimals}
        periodLabel={periodLabel}
        onOpen={open}
      />
    </div>
  );
}

type Shapes = { name: GeoName; keys: Map<number, string> };

function RegionCharts({
  title,
  data,
  level,
  shapes,
  shapesFailed,
  unit,
  decimals,
  periodLabel,
  onOpen,
}: {
  title: string;
  data: CrossSection;
  level: RegionLevel | null;
  shapes: Shapes | undefined;
  shapesFailed: boolean;
  unit: string | null;
  decimals: number | null;
  periodLabel: string;
  onOpen: (p: ChartClick) => void;
}) {
  // Memoized: EChart re-applies its option (resetting zoom/pan) whenever the object changes.
  const regions = data.regions;
  const national = data.national?.value ?? null;
  const rows = useMemo(() => rankedRegions(regions, level), [regions, level]);
  const ranking = useMemo(
    () => buildRankingOption(rows, { unit, decimals, national }),
    [rows, unit, decimals, national],
  );
  const mapOption = useMemo(
    () =>
      level && shapes
        ? buildMapOption(regions, { map: shapes.name, keys: shapes.keys, level, unit, decimals })
        : null,
    [regions, level, shapes, unit, decimals],
  );

  return (
    <>
      {!rows.length ? (
        <p className="text-sm text-muted-foreground" role="status">
          No data for this period.
        </p>
      ) : (
        <div className={level ? "grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]" : ""}>
          {level ? (
            <div className="min-w-0 space-y-1">
              {mapOption ? (
                <EChart
                  option={mapOption}
                  label={`${title} — choropleth map of ${LEVEL_LABELS[level].toLowerCase()}, ${periodLabel}`}
                  className="h-[26rem] w-full"
                  onClick={onOpen}
                />
              ) : shapesFailed ? (
                <p className="text-sm text-muted-foreground" role="status">
                  Could not load the map shapes.
                </p>
              ) : (
                <p className="text-sm text-muted-foreground" role="status">
                  Loading map…
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                Click a region to open its time series. Grey: no data. Boundaries: BPS / OCHA
                ROAP via geoBoundaries, CC BY 3.0 IGO.
              </p>
            </div>
          ) : null}
          <div className="max-h-[32rem] min-w-0 overflow-y-auto rounded-lg border">
            <EChart
              option={ranking}
              label={`${title} — region ranking, ${periodLabel}: ${rows
                .slice(0, 3)
                .map(regionLabel)
                .join(", ")}${rows.length > 3 ? ", …" : ""}`}
              className="w-full"
              style={{ height: Math.max(200, rows.length * 22 + 48) }}
              onClick={onOpen}
            />
          </div>
        </div>
      )}
    </>
  );
}

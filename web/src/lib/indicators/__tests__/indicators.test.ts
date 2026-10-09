import { describe, expect, it } from "vitest";

import type { IndicatorPoint } from "@/lib/api/client";
import {
  NATIONAL_DOMAIN,
  buildHistoryOption,
  changeDirection,
  describeChange,
  explorerHref,
  parseDashboardState,
  dashboardQueryString,
} from "@/lib/indicators/indicators";
import { indicator } from "@/test/fixtures";

describe("URL state", () => {
  it("defaults to the national domain and no open indicator", () => {
    expect(parseDashboardState(new URLSearchParams(""))).toEqual({
      domain: NATIONAL_DOMAIN,
      indicator: null,
    });
    expect(dashboardQueryString({ domain: "0000", indicator: null })).toBe("");
  });

  it("round-trips a province and an open indicator", () => {
    const state = parseDashboardState(new URLSearchParams("domain=1100&indicator=7"));
    expect(state).toEqual({ domain: "1100", indicator: 7 });
    expect(dashboardQueryString(state)).toBe("?domain=1100&indicator=7");
    expect(dashboardQueryString({ domain: "0000", indicator: 3 })).toBe("?indicator=3");
  });

  it("ignores malformed values", () => {
    expect(parseDashboardState(new URLSearchParams("domain=abc&indicator=x"))).toEqual({
      domain: NATIONAL_DOMAIN,
      indicator: null,
    });
  });
});

describe("change vs previous", () => {
  it("has a direction from the sign of the change", () => {
    expect(changeDirection(indicator({ change: 0.18 }))).toBe("up");
    expect(changeDirection(indicator({ change: -3552.2 }))).toBe("down");
    expect(changeDirection(indicator({ change: 0 }))).toBe("flat");
    expect(changeDirection(indicator({ change: null }))).toBeNull();
  });

  it("describes change, percentage and the previous periode", () => {
    expect(describeChange(indicator())).toBe("+0.18 (+5.8%) vs Agustus 2026");
    expect(
      describeChange(indicator({ change: -3552.2, change_pct: -13.34, previous: { periode: "Juli 2026", value: 26612.2 } })),
    ).toBe("−3,552.2 (−13.3%) vs Juli 2026");
    expect(describeChange(indicator({ change: 5, change_pct: null }))).toBe("+5 vs Agustus 2026");
    expect(describeChange(indicator({ change: 0, change_pct: 0 }))).toBe("No change vs Agustus 2026");
  });

  it("says why there is no change", () => {
    expect(describeChange(indicator({ previous: null, change: null, change_pct: null }))).toBe(
      "First recorded periode",
    );
    expect(
      describeChange(indicator({ previous: { periode: "Juli 2026", value: null }, change: null, change_pct: null })),
    ).toBe("Juli 2026: no numeric value");
  });
});

describe("explorer link", () => {
  it("points at the variable page when the variable is crawled", () => {
    expect(explorerHref(indicator())).toBe("/explorer/0000/2263");
    expect(explorerHref(indicator({ variable: null }))).toBeNull();
  });
});

describe("history chart", () => {
  const points: IndicatorPoint[] = [
    { periode: "Juli 2026", title: "x, Juli 2026", value: 2.95, first_seen: "2026-10-01T00:00:00Z", last_seen: "2026-10-03T00:00:00Z" },
    { periode: "Agustus 2026", title: "x, Agustus 2026", value: null, first_seen: "2026-10-04T00:00:00Z", last_seen: "2026-10-06T00:00:00Z" },
    { periode: "September 2026", title: "x, September 2026", value: 3.28, first_seen: "2026-10-07T00:00:00Z", last_seen: "2026-10-09T00:00:00Z" },
  ];

  it("plots one point per periode on a category axis, oldest first", () => {
    const option = buildHistoryOption(points, { unit: "Persen", name: "Inflasi" });
    expect(option.xAxis.data).toEqual(["Juli 2026", "Agustus 2026", "September 2026"]);
    expect(option.series[0].data).toEqual([2.95, null, 3.28]);
    expect(option.series[0].name).toBe("Inflasi");
    expect(option.yAxis.name).toBe("Persen");
    // A single periode still shows its point.
    const single = buildHistoryOption(points.slice(2), { unit: null, name: "Inflasi" });
    expect(single.series[0].showSymbol).toBe(true);
    expect(single.yAxis.name).toBe("");
  });
});

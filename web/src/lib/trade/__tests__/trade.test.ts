import { describe, expect, it } from "vitest";

import type { TradeBreakdown, TradePeriods, TradeSeriesResponse, TradeTotal } from "@/lib/api/client";
import {
  basisNote,
  breakdownLabel,
  buildBalanceOption,
  buildBreakdownOption,
  buildTrendOption,
  describeRange,
  effectiveRange,
  formatTonnes,
  formatUsd,
  formatUsdPrecise,
  granularityOf,
  monthChoices,
  parseTradeState,
  setRangeEnd,
  switchGranularity,
  tradeQueryString,
  yearChoices,
  type TradeState,
} from "@/lib/trade/trade";

const PERIODS: TradePeriods = {
  items: [
    { flow: "export", year: 2024, annual: true, months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] },
    { flow: "export", year: 2025, annual: false, months: [1, 2, 3] },
    { flow: "import", year: 2023, annual: true, months: [] },
    { flow: "import", year: 2025, annual: false, months: [1, 2] },
  ],
  latest_year: 2025,
  latest_month: "2025-03",
};

const state = (patch: Partial<TradeState> = {}): TradeState => ({
  flow: "export",
  from: null,
  to: null,
  top: 10,
  hs2: null,
  country: null,
  ...patch,
});

describe("URL state", () => {
  it("parses with defaults and drops invalid values", () => {
    expect(parseTradeState(new URLSearchParams())).toEqual(state());
    expect(
      parseTradeState(
        new URLSearchParams("flow=import&from=2024-11&to=2025-02&top=20&hs2=27&country=CHINA"),
      ),
    ).toEqual(state({ flow: "import", from: "2024-11", to: "2025-02", top: 20, hs2: "27", country: "CHINA" }));
    expect(parseTradeState(new URLSearchParams("flow=x&from=24&to=2024-13&top=7&hs2=7"))).toEqual(state());
  });

  it("serializes without defaults, round-trips", () => {
    expect(tradeQueryString(state())).toBe("");
    const s = state({ flow: "import", from: "2023", to: "2025", top: 5, hs2: "27", country: "KOREA, REP." });
    const qs = tradeQueryString(s);
    expect(qs).toBe("?flow=import&from=2023&to=2025&top=5&hs2=27&country=KOREA%2C+REP.");
    expect(parseTradeState(new URLSearchParams(qs.slice(1)))).toEqual(s);
  });
});

describe("ranges", () => {
  it("defaults to the latest year with data", () => {
    expect(effectiveRange(state(), PERIODS)).toEqual({ from: "2025", to: "2025" });
    expect(effectiveRange(state({ from: "2024-02" }), PERIODS)).toEqual({ from: "2024-02", to: "2024-02" });
    expect(effectiveRange(state({ to: "2024" }), PERIODS)).toEqual({ from: "2024", to: "2024" });
    expect(effectiveRange(state(), { items: [], latest_year: null, latest_month: null })).toBeNull();
  });

  it("lists the years and months with data", () => {
    expect(yearChoices(PERIODS)).toEqual([2023, 2024, 2025]);
    expect(monthChoices(PERIODS)).toHaveLength(15);
    expect(monthChoices(PERIODS)[0]).toBe("2024-01");
    expect(monthChoices(PERIODS).at(-1)).toBe("2025-03");
  });

  it("switches granularity within the data", () => {
    expect(granularityOf({ from: "2024", to: "2025" })).toBe("year");
    expect(granularityOf({ from: "2024", to: "2025-01" })).toBe("month");
    expect(switchGranularity({ from: "2024", to: "2025" }, "month", PERIODS)).toEqual({
      from: "2024-01",
      to: "2025-03",
    });
    expect(switchGranularity({ from: "2024-11", to: "2025-02" }, "year", PERIODS)).toEqual({
      from: "2024",
      to: "2025",
    });
  });

  it("keeps from <= to when one end moves past the other", () => {
    expect(setRangeEnd({ from: "2024", to: "2025" }, "from", "2025")).toEqual({ from: "2025", to: "2025" });
    expect(setRangeEnd({ from: "2025-02", to: "2025-03" }, "to", "2024-12")).toEqual({
      from: "2024-12",
      to: "2024-12",
    });
  });

  it("describes a range", () => {
    expect(describeRange({ start: "2024", end: "2024", granularity: "year" })).toBe("2024");
    expect(describeRange({ start: "2023", end: "2025", granularity: "year" })).toBe("2023–2025");
    expect(describeRange({ start: "2024-11", end: "2025-02", granularity: "month" })).toBe("Nov 2024 – Feb 2025");
    expect(describeRange({ start: "2025-03", end: "2025-03", granularity: "month" })).toBe("Mar 2025");
  });

  it("notes partial years summed from monthly figures", () => {
    const total = (periods: TradeTotal["periods"]): TradeTotal => ({ flow: "export", value_usd: 1, netweight_kg: 1, periods });
    expect(basisNote(total([{ year: 2024, basis: "annual", months: 12 }]))).toBeNull();
    expect(basisNote(total([{ year: 2025, basis: "monthly", months: 3 }]))).toBe(
      "2025: 3 months of monthly figures (no annual figures yet)",
    );
    expect(basisNote(total([{ year: 2025, basis: "monthly", months: 12 }]))).toBeNull();
    expect(basisNote(total([{ year: 2026, basis: "annual", months: 8 }]))).toBe(
      "2026: year to date (monthly figures through Aug)",
    );
  });
});

describe("formatting", () => {
  it("formats US$ and tonnes compactly", () => {
    expect(formatUsd(266_529_176_253.85)).toBe("US$266.5B");
    expect(formatUsd(12_300_000)).toBe("US$12.3M");
    expect(formatUsd(-3_200_000_000)).toBe("−US$3.2B");
    expect(formatUsd(null)).toBe("–");
    expect(formatUsdPrecise(1_002_300_000_000)).toBe("US$1.002T");
    expect(formatUsdPrecise(266_529_176_253.85)).toBe("US$266.5B");
    expect(formatUsdPrecise(-31_330_000_000)).toBe("−US$31.33B");
    expect(formatTonnes(1_234_000_000)).toBe("1.2M t");
    expect(formatTonnes(null)).toBe("–");
  });

  it("labels breakdown items", () => {
    expect(breakdownLabel({ key: "27", label: "Mineral fuels" }, "hs2")).toBe("27 Mineral fuels");
    expect(breakdownLabel({ key: "27", label: null }, "hs2")).toBe("27");
    expect(breakdownLabel({ key: "CHINA", label: "CHINA" }, "country")).toBe("CHINA");
    expect(breakdownLabel({ key: "", label: null }, "port")).toBe("Not stated");
  });
});

const BREAKDOWN: TradeBreakdown = {
  by: "hs2",
  flow: "export",
  top: 2,
  range: { start: "2024", end: "2024", granularity: "year" },
  total: { flow: "export", value_usd: 205, netweight_kg: 2000, periods: [] },
  items: [
    { key: "27", label: "Mineral fuels, mineral oils and products of their distillation", value_usd: 150, netweight_kg: 1500, share: 0.73 },
    { key: "15", label: "Fats", value_usd: 30, netweight_kg: 300, share: 0.15 },
  ],
  others: { count: 2, value_usd: 25, netweight_kg: 200, share: 0.12 },
};

describe("chart options", () => {
  it("top-N bars, largest at the top, item keys kept for clicks", () => {
    const option = buildBreakdownOption(BREAKDOWN, "#2a78d6");
    expect(option.yAxis.inverse).toBe(true);
    expect(option.yAxis.data[0]).toMatch(/^27 Mineral fuels, mineral oil.*…$/);
    expect(option.yAxis.data[0].length).toBeLessThanOrEqual(30);
    expect(option.series[0].data.map((d) => [d.key, d.value])).toEqual([
      ["27", 150],
      ["15", 30],
    ]);
  });

  const SERIES: TradeSeriesResponse = {
    hs2: null,
    hs2_label: null,
    country: null,
    range: null,
    series: [
      {
        flow: "export",
        points: [
          { period: "2025-01", date: "2025-01-01", value_usd: 10, netweight_kg: 1 },
          { period: "2025-02", date: "2025-02-01", value_usd: 12, netweight_kg: 1 },
        ],
      },
      { flow: "import", points: [{ period: "2025-01", date: "2025-01-01", value_usd: 14, netweight_kg: 1 }] },
    ],
    balance: [{ period: "2025-01", date: "2025-01-01", value_usd: -4 }],
  };

  it("trend: one line for the selected flow on a time axis", () => {
    const option = buildTrendOption(SERIES, "export");
    expect(option.xAxis.type).toBe("time");
    expect(option.series).toHaveLength(1);
    expect(option.series[0].name).toBe("Exports");
    expect(option.series[0].data).toEqual([
      ["2025-01-01", 10],
      ["2025-02-01", 12],
    ]);
    expect(buildTrendOption(SERIES, "import").series[0].data).toEqual([["2025-01-01", 14]]);
  });

  it("balance: exports and imports lines plus balance bars coloured by sign", () => {
    const option = buildBalanceOption(SERIES);
    expect(option.series.map((s) => [s.type, s.name])).toEqual([
      ["bar", "Balance"],
      ["line", "Exports"],
      ["line", "Imports"],
    ]);
    const bars = option.series[0].data as { value: [string, number]; itemStyle: { color: string } }[];
    expect(bars[0].value).toEqual(["2025-01-01", -4]);
    expect(bars[0].itemStyle.color).not.toBe(
      (buildBalanceOption({ ...SERIES, balance: [{ period: "2025-01", date: "2025-01-01", value_usd: 4 }] })
        .series[0].data[0] as { itemStyle: { color: string } }).itemStyle.color,
    );
  });
});

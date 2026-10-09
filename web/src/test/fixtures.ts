import type { Indicator, VariableDetail } from "@/lib/api/client";

/** Test data shaped like the read API's responses. */
const MONTHS = ["Januari", "Februari", "Maret"];

export function variableDetail(overrides: Partial<VariableDetail> = {}): VariableDetail {
  return {
    domain_id: "0000",
    domain_name: "Indonesia",
    domain_level: "pusat",
    var_id: 2263,
    title: "Inflasi Tahunan (Y-on-Y)",
    unit: "Persen",
    subject_id: 3,
    subject: "Inflasi",
    category: "Harga-Harga",
    definition: null,
    notes: null,
    decimal: 2,
    last_update: "2026-10-01T11:24:01",
    vervars: [
      { val: 1100, label: "PROV ACEH", group_label: "38 Provinsi" },
      { val: 3100, label: "PROV DKI JAKARTA", group_label: "38 Provinsi" },
      { val: 9999, label: "INDONESIA", group_label: "38 Provinsi" },
    ],
    turvars: [{ val: 0, label: "Tidak ada" }],
    turths: [
      ...MONTHS.map((label, i) => ({ val: i + 1, label, freq: "month" as const, has_data: true })),
      { val: 13, label: "Tahunan", freq: "year", has_data: false },
    ],
    periods: [{ th: 124, label: "2024" }],
    ...overrides,
  };
}


export function indicator(overrides: Partial<Indicator> = {}): Indicator {
  return {
    domain_id: "0000",
    indicator_id: 3,
    title: "Inflasi Year on Year, September 2026",
    label: "Inflasi Year on Year",
    name: "Pada September 2026 terjadi inflasi year-on-year sebesar 3,28 persen",
    value: 3.28,
    unit: "Persen",
    periode: "September 2026",
    category: 2,
    subject_csa: 536,
    data_source: "BPS",
    first_seen: "2026-10-07T09:00:00Z",
    last_seen: "2026-10-09T09:00:00Z",
    var: 2263,
    variable: { domain_id: "0000", var_id: 2263, title: "Inflasi Tahunan (Y-on-Y)" },
    previous: { periode: "Agustus 2026", value: 3.1 },
    change: 0.18,
    change_pct: 5.806,
    ...overrides,
  };
}

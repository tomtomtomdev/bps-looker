import type { VariableDetail } from "@/lib/api/client";

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


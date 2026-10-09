import type { RegionLevel } from "@/lib/explorer/series";

/**
 * Map shapes in `public/geo/` (see its README): provinces (34 = BPS domains, 38 = with the 2022
 * Papua provinces) and regencies, each feature keyed by its BPS code in `properties.code`.
 */
export type GeoName = "provinces-34" | "provinces-38" | "regencies";

export type GeoFeature = {
  type: "Feature";
  properties: { code: string; name: string; alt?: string };
  geometry: unknown;
};
export type GeoCollection = { type: "FeatureCollection"; features: GeoFeature[] };

/** Province codes that only exist in the 38-province set (Papua split, 2022). */
export const NEW_PAPUA_PROVINCES = [9200, 9500, 9600, 9700];

/** Province `xx00` (1100…9700), regency `xxyy` (yy ≠ 00); anything else (9999 = Indonesia,
 * categories) isn't a region. */
export function regionLevel(val: number): RegionLevel | null {
  if (val < 1100 || val > 9799) return null;
  return val % 100 === 0 ? "province" : "regency";
}

/** Levels with at least two members, provinces first. */
export function mapLevels(vals: number[]): RegionLevel[] {
  const count = { province: 0, regency: 0 };
  for (const v of vals) {
    const level = regionLevel(v);
    if (level) count[level] += 1;
  }
  return (["province", "regency"] as const).filter((l) => count[l] >= 2);
}

/** The requested level if the variable has it, else its first level (`null`: no regions). */
export function pickLevel(requested: RegionLevel | null, levels: RegionLevel[]): RegionLevel | null {
  return requested && levels.includes(requested) ? requested : (levels[0] ?? null);
}

export function geoNameFor(level: RegionLevel, vals: number[]): GeoName {
  if (level === "regency") return "regencies";
  return vals.some((v) => NEW_PAPUA_PROVINCES.includes(v)) ? "provinces-38" : "provinces-34";
}

/** The shapes for one period's values: by the codes that have a value (all codes if none). */
export function geoNameForData(
  level: RegionLevel,
  regions: { vervar: number; value?: number | null }[],
): GeoName {
  const valued = regions.filter((r) => r.value !== null && r.value !== undefined);
  return geoNameFor(level, (valued.length ? valued : regions).map((r) => r.vervar));
}

export function geoUrl(name: GeoName): string {
  return `/geo/${name}.json`;
}

/** vervar code → feature code (a regency's post-2022 `alt` code maps to the same shape). */
export function featureKeys(geo: GeoCollection): Map<number, string> {
  const keys = new Map<number, string>();
  for (const { properties: p } of geo.features) {
    keys.set(Number(p.code), p.code);
    if (p.alt) keys.set(Number(p.alt), p.code);
  }
  return keys;
}

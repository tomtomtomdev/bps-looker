import { readFileSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  featureKeys,
  geoNameFor,
  geoNameForData,
  geoUrl,
  mapLevels,
  pickLevel,
  regionLevel,
  type GeoCollection,
  type GeoName,
} from "@/lib/geo/regions";
import regions from "@/test/bps-regions.json";

const GEO_DIR = path.resolve(__dirname, "../../../../public/geo");

function load(name: GeoName): GeoCollection {
  return JSON.parse(readFileSync(path.join(GEO_DIR, `${name}.json`), "utf8")) as GeoCollection;
}

const codes = (geo: GeoCollection) => geo.features.map((f) => f.properties.code);

describe("map shapes in public/geo", () => {
  it.each(["provinces-34", "provinces-38", "regencies"] as const)(
    "%s: unique codes, every feature has a geometry, small enough",
    (name) => {
      const geo = load(name);
      expect(new Set(codes(geo)).size).toBe(geo.features.length);
      for (const f of geo.features) {
        expect(f.geometry, f.properties.code).toBeTruthy();
        expect(f.properties.name).toBeTruthy();
      }
      const limit = name === "regencies" ? 800_000 : 500_000;
      expect(statSync(path.join(GEO_DIR, `${name}.json`)).size).toBeLessThan(limit);
    },
  );

  it("every BPS province domain has a shape (34)", () => {
    const geo = load("provinces-34");
    expect(codes(geo).sort()).toEqual(Object.keys(regions.provinces).sort());
  });

  it("every 38-province vervar code (e.g. 0000/2263) has a shape", () => {
    const geo = load("provinces-38");
    expect(Object.keys(regions.provinces38)).toHaveLength(38);
    expect(codes(geo).sort()).toEqual(Object.keys(regions.provinces38).sort());
    expect(geo.features.find((f) => f.properties.code === "9200")?.properties.name).toBe(
      "Papua Barat Daya",
    );
  });

  it("every regency domain, and every post-2022 Papua regency code, maps to a shape", () => {
    const keys = featureKeys(load("regencies"));
    for (const code of Object.keys(regions.regencies)) expect(keys.get(Number(code))).toBe(code);
    // New codes alias the same shape: Merauke 9501 = 9401, Kota Sorong 9271 = 9171.
    for (const code of Object.keys(regions.newPapuaRegencies)) {
      expect(keys.get(Number(code)), code).toBeDefined();
    }
    expect(keys.get(9501)).toBe("9401");
    expect(keys.get(9271)).toBe("9171");
  });
});

describe("region levels", () => {
  it("classifies vervar codes", () => {
    expect(regionLevel(1100)).toBe("province");
    expect(regionLevel(9700)).toBe("province");
    expect(regionLevel(3273)).toBe("regency");
    expect(regionLevel(9999)).toBeNull();
    expect(regionLevel(1)).toBeNull();
    expect(regionLevel(0)).toBeNull();
    expect(regionLevel(1000)).toBeNull();
  });

  it("offers the levels a variable has", () => {
    expect(mapLevels([1100, 1200, 3100, 9999])).toEqual(["province"]);
    expect(mapLevels([1100, 1101, 1102, 1200, 1201])).toEqual(["province", "regency"]);
    expect(mapLevels([1, 2, 3])).toEqual([]);
    expect(pickLevel("regency", ["province", "regency"])).toBe("regency");
    expect(pickLevel("regency", ["province"])).toBe("province");
    expect(pickLevel(null, [])).toBeNull();
  });

  it("picks the 38-province map when a new Papua province is present", () => {
    expect(geoNameFor("province", [1100, 9100, 9400])).toBe("provinces-34");
    expect(geoNameFor("province", [1100, 9100, 9200, 9400])).toBe("provinces-38");
    expect(geoNameFor("regency", [1101])).toBe("regencies");
    const v = (vervar: number, value: number | null) => ({ vervar, value });
    // A 38-province variable in a pre-2022 year: the new codes have no values → 34 shapes.
    expect(geoNameForData("province", [v(9100, 1), v(9200, null), v(9400, 2)])).toBe("provinces-34");
    expect(geoNameForData("province", [v(9100, 1), v(9200, 3)])).toBe("provinces-38");
    expect(geoNameForData("province", [v(9200, null)])).toBe("provinces-38");
    expect(geoUrl("provinces-38")).toBe("/geo/provinces-38.json");
  });
});

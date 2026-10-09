# Indonesia map shapes (U4)

| File | Features | Keyed by | Used for |
|---|---|---|---|
| `provinces-38.json` | 38 provinces (2022+, incl. Papua Barat Daya 9200, Papua Selatan 9500, Papua Tengah 9600, Papua Pegunungan 9700) | `code` = BPS province code `xx00` | variables whose regions include any of the new Papua codes |
| `provinces-34.json` | 34 provinces (pre-2022; = BPS domain list) | `code` = `xx00` | every other province variable |
| `regencies.json` | 514 regencies/cities (2020) | `code` = BPS regency code (`xxyy`, cities `xx71+`); `alt` = post-2022 code of a Papua regency (Merauke 9401 → 9501) | regency variables |

Properties: `code`, `name` (+ `alt`). Coordinates WGS84, rounded to 0.001°.

## Source and license

- **geoBoundaries** `gbOpen` IDN ADM2, release commit `9469f09`
  (<https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IDN/ADM2/geoBoundaries-IDN-ADM2_simplified.geojson>),
  boundary source: Badan Pusat Statistik (BPS – Statistics Indonesia), World Food Programme,
  OCHA ROAP (2020).
- License: **Creative Commons Attribution 3.0 IGO (CC BY 3.0 IGO)**. Attribution:
  "Boundaries: BPS / OCHA ROAP via geoBoundaries (Runfola et al. 2020), CC BY 3.0 IGO" — shown
  under the map.
- Changes: regencies matched by name to BPS regency codes, water bodies dropped
  (Danau Toba, Waduk Cirata, …), simplified (mapshaper 0.6.113, 12 % / 10 % weighted),
  provinces dissolved from regencies.

## Rebuild

```sh
curl -L -o /tmp/geo-src/adm2.geojson <URL above>
uv run python -I scripts/build_geo.py /tmp/geo-src/adm2.geojson
```

The script matches against `web/src/test/bps-regions.json` (BPS domains + the 38-province and
new Papua regency vervar codes, exported from the dev DB) and fails on any unmatched shape or
regency without one. `src/lib/geo/__tests__/` checks every code in that list has a shape.

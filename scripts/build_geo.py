"""Build the web map shapes (``web/public/geo/*.json``) from geoBoundaries IDN ADM2 (U4).

Usage::

    uv run python -I scripts/build_geo.py PATH/TO/geoBoundaries-IDN-ADM2_simplified.geojson

Input: the geoBoundaries ``gbOpen`` IDN ADM2 release (BPS / OCHA ROAP 2020 regency boundaries,
CC BY 3.0 IGO — see ``web/public/geo/README.md``). Its features only carry ``shapeName``, so each
regency is matched by name to the BPS regency domains in ``web/src/test/bps-regions.json``
(``Kota …`` = city codes ``xx71+``); water bodies (``Danau …``, ``Waduk …``) are dropped.
Province codes: 34-province set = first two digits + ``00``; the 38-province set moves the
regencies of the four 2022 Papua provinces (9200, 9500, 9600, 9700) by their new BPS codes.

Shapes are simplified and dissolved with mapshaper (``pnpm dlx``, pinned) and written as
compact GeoJSON with properties ``code`` + ``name`` (regencies also ``alt``: the post-2022 code
of a Papua regency, e.g. Merauke 9401 → 9501).
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REGIONS = ROOT / "web/src/test/bps-regions.json"
OUT = ROOT / "web/public/geo"
MAPSHAPER = ["pnpm", "dlx", "mapshaper@0.6.113"]

# geoBoundaries names that differ from today's BPS names.
RENAMED = {"Toba Samosir": "Toba", "Mahakam Hulu": "Mahakam Ulu", "Mamuju Utara": "Pasangkayu"}
WATER = {"Danau", "Danau Toba", "Hutan", "Waduk Cirata", "Wadung Kedungombo"}


def norm(name: str) -> str:
    name = re.sub(r"\b(kabupaten|kab)\b", "", name.lower().replace("-", " ").replace(".", " "))
    return re.sub(r"\s+", "", name)


def is_city(code: str) -> bool:
    return int(code[2:]) >= 71


def title(label: str) -> str:
    words = label.title().split()
    return " ".join({"Dki": "DKI", "Di": "DI"}.get(w, w) for w in words)


def mapshaper(*args: str) -> None:
    subprocess.run([*MAPSHAPER, *args], check=True)


def main(adm2: Path) -> None:
    regions = json.loads(REGIONS.read_text())
    regencies: dict[str, str] = regions["regencies"]
    by_key = {("kota" if is_city(c) else "") + norm(n): c for c, n in regencies.items()}

    # Post-2022 Papua codes: same name (and city-ness) as an old 91xx/94xx regency.
    alt: dict[str, str] = {}
    for new, name in regions["newPapuaRegencies"].items():
        old = [
            c
            for c, n in regencies.items()
            if c[:2] in ("91", "94")
            and norm(n) == norm(name.removeprefix("Kota "))
            and is_city(c) == is_city(new)
        ]
        if len(old) != 1:
            raise SystemExit(f"no unique old code for {new} {name}: {old}")
        alt[old[0]] = new

    src = json.loads(adm2.read_text())
    features = []
    for f in src["features"]:
        name = f["properties"]["shapeName"]
        if name in WATER:
            continue
        code = by_key.get(norm(RENAMED.get(name, name)))
        if code is None:
            raise SystemExit(f"unmatched shape {name!r}")
        props: dict[str, Any] = {
            "code": code,
            "name": ("Kota " if is_city(code) else "") + regencies[code],
            "prov34": code[:2] + "00",
            "prov38": alt[code][:2] + "00" if code in alt else code[:2] + "00",
        }
        if code in alt:
            props["alt"] = alt[code]
        features.append({**f, "properties": props})
    missing = set(regencies) - {f["properties"]["code"] for f in features}
    if missing:
        raise SystemExit(f"regencies without a shape: {sorted(missing)}")

    names = {c: title(n) for c, n in regions["provinces38"].items()}
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "input.json"
        work.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        common = ["-simplify", "12%", "weighted", "keep-shapes", "-clean"]
        for n in (34, 38):
            mapshaper(
                "-i", str(work), *common, "-dissolve", f"prov{n}", "-rename-fields",
                f"code=prov{n}", "-o", f"{tmp}/provinces-{n}.json", "format=geojson",
                "precision=0.001",
            )  # fmt: skip
        mapshaper(
            "-i", str(work), "-simplify", "10%", "weighted", "keep-shapes", "-clean",
            "-filter-fields", "code,name,alt", "-o", f"{tmp}/regencies.json", "format=geojson",
            "precision=0.001",
        )  # fmt: skip
        for stem in ("provinces-34", "provinces-38", "regencies"):
            data = json.loads(Path(tmp, f"{stem}.json").read_text())
            for f in data["features"]:
                p = f["properties"]
                if stem != "regencies":
                    p["name"] = names[p["code"]]
                f["properties"] = {k: p[k] for k in ("code", "name", "alt") if k in p}
            data["features"].sort(key=lambda f: f["properties"]["code"])
            path = OUT / f"{stem}.json"
            path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
            print(f"{path.relative_to(ROOT)}: {len(data['features'])} features, "
                  f"{path.stat().st_size // 1024} KB")  # fmt: skip


if __name__ == "__main__":
    main(Path(sys.argv[1]))

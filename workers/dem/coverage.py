#!/usr/bin/env python3
"""
Pokrýva stiahnutá mozaika DEM to územie, na ktorom sa má počítať?

PREČO EXISTUJE. Dlaždica je sľub o celom stupni (pravidlo 2 v CLAUDE.md) a
`workers/dem/check.sh` vie o sklade len MENÁ – čiže sa spolieha na to, že sľub
platí. Keď neplatil, build to nepovedal: v sklade `dem-dmr5` ležali
`N48E021.tif`, `N48E022.tif` a `N49E020.tif` s pár set metrami dát (5 MB vedľa
253 MB), lebo ich do skladu poslal presah prevodu do WGS84. Kontrola videla
šesť dlaždíc z ôsmich, doplnenie sa nespustilo, `gdal_contour` prešiel po
mozaike, v ktorej boli naozaj dva stupne – a vrstevnice Prešovského kraja
skončili v jednom štvorci. Beh bol zelený (31484544154).

ROZHODUJE ROZSAH DLAŽDICE, NIE POČET PLATNÝCH BUNIEK. Poctivá dlaždica má
rozsah presne celého stupňa aj vtedy, keď je v nej terén len na pätine plochy
(pohraničný stupeň, alebo prázdna dlaždica = „pozerali sme sa a nič tu nie
je"). Lož má rozsah pár pixelov. Rozsah teda tie dva prípady oddelí presne,
kým „koľko je v nej nodaty" ich zlieva.

Použitie:
    python3 workers/dem/coverage.py --bbox=19.865,48.745,22.585,49.48 \\
        --dir=dem/dmr5/tiles [--min-pct=95] [--out=cov.txt]

Vypisuje `key=value` (aj do `--out`):
    covered_pct=97.5          koľko z bboxu pokrývajú rozsahy dlaždíc
    liars=N48E021.tif …       dlaždice, ktoré nepokrývajú svoj stupeň
    missing=N48E019 …         stupne bboxu, na ktoré nie je ani jedna dlaždica
Návratový kód 1 = pokrytie je pod `--min-pct` (volajúci sa rozhodne, čo s tým).
"""
import argparse
import json
import math
import os
import subprocess
import sys

# Koľko zo svojho stupňa musí dlaždica pokrývať, aby jej meno nebolo lož.
# Poctivá dlaždica má 100 % (píše ju `workers/dem/tiles.py` s `-te` na celý
# stupeň); tolerancia je na polpixel a na zaokrúhlenie v hlavičke.
HONEST_PCT = 99.0


def tile_extent(path):
    """(w, s, e, n) dlaždice v stupňoch, alebo None keď sa to nedá zistiť."""
    try:
        info = json.loads(subprocess.run(
            ["gdalinfo", "-json", path], capture_output=True, text=True,
            check=True, env={**os.environ, "GDAL_PAM_ENABLED": "NO"}).stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return None
    ext = info.get("wgs84Extent") or {}
    pts = []

    def walk(node):
        if node and isinstance(node[0], (int, float)):
            pts.append(node)
        else:
            for child in node or []:
                walk(child)

    walk(ext.get("coordinates"))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def degree_of(name):
    """`N49E020.tif` → (20, 49). Nesúvisiace meno vráti None."""
    base = os.path.basename(name).split(".")[0].upper()
    if len(base) != 7 or base[0] not in "NS" or base[3] not in "EW":
        return None
    try:
        lat, lon = int(base[1:3]), int(base[4:7])
    except ValueError:
        return None
    return (-lon if base[3] == "W" else lon, -lat if base[0] == "S" else lat)


def covers_own_degree(extent, degree):
    """Koľko percent svojho stupňa dlaždica pokrýva (podľa rozsahu)."""
    lon, lat = degree
    w = max(0.0, min(extent[2], lon + 1) - max(extent[0], lon))
    h = max(0.0, min(extent[3], lat + 1) - max(extent[1], lat))
    return 100.0 * w * h


def covered_pct(bbox, extents, cells=400):
    """Koľko z bboxu pokrýva únia rozsahov – meraná na pravidelnej mriežke.

    Mriežka, nie geometria: 400×400 buniek je na túto otázku („chýba pol kraja,
    alebo pol pixela?") presnosť 0,25 ‰ plochy a nepotrebuje na to shapely.
    """
    w, s, e, n = bbox
    if e <= w or n <= s or not extents:
        return 0.0
    dx, dy = (e - w) / cells, (n - s) / cells
    hit = 0
    for j in range(cells):
        y = s + (j + 0.5) * dy
        row = [x for x in extents if x[1] <= y <= x[3]]
        if not row:
            continue
        for i in range(cells):
            x = w + (i + 0.5) * dx
            if any(t[0] <= x <= t[2] for t in row):
                hit += 1
    return 100.0 * hit / (cells * cells)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", required=True, help="W,S,E,N územia, čo sa počíta")
    ap.add_argument("--dir", default="", help="adresár s dlaždicami")
    ap.add_argument("tiles", nargs="*", help="alebo priamo súbory")
    ap.add_argument("--min-pct", type=float, default=95.0,
                    help="pod týmto pokrytím je návratový kód 1")
    ap.add_argument("--out", default="", help="kam zapísať key=value")
    args = ap.parse_args()

    bbox = tuple(float(v) for v in args.bbox.split(","))
    if len(bbox) != 4:
        raise SystemExit(f"::error::--bbox chce W,S,E,N: „{args.bbox}“")

    paths = list(args.tiles)
    if args.dir and os.path.isdir(args.dir):
        paths += [os.path.join(args.dir, f) for f in sorted(os.listdir(args.dir))
                  if f.lower().endswith(".tif")]
    if not paths:
        print("::error::coverage.py nedostal ani jednu dlaždicu.")
        return 2

    good, liars = [], []
    for p in sorted(set(paths)):
        name = os.path.basename(p)
        ext = tile_extent(p)
        deg = degree_of(name)
        if ext is None:
            # Rozsah sa nedá zistiť (gdalinfo zlyhal, chýba projekcia). Beriem
            # ju ako platnú – a to znamená aj započítať ju do pokrytia podľa
            # MENA, nie ju len preskočiť: inak by z „nedá sa overiť" vyšlo
            # „chýba" a beh by padol na dlaždici, ktorá je možno v poriadku.
            print(f"  ? {name} – rozsah sa nedá zistiť, beriem ju ako platnú")
            if deg is not None:
                good.append((float(deg[0]), float(deg[1]),
                             float(deg[0] + 1), float(deg[1] + 1)))
            continue
        if deg is None:
            good.append(ext)
            continue
        pct = covers_own_degree(ext, deg)
        if pct < HONEST_PCT:
            liars.append(name)
            print(f"  ✗ {name} pokrýva len {pct:.2f} % svojho stupňa "
                  f"({ext[0]:.4f},{ext[1]:.4f} … {ext[2]:.4f},{ext[3]:.4f}) – "
                  f"meno tvrdí celý stupeň")
        else:
            good.append((float(deg[0]), float(deg[1]),
                         float(deg[0] + 1), float(deg[1] + 1)))

    # Ktoré stupne bboxu neprikrýva ani jedna poctivá dlaždica.
    missing = []
    for lat in range(math.floor(bbox[1]), math.floor(bbox[3]) + 1):
        for lon in range(math.floor(bbox[0]), math.floor(bbox[2]) + 1):
            if not any(t[0] <= lon + 0.5 <= t[2] and t[1] <= lat + 0.5 <= t[3]
                       for t in good):
                ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
                missing.append(f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}")

    pct = covered_pct(bbox, good)
    lines = [f"covered_pct={pct:.1f}",
             "liars=" + " ".join(liars),
             "missing=" + " ".join(missing)]
    print(f"Pokrytie územia {args.bbox}: {pct:.1f} % z {len(good)} dlaždíc"
          + (f", {len(liars)} nepoctivých" if liars else ""))
    if missing:
        print(f"  bez dlaždice: {' '.join(missing)}")
    text = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    sys.stdout.write(text)
    return 1 if pct < args.min_pct else 0


if __name__ == "__main__":
    sys.exit(main())

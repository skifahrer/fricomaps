#!/usr/bin/env python3
"""
Maska kraja: „patrí toto miesto (alebo táto dlaždica) do kraja?"

Pýtajú sa jej vrstvy z výškového modelu – tieňovanie sa podľa nej rozhoduje,
ktoré dlaždice vôbec kresliť, a vrstevnice so skalami ju dostanú ako `-cutline`
do gdalwarpu. Polygón vyrába `workers/plan/region-poly.py`.

BEZ SHAPELY, a je to zámer – tá istá úvaha ako vo `workers/dem/coverage.py`:
na otázku „je táto dlaždica v kraji?" stačí hrubá rastrová maska. Pri kraji
2,7° × 0,7° a mriežke 2048 buniek je bunka ~100 m, kým dlaždica na z14 má
~1,5 km, takže o zaradení dlaždice rozhoduje maska s rezervou. Presnosť na
pixel by nič nepriniesla: hranica má aj tak povolený presah pol dlaždice.

POL DLAŽDICE SMIE PREČNIEVAŤ. Dlaždica sa berie, keď sa jej okno ZVÄČŠENÉ
o pol svojej strany dotýka kraja. Bez tej rezervy by vrstva končila presne na
hranici a v mape by bola vidieť rovná hrana v mieste, kde ešte má byť terén;
s ňou hranica „presvitá" o pol dlaždice ďalej, čo je presne to, čo o mape
vyzerá prirodzene.

Použitie ako modul:
    m = mask_from_file("data/region.geojson", cells=2048)
    if m.touches(w, s, e, n): …
Alebo z príkazového riadka (kontrola a debug):
    python3 workers/lib/region-mask.py --poly=data/region.geojson \\
        --bbox=19.865,48.745,22.585,49.48 --zoom=14
"""
import argparse
import json
import sys


def rings_from_geojson(path):
    """GeoJSON → `[(prstenec, je_diera)]`; prstenec je zoznam `(lon, lat)`."""
    with open(path) as f:
        data = json.load(f)
    feats = (data.get("features") if data.get("type") == "FeatureCollection"
             else [data])
    out = []
    for feat in feats or []:
        geom = feat.get("geometry") if "geometry" in feat else feat
        if not geom:
            continue
        polys = ([geom.get("coordinates")] if geom.get("type") == "Polygon"
                 else geom.get("coordinates") or [])
        for poly in polys:
            for i, ring in enumerate(poly or []):
                pts = [(float(x), float(y)) for x, y in ring]
                if len(pts) >= 3:
                    out.append((pts, i > 0))     # prvý prstenec = obrys
    return out


def inside(rings, x, y):
    """Je bod v polygóne? Ray casting, diery odpočítané."""
    ok = False
    for ring, hole in rings:
        c = False
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                xx = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if x < xx:
                    c = not c
        if c:
            ok = not hole if not hole else False
            if hole:
                return False
    return ok


class Mask:
    """Rastrová maska kraja nad daným bboxom."""

    def __init__(self, rings, bbox, cells=2048):
        self.w, self.s, self.e, self.n = bbox
        self.rings = rings
        # Mriežka drží pomer strán, nech je bunka takmer kvadratická – inak by
        # bola v jednom smere desaťkrát hrubšia a rozhodovala by nerovnako.
        span_x, span_y = self.e - self.w, self.n - self.s
        if span_x <= 0 or span_y <= 0:
            raise ValueError(f"prázdny bbox {bbox}")
        self.nx = max(16, int(cells))
        self.ny = max(16, int(cells * span_y / span_x))
        self.dx, self.dy = span_x / self.nx, span_y / self.ny
        self.grid = bytearray(self.nx * self.ny)
        for j in range(self.ny):
            y = self.s + (j + 0.5) * self.dy
            row = j * self.nx
            for i in range(self.nx):
                if inside(rings, self.w + (i + 0.5) * self.dx, y):
                    self.grid[row + i] = 1
        self.hit = sum(self.grid)

    @property
    def pct(self):
        """Koľko percent bboxu je v kraji – to isté číslo ako v region-poly."""
        return 100.0 * self.hit / (self.nx * self.ny)

    def touches(self, w, s, e, n):
        """Dotýka sa okno kraja? Okno sa berie tak, ako prišlo (už zväčšené)."""
        if e < self.w or w > self.e or n < self.s or s > self.n:
            return False
        i0 = max(0, int((w - self.w) / self.dx))
        i1 = min(self.nx - 1, int((e - self.w) / self.dx))
        j0 = max(0, int((s - self.s) / self.dy))
        j1 = min(self.ny - 1, int((n - self.s) / self.dy))
        for j in range(j0, j1 + 1):
            row = j * self.nx
            if 1 in self.grid[row + i0:row + i1 + 1]:
                return True
        # Okno menšie než bunka masky (vysoké zoomy): rozhodne stred.
        return inside(self.rings, (w + e) / 2, (s + n) / 2)


def mask_from_file(path, bbox, cells=2048):
    return Mask(rings_from_geojson(path), bbox, cells)


def tile_box(z, x, y):
    """Okno dlaždice XYZ v stupňoch (lon/lat, Web Mercator)."""
    import math
    n = 2 ** z
    lon1 = x / n * 360.0 - 180.0
    lon2 = (x + 1) / n * 360.0 - 180.0
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat2 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon1, min(lat1, lat2), lon2, max(lat1, lat2)


def tile_touches(mask, z, x, y, grow=0.5):
    """Patrí dlaždica do kraja, keď smie prečnievať `grow` svojej strany?"""
    w, s, e, n = tile_box(z, x, y)
    gx, gy = (e - w) * grow, (n - s) * grow
    return mask.touches(w - gx, s - gy, e + gx, n + gy)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poly", required=True)
    ap.add_argument("--bbox", required=True, help="west,south,east,north")
    ap.add_argument("--zoom", type=int, default=14)
    ap.add_argument("--cells", type=int, default=2048)
    args = ap.parse_args()
    bbox = tuple(float(v) for v in args.bbox.split(","))
    m = mask_from_file(args.poly, bbox, args.cells)
    print(f"Maska kraja: {m.nx}×{m.ny} buniek, v kraji {m.pct:.1f} % bboxu")
    # Koľko dlaždíc na zoome padne mimo – to je to, čo sa už nebude počítať.
    import math
    n = 2 ** args.zoom
    def xt(lon):
        return int((lon + 180.0) / 360.0 * n)
    def yt(lat):
        r = math.radians(lat)
        return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n)
    x0, x1 = xt(bbox[0]), xt(bbox[2])
    y0, y1 = yt(bbox[3]), yt(bbox[1])
    vsetkych = (x1 - x0 + 1) * (y1 - y0 + 1)
    v_kraji = sum(1 for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)
                  if tile_touches(m, args.zoom, x, y))
    print(f"z{args.zoom}: {v_kraji} z {vsetkych} dlaždíc sa dotýka kraja "
          f"(mimo {vsetkych - v_kraji}, teda "
          f"{100 * (vsetkych - v_kraji) / vsetkych:.0f} % práce odpadne)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Polygón kraja (nie jeho obdĺžnik) do `data/region.geojson`.

PREČO. Vrstvy z výškového modelu – vrstevnice, skaly, tieňovanie – sa doteraz
počítali na BBOXE regiónu. Bbox Prešovského kraja je 19.865,48.745,22.585,49.48,
čo je obdĺžnik ~199×82 km = 16 300 km², kým samotný kraj má 8 973 km². Takmer
polovica práce teda padla mimo kraj – do susedných krajov, do Poľska, na Ukrajinu
a do Maďarska. A nie je to len práca navyše:

  * DMR 5.0 je LEN Slovensko, takže za hranicou je v modeli NODATA. Hranica
    dát a nodaty je pre `gdaldem slope` zvislá stena – sklon 90°. V behu
    31635772047 z toho vyšlo 13 403 km² „skalnej plochy" (bbox má 16 300 km²,
    čiže skalou bolo označené skoro celé územie), zlepovanie švov to nedalo
    dokopy a spadlo na náhradné riešenie s 375 nezlepenými plochami.
  * Tieňovanie tie prázdne miesta vykreslí ako biele, takže mapa má rovnú
    hranu tam, kde končia dáta.

Polygón sa neberie z OSM ani sa nekreslí ručne – **stiahne sa ten istý `.poly`,
ktorým je orezaný náš PBF** (openstreetmap.fr ich zverejňuje vedľa extraktov).
Vďaka tomu je mapa a jej výškové vrstvy orezané ROVNAKO; druhá definícia hranice
kraja by sa raz rozišla s tou prvou (pravidlo 1).

Formát `.poly` je textový: meno, potom bloky prstencov (`!` na začiatku mena
znamená dieru), každý blok končí `END` a celý súbor tiež.

SUROVÝ `.poly` SA ODKLADÁ TIEŽ (`--poly-out`), a nie je to duplicita: číta ho
Planetiler (`--polygon=…`, „emit any tile that intersects the shape"), takže
sa dlaždice mapy prestanú vyrábať na celom obdĺžniku bboxu. Prekladať ho späť
z GeoJSONu by znamenalo písať `.poly` druhýkrát a stačí, aby sa raz rozišiel
s tým, čím je orezaný PBF – preto sa ukladá presne to, čo prišlo zo servera.
Keď sa polygón stiahnuť nepodarí, `.poly` NEVZNIKNE (a `--polygon` sa
Planetileru nedá) – náhradný obdĺžnik z bboxu je presne to, čo Planetiler robí
aj bez neho, takže by len predstieral orez.

Použitie:
    python3 workers/plan/region-poly.py --region=presovsky --out=data/region.geojson
    python3 workers/plan/region-poly.py --region=presovsky --out=… --poly-out=data/region.poly
    python3 workers/plan/region-poly.py --region=presovsky --out=… --summary=$GITHUB_STEP_SUMMARY
"""
import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKERS = os.path.dirname(_HERE)
_DATA = os.path.join(_WORKERS, "data")

# Polygóny ležia vedľa extraktov, ale v inom priečinku a BEZ `-latest` v mene:
# `extracts/europe/slovakia/presovsky-latest.osm.pbf` → `polygons/europe/slovakia/presovsky.poly`.
POLY_BASE = os.environ.get("OSMFR_POLYGONS",
                           "https://download.openstreetmap.fr/polygons")


def regions(path=None):
    with open(path or os.path.join(_DATA, "regions.json")) as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def poly_url(reg):
    """URL `.poly` k prvému slugu regiónu, alebo `None` (Slovensko ako celok)."""
    osmfr = reg.get("osmfr") or {}
    slugs = osmfr.get("slugs") or []
    if not slugs:
        return None
    slug = slugs[0].removesuffix("-latest")
    return f"{POLY_BASE}/{osmfr.get('dir', '')}/{slug}.poly"


def parse_poly(text):
    """`.poly` → `[(prstenec, je_diera)]`, prstenec je zoznam `(lon, lat)`."""
    rings, ring, hole = [], None, False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "polygon":
            continue
        if line == "END":
            if ring is not None:
                if len(ring) >= 3:
                    rings.append((ring, hole))
                ring = None
            continue
        parts = line.split()
        if len(parts) == 2:
            try:
                lon, lat = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            if ring is None:
                ring, hole = [], False
            ring.append((lon, lat))
        else:
            # Meno prstenca – `!` znamená dieru.
            ring, hole = [], line.startswith("!")
    return rings


def ring_bbox(rings):
    xs = [x for ring, _ in rings for x, _ in ring]
    ys = [y for ring, _ in rings for _, y in ring]
    return min(xs), min(ys), max(xs), max(ys)


def ring_area_km2(ring):
    """Plocha prstenca v km² – rovinná aproximácia, stačí na pomer a výpis."""
    if len(ring) < 3:
        return 0.0
    lat0 = sum(y for _, y in ring) / len(ring)
    kx = 111.32 * math.cos(math.radians(lat0))
    ky = 110.57
    s = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        s += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
    return abs(s) / 2.0


def geojson(rings):
    """Prstence → GeoJSON. Diery idú k poslednému vonkajšiemu prstencu.

    Je to zjednodušenie: `.poly` neurčuje, ku ktorému obrysu diera patrí, a pri
    kraji je vonkajší prstenec jeden veľký. Keby ich bolo viac (ostrov), diera
    v nesprávnom polygóne by pri `-cutline` nič nepokazila – gdalwarp berie
    úniu, takže by len nebola dierou.
    """
    polys, holes = [], []
    for ring, hole in rings:
        closed = ring if ring[0] == ring[-1] else ring + [ring[0]]
        coords = [[round(x, 6), round(y, 6)] for x, y in closed]
        (holes if hole else polys).append(coords)
    if not polys:
        return None
    shapes = [[p] for p in polys]
    for h in holes:
        shapes[-1].append(h)
    geom = ({"type": "Polygon", "coordinates": shapes[0]} if len(shapes) == 1
            else {"type": "MultiPolygon", "coordinates": shapes})
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {}, "geometry": geom}]}


def bbox_rect(bbox):
    """Náhrada, keď polygón nie je: obdĺžnik z bboxu (teda dnešné správanie)."""
    w, s, e, n = bbox
    ring = [(w, s), (e, s), (e, n), (w, n), (w, s)]
    return [(list(ring), False)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True, help="kľúč z data/regions.json")
    ap.add_argument("--regions", default="", help="cesta k regions.json")
    ap.add_argument("--out", default="data/region.geojson")
    ap.add_argument("--poly-out", default="",
                    help="kam uložiť surový .poly (pre Planetiler --polygon)")
    ap.add_argument("--summary", default="", help="kam dopísať súhrn")
    args = ap.parse_args()

    regs = regions(args.regions or None)
    reg = regs.get(args.region)
    if not reg:
        print(f"::error::Neznámy región '{args.region}'. Známe: "
              f"{', '.join(sorted(regs))}", file=sys.stderr)
        return 1
    bbox = tuple(reg["bbox"])
    url = poly_url(reg)

    rings, zdroj, raw = None, "", ""
    if url:
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                raw = r.read().decode("utf-8", "replace")
            rings = parse_poly(raw)
            zdroj = url
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            # NIE JE TO CHYBA BEHU: bez polygónu sa dá počítať na bboxe ako
            # doteraz. Musí to ale byť nahlas – ticho by to znamenalo hodiny
            # počítania mimo kraj a rovnú hranu v tieňovaní.
            print(f"::warning::Polygón kraja sa nepodarilo stiahnuť "
                  f"({url}: {exc}) – vrstvy z DEM sa spočítajú na CELOM BBOXE "
                  f"regiónu, teda aj mimo kraj. Skús beh zopakovať.")
    if not rings:
        rings, zdroj, raw = bbox_rect(bbox), "bbox regiónu (polygón nie je)", ""

    data = geojson(rings)
    if not data:
        print("::error::Polygón kraja nemá ani jeden vonkajší prstenec.",
              file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(data, f)
    # Surový `.poly` pre Planetiler – len keď je naozaj zo servera (viď hlavička).
    if args.poly_out and raw:
        os.makedirs(os.path.dirname(args.poly_out) or ".", exist_ok=True)
        with open(args.poly_out, "w") as f:
            f.write(raw)

    pw, ps, pe, pn = ring_bbox(rings)
    plocha = sum(ring_area_km2(r) for r, hole in rings if not hole) \
        - sum(ring_area_km2(r) for r, hole in rings if hole)
    bw, bs, be, bn = bbox
    bbox_km2 = ring_area_km2([(bw, bs), (be, bs), (be, bn), (bw, bn)])
    podiel = 100 * plocha / bbox_km2 if bbox_km2 else 0
    prstencov = sum(1 for _, hole in rings if not hole)
    dier = sum(1 for _, hole in rings if hole)

    print(f"Polygón kraja: {args.out}")
    print(f"  zdroj                {zdroj}")
    if args.poly_out:
        print(f"  .poly pre Planetiler  "
              f"{args.poly_out if raw else 'NIE JE (dlaždice pôjdu na celom bboxe)'}")
    print(f"  prstencov            {prstencov} (+{dier} dier), "
          f"{sum(len(r) for r, _ in rings)} bodov")
    print(f"  bbox polygónu        {pw:.3f},{ps:.3f},{pe:.3f},{pn:.3f}")
    print(f"  bbox regiónu         {bw},{bs},{be},{bn}")
    # TOTO JE TO ČÍSLO, o ktoré ide: koľko práce padalo mimo kraj.
    print(f"  plocha kraja         {plocha:,.0f} km² z {bbox_km2:,.0f} km² "
          f"bboxu = {podiel:.0f} %")
    print(f"  mimo kraj            {bbox_km2 - plocha:,.0f} km² "
          f"({100 - podiel:.0f} % bboxu) sa už nepočíta")
    if args.summary:
        with open(args.summary, "a") as f:
            f.write(f"- **Orez na kraj**: {plocha:,.0f} km² z "
                    f"{bbox_km2:,.0f} km² bboxu ({podiel:.0f} %), "
                    f"{prstencov} prstenec/ov\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

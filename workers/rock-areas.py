#!/usr/bin/env python3
"""
DEM → skalné plochy ako vektor (GeoPackage).

„Husté vrstevnice = skala" je len iný pohľad na veľký sklon, ktorý navyše
závisí od intervalu vrstevníc a od zoomu. Skaly sa preto počítajú priamo zo
sklonu terénu:

    DEM → EPSG:3035 (metre) → gdaldem slope → mozaika sklonu →
    gdal_contour -p (izolínie sklonu ako PLOCHY) → rozbitie na plochy →
    filter najmenšej plochy → trieda `steep` / `cliff`

TVAR PLÔCH: obrys je izolínia sklonu, čiže presne tá čiara, kde terén
prekročí prah. Skala tak má taký tvar, aký naozaj má – zubatý pás pod
hrebeňom, oblúk okolo žľabu, ostrov brala v suti.

DIERY: kde je vnútri steny miesto s menším sklonom (police, terasa, zarastený
stupeň), vypadne z plochy **diera** – tá plocha sa nezafarbí, aj keď je
dookola všade nad prahom. Presne to robí `gdal_contour -p`: pásmo [prah, ∞)
je polygón s vnútornými prstencami tam, kde hodnota pod prah klesla.

PREČO SA VEKTORIZUJE NARAZ, A NIE PO ČASTIACH: keď sa každá časť územia
vektorizovala zvlášť a výsledky sa lepili (`-clipsrc` + `ST_Union`), diera
prerezaná hranicou časti sa zmenila na zárez v okraji a späť sa už nezlepila
– overené, z dvoch plôch s dierami vyšli štyri bez dier. Preto sa **po
častiach počíta len raster sklonu** (to je tá pamäťovo drahá časť), zapíše sa
na disk a `gdal_contour` potom ide **jedným priechodom nad celou mozaikou**.
Žiadne švy, žiadne zlepovanie, diery na správnych miestach.

Aby sa mozaika zmestila na disk, ukladá sa sklon ako **Byte s krokom 0,5°**
(hodnota = 2× stupne, 0–180). Float32 by bol 4× väčší a 0,5° je na prahovanie
viac než dosť – prahy sú aj tak celé stupne.

Aby sklon na okraji časti nebol zrezaný, každá sa počíta s presahom
niekoľkých pixelov a zapíše sa až orezaná presne na svoju hranicu. Hranice
častí sú prichytené na mriežku, takže dlaždice mozaiky na seba sadnú presne.

AKÝ JE TO DETAIL: obrys sleduje mriežku sklonu (`--res`), ale skutočný detail
nemôže byť lepší než zdrojový DEM – Sonny má pre Slovensko mriežku 20 m.
Jemnejšia mriežka teda robí obrys hladším a presnejšie umiestneným (sklon sa
medzi bunkami DEM interpoluje), nové detaily terénu však nevymyslí. Script to
na konci vypíše aj s rozmerom buniek DEM.

Použitie:
    python3 workers/rock-areas.py --dem=dem/all.vrt --bbox=W,S,E,N \\
        --res=2 --slope=50 --cliff=65 --min-area=4 --out=data/rock.gpkg
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

METRIC = "EPSG:3035"  # LAEA Európa – pre naše šírky skresľuje plochy minimálne
SCALE = 2  # sklon sa ukladá ako Byte v krokoch 0,5° (hodnota = 2× stupne)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def to_metric(bbox):
    """Bbox v stupňoch → rozsah v metroch (EPSG:3035)."""
    w, s, e, n = bbox
    pts = "\n".join(f"{x} {y}" for x, y in
                    [(w, s), (e, s), (w, n), (e, n), ((w + e) / 2, s), ((w + e) / 2, n)])
    out = run(["gdaltransform", "-s_srs", "EPSG:4326", "-t_srs", METRIC],
              input=pts).stdout.split()
    xs = [float(v) for v in out[0::3]]
    ys = [float(v) for v in out[1::3]]
    return min(xs), min(ys), max(xs), max(ys)


def dem_cell_metres(dem, lat):
    """Rozmer bunky zdrojového DEM v metroch – aby sa dal vypísať skutočný
    detail, nie len tá mriežka, na ktorej sa sklon počíta."""
    try:
        info = json.loads(run(["gdalinfo", "-json", dem]).stdout)
        gt = info["geoTransform"]
        wkt = info.get("coordinateSystem", {}).get("wkt", "")
        dx, dy = abs(gt[1]), abs(gt[5])
        if wkt.startswith("GEOGCRS") or wkt.startswith("GEOGCS"):
            return dx * 111320 * math.cos(math.radians(lat)), dy * 110540
        return dx, dy
    except Exception:
        return None, None


def ogr_count(path, layer="rock"):
    try:
        out = run(["ogrinfo", "-so", path, layer]).stdout
        for line in out.splitlines():
            if line.startswith("Feature Count"):
                return int(line.split(":")[1])
    except subprocess.CalledProcessError:
        pass
    return 0


def area_stats(metric_gpkg):
    """Počet plôch, celková/najväčšia/najmenšia/priemerná plocha v m² a koľko
    z nich ukrajujú diery.

    Počíta sa nad metrickou verziou, takže ST_Area vracia rovno metre
    štvorcové – v stupňoch by to bolo číslo bez významu.
    """
    sql = ("SELECT COUNT(*) AS n, SUM(ST_Area(geom)) AS total, "
           "MAX(ST_Area(geom)) AS amax, MIN(ST_Area(geom)) AS amin, "
           "AVG(ST_Area(geom)) AS aavg FROM rock")
    try:
        out = run(["ogr2ogr", "-f", "CSV", "/vsistdout/", metric_gpkg,
                   "-dialect", "SQLITE", "-sql", sql]).stdout.strip().splitlines()
        st = {k: float(v or 0) for k, v in
              zip(["n", "total", "max", "min", "avg"], out[1].split(","))}
    except Exception:
        return {}
    # Koľko plochy ukrajujú diery = plocha vonkajšieho obrysu mínus skutočná.
    try:
        sql2 = ("SELECT SUM(ST_Area(ST_Buildarea(ST_ExteriorRing(geom)))) AS outer_, "
                "SUM(CASE WHEN ST_NumInteriorRing(geom) > 0 THEN 1 ELSE 0 END) AS withholes "
                "FROM (SELECT ST_GeometryN(geom, 1) AS geom FROM rock)")
        out2 = run(["ogr2ogr", "-f", "CSV", "/vsistdout/", metric_gpkg,
                    "-dialect", "SQLITE", "-sql", sql2]).stdout.strip().splitlines()
        o, wh = out2[1].split(",")
        st["holes_km2"] = max(0.0, (float(o or 0) - st["total"]) / 1e6)
        st["with_holes"] = float(wh or 0)
    except Exception:
        pass
    return st


def slope_tiles(dem, x0, y0, x1, y1, res, chunk_cells, tmp):
    """Raster sklonu po častiach na disk (Byte, krok 0,5°) → zoznam dlaždíc.

    Toto je jediná časť, ktorá sa musí krájať: bbox kraja má pri 2 m vyše
    3 miliardy buniek, čo je vo Float32 ~13 GB na jeden raster. Vektorizuje
    sa až mozaika, naraz – inak by sa diery prerezané hranicou časti stratili.
    """
    # Hranice častí prichytené na mriežku, nech dlaždice mozaiky sadnú presne.
    snap = lambda v, up: (math.ceil(v / res) if up else math.floor(v / res)) * res
    x0, y0, x1, y1 = snap(x0, False), snap(y0, False), snap(x1, True), snap(y1, True)
    width_m, height_m = x1 - x0, y1 - y0

    side = math.sqrt(chunk_cells) * res
    nx = max(1, math.ceil(width_m / side))
    ny = max(1, math.ceil(height_m / side))
    step_x = math.ceil(width_m / nx / res) * res
    step_y = math.ceil(height_m / ny / res) * res
    margin = 8 * res  # presah, aby sklon na okraji časti nebol zrezaný

    total = (width_m / res) * (height_m / res)
    print(f"Územie {width_m/1000:.0f}×{height_m/1000:.0f} km, mriežka sklonu "
          f"{res:g} m → {total/1e6:.0f} mil. buniek, {nx}×{ny} častí "
          f"po {step_x/1000:.1f}×{step_y/1000:.1f} km", flush=True)

    tiles = []
    dem_tif = os.path.join(tmp, "chunk.tif")
    slope_tif = os.path.join(tmp, "slope.tif")
    for iy in range(ny):
        for ix in range(nx):
            cx0, cy0 = x0 + ix * step_x, y0 + iy * step_y
            cx1, cy1 = min(cx0 + step_x, x1), min(cy0 + step_y, y1)
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            out = os.path.join(tmp, f"slope-{iy:03d}-{ix:03d}.tif")
            for f in (dem_tif, slope_tif):
                if os.path.exists(f):
                    os.remove(f)

            run(["gdalwarp", "-q", "-overwrite", "-t_srs", METRIC,
                 "-te", repr(cx0 - margin), repr(cy0 - margin),
                 repr(cx1 + margin), repr(cy1 + margin),
                 "-tr", repr(res), repr(res), "-r", "cubicspline",
                 "-ot", "Float32", "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES",
                 "-multi", dem, dem_tif])
            run(["gdaldem", "slope", "-q", "-compute_edges",
                 "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES", dem_tif, slope_tif])
            # Presah preč a Float32 → Byte s krokom 0,5°: mozaika celého kraja
            # sa vo Float32 na disk runnera nezmestí.
            run(["gdal_translate", "-q", "-ot", "Byte",
                 "-scale", "0", repr(90.0), "0", repr(90.0 * SCALE),
                 "-projwin", repr(cx0), repr(cy1), repr(cx1), repr(cy0),
                 "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=2", "-co", "TILED=YES",
                 slope_tif, out])
            tiles.append(out)
            print(f"  [{len(tiles)}/{nx*ny}] sklon spočítaný", flush=True)

    for f in (dem_tif, slope_tif):
        if os.path.exists(f):
            os.remove(f)
    return tiles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dem", required=True)
    ap.add_argument("--bbox", required=True, help="west,south,east,north v stupňoch")
    ap.add_argument("--out", required=True, help="výstupný GeoPackage (vrstva rock)")
    ap.add_argument("--res", type=float, default=2.0, help="mriežka na sklon v metroch")
    ap.add_argument("--slope", type=float, default=50.0, help="prah sklonu v stupňoch")
    ap.add_argument("--cliff", type=float, default=65.0, help="prah triedy `cliff`")
    ap.add_argument("--min-area", type=float, default=4.0, help="najmenšia plocha v m²")
    ap.add_argument("--simplify", type=float, default=0.0, help="0 = presný obrys")
    ap.add_argument("--chunk-cells", type=float, default=150e6,
                    help="strop buniek na jednu časť pri počítaní sklonu")
    ap.add_argument("--stats", default="", help="kam zapísať štatistiku (key=value)")
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args()

    bbox = tuple(float(v) for v in args.bbox.split(","))
    x0, y0, x1, y1 = to_metric(bbox)
    res = args.res
    dem_dx, dem_dy = dem_cell_metres(args.dem, (bbox[1] + bbox[3]) / 2)
    if dem_dx:
        print(f"Zdrojový DEM má bunku ~{dem_dx:.0f}×{dem_dy:.0f} m – to je "
              f"strop skutočného detailu; mriežka {res:g} m len hladší obrys.")

    tmp = tempfile.mkdtemp(prefix="rock-", dir=os.path.dirname(args.out) or ".")
    try:
        # ---------- 1. sklon po častiach na disk ----------
        tiles = slope_tiles(args.dem, x0, y0, x1, y1, res, args.chunk_cells, tmp)
        if not tiles:
            print("::warning::Nepodarilo sa spočítať sklon ani pre jednu časť.")
            return 1
        mb = sum(os.path.getsize(t) for t in tiles) / 1048576
        print(f"Mozaika sklonu: {len(tiles)} dlaždíc, {mb:.0f} MB na disku")

        vrt = os.path.join(tmp, "slope.vrt")
        run(["gdalbuildvrt", "-q", vrt] + tiles)

        # ---------- 2. vektorizácia NARAZ nad celou mozaikou ----------
        # Jediný priechod = žiadne švy a diery ostanú dierami. Prahy sú
        # v jednotkách uloženého rastra (0,5° na krok).
        bands = os.path.join(tmp, "bands.gpkg")
        print("Vektorizujem sklon (jedným priechodom nad celým územím)…", flush=True)
        run(["gdal_contour", "-q", "-p",
             "-fl", repr(args.slope * SCALE), repr(args.cliff * SCALE),
             "-amin", "smin", "-amax", "smax",
             "-f", "GPKG", "-nln", "band", vrt, bands])

        for t in tiles:  # mozaika je vyše gigabajtu, ďalej ju netreba
            os.remove(t)

        # ---------- 3. rozbitie na plochy ----------
        # gdal_contour zlepí každé pásmo do jedného multipolygónu; bez
        # rozbitia by sa nedala merať plocha jednotlivej skaly. Diery
        # rozbitie NErieši – vnútorné prstence ostávajú v svojej ploche.
        exploded = os.path.join(tmp, "rock-exploded.gpkg")
        lo, hi = int(args.slope), int(args.cliff)
        run(["ogr2ogr", "-f", "GPKG", exploded, bands, "band", "-nln", "rock",
             "-dialect", "SQLITE",
             "-sql", f"SELECT CASE WHEN smin >= {args.cliff * SCALE} THEN 'cliff' "
                     f"ELSE 'steep' END AS class, geom FROM band "
                     f"WHERE smin >= {args.slope * SCALE}",
             "-explodecollections", "-nlt", "POLYGON"])
        os.remove(bands)
        if ogr_count(exploded) == 0:
            print("::warning::Nenašla sa ani jedna plocha nad prahom sklonu.")
            return 1

        # ---------- 4. filter najmenšej plochy + atribúty ----------
        # Diery sa NEZAPĹŇAJÚ ani nefiltrujú: presne o ne ide. Miesto pod
        # prahom vnútri steny má ostať nezafarbené, aj keď je dookola všade
        # sklon nad prahom.
        stage = exploded
        final_metric = os.path.join(tmp, "rock-final.gpkg")
        sql = (f"SELECT class, CASE WHEN class = 'cliff' THEN {hi} ELSE {lo} END "
               f"AS slope, CAST(ST_Area(geom) AS INTEGER) AS area, geom "
               f"FROM rock WHERE ST_Area(geom) >= {args.min_area}")
        simplify = ["-simplify", repr(args.simplify)] if args.simplify else []
        try:
            run(["ogr2ogr", "-f", "GPKG", final_metric, stage, "-nln", "rock",
                 "-dialect", "SQLITE", "-sql", sql] + simplify)
        except subprocess.CalledProcessError:
            print("::warning::Filter najmenšej plochy (ST_Area) nefunguje – "
                  "skaly idú bez neho.")
            sql = sql.replace(f" WHERE ST_Area(geom) >= {args.min_area}", "")
            sql = sql.replace("CAST(ST_Area(geom) AS INTEGER) AS area, ", "")
            run(["ogr2ogr", "-f", "GPKG", final_metric, stage, "-nln", "rock",
                 "-dialect", "SQLITE", "-sql", sql] + simplify)

        st = area_stats(final_metric)
        run(["ogr2ogr", "-f", "GPKG", args.out, final_metric, "-nln", "rock",
             "-overwrite", "-t_srs", "EPSG:4326"])
        n = int(st.get("n", ogr_count(args.out)))
        print(f"Skalných plôch: {n}")
        if st:
            print(f"  spolu {st['total']/1e6:.2f} km², najväčšia "
                  f"{st['max']/10000:.1f} ha, najmenšia {st['min']:.0f} m², "
                  f"priemer {st['avg']:.0f} m²")
            if "holes_km2" in st:
                print(f"  dier (miest pod prahom vnútri skaly): "
                      f"{int(st['with_holes'])} plôch ich má, "
                      f"vykrojených {st['holes_km2']:.2f} km²")

        if args.stats:
            with open(args.stats, "w") as f:
                f.write(f"count={n}\n")
                f.write(f"grid_m={res:g}\n")
                f.write(f"min_area_m2={args.min_area:g}\n")
                f.write(f"slope_deg={lo}\ncliff_deg={hi}\n")
                f.write(f"slope_step_deg={1.0/SCALE:g}\n")
                if dem_dx:
                    f.write(f"dem_cell_m={dem_dx:.0f}\n")
                if st:
                    f.write(f"total_km2={st['total']/1e6:.2f}\n")
                    f.write(f"max_ha={st['max']/10000:.1f}\n")
                    f.write(f"min_m2={st['min']:.0f}\n")
                    f.write(f"avg_m2={st['avg']:.0f}\n")
                if "holes_km2" in st:
                    f.write(f"with_holes={int(st['with_holes'])}\n")
                    f.write(f"holes_km2={st['holes_km2']:.2f}\n")
        return 0
    finally:
        if not args.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

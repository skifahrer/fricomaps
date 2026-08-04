#!/usr/bin/env python3
"""
DEM → skalné plochy ako vektor (GeoPackage), počítané po častiach.

„Husté vrstevnice = skala" je len iný pohľad na veľký sklon, ktorý navyše
závisí od intervalu vrstevníc a od zoomu. Skaly sa preto počítajú priamo zo
sklonu terénu:

    DEM → EPSG:3035 (metre) → gdaldem slope → gdal_contour -p (izolínie
    sklonu ako PLOCHY) → zlúčenie → rozbitie na samostatné plochy →
    filter najmenšej plochy → trieda `steep` / `cliff`

TVAR PLÔCH: obrys je izolínia sklonu, čiže presne tá čiara, kde terén
prekročí prah. Skala tak má taký tvar, aký naozaj má – zubatý pás pod
hrebeňom, oblúk okolo žľabu, ostrov brala v suti. Žiadna mriežka, žiadne
štvorčeky.

ZLUČOVANIE: územie sa počíta po častiach (nižšie), takže jedna stena môže
vyjsť ako niekoľko kusov zrezaných na hranici časti. Na konci sa preto všetko
v rámci triedy zlúči (`ST_Union`) a hneď rozbije späť na samostatné plochy
(`-explodecollections`). Čo spolu súvisí, je jeden polygón; čo spolu
nesúvisí, ostáva samostatné.

PREČO PO ČASTIACH: pri jemnej mriežke je raster so sklonom obrovský. Bbox
kraja má pri 2 m vyše 3 miliardy buniek, čo je ~13 GB na jeden raster – viac,
než má runner miesta aj pamäte. Územie sa preto krája na dlaždice (default
~150 mil. buniek na kus), každá sa spracuje samostatne a hneď po sebe upratá.
Čas rastie lineárne, pamäť ani disk nie.

Aby sklon na okraji dlaždice nebol zrezaný, každá sa počíta s presahom
niekoľkých pixelov a výsledné plochy sa orežú presne na jej hranicu
(`-clipsrc`). Susedné kusy tak na seba nadväzujú bez medzery aj bez prekryvu.

AKÝ JE TO DETAIL: obrys sleduje mriežku sklonu (`--res`, default 2 m), ale
skutočný detail nemôže byť lepší než zdrojový DEM – Sonny má pre Slovensko
mriežku 20 m. Jemnejšia mriežka teda robí obrys hladším a presnejšie
umiestneným (sklon sa medzi bunkami DEM interpoluje), nové detaily terénu
však nevymyslí. Script to na konci vypíše aj s rozmerom buniek DEM.

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
    """Počet plôch, celková/najväčšia/najmenšia/priemerná plocha v m².

    Počíta sa nad metrickou verziou, takže ST_Area vracia rovno metre
    štvorcové – v stupňoch by to bolo číslo bez významu.
    """
    sql = ("SELECT COUNT(*) AS n, SUM(ST_Area(geom)) AS total, "
           "MAX(ST_Area(geom)) AS amax, MIN(ST_Area(geom)) AS amin, "
           "AVG(ST_Area(geom)) AS aavg FROM rock")
    try:
        out = run(["ogr2ogr", "-f", "CSV", "/vsistdout/", metric_gpkg,
                   "-dialect", "SQLITE", "-sql", sql]).stdout.strip().splitlines()
        vals = out[1].split(",")
        return {k: float(v or 0) for k, v in
                zip(["n", "total", "max", "min", "avg"], vals)}
    except Exception:
        return {}


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
                    help="strop buniek na jednu časť (pamäť a disk)")
    ap.add_argument("--dissolve", default="true",
                    help="zlúčiť susediace plochy do väčších polygónov")
    ap.add_argument("--stats", default="", help="kam zapísať štatistiku (key=value)")
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args()

    bbox = tuple(float(v) for v in args.bbox.split(","))
    x0, y0, x1, y1 = to_metric(bbox)
    res = args.res
    width_m, height_m = x1 - x0, y1 - y0
    total_cells = (width_m / res) * (height_m / res)
    dem_dx, dem_dy = dem_cell_metres(args.dem, (bbox[1] + bbox[3]) / 2)

    # Štvorcové časti tak, aby sa každá zmestila do stropu buniek.
    side = math.sqrt(args.chunk_cells) * res
    nx = max(1, math.ceil(width_m / side))
    ny = max(1, math.ceil(height_m / side))
    step_x, step_y = width_m / nx, height_m / ny
    margin = 8 * res  # presah, aby sklon na okraji časti nebol zrezaný

    print(f"Územie {width_m/1000:.0f}×{height_m/1000:.0f} km, mriežka sklonu "
          f"{res} m → {total_cells/1e6:.0f} mil. buniek, {nx}×{ny} častí "
          f"po {step_x/1000:.1f}×{step_y/1000:.1f} km")
    if dem_dx:
        print(f"Zdrojový DEM má bunku ~{dem_dx:.0f}×{dem_dy:.0f} m – to je "
              f"strop skutočného detailu; mriežka {res} m len hladší obrys.")

    tmp = tempfile.mkdtemp(prefix="rock-", dir=os.path.dirname(args.out) or ".")
    metric_gpkg = os.path.join(tmp, "rock-metric.gpkg")
    done = 0
    try:
        for iy in range(ny):
            for ix in range(nx):
                cx0, cy0 = x0 + ix * step_x, y0 + iy * step_y
                cx1, cy1 = cx0 + step_x, cy0 + step_y
                dem_tif = os.path.join(tmp, "chunk.tif")
                slope_tif = os.path.join(tmp, "slope.tif")
                band_gpkg = os.path.join(tmp, "band.gpkg")
                for f in (dem_tif, slope_tif, band_gpkg):
                    if os.path.exists(f):
                        os.remove(f)

                run(["gdalwarp", "-q", "-overwrite", "-t_srs", METRIC,
                     "-te", repr(cx0 - margin), repr(cy0 - margin),
                     repr(cx1 + margin), repr(cy1 + margin),
                     "-tr", repr(res), repr(res), "-r", "cubicspline",
                     "-ot", "Float32", "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES",
                     "-multi", args.dem, dem_tif])
                run(["gdaldem", "slope", "-q", "-compute_edges",
                     "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES", dem_tif, slope_tif])

                # Izolínie sklonu ako plochy: pásmo [slope, cliff) a [cliff, ∞).
                # Obrys je presne tá čiara, kde terén prekročí prah – teda
                # skutočný tvar skaly, nie mriežka.
                run(["gdal_contour", "-q", "-p",
                     "-fl", repr(args.slope), repr(args.cliff),
                     "-amin", "smin", "-amax", "smax",
                     "-f", "GPKG", "-nln", "band", slope_tif, band_gpkg])

                # Orez presne na časť (bez presahu), nech na seba susedné kusy
                # nadväzujú bez prekryvu. Zlepenie rieši dissolve na konci.
                cmd = ["ogr2ogr", "-f", "GPKG", metric_gpkg, band_gpkg, "band",
                       "-nln", "rock", "-nlt", "MULTIPOLYGON",
                       "-where", f"smin >= {args.slope}",
                       "-clipsrc", repr(cx0), repr(cy0), repr(cx1), repr(cy1)]
                cmd += ["-append"] if os.path.exists(metric_gpkg) else []
                run(cmd)

                done += 1
                print(f"  [{done}/{nx*ny}] hotových častí", flush=True)

        if not os.path.exists(metric_gpkg):
            print("::warning::Nenašla sa ani jedna plocha nad prahom sklonu.")
            return 1

        # ---------- zlúčenie ----------
        # ST_Union v rámci triedy: čo sa dotýka (aj cez hranicu časti), splynie
        # do jedného polygónu. Hneď potom -explodecollections, takže z jedného
        # multipolygónu na triedu vypadnú samostatné skaly.
        stage = metric_gpkg
        if str(args.dissolve).lower() in ("1", "true", "yes"):
            merged = os.path.join(tmp, "rock-merged.gpkg")
            sql = (f"SELECT CASE WHEN smin >= {args.cliff} THEN 'cliff' ELSE 'steep' END "
                   f"AS class, ST_Union(geom) AS geom FROM rock GROUP BY 1")
            try:
                run(["ogr2ogr", "-f", "GPKG", merged, metric_gpkg, "-nln", "rock",
                     "-dialect", "SQLITE", "-sql", sql,
                     "-explodecollections", "-nlt", "POLYGON"])
                stage = merged
                print(f"Zlúčené do súvislých plôch: {ogr_count(merged)}")
            except subprocess.CalledProcessError as exc:
                print("::warning::Zlučovanie plôch (ST_Union) zlyhalo – plochy "
                      "ostanú tak, ako vyšli po častiach.")
                print(exc.stderr[-800:] if exc.stderr else "")

        if stage is metric_gpkg:
            # Bez dissolve treba aspoň rozbiť multipolygóny na kusy, inak by
            # sa nedala merať plocha jednotlivej skaly.
            exploded = os.path.join(tmp, "rock-exploded.gpkg")
            run(["ogr2ogr", "-f", "GPKG", exploded, metric_gpkg, "-nln", "rock",
                 "-dialect", "SQLITE",
                 "-sql", f"SELECT CASE WHEN smin >= {args.cliff} THEN 'cliff' "
                         f"ELSE 'steep' END AS class, geom FROM rock",
                 "-explodecollections", "-nlt", "POLYGON"])
            stage = exploded

        # ---------- filter najmenšej plochy + atribúty ----------
        # Až tu, nad hotovými plochami: plocha sa počíta v metroch a je to
        # jeden priechod.
        final_metric = os.path.join(tmp, "rock-final.gpkg")
        lo = int(args.slope)
        hi = int(args.cliff)
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
        if st:
            print(f"Skalných plôch: {n:,}".replace(",", " "))
            print(f"  spolu {st['total']/1e6:.2f} km², najväčšia "
                  f"{st['max']/10000:.1f} ha, najmenšia {st['min']:.0f} m², "
                  f"priemer {st['avg']:.0f} m²")
        else:
            print(f"Skalných plôch: {n}")

        if args.stats:
            with open(args.stats, "w") as f:
                f.write(f"count={n}\n")
                f.write(f"grid_m={res:g}\n")
                f.write(f"min_area_m2={args.min_area:g}\n")
                f.write(f"slope_deg={lo}\ncliff_deg={hi}\n")
                if dem_dx:
                    f.write(f"dem_cell_m={dem_dx:.0f}\n")
                if st:
                    f.write(f"total_km2={st['total']/1e6:.2f}\n")
                    f.write(f"max_ha={st['max']/10000:.1f}\n")
                    f.write(f"min_m2={st['min']:.0f}\n")
                    f.write(f"avg_m2={st['avg']:.0f}\n")
        return 0
    finally:
        if not args.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

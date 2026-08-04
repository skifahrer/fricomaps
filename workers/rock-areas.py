#!/usr/bin/env python3
"""
DEM → skalné plochy ako vektor (GeoPackage), počítané po častiach.

„Husté vrstevnice = skala" je len iný pohľad na veľký sklon, ktorý navyše
závisí od intervalu vrstevníc a od zoomu. Skaly sa preto počítajú priamo zo
sklonu terénu:

    DEM → EPSG:3035 (metre) → gdaldem slope → gdal_contour -p (izolínie
    sklonu ako PLOCHY) → rozbitie na kusy → filter najmenšej plochy → class

PREČO PO ČASTIACH: pri jemnej mriežke je raster so sklonom obrovský. Bbox
kraja má pri 2 m vyše 3 miliardy buniek, čo je ~13 GB na jeden raster – viac,
než má runner miesta aj pamäte. Územie sa preto krája na dlaždice (default
~150 mil. buniek na kus), každá sa spracuje samostatne a hneď po sebe upratá.
Čas rastie lineárne, pamäť ani disk nie.

Aby sklon na okraji dlaždice nebol zrezaný, každá sa počíta s presahom
niekoľkých pixelov a výsledné plochy sa orežú presne na jej hranicu
(`-clipsrc`). Susedné kusy tak na seba nadväzujú bez medzery aj bez prekryvu.

Použitie:
    python3 workers/rock-areas.py --dem=dem/all.vrt --bbox=W,S,E,N \\
        --res=2 --slope=40 --cliff=55 --min-area=1 --out=data/rock.gpkg
"""
import argparse
import csv
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


def pieces_from_slope(slope_tif, piece, fill, lo, hi, out_csv, clip):
    """Zo sklonu spraví jeden malý polygón na každú bunku P×P nad prahom.

    Súvislá stena je inak jedna obrovská plocha; takto z nej vznikne mriežka
    samostatných kúskov, ktoré sa v mape čítajú ako skalné šrafovanie.
    `fill` < 1 kúsok zmenší dovnútra bunky, takže susedné sa nedotýkajú a
    medzi nimi presvitá podklad.
    """
    import numpy as np
    agg = slope_tif.replace(".tif", f"-p{int(piece)}.tif")
    subprocess.run(["gdalwarp", "-q", "-overwrite", "-tr", repr(piece), repr(piece),
                    "-r", "average", "-co", "COMPRESS=DEFLATE", slope_tif, agg],
                   check=True, capture_output=True)
    info = subprocess.run(["gdalinfo", "-json", agg], check=True,
                          capture_output=True, text=True).stdout
    import json as _json
    gt = _json.loads(info)["geoTransform"]
    w, h = _json.loads(info)["size"]
    raw = agg.replace(".tif", ".raw")
    subprocess.run(["gdal_translate", "-q", "-of", "ENVI", "-ot", "Float32", agg, raw],
                   check=True, capture_output=True)
    a = np.fromfile(raw, dtype="<f4").reshape(h, w)

    cx0, cy0, cx1, cy1 = clip
    half = piece * fill / 2.0
    rows = np.nonzero(a >= lo)
    n = 0
    with open(out_csv, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["wkt", "slope", "class"])
        for iy, ix in zip(*rows):
            x = gt[0] + (ix + 0.5) * gt[1]
            y = gt[3] + (iy + 0.5) * gt[5]
            if not (cx0 <= x < cx1 and cy0 <= y < cy1):
                continue  # kúsky mimo časti spraví susedná časť
            v = float(a[iy, ix])
            wr.writerow([
                f"POLYGON(({x-half} {y-half},{x+half} {y-half},"
                f"{x+half} {y+half},{x-half} {y+half},{x-half} {y-half}))",
                int(v), "cliff" if v >= hi else "steep",
            ])
            n += 1
    for f in (agg, raw, raw + ".hdr", agg.replace(".tif", ".raw.aux.xml")):
        if os.path.exists(f):
            os.remove(f)
    return n


def ogr_count(path, layer="rock"):
    try:
        out = run(["ogrinfo", "-so", path, layer]).stdout
        for line in out.splitlines():
            if line.startswith("Feature Count"):
                return int(line.split(":")[1])
    except subprocess.CalledProcessError:
        pass
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dem", required=True)
    ap.add_argument("--bbox", required=True, help="west,south,east,north v stupňoch")
    ap.add_argument("--out", required=True, help="výstupný GeoPackage (vrstva rock)")
    ap.add_argument("--res", type=float, default=2.0, help="mriežka na sklon v metroch")
    ap.add_argument("--slope", type=float, default=40.0, help="prah sklonu v stupňoch")
    ap.add_argument("--cliff", type=float, default=55.0, help="prah triedy `cliff`")
    ap.add_argument("--min-area", type=float, default=1.0, help="najmenšia plocha v m²")
    ap.add_argument("--simplify", type=float, default=0.0, help="0 = presný obrys")
    ap.add_argument("--chunk-cells", type=float, default=150e6,
                    help="strop buniek na jednu časť (pamäť a disk)")
    ap.add_argument("--piece", type=float, default=0.0,
                    help="rozdeliť skaly na kúsky P×P metrov (0 = súvislé plochy)")
    ap.add_argument("--piece-fill", type=float, default=0.8,
                    help="akú časť bunky kúsok vyplní (1 = celý štvorec)")
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args()

    bbox = tuple(float(v) for v in args.bbox.split(","))
    x0, y0, x1, y1 = to_metric(bbox)
    res = args.res
    width_m, height_m = x1 - x0, y1 - y0
    total_cells = (width_m / res) * (height_m / res)

    # Štvorcové časti tak, aby sa každá zmestila do stropu buniek.
    side = math.sqrt(args.chunk_cells) * res
    nx = max(1, math.ceil(width_m / side))
    ny = max(1, math.ceil(height_m / side))
    step_x, step_y = width_m / nx, height_m / ny
    margin = 8 * res  # presah, aby sklon na okraji časti nebol zrezaný

    print(f"Územie {width_m/1000:.0f}×{height_m/1000:.0f} km, mriežka {res} m "
          f"→ {total_cells/1e6:.0f} mil. buniek, {nx}×{ny} častí "
          f"po {step_x/1000:.1f}×{step_y/1000:.1f} km")

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
                if args.piece > 0:
                    # Malé kúsky: jeden polygón na bunku P×P nad prahom.
                    csv_path = os.path.join(tmp, "pieces.csv")
                    made = pieces_from_slope(
                        slope_tif, args.piece, args.piece_fill,
                        args.slope, args.cliff, csv_path,
                        (cx0, cy0, cx1, cy1))
                    if made:
                        cmd = ["ogr2ogr", "-f", "GPKG", metric_gpkg, csv_path,
                               "-nln", "rock", "-a_srs", METRIC,
                               "-oo", "GEOM_POSSIBLE_NAMES=wkt",
                               "-oo", "AUTODETECT_TYPE=YES", "-lco", "GEOMETRY_NAME=geom"]
                        cmd += ["-append"] if os.path.exists(metric_gpkg) else []
                        run(cmd)
                    os.path.exists(csv_path) and os.remove(csv_path)
                else:
                    run(["gdal_contour", "-q", "-p",
                         "-fl", repr(args.slope), repr(args.cliff),
                         "-amin", "smin", "-amax", "smax",
                         "-f", "GPKG", "-nln", "band", slope_tif, band_gpkg])

                    # Rozbitie pásiem na jednotlivé plochy + orez presne na
                    # časť. gdal_contour zlepí každé pásmo do jedného
                    # multipolygónu, takže bez -explodecollections by sa
                    # nedala merať plocha jednotlivej skaly.
                    cmd = ["ogr2ogr", "-f", "GPKG", metric_gpkg, band_gpkg, "band",
                           "-nln", "rock", "-explodecollections",
                           "-where", f"smin >= {args.slope}",
                           "-clipsrc", repr(cx0), repr(cy0), repr(cx1), repr(cy1)]
                    cmd += ["-append"] if os.path.exists(metric_gpkg) else []
                    run(cmd)

                done += 1
                print(f"  [{done}/{nx*ny}] {ogr_count(metric_gpkg)} plôch spolu",
                      flush=True)

        if not os.path.exists(metric_gpkg):
            print("::warning::Nenašla sa ani jedna plocha nad prahom sklonu.")
            return 1

        # Filter najmenšej plochy a triedy až nakoniec – nad hotovou vrstvou
        # je to jeden priechod a plocha sa počíta v metroch, nie v stupňoch.
        if args.piece > 0:
            run(["ogr2ogr", "-f", "GPKG", args.out, metric_gpkg, "-nln", "rock",
                 "-overwrite", "-t_srs", "EPSG:4326"])
            print(f"Skalných kúskov: {ogr_count(args.out)} "
                  f"(po {args.piece:.0f}×{args.piece:.0f} m)")
            return 0

        final_metric = os.path.join(tmp, "rock-final.gpkg")
        sql = (f"SELECT *, CAST(smin AS INTEGER) AS slope, "
               f"CASE WHEN smin >= {args.cliff} THEN 'cliff' ELSE 'steep' END AS class "
               f"FROM rock WHERE ST_Area(geom) >= {args.min_area}")
        simplify = ["-simplify", repr(args.simplify)] if args.simplify else []
        try:
            run(["ogr2ogr", "-f", "GPKG", final_metric, metric_gpkg, "-nln", "rock",
                 "-dialect", "SQLITE", "-sql", sql] + simplify)
        except subprocess.CalledProcessError:
            print("::warning::Filter najmenšej plochy (ST_Area) nefunguje – "
                  "skaly idú bez neho.")
            sql = sql.split(" WHERE ")[0]
            run(["ogr2ogr", "-f", "GPKG", final_metric, metric_gpkg, "-nln", "rock",
                 "-dialect", "SQLITE", "-sql", sql] + simplify)

        run(["ogr2ogr", "-f", "GPKG", args.out, final_metric, "-nln", "rock",
             "-overwrite", "-t_srs", "EPSG:4326"])
        n = ogr_count(args.out)
        print(f"Skalných plôch: {n}")
        return 0
    finally:
        if not args.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

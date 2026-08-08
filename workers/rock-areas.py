#!/usr/bin/env python3
"""
DEM → skalné plochy ako vektor (GeoPackage).

„Husté vrstevnice = skala" je len iný pohľad na veľký sklon, ktorý navyše
závisí od intervalu vrstevníc a od zoomu. Skaly sa preto počítajú priamo zo
sklonu terénu:

    DEM → EPSG:3035 (metre) → gdaldem slope → mozaika sklonu →
    gdal_contour -p (izolínie sklonu ako PLOCHY) → rozbitie na plochy →
    filter najmenšej plochy → jedna trieda, diery ostávajú

TVAR PLÔCH: obrys je izolínia sklonu, čiže presne tá čiara, kde terén
prekročí prah. Skala tak má taký tvar, aký naozaj má – zubatý pás pod
hrebeňom, oblúk okolo žľabu, ostrov brala v suti.

JEDNA TRIEDA (predvolene, `--plne`): von ide jedno pásmo [prah, ∞), teda
žiadna plocha vnútri inej plochy. `--plne=0` vráti aj druhé pásmo `cliff`
(od `--cliff`), ktoré leží v diere pásma `steep`.

DIERY: kde je vnútri steny miesto s menším sklonom (police, terasa, zarastený
stupeň), vypadne z plochy **diera** – tá plocha sa nezafarbí, aj keď je
dookola všade nad prahom. Presne to robí `gdal_contour -p`: pásmo [prah, ∞) je
polygón s vnútornými prstencami tam, kde hodnota pod prah klesla. Práve tie
diery robia tvar skaly čitateľným; `--zapln-diery=1` ich zaplní a zo skál
budú súvislé klaksy.

PREČO SA VEKTORIZUJE NARAZ, A NIE PO ČASTIACH: keď sa každá časť územia
vektorizovala zvlášť a výsledky sa lepili (`-clipsrc` + `ST_Union`), diera
prerezaná hranicou časti sa zmenila na zárez v okraji a späť sa už nezlepila
– overené, z dvoch plôch s dierami vyšli štyri bez dier. Preto sa **po
častiach počíta len raster sklonu** (to je tá pamäťovo drahá časť), zapíše sa
na disk a `gdal_contour` potom ide **jedným priechodom nad celou mozaikou**.
Žiadne švy, žiadne zlepovanie, diery na správnych miestach.

Sklon sa ukladá ako **Int16 v stotinách stupňa**. Byte s krokom 0,5° by bol
polovičný, ale robil obrys zubatý: pri hrubom kroku vznikajú v poli sklonu
plošiny a izolínia po nich chodí po hranách buniek, teda schodíkmi. Int16
0,01° dáva prakticky zhodný výsledok ako presný Float32 pri štvrtinovej
veľkosti rastra.

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
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

METRIC = "EPSG:3035"  # LAEA Európa – pre naše šírky skresľuje plochy minimálne
# Sklon sa ukladá ako Int16 v stotinách stupňa. Predtým to bol Byte s krokom
# 0,5° a práve ten robil obrys zubatý: pri hrubom kroku vznikajú v poli sklonu
# plošiny a izolínia po nich chodí po hranách buniek, teda schodíkmi. Namerané
# na tom istom území – Byte 0,5° dal 481 plôch a 844 bodov na plochu,
# Int16 0,01° dal 319 plôch a 1328 bodov, čo je zhodné s presným Float32
# (321 plôch, 1320 bodov) pri štvrtinovej veľkosti rastra.
SCALE = 100

# Namerané na GitHub runneri (ubuntu-latest, 4 jadrá). Slúžia len na odhad
# dopredu – aby sa dalo povedať „toto potrvá tri hodiny" PRED tým, než sa tri
# hodiny minú, nie po nich.
# Slope: 170 častí / 23,1 mld. buniek za 75 min v behu 30948662582.
SLOPE_CELLS_PER_S = 5.1e6    # gdalwarp + gdaldem slope + gdal_translate
# Contour: ten istý beh mal po 105 min ešte nedokončených 23,1 mld., takže
# rýchlosť je NAJVIAC 3,7 mil./s. Berieme 3,5 – odhad má radšej prestreliť.
CONTOUR_CELLS_PER_S = 3.5e6  # gdal_contour -p nad hotovou mozaikou
# Ten istý beh na OOM NEspadol, čiže pri 23,1 mld. buniek bol pod 16 GB.
# Pamäť teda nie je to, o čo sa zadanie zabije – zabije sa o čas.
CONTOUR_MB_PER_GCELL = 700   # špička pamäte gdal_contour na miliardu buniek
MOSAIC_MB_PER_GCELL = 240    # Int16 + DEFLATE + PREDICTOR (Byte bol 50, ale zubatý)


# Tep, progress GDALu a meranie sú vo `watch.py` – používajú ich aj kroky
# workflowu, tak nech je to jedna implementácia a nie dve, ktoré sa časom
# rozídu.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from watch import hms, dir_mb, Heartbeat, run_watched  # noqa: E402


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


def chunk_plan(x0, y0, x1, y1, res, chunk_cells, bbox, side_m=0):
    """Rozdelenie na časti + zoznam tých, ktoré naozaj ležia v území.

    EPSG:3035 je pootočená voči poludníkom, takže obdĺžnik opísaný bboxu
    regiónu je v metroch výrazne väčší než samotný región – pri Prešovskom
    kraji 208×111 km namiesto 200×82 km, teda o tretinu buniek navyše.
    Časti, ktoré do bboxu vôbec nezasahujú, sa preto preskočia.

    `side_m` prebije veľkosť časti (v metroch). Používa to `pick_res`, ktorý
    potrebuje len zistiť, koľko plochy naozaj leží v území – nezávisle od
    toho, akú jemnú mriežku nakoniec vyberie.
    """
    snap = lambda v, up: (math.ceil(v / res) if up else math.floor(v / res)) * res
    x0, y0, x1, y1 = snap(x0, False), snap(y0, False), snap(x1, True), snap(y1, True)
    width_m, height_m = x1 - x0, y1 - y0

    side = side_m or math.sqrt(chunk_cells) * res
    nx = max(1, math.ceil(width_m / side))
    ny = max(1, math.ceil(height_m / side))
    step_x = math.ceil(width_m / nx / res) * res
    step_y = math.ceil(height_m / ny / res) * res

    chunks = []
    for iy in range(ny):
        for ix in range(nx):
            cx0, cy0 = x0 + ix * step_x, y0 + iy * step_y
            cx1, cy1 = min(cx0 + step_x, x1), min(cy0 + step_y, y1)
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            chunks.append((iy, ix, cx0, cy0, cx1, cy1))

    keep = [c for c in chunks if intersects_bbox(c[2], c[3], c[4], c[5], bbox)]
    cells = sum(((c[4] - c[2]) / res) * ((c[5] - c[3]) / res) for c in keep)
    return keep, len(chunks), cells, (nx, ny, step_x, step_y, width_m, height_m)


def intersects_bbox(cx0, cy0, cx1, cy1, bbox):
    """Zasahuje časť (v metroch) do bboxu územia (v stupňoch)?"""
    pts = "\n".join(f"{x} {y}" for x, y in
                     [(cx0, cy0), (cx1, cy0), (cx0, cy1), (cx1, cy1),
                      ((cx0 + cx1) / 2, cy0), ((cx0 + cx1) / 2, cy1),
                      (cx0, (cy0 + cy1) / 2), (cx1, (cy0 + cy1) / 2)])
    try:
        out = run(["gdaltransform", "-s_srs", METRIC, "-t_srs", "EPSG:4326"],
                  input=pts).stdout.split()
    except subprocess.CalledProcessError:
        return True  # keď sa to nedá zistiť, radšej počítať než vynechať
    xs = [float(v) for v in out[0::3]]
    ys = [float(v) for v in out[1::3]]
    return not (max(xs) < bbox[0] or min(xs) > bbox[2]
                or max(ys) < bbox[1] or min(ys) > bbox[3])


# Z čoho `--res=auto` vyberá. Jemnejšie než 0,5 m nemá zmysel ani pri 1 m
# LiDARe – obrys by sa už len leštil a buniek by pribudlo štvornásobne.
RES_LADDER = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0, 15.0, 20.0)


def pick_res(x0, y0, x1, y1, chunk_cells, bbox, budget_min, dem_cell_m):
    """Najjemnejšia mriežka, ktorá sa ešte zmestí do rozpočtu času.

    „Čo najpodrobnejšie" nie je jedno číslo: pre jedno pohorie sa zmestí
    polmetrová mriežka, pre celý kraj ani dvojmetrová. Namiesto toho, aby
    to musel užívateľ hádať (a buď dostal hrubé skaly, alebo beh, ktorý
    padne na timeout), sa to spočíta – z toho istého odhadu, ktorý potom
    strážia rozpočtové hlášky.

    Dva stropy zdola:
      * desatina bunky zdrojového DEM – jemnejšia mriežka už nové detaily
        terénu nevymyslí, len interpoluje medzi tými istými výškami,
      * 0,5 m absolútne.
    """
    # Koľko plochy naozaj leží v území, zistené na hrubom rastri častí –
    # nezávisí to od mriežky, tak sa to počíta raz a lacno.
    side = max(2000.0, math.sqrt((x1 - x0) * (y1 - y0) / 50.0))
    probe, _, _, _ = chunk_plan(x0, y0, x1, y1, 10.0, chunk_cells, bbox,
                                side_m=side)
    area_m2 = sum((c[4] - c[2]) * (c[5] - c[3]) for c in probe)
    if not area_m2:
        return RES_LADDER[3]  # nič sa netrafilo – nech to povie až chunk_plan

    floor = max(0.5, round((dem_cell_m or 0) / 10.0, 1))
    per_s = 1.0 / (1.0 / SLOPE_CELLS_PER_S + 1.0 / CONTOUR_CELLS_PER_S)
    budget_s = budget_min * 60 if budget_min else float("inf")

    print("── Výber mriežky (rock_res=auto) ────────────────────")
    print(f"  plocha územia   {area_m2/1e6:.0f} km²")
    if dem_cell_m:
        print(f"  bunka DEM       {dem_cell_m:.0f} m → jemnejšie než "
              f"{floor:g} m nemá zmysel")
    else:
        print(f"  bunka DEM       neznáma → dolný strop {floor:g} m")
    chosen = None
    for res in RES_LADDER:
        if res < floor:
            continue
        est = area_m2 / (res * res) / per_s
        fits = est <= budget_s
        print(f"  {res:>4g} m  {area_m2/(res*res)/1e9:6.2f} mld. buniek  "
              f"~{hms(est)}  {'✓' if fits else '× nad rozpočet'}")
        if fits and chosen is None:
            chosen = res
    if chosen is None:
        chosen = RES_LADDER[-1]
        print(f"::warning::Ani najhrubšia mriežka {chosen:g} m sa do rozpočtu "
              f"{hms(budget_s)} nezmestí – skús menší výrez (input „area“).")
    print(f"  vybrané         {chosen:g} m")
    print("─────────────────────────────────────────────────────", flush=True)
    return chosen


def print_plan(cells, res, n_chunks, n_all, geom, budget_min):
    """Čo to bude stáť – PRED tým, než sa to začne počítať.

    Trojhodinový beh, ktorý spadne na timeout, je najhorší možný výsledok:
    minie celý rozpočet a nevyrobí nič. Toto to povie za pár sekúnd.
    """
    nx, ny, step_x, step_y, width_m, height_m = geom
    slope_s = cells / SLOPE_CELLS_PER_S
    contour_s = cells / CONTOUR_CELLS_PER_S
    total_s = slope_s + contour_s
    mosaic_mb = cells / 1e9 * MOSAIC_MB_PER_GCELL
    peak_mb = cells / 1e9 * CONTOUR_MB_PER_GCELL

    print("── Plán výpočtu skál ────────────────────────────────")
    print(f"  územie          {width_m/1000:.0f}×{height_m/1000:.0f} km "
          f"(obdĺžnik v EPSG:3035)")
    print(f"  mriežka         {res:g} m")
    print(f"  buniek          {cells/1e9:.2f} mld.")
    print(f"  častí           {n_chunks} z {n_all} "
          f"({n_all - n_chunks} mimo územia sa preskočí), "
          f"po {step_x/1000:.1f}×{step_y/1000:.1f} km")
    print(f"  odhad sklon     {hms(slope_s)}")
    print(f"  odhad obrysy    {hms(contour_s)}")
    print(f"  odhad SPOLU     {hms(total_s)}  (rozpočet {hms(budget_min * 60)})")
    print(f"  mozaika na disk ~{mosaic_mb/1024:.1f} GB")
    print(f"  špička pamäte   ~{peak_mb/1024:.1f} GB")
    print("─────────────────────────────────────────────────────", flush=True)

    if budget_min and total_s > budget_min * 60:
        # Čo by sa zmestilo: čas rastie lineárne s počtom buniek a ten
        # kvadraticky s jemnosťou mriežky, takže hrubšia mriežka pomôže
        # druhou mocninou, menšie územie priamo úmerne.
        share = budget_min * 60 / total_s
        ok_res = res / math.sqrt(share)
        print(f"::error::Skaly by trvali {hms(total_s)} ({cells/1e9:.2f} mld. "
              f"buniek), rozpočet je {hms(budget_min * 60)}. Celý job má na "
              f"runneri 3 hodiny a musí sa doň zmestiť aj mapa – takto by "
              f"spadol na timeout a nevyrobil NIČ.")
        print(f"::error::Zmestí sa: (1) rock_res aspoň "
              f"{math.ceil(ok_res * 10) / 10:g} m na tomto území, alebo "
              f"(2) rock_area na výrez s ~{share*100:.0f} % plochy – napr. "
              f"vysoke_tatry, tatry, slovensky_raj (viď workers/areas.json). "
              f"Rozpočet sa dá zdvihnúť cez ROCK_BUDGET_MIN v env.")
        return False
    return True


def slope_tiles(dem, chunks, res, tmp, heartbeat_every):
    """Raster sklonu po častiach na disk (Byte, krok 0,5°) → zoznam dlaždíc.

    Toto je jediná časť, ktorá sa musí krájať: bbox kraja má pri 2 m miliardy
    buniek, čo je vo Float32 desiatky GB na jeden raster. Vektorizuje sa až
    mozaika, naraz – inak by sa diery prerezané hranicou časti stratili.
    """
    margin = 8 * res  # presah, aby sklon na okraji časti nebol zrezaný
    tiles = []
    dem_tif = os.path.join(tmp, "chunk.tif")
    slope_tif = os.path.join(tmp, "slope.tif")
    t0 = time.time()
    hb = Heartbeat("sklon", tmp=tmp, every=heartbeat_every)
    hb.start()
    try:
        for iy, ix, cx0, cy0, cx1, cy1 in chunks:
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
            run(["gdal_translate", "-q", "-ot", "Int16",
                 "-scale", "0", repr(90.0), "0", repr(90.0 * SCALE),
                 "-projwin", repr(cx0), repr(cy1), repr(cx1), repr(cy0),
                 "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=2", "-co", "TILED=YES",
                 slope_tif, out])
            tiles.append(out)

            done, total = len(tiles), len(chunks)
            elapsed = time.time() - t0
            eta = elapsed / done * (total - done)
            print(f"  [{done}/{total}] sklon – {hms(elapsed)} za sebou, "
                  f"zostáva ~{hms(eta)}, mozaika {dir_mb(tmp):.0f} MB", flush=True)
    finally:
        hb.stop()

    for f in (dem_tif, slope_tif):
        if os.path.exists(f):
            os.remove(f)
    return tiles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dem", required=True)
    ap.add_argument("--bbox", required=True, help="west,south,east,north v stupňoch")
    ap.add_argument("--out", required=True, help="výstupný GeoPackage (vrstva rock)")
    ap.add_argument("--res", default="auto",
                    help="mriežka na sklon v metroch, alebo `auto` = "
                         "najjemnejšia, ktorá sa zmestí do rozpočtu času")
    ap.add_argument("--slope", type=float, default=50.0, help="prah sklonu v stupňoch")
    ap.add_argument("--cliff", type=float, default=65.0,
                    help="prah triedy `cliff` (použije sa len bez `--plne`)")
    # Plné plochy: jedno pásmo a zaplnené diery. Dokopy z toho je „jedna
    # skala = jedna sivá plocha", nič v ničom a nič presvitajúce.
    ap.add_argument("--plne", type=int, default=1,
                    help="1 = jedno pásmo a jedna trieda (žiadna plocha "
                         "vnútri inej), 0 = pásma steep/cliff ako predtým")
    ap.add_argument("--zapln-diery", type=int, default=0,
                    help="1 = zaplniť diery (súvislé plochy namiesto tvaru)")
    ap.add_argument("--min-area", type=float, default=-1.0,
                    help="najmenšia plocha v m²; -1 = jedna bunka mriežky "
                         "(menší útvar už nie je tvar terénu, ale jedna bunka)")
    ap.add_argument("--simplify", type=float, default=-1.0,
                    help="tolerancia zjednodušenia obrysu v metroch; "
                         "-1 = štvrtina mriežky (odstráni schodíky), 0 = vypnuté")
    ap.add_argument("--smooth", type=int, default=2,
                    help="koľkokrát zaobliť rohy obrysu (Chaikin); "
                         "0 = vypnuté, 2 = odporúčané")
    ap.add_argument("--chunk-cells", type=float, default=150e6,
                    help="strop buniek na jednu časť pri počítaní sklonu")
    ap.add_argument("--budget-min", type=float, default=100.0,
                    help="koľko minút smie výpočet trvať; nad odhad sa "
                         "nezačne počítať a povie sa, čo zmenšiť (0 = bez stropu)")
    ap.add_argument("--max-rss-gb", type=float, default=12.0,
                    help="strop pamäte pre gdal_contour (0 = bez stropu)")
    ap.add_argument("--heartbeat", type=float, default=30.0,
                    help="ako často hlásiť, že sa stále počíta (s)")
    ap.add_argument("--stats", default="", help="kam zapísať štatistiku (key=value)")
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args()

    bbox = tuple(float(v) for v in args.bbox.split(","))
    x0, y0, x1, y1 = to_metric(bbox)
    dem_dx, dem_dy = dem_cell_metres(args.dem, (bbox[1] + bbox[3]) / 2)

    if str(args.res).strip().lower() in ("auto", "", "0"):
        res = pick_res(x0, y0, x1, y1, args.chunk_cells, bbox,
                       args.budget_min, dem_dx)
    else:
        res = float(args.res)
    # Štvrtina bunky: zmaže schodíky po hranách buniek, ale obrys neposunie
    # o viac než štvrtinu mriežky. Namerané: bodov na obrys klesne 5,7×
    # (423 763 → 74 395) a počet plôch sa nezmení vôbec. Ostré rohy, ktoré
    # po ňom ostanú, zaobli `--smooth` na konci.
    if args.simplify < 0:
        args.simplify = res / 4.0
    # Najmenšia skala = jedna bunka mriežky. Pri `--res=auto` sa mriežka
    # vyberá až tu, takže sa to nedá spočítať v shelli pred spustením.
    if args.min_area < 0:
        args.min_area = round(res * res, 2)
    if dem_dx:
        print(f"Zdrojový DEM má bunku ~{dem_dx:.0f}×{dem_dy:.0f} m – to je "
              f"strop skutočného detailu; mriežka {res:g} m len hladší obrys.")

    # Plán a strážca ešte pred prvým gdalwarpom: trojhodinový beh, ktorý
    # spadne na timeout, je horší než beh, ktorý sa vôbec nezačne.
    chunks, n_all, cells, geom = chunk_plan(x0, y0, x1, y1, res,
                                            args.chunk_cells, bbox)
    if not chunks:
        print("::error::Ani jedna počítaná časť nezasahuje do územia "
              f"{args.bbox} – skontroluj bbox.")
        return 2
    if not print_plan(cells, res, len(chunks), n_all, geom, args.budget_min):
        return 2

    t_start = time.time()
    tmp = tempfile.mkdtemp(prefix="rock-", dir=os.path.dirname(args.out) or ".")
    try:
        # ---------- 1. sklon po častiach na disk ----------
        tiles = slope_tiles(args.dem, chunks, res, tmp, args.heartbeat)
        if not tiles:
            print("::warning::Nepodarilo sa spočítať sklon ani pre jednu časť.")
            return 1
        mb = sum(os.path.getsize(t) for t in tiles) / 1048576
        print(f"Mozaika sklonu: {len(tiles)} dlaždíc, {mb:.0f} MB na disku, "
              f"sklon trval {hms(time.time() - t_start)}")

        vrt = os.path.join(tmp, "slope.vrt")
        run(["gdalbuildvrt", "-q", vrt] + tiles)

        # ---------- 2. vektorizácia NARAZ nad celou mozaikou ----------
        # Jediný priechod = žiadne švy a diery ostanú dierami. Prahy sú
        # v jednotkách uloženého rastra (0,5° na krok).
        bands = os.path.join(tmp, "bands.gpkg")
        print(f"Vektorizujem sklon jedným priechodom nad celým územím "
              f"({cells/1e9:.2f} mld. buniek, odhad "
              f"{hms(cells / CONTOUR_CELLS_PER_S)})…", flush=True)
        # PLNÉ PLOCHY (`--plne`, predvolene zapnuté): jediná úroveň, teda
        # jediné pásmo „sklon nad prahom". Druhá úroveň (`cliff`) mala zmysel,
        # kým sa kreslila tmavšie – ležala v diere pásma `steep` a spolu
        # dláždili územie bez prekryvu. Odkedy sú všetky plochy jedna sivá bez
        # priehľadnosti, je z nej len dvojnásobok prstencov na obtiahnutie.
        urovne = ([repr(args.slope * SCALE)] if args.plne else
                  [repr(args.slope * SCALE), repr(args.cliff * SCALE)])
        try:
            run_watched(["gdal_contour", "-p", "-fl"] + urovne +
                        ["-amin", "smin", "-amax", "smax",
                         "-f", "GPKG", "-nln", "band", vrt, bands],
                        "gdal_contour", tmp=tmp,
                        max_rss_mb=args.max_rss_gb * 1024)
        except MemoryError:
            print("::error::Vektorizácia sa nezmestila do pamäte. Zmenši "
                  "územie cez rock_area alebo zvoľ hrubšiu mriežku rock_res.")
            return 2

        for t in tiles:  # mozaika je vyše gigabajtu, ďalej ju netreba
            os.remove(t)

        # ---------- 3. rozbitie na plochy ----------
        # gdal_contour zlepí každé pásmo do jedného multipolygónu; bez
        # rozbitia by sa nedala merať plocha jednotlivej skaly. Diery
        # rozbitie NErieši – vnútorné prstence ostávajú v svojej ploche.
        exploded = os.path.join(tmp, "rock-exploded.gpkg")
        lo, hi = int(args.slope), int(args.cliff)
        trieda = ("'steep' AS class" if args.plne else
                  f"CASE WHEN smin >= {args.cliff * SCALE} THEN 'cliff' "
                  f"ELSE 'steep' END AS class")
        run(["ogr2ogr", "-f", "GPKG", exploded, bands, "band", "-nln", "rock",
             "-dialect", "SQLITE",
             "-sql", f"SELECT {trieda}, geom FROM band "
                     f"WHERE smin >= {args.slope * SCALE}",
             "-explodecollections", "-nlt", "POLYGON"])
        os.remove(bands)
        if ogr_count(exploded) == 0:
            print("::warning::Nenašla sa ani jedna plocha nad prahom sklonu.")
            return 1

        # ---------- 4. filter najmenšej plochy + atribúty ----------
        # DIERY OSTÁVAJÚ: miesto pod prahom vnútri steny (polica, terasa,
        # zarastený stupeň) sa nezafarbí, aj keď je dookola všade sklon nad
        # prahom. Práve ony robia tvar skaly čitateľným.
        #
        # `--zapln-diery=1` ich zaplní (von ide len vonkajší prstenec). Bolo
        # to kedysi súčasťou `--plne` a bola to chyba – zo skál vyšli súvislé
        # klaksy, v ktorých nebolo vidieť žiaden detail.
        stage = exploded
        final_metric = os.path.join(tmp, "rock-final.gpkg")
        geom = ("ST_BuildArea(ST_ExteriorRing(geom))"
                if args.zapln_diery else "geom")
        sql = (f"SELECT class, CASE WHEN class = 'cliff' THEN {hi} ELSE {lo} END "
               f"AS slope, CAST(ST_Area({geom}) AS INTEGER) AS area, "
               f"{geom} AS geom "
               f"FROM rock WHERE ST_Area({geom}) >= {args.min_area}")
        simplify = ["-simplify", repr(args.simplify)] if args.simplify else []
        try:
            run(["ogr2ogr", "-f", "GPKG", final_metric, stage, "-nln", "rock",
                 "-dialect", "SQLITE", "-sql", sql] + simplify)
        except subprocess.CalledProcessError:
            # `ST_BuildArea` je zo spatialite a nemusí byť. Skaly s dierami sú
            # lepšie než žiadne skaly, tak sa najprv skúsi vynechať zapĺňanie
            # a až potom celý filter.
            if args.zapln_diery:
                print("::warning::Zapĺňanie dier (ST_BuildArea) nefunguje – "
                      "spatialite pravdepodobne chýba. Skaly idú s dierami.")
                geom = "geom"
                sql = (f"SELECT class, CASE WHEN class = 'cliff' THEN {hi} "
                       f"ELSE {lo} END AS slope, "
                       f"CAST(ST_Area(geom) AS INTEGER) AS area, geom "
                       f"FROM rock WHERE ST_Area(geom) >= {args.min_area}")
            try:
                run(["ogr2ogr", "-f", "GPKG", final_metric, stage, "-nln",
                     "rock", "-dialect", "SQLITE", "-sql", sql] + simplify)
            except subprocess.CalledProcessError:
                print("::warning::Filter najmenšej plochy (ST_Area) nefunguje – "
                      "skaly idú bez neho.")
                sql = sql.replace(f" WHERE ST_Area(geom) >= {args.min_area}", "")
                sql = sql.replace("CAST(ST_Area(geom) AS INTEGER) AS area, ", "")
                run(["ogr2ogr", "-f", "GPKG", final_metric, stage, "-nln",
                     "rock", "-dialect", "SQLITE", "-sql", sql] + simplify)

        # ---------- 5. zaoblenie obrysu ----------
        # Zjednodušenie vyššie zmaže schodíky, ale to, čo po ňom ostane, sú
        # ostré rohy – priemerný lom medzi segmentmi vyskočí zo 4,6° na 28,5°
        # a práve tak vyzerá skala pri max zoome „zubatá". Chaikin ich zaobli
        # (2 prechody → 7,7°). Robí sa to ešte v metroch, aby tolerancie
        # sedeli, a pred prepočtom do EPSG:4326.
        if args.smooth > 0:
            smoothed = os.path.join(tmp, "rock-smooth.gpkg")
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "smooth-polygons.py")
            try:
                out = run([sys.executable, script, f"--in={final_metric}",
                           f"--out={smoothed}", "--layer=rock",
                           f"--passes={args.smooth}"])
                print(out.stdout.rstrip(), flush=True)
                final_metric = smoothed
            except subprocess.CalledProcessError as exc:
                print("::warning::Zaoblenie obrysu zlyhalo, skaly idú zubaté: "
                      f"{(exc.stderr or '').strip()[:300]}")

        st = area_stats(final_metric)
        run(["ogr2ogr", "-f", "GPKG", args.out, final_metric, "-nln", "rock",
             "-overwrite", "-t_srs", "EPSG:4326"])
        n = int(st.get("n", ogr_count(args.out)))
        took = time.time() - t_start
        print(f"Skalných plôch: {n} (celý výpočet {hms(took)}, "
              f"{cells/1e9:.2f} mld. buniek → "
              f"{cells/max(took, 1)/1e6:.1f} mil. buniek/s)")
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
                # Odkiaľ skaly sú. Súhrn buildu podľa toho vyberá tabuľku –
                # skaly z tieňovaných dlaždíc (workers/shading-rocks.py)
                # nemajú ani sklon, ani mriežku.
                f.write("source=dem\n")
                f.write(f"count={n}\n")
                f.write(f"grid_m={res:g}\n")
                f.write(f"min_area_m2={args.min_area:g}\n")
                f.write(f"slope_deg={lo}\ncliff_deg={hi}\n")
                f.write(f"plne={int(bool(args.plne))}\n")
                f.write(f"zapln_diery={int(bool(args.zapln_diery))}\n")
                f.write(f"slope_step_deg={1.0/SCALE:g}\n")
                f.write(f"simplify_m={args.simplify:g}\n")
                f.write(f"smooth_passes={args.smooth}\n")
                f.write(f"cells_g={cells/1e9:.2f}\n")
                f.write(f"took={hms(took)}\n")
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

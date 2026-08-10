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

SKLON SEM CHODÍ HOTOVÝ. Počíta ho `workers/slope-chunks.py` po častiach
absolútnej mriežky a ukladá ich do trvalého skladu (cache + release), takže
zrušený beh o hotové časti nepríde a ďalší dopočíta len zvyšok. Tu ostáva len
ten jeden priechod, ktorý sa deliť nedá. Vedľajší zisk: zmena prahu `--slope`
už NEznamená nové čítanie DEM – prahy sa uplatňujú až tu.

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

Použitie (mriežku aj mozaiku dáva slope-chunks.py, tak sa musia podať):
    python3 workers/slope-chunks.py --bbox=W,S,E,N --res=auto --drive \\
        --out=slope-chunks --stats=slope.txt
    python3 workers/rock-areas.py --slope-vrt=slope-chunks/slope-r2.vrt \\
        --bbox=W,S,E,N --res=2 --slope=50 --cliff=65 --out=data/rock.gpkg
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
# Contour: cena `gdal_contour -p` ide so ZDROJOVÝMI bunkami – s tým, koľko ich
# prečíta – a NIE s tým, na akú mriežku trasuje. Sú to dve merania toho istého
# územia (Vysoké Tatry, 689 km², sklad na 1 m), ktoré sa líšia len mriežkou
# trasovania:
#
#   beh          trasuje sa na   buniek trasovania   celkom   zdrojových buniek/s
#   31357217326  1 m             0,71 mld.           97 min   123 tis.
#   31360120952  2 m             0,18 mld.           98 min   121 tis.
#
# Štvrtina buniek na trasovanie, ROVNAKÝ čas – a zhoda v prepočte na zdrojové
# bunky je na 1,5 %. Predtým tu stál model, ktorý cenu viazal na mriežku
# trasovania (`res^1,42`); tie dva riadky ho vyvracajú a je preč. Predtým tu
# stála konštanta 3,5 mil./s a bola 29× vedľa.
#
# ČO Z TOHO PLYNIE PRE ZADANIE: obrys sa nezlacní tým, že sa trasuje hrubšie,
# ale tým, že je hrubší SKLAD. `pick_res` preto účtuje vektorizáciu mriežke
# skladu – to je jediné číslo, ktoré s ňou naozaj hýbe.
#
# DVA BODY SÚ DVA BODY. Je to jedno územie a jeden typ terénu; presné číslo
# príde vždy až z percent počas behu (`watch.py`) a keď sa s ním beh rozíde
# viac než 3×, povie to na konci sám – a vtedy sa toto číslo má prepísať.
CONTOUR_SRC_CELLS_PER_S = 1.2e5
# Ten istý beh na OOM NEspadol, čiže pri 23,1 mld. buniek bol pod 16 GB.
# Pamäť teda nie je to, o čo sa zadanie zabije – zabije sa o čas.
CONTOUR_MB_PER_GCELL = 700   # špička pamäte gdal_contour na miliardu buniek
MOSAIC_MB_PER_GCELL = 240    # Int16 + DEFLATE + PREDICTOR (Byte bol 50, ale zubatý)


# Tep, progress GDALu a meranie sú vo `watch.py` – používajú ich aj kroky
# workflowu, tak nech je to jedna implementácia a nie dve, ktoré sa časom
# rozídu.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from watch import hms, run_watched  # noqa: E402


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


# Z čoho `--res=auto` vyberá. Najjemnejšie je 1 m: ani 1 m LiDAR pod to nedá
# nový detail (len interpoluje) a pixel dlaždice má pri z16 aj tak 1,57 m.
# Polmetrová priečka tu bola a stála štvornásobok buniek za nič – viď pick_res.
RES_LADDER = (1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0, 15.0, 20.0)

# Podlaha mriežky, na ktorej sa VEKTORIZUJE. Pixel dlaždice má pri z16 – kam
# skaly naozaj idú – 1,57 m, takže obrys trasovaný jemnejšie sa v mape nemá ako
# zobraziť a nesie len body, ktoré `--simplify` aj tak zmaže.
#
# ČAS TO NEUŠETRÍ – to je zmerané a stálo to jeden beh (viď
# `CONTOUR_SRC_CELLS_PER_S`). Zmysel má, lebo výstup je menší a pamäť nižšia;
# na dĺžku behu je páka inde, v hrubšom sklade.
VEC_FLOOR_M = 1.6


def pick_vec_res(res, floor=VEC_FLOOR_M):
    """Mriežka vektorizácie: najjemnejšia, ktorú je ešte vidieť – ale nikdy
    jemnejšia než uložený sklon.

    ČAS TÝMTO NEUŠETRÍŠ, a je to zmerané: beh 31360120952 trasoval na 2 m
    namiesto 1 m, buniek bolo štyrikrát menej a trvalo to rovnako (viď
    `CONTOUR_SRC_CELLS_PER_S`). Ostáva to tu preto, že menší výstup je menej
    pamäte a menej bodov na obrys, ktoré by aj tak zmazal `--simplify` –
    ale rozpočet sa tým NERIEŠI a nesmie sa tak tváriť. Na čas je jediná
    páka hrubší sklad (`rock_res`).
    """
    for r in RES_LADDER:
        if r < res or r < floor:
            continue
        return r
    return max(res, RES_LADDER[-1])


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
      * 1 m absolútne.

    TEN ABSOLÚTNY STROP BOL 0,5 m A PRI DMR 5.0 TO BOLA CHYBA. Model má bunku
    1 m, takže z `max(0.5, 0.1)` vyšlo 0,5 m – dvojnásobné prevzorkovanie
    v každej osi, čiže ŠTVORNÁSOBOK buniek, ktoré nenesú ani o jeden meter
    terénu viac. Pri z18 má pixel dlaždice 0,39 m a pri z16 (kam skaly naozaj
    idú) 1,57 m, takže tá polovica metra nie je vidieť ani teoreticky.
    Zaplatilo sa za ňu ale plnou cenou: beh 31334778253 strávil na 2 km²
    štvrť hodiny a nedošiel ani do tretiny.

    Hladší obrys, kvôli ktorému to prevzorkovanie bolo, robia `--simplify`
    a `--smooth` (Chaikin) za zlomok ceny – zaoblujú hotové čiary, nie milióny
    buniek navyše.

    Pre ostatné zdroje sa nemení nič: `dmr35` (10 m) mal aj má 1 m, `sonny`
    (20 m) mal aj má 2 m – tam strop drží desatina bunky, nie toto číslo.
    """
    # Koľko plochy naozaj leží v území, zistené na hrubom rastri častí –
    # nezávisí to od mriežky, tak sa to počíta raz a lacno.
    side = max(2000.0, math.sqrt((x1 - x0) * (y1 - y0) / 50.0))
    probe, _, _, _ = chunk_plan(x0, y0, x1, y1, 10.0, chunk_cells, bbox,
                                side_m=side)
    area_m2 = sum((c[4] - c[2]) * (c[5] - c[3]) for c in probe)
    if not area_m2:
        return RES_LADDER[3]  # nič sa netrafilo – nech to povie až chunk_plan

    floor = max(1.0, round((dem_cell_m or 0) / 10.0, 1))
    budget_s = budget_min * 60 if budget_min else float("inf")

    print("── Výber mriežky (rock_res=auto) ────────────────────")
    print(f"  plocha územia   {area_m2/1e6:.0f} km²")
    if dem_cell_m:
        print(f"  bunka DEM       {dem_cell_m:.0f} m → jemnejšie než "
              f"{floor:g} m nemá zmysel")
    else:
        print(f"  bunka DEM       neznáma → dolný strop {floor:g} m")
    # Dve polovice, dva riadky. Jedno číslo za obe tu bolo, kým sa obe počítali
    # na tej istej mriežke – a skrývalo, že drahšia je tá druhá: pri 1 m stojí
    # sklon 2 minúty a vektorizácia hodinu a pol. Kým to bolo zlepené, nedalo
    # sa z tabuľky prísť na to, čo vlastne zdvojnásobenie mriežky ušetrí.
    chosen = None
    for res in RES_LADDER:
        if res < floor:
            continue
        vec = pick_vec_res(res)
        cells = area_m2 / (res * res)
        s_slope = cells / SLOPE_CELLS_PER_S
        # Vektorizácia sa účtuje TEJTO mriežke, nie tej, na ktorú sa trasuje:
        # `gdal_contour` prečíta zdrojové bunky tak či tak a zaplatí za ne.
        s_vec = cells / CONTOUR_SRC_CELLS_PER_S
        est = s_slope + s_vec
        fits = est <= budget_s
        print(f"  {res:>4g} m  {cells/1e9:5.2f} mld.  sklon ~{hms(s_slope)}"
              f"  + vektory ~{hms(s_vec)} (trasuje sa na {vec:g} m)"
              f"  = ~{hms(est)}  {'✓' if fits else '× nad rozpočet'}")
        if fits and chosen is None:
            chosen = res
    if chosen is None:
        chosen = RES_LADDER[-1]
        print(f"::warning::Ani najhrubšia mriežka {chosen:g} m sa do rozpočtu "
              f"{hms(budget_s)} nezmestí – skús menší výrez (input „area“).")
    print(f"  vybrané         {chosen:g} m")
    print("─────────────────────────────────────────────────────", flush=True)
    return chosen


def mosaic_cells(vrt):
    """Koľko buniek má hotová mozaika sklonu – na odhad času vektorizácie."""
    try:
        info = json.loads(run(["gdalinfo", "-json", vrt]).stdout)
        w, h = info["size"]
        return float(w) * float(h)
    except Exception:
        return 0.0


def mosaic_info(vrt):
    """(šírka, výška, rozsah v metroch, počet zdrojov) hotovej mozaiky."""
    try:
        info = json.loads(run(["gdalinfo", "-json", vrt]).stdout)
        w, h = info["size"]
        gt = info["geoTransform"]
        x0, y1 = gt[0], gt[3]
        x1, y0 = x0 + gt[1] * w, y1 + gt[5] * h
        try:
            zdroje = open(vrt).read().count("<SourceFilename")
        except OSError:
            zdroje = 0
        return int(w), int(h), (x0, y0, x1, y1), zdroje
    except Exception:
        return 0, 0, None, 0


def clip_vrt(vrt, box, res, tmp, src_res=0.0):
    """Mozaika orezaná presne na územie, ktoré si beh vypýtal – a keď treba,
    rovno na hrubšej mriežke.

    PREČO TO NIE JE ZBYTOČNÉ. Sklad sklonu má ABSOLÚTNU mriežku častí – to je
    jeho zmysel, lebo tá istá zem tak padne vždy do tej istej časti a časti sa
    dajú znovu použiť. Lenže mozaika je potom zjednotenie CELÝCH častí, nie
    územia: pri strane časti 2 048 m môže 2 km² štvorec pretínať štyri z nich,
    čiže 67 mil. buniek namiesto 8 mil.

    `gdal_contour` potom vektorizoval osemnásobok toho, čo treba – a tie plochy
    navyše nikto neorezal, takže skončili v mape mimo územia, ktoré si beh
    vypýtal. Toto je oboje naraz: menej práce aj správny výsledok.

    Orezáva sa VRT, nie dáta – je to zápis do XML, nie kopírovanie rastra,
    takže to stojí milisekundy a časti v sklade ostávajú nedotknuté.

    Hranice sa prichytávajú na mriežku `res`, aby sa bunky neposunuli o zlomok
    a `gdal_contour` nedostal mriežku posunutú o pol bunky.

    ZHRUBNUTIE IDE TOU ISTOU CESTOU. Keď je `res` hrubšie než `src_res`
    (mriežka skladu), VRT si rovno vypýta hrubšie bunky a priemeruje – takže
    zhrubnutie NESTOJÍ ďalší priechod nad rastrom, je to ten istý zápis do XML.
    Priemer, a nie najbližší sused: `average` je najbližšie tomu, ako by sklon
    vyšiel, keby sa rovno počítal na hrubšej mriežke (hrubší DEM dáva miernejšie
    sklony), kým `nearest` by z 1 m poľa vybral každú štvrtú bunku aj s jej
    zrnom – čiže presne ten šum, kvôli ktorému je jemná mriežka drahá.
    """
    x0 = math.floor(box[0] / res) * res
    y0 = math.floor(box[1] / res) * res
    x1 = math.ceil(box[2] / res) * res
    y1 = math.ceil(box[3] / res) * res
    out = os.path.join(tmp, "slope-clip.vrt")
    hrubsie = ["-r", "average"] if src_res and res > src_res else []
    run(["gdalbuildvrt", "-q", "-te", repr(x0), repr(y0), repr(x1), repr(y1),
         "-tr", repr(res), repr(res)] + hrubsie + [out, vrt])
    return out


def main():
    ap = argparse.ArgumentParser()
    # Sklon už tento skript nepočíta – dostane ho hotový z `slope-chunks.py`,
    # ktorý ho robí po častiach a ukladá do trvalého skladu. Vektorizácia tu
    # ostáva JEDNÝM priechodom nad celou mozaikou; to je to podstatné, čo sa
    # rozdeliť nedá (viď zápis o dierach hore).
    ap.add_argument("--slope-vrt", required=True,
                    help="mozaika sklonu z workers/slope-chunks.py")
    ap.add_argument("--dem", default="",
                    help="zdrojový DEM – len na výpis skutočného detailu")
    ap.add_argument("--bbox", required=True, help="west,south,east,north v stupňoch")
    ap.add_argument("--out", required=True, help="výstupný GeoPackage (vrstva rock)")
    ap.add_argument("--vec-res", default="auto",
                    help="mriežka vektorizácie v metroch, alebo `auto` "
                         "(nikdy jemnejšia než --res)")
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
    ap.add_argument("--budget-min", type=float, default=30.0,
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
    dem_dx, dem_dy = (dem_cell_metres(args.dem, (bbox[1] + bbox[3]) / 2)
                      if args.dem else (None, None))

    # Mriežku vyberá `slope-chunks.py` (musí ju poznať skôr, než začne
    # počítať) a sem príde hotová. Dva výbery toho istého by sa raz rozišli
    # a vektorizovalo by sa niečo iné, než sa počítalo.
    if str(args.res).strip().lower() in ("auto", "", "0"):
        print("::error::--res musí byť konkrétne číslo: mriežku vyberá "
              "workers/slope-chunks.py (`--print-res`) a tento skript ju "
              "dostáva hotovú.")
        return 2
    res = float(args.res)

    # Mriežka VEKTORIZÁCIE. Sklon ostáva v sklade taký, aký je – toto je len
    # pohľad naň pri trasovaní, a nemusí byť rovnako jemný: pri z16 má pixel
    # 1,57 m, takže obrys trasovaný na 1 m nesie len body, ktoré `--simplify`
    # aj tak zmaže. ČAS TÝM NEUŠETRÍ (zmerané, beh 31360120952) – šetrí sa
    # pamäť a veľkosť výstupu. `--vec-res=<res>` to prebije.
    box = to_metric(bbox)
    plocha = (box[2] - box[0]) * (box[3] - box[1])
    if str(args.vec_res).strip().lower() in ("auto", "", "0"):
        vec_res = pick_vec_res(res)
    else:
        vec_res = max(res, float(args.vec_res))
    # Štvrtina bunky: zmaže schodíky po hranách buniek, ale obrys neposunie
    # o viac než štvrtinu mriežky. Namerané: bodov na obrys klesne 5,7×
    # (423 763 → 74 395) a počet plôch sa nezmení vôbec. Ostré rohy, ktoré
    # po ňom ostanú, zaobli `--smooth` na konci.
    #
    # Obe čísla idú z mriežky VEKTORIZÁCIE, nie zo skladu: geometria vzniká na
    # nej, tak schodíky aj najmenšia zmysluplná plocha patria k nej.
    if args.simplify < 0:
        args.simplify = vec_res / 4.0
    # Najmenšia skala = jedna bunka mriežky. Pri `--res=auto` sa mriežka
    # vyberá až tu, takže sa to nedá spočítať v shelli pred spustením.
    if args.min_area < 0:
        args.min_area = round(vec_res * vec_res, 2)
    if dem_dx:
        print(f"Zdrojový DEM má bunku ~{dem_dx:.0f}×{dem_dy:.0f} m – to je "
              f"strop skutočného detailu; mriežka {res:g} m len hladší obrys.")

    # ---------- 1. hotová mozaika sklonu ----------
    vrt = args.slope_vrt
    if not os.path.exists(vrt):
        print(f"::error::Mozaika sklonu {vrt} neexistuje – najprv musí prejsť "
              f"workers/slope-chunks.py.")
        return 2
    mw, mh, mbox, zdrojov = mosaic_info(vrt)
    cells = float(mw) * mh if mw else mosaic_cells(vrt)
    print(f"Mozaika sklonu: {vrt}, {mw}×{mh} px = {cells / 1e9:.2f} mld. buniek "
          f"pri mriežke {res:g} m ({zdrojov} častí skladu)")

    t_start = time.time()
    tmp = tempfile.mkdtemp(prefix="rock-", dir=os.path.dirname(args.out) or ".")
    try:
        # ---------- 2. orez mozaiky na územie ----------
        # Sklad má absolútnu mriežku častí, takže mozaika je zjednotenie CELÝCH
        # častí – nie územia. Bez orezu sa vektorizuje aj to okolo a tie plochy
        # potom skončia v mape mimo výrezu, ktorý si beh vypýtal. Viď `clip_vrt`.
        #
        # Reže sa PRED strážcom rozpočtu nižšie: ten má merať prácu, ktorá sa
        # naozaj spraví, nie tú, ktorú sme sa práve rozhodli nerobiť.
        treba = plocha / (vec_res * vec_res)
        if vec_res > res or (mbox and cells and treba and cells > treba * 1.05):
            vrt = clip_vrt(vrt, box, vec_res, tmp, src_res=res)
            cw, ch, _, _ = mosaic_info(vrt)
            orezane = float(cw) * ch
            preco = ("orez na územie" if vec_res == res else
                     f"orez na územie a mriežka {res:g} → {vec_res:g} m")
            # „Menej buniek", nie „menej práce": orez prácu naozaj ušetrí
            # (tie bunky sa neprečítajú), zhrubnutie NIE – prečítať sa musia
            # tak či tak, len sa na ne trasuje hrubšie. Zmerané, beh
            # 31360120952.
            print(f"Pohľad na sklad ({preco}): {mw}×{mh} → {cw}×{ch} px, "
                  f"{cells / 1e9:.2f} → {orezane / 1e9:.2f} mld. buniek na "
                  f"trasovanie. Časti skladu ostávajú celé aj v plnom "
                  f"rozlíšení, reže sa len pohľad na ne.")
            cells = orezane
        else:
            print(f"Mozaika už sedí na územie ({treba / 1e9:.2f} mld. buniek "
                  f"treba) – nič sa neoreže.")

        # KOĽKO SA PREČÍTA, nie koľko sa vytrasuje. To je to číslo, ktoré
        # rozhoduje o čase (viď `CONTOUR_SRC_CELLS_PER_S`) – trasovanie na
        # hrubšej mriežke zdrojové bunky neušetrí, `gdal_contour` ich musí
        # prečítať a spriemerovať tak či tak. Počíta sa z toho, čo sa naozaj
        # číta (okno po oreze), nie z bboxu.
        src_cells = cells * (vec_res / res) ** 2

        # Strážca ešte pred vektorizáciou: trojhodinový beh, ktorý spadne na
        # timeout, je horší než beh, ktorý sa vôbec nezačne. Sklon je už hotový
        # a zaplatený, takže sa tu meria len ten jeden priechod. Je to hrubé
        # sito – skutočný čas stráži `max_s` nižšie, na nameraných sekundách.
        if args.budget_min > 0 and src_cells:
            odhad = src_cells / CONTOUR_SRC_CELLS_PER_S / 60
            if odhad > args.budget_min:
                print(f"::error::Vektorizácia prečíta {src_cells / 1e9:.2f} mld. "
                      f"buniek skladu a trvala by ~{odhad:.0f} min, rozpočet je "
                      f"{args.budget_min:.0f}. Pomôže HRUBŠÍ SKLAD (`rock_res`, "
                      f"teraz {res:g} m – zdvojnásobenie je štvrtina čítania) "
                      f"alebo menší výrez (`area`); hrubšie trasovanie "
                      f"(`rock_vec_res`) na tomto nezmení nič. Sklon v sklade "
                      f"ostáva, takže sa nezahodí.")
                return 2

        # ---------- 3. vektorizácia NARAZ nad celou mozaikou ----------
        # Jediný priechod = žiadne švy a diery ostanú dierami. Prahy sú
        # v jednotkách uloženého rastra (0,5° na krok).
        bands = os.path.join(tmp, "bands.gpkg")
        print(f"Vektorizujem sklon jedným priechodom nad celým územím "
              f"(trasuje sa {cells/1e9:.2f} mld. buniek na mriežke "
              f"{vec_res:g} m, číta sa {src_cells/1e9:.2f} mld. zo skladu – "
              f"a rozhoduje to druhé číslo). Hrubý odhad "
              f"{hms(src_cells / CONTOUR_SRC_CELLS_PER_S)}; presnejší príde "
              f"z percent po pár minútach…", flush=True)
        # PLNÉ PLOCHY (`--plne`, predvolene zapnuté): jediná úroveň, teda
        # jediné pásmo „sklon nad prahom". Druhá úroveň (`cliff`) mala zmysel,
        # kým sa kreslila tmavšie – ležala v diere pásma `steep` a spolu
        # dláždili územie bez prekryvu. Odkedy sú všetky plochy jedna sivá bez
        # priehľadnosti, je z nej len dvojnásobok prstencov na obtiahnutie.
        urovne = ([repr(args.slope * SCALE)] if args.plne else
                  [repr(args.slope * SCALE), repr(args.cliff * SCALE)])
        try:
            # ROZPOČET SA STRÁŽI NA NAMERANOM ČASE, nie len na odhade pred
            # spustením. Odhad stojí na `CONTOUR_CELLS_PER_S` a tá sa vie
            # mýliť aj osemdesiatnásobne, takže strážca, ktorý sa pýta len
            # jej, prepustí čokoľvek – beh 31334778253 tak bežal štvrť hodiny
            # na 2 km² a zastavil ho až človek. `watch.py` ten strop vie,
            # len mu ho dovtedy nikto nepodal.
            zvysok_s = max(60.0, args.budget_min * 60 - (time.time() - t_start))
            run_watched(["gdal_contour", "-p", "-fl"] + urovne +
                        ["-amin", "smin", "-amax", "smax",
                         "-f", "GPKG", "-nln", "band", vrt, bands],
                        "gdal_contour", tmp=tmp,
                        max_rss_mb=args.max_rss_gb * 1024,
                        max_s=zvysok_s if args.budget_min > 0 else 0)
        except MemoryError:
            print("::error::Vektorizácia sa nezmestila do pamäte. Zmenši "
                  "územie cez rock_area alebo zvoľ hrubšiu mriežku rock_res.")
            return 2
        except TimeoutError:
            hotovo = time.time() - t_start
            print(f"::error::Vektorizácia bežala {hms(hotovo)} a rozpočet je "
                  f"{args.budget_min:.0f} min – zastavené. Sklon v sklade "
                  f"ostáva, takže sa nezahodil. Ďalej: hrubšia mriežka "
                  f"vektorizácie (`--vec-res`, teraz {vec_res:g} m – každé "
                  f"zdvojnásobenie je rádovo desatina práce a sklad sa "
                  f"nedotkne, takže sa nič neprepočítava), menší výrez "
                  f"(`area`), alebo vyšší `rock_budget_min`, ak to naozaj má "
                  f"trvať dlhšie.")
            return 2

        # Mozaika sa tu ZÁMERNE NEMAŽE, hoci je vyše gigabajtu: sú to časti
        # trvalého skladu (`slope-chunks.py`) a ukladajú sa do cache aj do
        # releasu. Práve preto, aby ich ďalší beh nemusel počítať znova.

        # ---------- 4. rozbitie na plochy ----------
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

        # ---------- 5. filter najmenšej plochy + atribúty ----------
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

        # ---------- 6. zaoblenie obrysu ----------
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
        naozaj = src_cells / max(took, 1)
        print(f"Skalných plôch: {n} (celý výpočet {hms(took)}, "
              f"prečítaných {src_cells/1e9:.2f} mld. buniek skladu → "
              f"{naozaj/1e3:.0f} tis. buniek/s; trasovalo sa "
              f"{cells/1e9:.2f} mld. na {vec_res:g} m)")
        # Odhady sa robia z konštánt hore a tie sa časom rozídu s realitou –
        # a keď sa rozídu, prestane platiť aj strážca rozpočtu, ktorý na nich
        # stojí. Nech to teda beh povie sám, nech sa nemusí hľadať. Obe strany:
        # model, ktorý prestreľuje, blokuje platné zadania rovnako spoľahlivo,
        # ako ten podstreľujúci prepustí neplatné.
        if naozaj and max(CONTOUR_SRC_CELLS_PER_S / naozaj,
                          naozaj / CONTOUR_SRC_CELLS_PER_S) > 3:
            print(f"::warning::Vektorizácia prečítala {naozaj/1e3:.0f} tis. "
                  f"buniek skladu/s, ale `CONTOUR_SRC_CELLS_PER_S` "
                  f"v rock-areas.py hovorí "
                  f"{CONTOUR_SRC_CELLS_PER_S/1e3:.0f} tis. – teda "
                  f"{max(CONTOUR_SRC_CELLS_PER_S/naozaj, naozaj/CONTOUR_SRC_CELLS_PER_S):.0f}× "
                  f"vedľa. Odhad aj strážca rozpočtu z toho vychádzajú; prepíš "
                  f"ju podľa tohto behu (sklad {res:g} m, trasovanie "
                  f"{vec_res:g} m).")
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
                f.write(f"vec_grid_m={vec_res:g}\n")
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

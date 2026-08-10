#!/usr/bin/env python3
"""
Plán skál: na akej mriežke, koľko buniek a ako dlho to potrvá.

Oddelené od `rock-areas.py` zámerne – sú to dve otázky a pýtajú sa ich dvaja.
`slope-chunks.py` sa pýta PRED výpočtom („akú mriežku zvoliť, koľko častí
z toho bude"), `rock-areas.py` až pri ňom („koľko to ešte potrvá"). Kým to bol
jeden súbor, musel si ho slope-chunks načítať celý aj s vektorizáciou, ktorá
ho nezaujíma – a súbor prerástol strop 800 riadkov (pravidlo 5 v CLAUDE.md).

Sú tu aj namerané rýchlosti, z ktorých odhady vychádzajú. Keď sa beh s nimi
rozíde viac než 3×, povie to sám na konci a číslo sa má prepísať – aj s číslom
behu, z ktorého to nové je (pravidlo 4).
"""
import json
import math
import os
import subprocess
import sys

# `watch.py` je spoločný (skaly zo sklonu aj z tieňovania, dlhé kroky
# workflowu), tak leží vo `workers/lib/` a nie vedľa jedného z nich.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from watch import hms  # noqa: E402

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
#   beh          trasuje sa na   buniek trasovania   dokedy bežal   kam došiel
#   31357217326  1 m             0,71 mld.           21 min         17 %
#   31360120952  2 m             0,18 mld.           30 min         26 %
#
# POZOR, ANI JEDEN Z TÝCH BEHOV NEDOBEHOL – oba zrušil človek. Kým tu stálo
# „97 min" a „98 min", boli to ODHADY, ktoré si tep vypísal z tejto konštanty
# („beží 0:16:30 z 1:39:59"), nie namerané časy. Číslo teda potvrdzovalo samo
# seba a rovnaká chyba tu už raz bola (3,5 mil./s z behu 30948662582, ktorý
# tiež nedobehol). Skutočná rýchlosť nie je známa a je NIŽŠIA.
#
# A HLAVNE: NIE JE KONŠTANTNÁ. `gdal_contour -p` nad jemným sklonom výrazne
# spomaľuje, ako pribúdajú rozpracované prstence. Namerané v behu 31418794845
# (689 km², sklad 1 m, trasovanie 2 m) – tempo po krokoch po 2,5 %:
#
#   do 17,5 %   ~2 %/min        22,5 % → 25 %    6:55 na krok
#   22,5 %      1,07 %/min      25 % → 27,5 %    9:45 na krok
#   25 %        0,36 %/min      ďalší krok       > 26 min (beh zrušený)
#
# Každý ďalší krok je ~1,4× dlhší než predošlý. Lineárny odhad z tejto
# konštanty sľuboval 1:39; pri tom raste sa priechod nedokončí ani za hodiny.
# ŽIADNY beh skál nad celým výrezom zatiaľ nedobehol – všetky hotové sú
# 4 km² testy (1–2 min).
#
# Konštanta tu teda ostáva len ako HRUBÉ rádové vodítko pre `pick_res`; skutočný
# obraz dáva až tep, ktorý hlási, keď tempo klesá (`workers/lib/watch.py`).
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
    """Najjemnejšia mriežka, ktorá má ešte zmysel (a zmestí sa do rozpočtu).

    Rozpočet je predvolene ŽIADNY (`budget_min=0`), takže rozhodujú len
    dva stropy zdola nižšie. Kladné číslo z neho spraví aj strop času –
    vtedy sa z rebríka vyberie prvá mriežka, ktorá sa doň zmestí.

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

    # PODLAHA MRIEŽKY SKLADU. Dva dôvody, oba o tom, čo je vidieť – nie o čase:
    #   * desatina bunky zdrojového DEM: jemnejšie sa už len interpoluje medzi
    #     tými istými výškami, nový detail terénu z toho nevznikne,
    #   * pixel dlaždice pri z16, kam skaly idú (`VEC_FLOOR_M`, 1,57 m): obrys
    #     z jemnejšieho skladu sa v mape nemá ako zobraziť.
    #
    # To druhé tu dlho nebolo a `auto` preto pri DMR 5.0 (bunka 1 m) siahalo na
    # 1 m sklad. Na 689 km² z toho bolo 0,71 mld. buniek do jedného priechodu
    # `gdal_contour -p` – beh 31418794845 sa na 27,5 % spomalil na 0,26 %/min
    # a zrušili ho po 53 minútach. Pri 2 m je buniek štvrtina a detail v mape
    # rovnaký, lebo pod pixel dlaždice sa aj tak nedostane.
    floor = max(VEC_FLOOR_M, round((dem_cell_m or 0) / 10.0, 1))
    budget_s = budget_min * 60 if budget_min else float("inf")

    print("── Výber mriežky (rock_res=auto) ────────────────────")
    print(f"  plocha územia   {area_m2/1e6:.0f} km²")
    print("  rozpočet        " + ("bez stropu – berie sa najjemnejšia, "
                                  "ktorá má zmysel"
                                  if budget_s == float("inf")
                                  else f"{budget_min:g} min"))
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
        # Bez rozpočtu sa nič „nezmestí ani nezmestí" – vtedy je stĺpec len
        # odhad času, nie súd nad ním.
        znak = "" if budget_s == float("inf") else (
            "  ✓" if fits else "  × nad rozpočet")
        print(f"  {res:>4g} m  {cells/1e9:5.2f} mld.  sklon ~{hms(s_slope)}"
              f"  + vektory ~{hms(s_vec)} (trasuje sa na {vec:g} m)"
              f"  = ~{hms(est)}{znak}")
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

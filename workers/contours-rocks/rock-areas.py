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

SKLON SEM CHODÍ HOTOVÝ. Počíta ho `workers/contours-rocks/slope-chunks.py` po častiach
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
    python3 workers/contours-rocks/slope-chunks.py --bbox=W,S,E,N --res=auto --drive \\
        --out=slope-chunks --stats=slope.txt
    python3 workers/contours-rocks/rock-areas.py --slope-vrt=slope-chunks/slope-r2.vrt \\
        --bbox=W,S,E,N --res=2 --slope=50 --cliff=65 --out=data/rock.gpkg
"""
import argparse
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time

# Mriežka, rozsahy a odhady času sú vo `rock-plan.py` – tú istú odpoveď
# potrebuje `slope-chunks.py` ešte PRED výpočtom, takže má vlastný modul
# a nie kópiu (pravidlo 1). Sem sa preberajú hotové mená, neprepočítavajú sa.
def _load(name, path):
    """workers/*.py sa kvôli pomlčke v mene nedajú `import`-núť normálne."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(os.path.dirname(os.path.abspath(__file__)), path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


plan = _load("rock_plan", "rock-plan.py")
# Obrysy po blokoch sú spoločné so skalami z tieňovania, tak sú v lib.
bloky_mod = _load("contour_blocks", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lib", "contour-blocks.py"))
METRIC, SCALE = plan.METRIC, plan.SCALE
SLOPE_CELLS_PER_S = plan.SLOPE_CELLS_PER_S
CONTOUR_SRC_CELLS_PER_S = plan.CONTOUR_SRC_CELLS_PER_S
CONTOUR_MB_PER_GCELL = plan.CONTOUR_MB_PER_GCELL
MOSAIC_MB_PER_GCELL = plan.MOSAIC_MB_PER_GCELL
RES_LADDER, VEC_FLOOR_M = plan.RES_LADDER, plan.VEC_FLOOR_M
run, to_metric, dem_cell_metres = plan.run, plan.to_metric, plan.dem_cell_metres
chunk_plan, intersects_bbox = plan.chunk_plan, plan.intersects_bbox
pick_res, pick_vec_res = plan.pick_res, plan.pick_vec_res
mosaic_cells, mosaic_info, clip_vrt = plan.mosaic_cells, plan.mosaic_info, plan.clip_vrt

# Tep, progress GDALu a meranie sú vo `watch.py` – používajú ich aj kroky
# workflowu, tak nech je to jedna implementácia a nie dve, ktoré sa časom
# rozídu.
# `watch.py` je spoločný (skaly zo sklonu aj z tieňovania, dlhé kroky
# workflowu), tak leží vo `workers/lib/` a nie vedľa jedného z nich.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from watch import hms, run_watched  # noqa: E402


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


def main():
    ap = argparse.ArgumentParser()
    # Sklon už tento skript nepočíta – dostane ho hotový z `slope-chunks.py`,
    # ktorý ho robí po častiach a ukladá do trvalého skladu. Vektorizácia tu
    # ostáva JEDNÝM priechodom nad celou mozaikou; to je to podstatné, čo sa
    # rozdeliť nedá (viď zápis o dierach hore).
    ap.add_argument("--slope-vrt", required=True,
                    help="mozaika sklonu z workers/contours-rocks/slope-chunks.py")
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
    # 0 = bez rozpočtu, a to je predvolené: „koľko som ochotný čakať" je
    # voľba behu (`rock_res` vo formulári), nie konštanta pre všetkých.
    # OBRYSY PO BLOKOCH. `gdal_contour -p` nad celou mozaikou naraz je
    # superlineárny: čím viac rozpracovaných prstencov drží, tým drahšie je
    # pridať ďalší (beh 31418794845 – tempo padlo z 2 na 0,26 %/min a beh
    # nedobehol). Blok je malý raster, takže sa prstence poskladajú rýchlo,
    # pamäť je zhora ohraničená a hotové bloky ostávajú na disku, takže
    # zrušený beh nezahodí prácu. 0 = jeden priechod (staré správanie).
    ap.add_argument("--block-px", type=int, default=4096,
                    help="strana bloku v pixeloch pri vektorizácii "
                         "(0 = jeden priechod nad celou mozaikou)")
    ap.add_argument("--budget-min", type=float, default=0.0,
                    help="koľko minút MÁ výpočet trvať: podľa toho sa vyberá "
                         "mriežka (`--res=auto`) a nad tým sa povie, čo "
                         "zmenšiť – výpočet to ale NEZASTAVÍ (0 = neriešiť)")
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
              "workers/contours-rocks/slope-chunks.py (`--print-res`) a tento skript ju "
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
              f"workers/contours-rocks/slope-chunks.py.")
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

        # ROZPOČET JE ODHAD, NIE VYPÍNAČ. Nad ním sa POVIE, že to potrvá
        # dlhšie, a počíta sa ďalej. Zastavovanie tu bolo dvakrát – pred
        # spustením (`return 2`) aj počas behu (`max_s` podaný tepu) – a ani
        # raz nič nezachránilo: vektorizácia je JEDEN nedeliteľný priechod nad
        # celou mozaikou (viď zápis o dierach hore), takže zabitý
        # `gdal_contour` nenechá ani neúplný výsledok. Zastavenie znamenalo
        # presne to isté ako timeout jobu, len skôr – a bez šance, že by to
        # dobehlo. Odhad pritom stojí na `CONTOUR_SRC_CELLS_PER_S`, ktorá bola
        # už dvakrát rádovo vedľa (29× a 78×), takže vypínač na nej zavesený
        # zháňal aj zadania, čo by v pohode dobehli. Ostáva strop PAMÄTE (OOM
        # zabije runner a v logu nie je nič) a timeout jobu vo workflowe.
        odhad_s = src_cells / CONTOUR_SRC_CELLS_PER_S if src_cells else 0.0
        if args.budget_min > 0 and odhad_s > args.budget_min * 60:
            print(f"::warning::Vektorizácia prečíta {src_cells / 1e9:.2f} mld. "
                  f"buniek skladu a potrvá odhadom ~{hms(odhad_s)}, čo je nad "
                  f"rozpočet {args.budget_min:.0f} min – NEZASTAVUJEM ju, "
                  f"nechávam dobehnúť (zastaví ju až timeout jobu). Keď to má "
                  f"byť rýchlejšie: HRUBŠÍ SKLAD (`rock_res`, teraz {res:g} m – "
                  f"zdvojnásobenie je štvrtina čítania) alebo menší výrez "
                  f"(`area`); hrubšie trasovanie (`rock_vec_res`) na tomto "
                  f"nezmení nič. Sklon v sklade ostáva tak či tak.")

        # ---------- 3. vektorizácia NARAZ nad celou mozaikou ----------
        # Jediný priechod = žiadne švy a diery ostanú dierami. Prahy sú
        # v jednotkách uloženého rastra (0,5° na krok).
        # Plán PRED spustením: bez neho je v logu hodina ticha a spätne sa
        # nedá povedať ani to, čo do toho išlo.
        bands = os.path.join(tmp, "bands.gpkg")
        print("── Vektorizácia sklonu (gdal_contour -p) ────────────")
        print(f"  vstup           {vrt}")
        print(f"  číta sa         {src_cells / 1e9:.2f} mld. buniek skladu "
              f"({res:g} m) – toto rozhoduje o čase")
        print(f"  trasuje sa      {cells / 1e9:.2f} mld. buniek na {vec_res:g} m")
        print(f"  prahy           sklon ≥ {args.slope:g}°"
              + ("" if args.plne else f", steny ≥ {args.cliff:g}°"))
        print(f"  odhad           ~{hms(odhad_s)} pri "
              f"{CONTOUR_SRC_CELLS_PER_S / 1e3:.0f} tis. buniek/s"
              + (f", rozpočet {args.budget_min:.0f} min"
                 if args.budget_min > 0 else "") + "; presný príde z percent")
        if args.block_px > 0:
            print(f"  po blokoch      {args.block_px}×{args.block_px} px – hotový "
                  f"blok ostáva na disku, takže zrušený beh sa dá nadviazať")
            print(f"  stropy          pamäť {args.max_rss_gb:g} GB; čas NEOBMEDZENÝ "
                  f"(tep každých {args.heartbeat:g} s)")
        else:
            print(f"  stropy          pamäť {args.max_rss_gb:g} GB; čas NEOBMEDZENÝ "
                  f"– priechod sa nedá prerušiť a nadviazať, tak beží, kým nie je "
                  f"hotový (percentá po 2,5 %, tep každých {args.heartbeat:g} s)")
        print("─────────────────────────────────────────────────────", flush=True)
        # PLNÉ PLOCHY (`--plne`, predvolene zapnuté): jediná úroveň, teda
        # jediné pásmo „sklon nad prahom". Druhá úroveň (`cliff`) mala zmysel,
        # kým sa kreslila tmavšie – ležala v diere pásma `steep` a spolu
        # dláždili územie bez prekryvu. Odkedy sú všetky plochy jedna sivá bez
        # priehľadnosti, je z nej len dvojnásobok prstencov na obtiahnutie.
        urovne = ([repr(args.slope * SCALE)] if args.plne else
                  [repr(args.slope * SCALE), repr(args.cliff * SCALE)])
        atributy = ["-amin", "smin", "-amax", "smax"]
        try:
            if args.block_px > 0:
                # PO BLOKOCH. Hotový blok je na disku, takže zrušený beh
                # nezahodí prácu – to je ten hlavný rozdiel oproti jednému
                # priechodu, ktorý sa prerušiť ani nadviazať nedá.
                _, _, mbox_v, _ = mosaic_info(vrt)
                ox, oy = (mbox_v[0], mbox_v[3]) if mbox_v else (0.0, 0.0)
                d, n_blokov = bloky_mod.po_blokoch(
                    vrt, os.path.join(tmp, "bloky"), urovne, atributy,
                    args.block_px, (ox, oy, vec_res),
                    heartbeat=args.heartbeat,
                    max_rss_mb=args.max_rss_gb * 1024)
                seq = os.path.join(tmp, "bloky.geojsonl")
                n_utvarov = bloky_mod.zlej(d, seq)
                print(f"  {n_blokov} blokov → {n_utvarov} útvarov", flush=True)
                # Švy: plocha aj diera preseknutá hranicou bloku sa spoja späť.
                seq = bloky_mod.zlep_svy(seq, tmp, klucovy_atribut="smin",
                                         heartbeat=args.heartbeat)
                run(["ogr2ogr", "-f", "GPKG", bands, seq, "-nln", "band"])
            else:
                # ŽIADNY `max_s`: strop času tu nemá čo zachrániť (viď rozvahu
                # nad odhadom vyššie). Tep dostane `--heartbeat`, aby sa dalo
                # zhora nastaviť, ako často má byť počuť – dovtedy sa ten input
                # bral a ticho zahadzoval.
                run_watched(["gdal_contour", "-p", "-fl"] + urovne + atributy +
                            ["-f", "GPKG", "-nln", "band", vrt, bands],
                            "gdal_contour", tmp=tmp, every=args.heartbeat,
                            max_rss_mb=args.max_rss_gb * 1024)
        except MemoryError:
            print("::error::Vektorizácia sa nezmestila do pamäte. Zmenši "
                  "územie cez rock_area alebo zvoľ hrubšiu mriežku rock_res.")
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
                                  "smooth-shapes.py")
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
        # a keď sa rozídu, prestane platiť aj výber mriežky (`--res=auto`),
        # ktorý na nich stojí. Nech to teda beh povie sám, nech sa nemusí
        # hľadať. Obe strany: model, ktorý prestreľuje, zbytočne zhrubne
        # mriežku, ten podstreľujúci zase sľúbi štvrťhodinu a beží hodinu.
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
                # skaly z tieňovaných dlaždíc (workers/rocks-shading/build.py)
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

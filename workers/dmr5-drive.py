#!/usr/bin/env python3
"""
DMR 5.0 (ETRS89) z Google Drive → výškový model do releasu.

ČO JE ZDROJ. Dva súbory na Drive, oba čítané cez HTTP Range, nič sa nesťahuje
celé:

    dmr5_etrs89.tif       145,39 GiB   423 518 × 207 589 px, mriežka 1 m
    dmr5_etrs89.tif.ovr    43,35 GiB   pyramídy: 2, 4, 8, 16, 32, 64, 128, 256 m

Oba sú BigTIFF, dlaždicované 128×128, Float32, nodata 3,4e38 (hlavný LZW,
pyramídy deflate). Georeferencia je v hlavnom súbore:

    CRS      EPSG:3046 – ETRS89 / TM zone N34 (cm 21° E, k₀ 0,9996, FE 500 000)
    origin   X 191 148,0   Y 5 497 220,0   (ľavý horný ROH, nie stred pixela)
    bunka    1,0 × 1,0 m

PREČO TO NIE JE `dmr5-raster.py`. Ten číta archív ÚGKK cez
`/vsizip//vsicurl/`, kde je raster zabalený jedným deflate prúdom – v ňom sa
nedá skočiť dopredu, takže celý ten script je postavený na pravidle „čítaj
raz a sekvenčne". Tu to pravidlo neplatí: súbory sú holé, každá dlaždica má
vlastnú kompresiu a Range funguje na ľubovoľnom offsete (overené na 20 GB aj
145 GB). Náhodný prístup je zadarmo, a tým sa mení celý návrh – výrez sa
neplatí vzdialenosťou od začiatku súboru, ale počtom dlaždíc, ktoré ho
pretínajú.

ČÍTA SA PRIHLÁSENÝ AKO VLASTNÍK, keď je čím. Verejný odkaz („ktokoľvek
s odkazom") má denný limit sťahovania na súbor a ten zdieľajú všetci, kto naň
siahnu – keď sa vyčerpá, DMR 5.0 sa v tom behu nedoplní vôbec, lebo je to
jediná cesta k nemu (beh 31315890474). Token vlastníka zo secretu
`GDRIVE_CREDENTIALS` ten strop posúva rádovo vyššie; drží ho
`workers/drive-auth.py` a `--auth-check` povie, ktorým účtom beh číta. Bez
secretu sa nič nemení, len sa výslovne vypíše, že platí verejný limit.

TRI VECI, KTORÉ TENTO SÚBOR RIEŠI:

  1. DRIVE KLAME O VEĽKOSTI. Na HEAD vracia `content-length: 0`, takže GDAL
     súbor odmietne. Obchádza to `workers/drive-serve.py` – malý HTTP server
     na localhoste, ktorý tú jednu hlavičku opraví. Podáva OBA súbory pod
     jedným menom (`dmr5_etrs89.tif` a `dmr5_etrs89.tif.ovr`), takže si GDAL
     nájde pyramídy ako sidecar sám a pri hrubšom cieli číta z nich. Overené:
     `gdalinfo` vypíše všetkých 8 úrovní, otvorenie 145 GiB trvá 8 s a stojí
     9 požiadaviek / 0,3 MB.

  2. LATENCIA, NIE ŠÍRKA PÁSMA. Jeden Range request na Drive trvá rádovo
     0,1–1 s bez ohľadu na to, koľko bajtov nesie. Výrez 6×6 km pri 1 m
     (243 požiadaviek, 103 MB) trval jedným procesom 90 s, čo je 1,1 MB/s –
     pritom to isté pásmo utiahne 75 MB/s. Zmerané na 48 náhodných výrezoch:
     1 vlákno 1 143 ms/req, 8 vlákien 147 ms/req, 24 vlákien 68 ms/req.
     Preto sa okno KRÁJA NA BLOKY a tie sa čítajú súbežne (`--jobs`).
     GDAL je vnútri jedného `gdal_translate` v čítaní jednovláknový, takže
     súbežnosť musí prísť zvonku, z viacerých procesov.

  3. VÝŠKY SÚ ELIPSOIDICKÉ, NIE Bpv. Toto je ETRS89 verzia a nesie výšky nad
     elipsoidom GRS80. Vidno to na štatistike v súbore: maximum 2 697,03 m,
     kým Gerlachovský štít má 2 654,4 m n. m. Bpv – rozdiel +42,6 m je
     geoidová undulácia. Na SKALY to nevadí (geoid sa mení plynulo, sklon
     ostáva ten istý), ale vrstevnice by z toho vyšli popísané o 42 m vyššie
     než z ostatných zdrojov v pipeline. Preto sa predvolene odčíta geoid
     EGM2008 (`--geoid=egm2008`). Kontrola na Gerlachu: 2 697,03 m
     elipsoidicky → 2 654,37 m po prevode, oficiálne 2 654,4 m. Sedí na 3 cm.

VÝSTUP JE TEN ISTÝ AKO DOTERAZ, aby `workers/fetch-dem.sh` nemusel vedieť,
odkiaľ dáta prišli:

    pohorie          out/ugkk-<area>.tif      jeden COG vo WGS84 → dem-ugkk
    celé Slovensko   out/N49E019.tif …        dlaždice 1°×1°     → dem-dmr5
    výrez + --tiles  out/N49E019.tif …        len dotknuté stupne → dem-dmr5

Tretí riadok je to, čo si Build map dopĺňa sám: tieňovanie chce dlaždicovú
podobu (na celý región 1 m neexistuje), ale nepotrebuje kvôli nej celú
krajinu – stačia stupne, ktoré jeho bbox pretína. Okno sa pri `--tiles`
rozširuje na celé stupne, lebo meno `N49E020.tif` je sľub o celej dlaždici
a polovičná by v ďalšom behu prešla kontrolou ako hotová.

`--area` BERIE AJ BBOX, a to je pri výreze to podstatné. Build map ho tak aj
volá: čo sa má prečítať, je územie, ktoré si beh naozaj vypýtal (výrez pretnutý
s regiónom, pri rýchlom teste štvorec na pár km²) – nie celý obdĺžnik pohoria
z `areas.json`. Meno výsledku sa vtedy podá zvlášť cez `--asset`, lebo
`ugkk-20,49,21,50.tif` si build vypýtať nevie. Kým sa podával len kľúč
pohoria, prečítal sa vždy celý obdĺžnik z `areas.json` a rýchly test na 2 km²
čítal z Drive 541 km² Vysokých Tatier.

ROZDELENÉ NA FÁZY (`--stage`), aby dlhé čakanie nebolo jeden nemý krok:

    plan     otvor zdroj, spočítaj okno a bloky, povedz, čo to bude stáť
    read     prečítaj bloky z Drive (jediná fáza, ktorá siaha na sieť)
    finish   mozaika → COG alebo 1° dlaždice (už len nad diskom)
    all      všetko za sebou, ako predtým (predvolené pri ručnom spustení)

Fázy si podávajú stav cez `<work>/dmr5-drive-stav.json` a rozčítané bloky
cez `<work>/blok-*.tif`, takže sa dajú spustiť ako samostatné kroky workflowu
– každý s vlastným nadpisom v logu a vlastným postupom.

Použitie:
    python3 workers/dmr5-drive.py --area=vysoke_tatry --grid-m=1 \\
        --out=out --asset=ugkk-vysoke_tatry.tif
    python3 workers/dmr5-drive.py --area=20.0,49.1,20.1,49.2 --grid-m=1 \\
        --out=out --asset=ugkk-vysoke_tatry_test2.tif
    python3 workers/dmr5-drive.py --area=cele_slovensko --grid-m=5 --out=out
    python3 workers/dmr5-drive.py --area=20,49,21,50 --grid-m=5 --tiles --out=out
    python3 workers/dmr5-drive.py --stage=plan --area=vysoke_tatry --grid-m=1
    python3 workers/dmr5-drive.py --probe-only
    python3 workers/dmr5-drive.py --auth-check
"""
import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))

# Drive file id. Nie sú tajomstvo (sú to tie isté id ako v zdieľanom odkaze),
# preto smú byť v repozitári – tajomstvom je až token vlastníka v secrete
# GDRIVE_CREDENTIALS, ktorý dvíha limit sťahovania (viď `drive-auth.py`).
TIF_ID = "1A4q6T-S8IZbODMDsowGr_DihzcQf22wI"
OVR_ID = "1p07TFZwG6LzbdkWdK3gV_ccvOubd2Xqi"
TIF_NAME = "dmr5_etrs89.tif"

SRC_EPSG = 3046           # ETRS89 / TM zone N34, priamo z GeoTIFF tagov
# Elipsoidické výšky nad GRS80 (ETRS89) → ortometrické (EGM2008 ≈ Bpv).
SRC_VERT = 4937           # ETRS89 (3D, elipsoidické výšky)
DST_VERT = 3855           # EGM2008 height

# Stav medzi fázami. Leží v `--work`, ktorý medzi krokmi jobu prežije.
STATE = "dmr5-drive-stav.json"

# Odhad ceny čítania, NA PIXEL ZDROJA. Namerané, nie odhadnuté od stola:
# výrez 5,2 × 5,6 km pri 1 m (29 km² = 29 mil. px na plnom rozlíšení) trval
# 1,2 min a stiahol 0,11 GB v 697 požiadavkách, pri `--jobs=12`.
#
# Na pixel ZDROJA, a nie cieľa, preto, že hrubšiu mriežku číta GDAL z pyramíd
# a tie majú vlastné rozlíšenie: cieľ 5 m sa berie z úrovne 4 m, čiže sa
# prečíta (5/4)² = 1,6× viac pixelov, než má výstup. Kontrola na inom konci
# rozsahu: jeden 1° stupeň na 5 m = 8 065 km² z pyramídy 4 m = 504 mil. px,
# čiže ~21 min a ~1,9 GB. Sedí s tým, čo o cene stupňa hovorí check-dem.sh.
#
# Je to rádový odhad – má povedať „minúty alebo hodiny", nie predpovedať minútu.
PX_PER_MIN = 24e6         # pri --jobs=12
BYTES_PER_PX = 3.8


def load(name, path):
    """workers/*.py sa kvôli pomlčke v mene nedajú `import`-núť normálne.

    Cez `sys.modules` preto, aby ten istý modul nevznikol dvakrát:
    `drive-auth.py` si `drive-serve.py` vypýta tiež (berie si odtiaľ spojenia)
    a dve kópie shimu by boli dva nezávislé bazény spojení.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


drive = load("drive_serve", "drive-serve.py")
auth = load("drive_auth", "drive-auth.py")       # kto sme na Drive
raster = load("dmr5_raster", "dmr5-raster.py")   # Heartbeat, run_live, pomocníci

LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


def run(cmd, env, label=None, expect=None, watch=None):
    if label:
        print("  $ " + " ".join(str(c) for c in cmd), flush=True)
        with raster.Heartbeat(label, expect_bytes=expect, watch=watch):
            return subprocess.run([str(c) for c in cmd], check=True, env=env)
    return subprocess.run([str(c) for c in cmd], check=True, env=env,
                          capture_output=True, text=True)


# ---------- okno a bloky ----------

def src_window(bbox_wgs, wkt_file, info, env, pad_px=8):
    """WGS84 bbox → okno v projekcii zdroja, orezané na rozsah rastra.

    Orezanie nie je kozmetika: `gdal_translate -projwin` by presahujúcu časť
    DOPLNIL nulami a nula je platná výška, takže by z toho v mape bolo more,
    nie diera.
    """
    w, s, e, n = bbox_wgs
    pts = "\n".join(f"{x} {y}" for x, y in
                    ((w, s), (w, n), (e, s), (e, n),
                     ((w + e) / 2, s), ((w + e) / 2, n)))
    r = subprocess.run(["gdaltransform", "-s_srs", "EPSG:4326",
                        "-t_srs", wkt_file],
                       input=pts, capture_output=True, text=True, env=env)
    xs, ys = [], []
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) >= 2:
            xs.append(float(f[0]))
            ys.append(float(f[1]))
    if not xs:
        raise SystemExit(f"::error::bbox {bbox_wgs} sa nedá prepočítať do zdroja")

    gt = info["geoTransform"]
    px, py = info["size"]
    rw, rn = gt[0], gt[3]
    re_, rs = rw + gt[1] * px, rn + gt[5] * py
    pad = pad_px * abs(gt[1])
    box = (max(min(xs) - pad, rw), max(min(ys) - pad, rs),
           min(max(xs) + pad, re_), min(max(ys) + pad, rn))
    if box[0] >= box[2] or box[1] >= box[3]:
        raise SystemExit("::error::Výrez nemá s rastrom spoločný ani jeden "
                         "pixel – skontroluj `area`.")
    log(f"  okno v EPSG:{SRC_EPSG}: {box[0]:.0f},{box[1]:.0f} … "
        f"{box[2]:.0f},{box[3]:.0f}  "
        f"({(box[2] - box[0]) / 1000:.1f} × {(box[3] - box[1]) / 1000:.1f} km)")
    return box


def blocks(box, grid_m, jobs, max_px=4096):
    """Okno → zoznam blokov PRICHYTENÝCH NA CIEĽOVÚ MRIEŽKU.

    Prichytenie je to podstatné: keby hranica bloku padla doprostred cieľovej
    bunky, susedné bloky by mali navzájom posunuté mriežky a `gdalbuildvrt`
    by ich nezlepil bez švu. Preto sa všetky hranice zaokrúhľujú na násobok
    `grid_m` v tej istej sústave.
    """
    w = math.floor(box[0] / grid_m) * grid_m
    s = math.floor(box[1] / grid_m) * grid_m
    e = math.ceil(box[2] / grid_m) * grid_m
    n = math.ceil(box[3] / grid_m) * grid_m

    nx_px, ny_px = (e - w) / grid_m, (n - s) / grid_m
    # Chceme aspoň `jobs` blokov (nech je čo paralelizovať) a zároveň žiadny
    # väčší než max_px – jeden obrovský blok by zdržal celý beh na konci.
    nx = max(1, math.ceil(nx_px / max_px))
    ny = max(1, math.ceil(ny_px / max_px))
    while nx * ny < jobs and (nx_px / nx > 512 or ny_px / ny > 512):
        if nx_px / nx >= ny_px / ny:
            nx += 1
        else:
            ny += 1

    out = []
    for j in range(ny):
        for i in range(nx):
            bw = w + math.floor(i * nx_px / nx) * grid_m
            be = w + math.floor((i + 1) * nx_px / nx) * grid_m
            bs = s + math.floor(j * ny_px / ny) * grid_m
            bn = s + math.floor((j + 1) * ny_px / ny) * grid_m
            if be > bw and bn > bs:
                out.append((bw, bs, be, bn))
    return out, (nx, ny)


def read_blocks(src, parts, grid_m, work, jobs, env, native_m=1.0):
    """Bloky sa čítajú SÚBEŽNE – latencia Drive sa inak nedá prekonať.

    Vracia zoznam hotových súborov. Prázdne bloky (samé nodata) sa
    nezahadzujú: diera v mozaike by sa v ďalšom kroku doplnila nulami.

    HOTOVÝ BLOK SA NEČÍTA ZNOVA. Zapisuje sa cez `.part` a až premenovanie
    z neho spraví blok – takže krok, ktorý spadol alebo mu došiel čas,
    o prečítané bloky nepríde a ďalší dopočíta len zvyšok. To isté robí
    sklad častí sklonu a z rovnakého dôvodu: hodina čítania z Drive je
    najdrahšia vec v celej pipeline.
    """
    os.makedirs(work, exist_ok=True)
    resample = [] if abs(grid_m - native_m) < 1e-9 else ["-r", "average"]
    tr = [] if abs(grid_m - native_m) < 1e-9 else ["-tr", repr(grid_m), repr(grid_m)]
    t0 = time.time()
    done_n = [0]
    reused = [0]
    lock = threading.Lock()

    def one(idx_part):
        idx, (bw, bs, be, bn) = idx_part
        dest = os.path.join(work, f"blok-{idx:04d}.tif")
        hit = os.path.exists(dest) and os.path.getsize(dest) > 0
        if not hit:
            tmp = dest + ".part"
            cmd = ["gdal_translate", "-q",
                   "-projwin", repr(bw), repr(bn), repr(be), repr(bs),
                   *tr, *resample, "-ovr", "AUTO",
                   "-of", "GTiff", "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3",
                   "-co", "TILED=YES", "-co", "BIGTIFF=YES",
                   src, tmp]
            subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)
            os.replace(tmp, dest)
        # Postup sa vypisuje PO KAŽDOM bloku, nie len raz za pol minúty:
        # inak sa z logu nedá povedať, či beh napreduje, alebo visí na jednom
        # bloku, ktorému Drive neodpovedá.
        with lock:
            done_n[0] += 1
            if hit:
                reused[0] += 1
            el = time.time() - t0
            eta = el / done_n[0] * (len(parts) - done_n[0])
            print(f"  [{done_n[0]}/{len(parts)}] blok-{idx:04d} "
                  f"{'už bol' if hit else 'prečítaný'}, "
                  f"{os.path.getsize(dest) / 1e6:.1f} MB – "
                  f"{el / 60:.1f} min za sebou, zostáva ~{eta / 60:.1f} min",
                  flush=True)
        return dest

    with raster.Heartbeat(f"čítanie {len(parts)} blokov z Drive"):
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            done = list(ex.map(one, enumerate(parts)))
    mb = sum(os.path.getsize(p) for p in done) / 1048576
    log(f"  bloky hotové za {(time.time() - t0) / 60:.1f} min, {mb:.0f} MB na "
        f"disku" + (f" (z toho {reused[0]} už bolo z predošlého pokusu)"
                    if reused[0] else ""))
    return done


# ---------- výstupy ----------

def to_wgs84(parts, dest, bbox_wgs, grid_m, work, env, geoid):
    """Mozaika blokov → jeden COG vo WGS84, s prevodom výšok.

    Warp beží nad DISKOM, nie nad Drive: preskakovanie v mozaike je zadarmo,
    kým každý skok cez sieť stojí ďalší request.
    """
    vrt = os.path.join(work, "mozaika.vrt")
    run(["gdalbuildvrt", "-q", vrt, *parts], env)

    dx, dy = raster.degrees_per_metre((bbox_wgs[1] + bbox_wgs[3]) / 2)
    if geoid == "egm2008":
        srs = [f"-s_srs", f"EPSG:{SRC_EPSG}+{SRC_VERT}",
               "-t_srs", f"EPSG:4326+{DST_VERT}",
               # Bez tejto poistky by GDAL pri chýbajúcej mriežke geoidu ticho
               # nechal elipsoidické výšky – a to je presne ten tichý omyl,
               # ktorý sa nájde až na hotovej mape.
               "-to", "ERROR_ON_MISSING_VERT_SHIFT=YES"]
        log("  výšky: elipsoidické (ETRS89) → ortometrické (EGM2008 ≈ Bpv)")
    else:
        srs = ["-s_srs", f"EPSG:{SRC_EPSG}", "-t_srs", "EPSG:4326"]
        log("::warning::Výšky ostávajú elipsoidické – sú o ~42 m vyššie než "
            "Bpv. Na skaly a tieňovanie to nevadí, na vrstevnice áno.")

    run(["gdalwarp", "-overwrite", *srs,
         "-te", *[repr(v) for v in bbox_wgs],
         "-tr", repr(grid_m * dx), repr(grid_m * dy),
         "-r", "bilinear", "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
         "-of", "COG", "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3",
         "-co", "RESAMPLING=BILINEAR", "-co", "NUM_THREADS=ALL_CPUS",
         vrt, dest], env, label="prevod do WGS84", watch=dest)
    log(f"  {os.path.basename(dest)}: {os.path.getsize(dest) / 1048576:.0f} MB")
    return dest


def country_tiles(parts, out_dir, work, env, geoid):
    """Mozaika → dlaždice 1°×1° vo WGS84, ako ich čaká build mapy.

    Používa sa na celé Slovensko aj na `--tiles` s výrezom. Rez na stupne robí
    `dem-tiles.py` podľa rozsahu rastra – preto sa okno pred čítaním rozširuje
    na celé stupne, nech pod menom `N49E020.tif` nikdy neleží len jeho kúsok.
    """
    vrt = os.path.join(work, "mozaika.vrt")
    run(["gdalbuildvrt", "-q", vrt, *parts], env)
    merged = os.path.join(work, "dmr5-national.tif")
    if geoid == "egm2008":
        srs = ["-s_srs", f"EPSG:{SRC_EPSG}+{SRC_VERT}",
               "-t_srs", f"EPSG:4326+{DST_VERT}",
               "-to", "ERROR_ON_MISSING_VERT_SHIFT=YES"]
    else:
        srs = ["-s_srs", f"EPSG:{SRC_EPSG}", "-t_srs", "EPSG:4326"]
    run(["gdalwarp", "-overwrite", *srs, "-r", "bilinear",
         "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
         "-of", "GTiff", "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3",
         "-co", "TILED=YES", "-co", "BIGTIFF=YES", "-co", "NUM_THREADS=ALL_CPUS",
         vrt, merged], env, label="prevod do WGS84", watch=merged)
    run(["python3", os.path.join(_HERE, "dem-tiles.py"), "--out", out_dir, merged],
        env, label="krájanie na 1° dlaždice")
    return merged


# ---------- stav medzi fázami ----------

def state_path(work):
    return os.path.join(work, STATE)


# Koľko riadkov LOGu už v stave je. Fázy sú samostatné procesy, takže bez
# tohto by v súhrne na konci ostal len log poslednej z nich.
_LOG_SAVED = 0


def save_state(work, state):
    global _LOG_SAVED
    state["log"] = state.get("log", []) + LOG[_LOG_SAVED:]
    _LOG_SAVED = len(LOG)
    os.makedirs(work, exist_ok=True)
    with open(state_path(work), "w") as f:
        json.dump(state, f, indent=1)


def load_state(work):
    """Stav z fázy `plan`. Keď chýba, volajúci spustil fázy v zlom poradí –
    a to je chyba zadania, nie niečo, čo sa dá dopočítať: `read` bez plánu by
    prečítal iné okno, než na aké sa pýtal `plan`."""
    p = state_path(work)
    if not os.path.exists(p):
        raise SystemExit(
            f"::error::Chýba {p} – fáza sa spúšťa až po `--stage=plan`.")
    with open(p) as f:
        return json.load(f)


def credentials():
    """Prihlásenie na Drive z prostredia, alebo None (= verejný odkaz).

    Chybu prekladá na `::error::` a pád: kto secret nastavil, čaká prihlásený
    beh, a ticho spadnúť na verejný denný limit sa pozná až vtedy, keď Drive
    po pol dni prestane púšťať. Rozpis vo `workers/drive-auth.py`.
    """
    try:
        creds = auth.from_env()
        if creds is None:
            return None
        # Token si vypýtaj HNEĎ: keď ho Drive nedá, povie sa to tu a nie po
        # hodine čítania. Toto je tvrdá chyba – s pokazeným prihlásením sa
        # nedá čítať a ticho prejsť na verejný limit je zakázané.
        creds.token()
    except auth.AuthError as exc:
        raise SystemExit(f"::error::{exc}")
    try:
        auth.whoami(creds)
    except auth.AuthError as exc:
        # Toto je len na výpis „ktorým účtom čítame". Keď zlyhá práve táto
        # jedna metadátová požiadavka, čítanie tým nekončí – token platí
        # a bloky si prípadnú chybu ohlásia samy a presnejšie.
        log(f"::warning::Účet sa nepodarilo zistiť ({exc}). Čítanie beží "
            f"prihlásené, len sa v logu nebude vedieť ktorým účtom.")
    return creds


def serve_drive(port=0):
    """Shim nad OBOMA súbormi DMR 5.0. Vracia (base, sizes, stats, creds).

    Jediné miesto, kde je napísané, ktoré id sa podávajú a s akým prihlásením
    – `open_source` aj `slope-chunks.py` si to volajú odtiaľto, nech sa zdroj
    dá vymeniť na jednom mieste.
    """
    creds = credentials()
    base, sizes, stats = drive.serve(
        {TIF_NAME: TIF_ID, TIF_NAME + ".ovr": OVR_ID}, port, creds=creds)
    return base, sizes, stats, creds


def auth_check():
    """Povedz, ktorým účtom sa bude čítať, a či na oba súbory vidí.

    Vlastný krok workflowu preto, že je LACNÝ (token + dve metadátové
    požiadavky) a odpovedá na to, čo sa inak zistí až vtedy, keď Drive
    prestane dávať dáta: čítame ako vlastník s vysokým stropom, alebo
    verejným odkazom s denným?
    """
    print("Prístup k DMR 5.0 na Google Drive:")
    try:
        return auth.do_check(argparse.Namespace(file=[TIF_ID, OVR_ID]))
    except auth.AuthError as exc:
        print(f"::error::{exc}")
        return 2


def open_source(args):
    """Shim nad Drive + otvorený raster.

    Vracia (src, env, info, native_m, stats, creds). Robia to fázy `plan`
    a `read`; `finish` už na sieť nesiaha vôbec, počíta nad blokmi na disku –
    a práve preto je oddelená.
    """
    log("Otváram DMR 5.0 (ETRS89) na Drive cez lokálny shim…")
    base, sizes, stats, creds = serve_drive(args.port)
    log(f"  prístup: {auth.describe(creds)}")
    for name, size in sizes.items():
        log(f"  {name}: {size / 2**30:.2f} GiB")
    src = f"/vsicurl/{base}/{TIF_NAME}"
    env = drive.gdal_env()
    if args.geoid == "egm2008":
        # Mriežku geoidu si PROJ stiahne z CDN, keď ju nemá lokálne.
        env["PROJ_NETWORK"] = "ON"

    t0 = time.time()
    info = json.loads(run(["gdalinfo", "-json", "-nomd", src], env).stdout)
    ov = [o["size"] for o in info["bands"][0].get("overviews", [])]
    log(f"  otvorené za {time.time() - t0:.1f} s: "
        f"{info['size'][0]:,} × {info['size'][1]:,} px, "
        f"mriežka {abs(info['geoTransform'][1]):g} m, {len(ov)} úrovní pyramíd")
    if not ov:
        log("::warning::Pyramídy sa nenašli – hrubšie mriežky sa budú počítať "
            "z plného 1 m rastra a potrvá to násobne dlhšie.")
    return src, env, info, abs(info["geoTransform"][1]), stats, creds


def drive_totals(state, stats):
    """Prirátaj, čo z Drive prišlo v tejto fáze, k tomu, čo prišlo v predošlých.

    Fázy sú samostatné procesy, takže počítadlo shimu začína v každej od nuly –
    bez tohto by súhrn na konci tvrdil, že sa stiahlo len to z poslednej.
    """
    if stats is None:
        return state.get("drive_bytes", 0), state.get("drive_requests", 0)
    with stats["lock"]:
        req, got = stats["requests"], stats["bytes"]
    state["drive_bytes"] = state.get("drive_bytes", 0) + got
    state["drive_requests"] = state.get("drive_requests", 0) + req
    return state["drive_bytes"], state["drive_requests"]


# ---------- fáza 1: plán ----------

def stage_plan(args):
    """Otvor zdroj, spočítaj okno a bloky a povedz, čo to bude stáť.

    Vlastná fáza preto, že je LACNÁ (otvorenie stojí 9 požiadaviek a 0,3 MB)
    a odpovedá na jedinú otázku, ktorá pred hodinovým čítaním zaujíma: koľko
    toho bude. Trojhodinový krok, ktorý spadne na timeout, je najhorší možný
    výsledok – minie celý rozpočet a nevyrobí nič.
    """
    src, env, info, native_m, stats, creds = open_source(args)
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    wkt_file = os.path.join(args.work, "src.wkt")
    with open(wkt_file, "w") as f:
        f.write((info.get("coordinateSystem") or {}).get("wkt", ""))

    area_name, bbox = raster.resolve_area(args.area, os.path.join(_HERE, "areas.json"))

    # DLAŽDICOVÝ REŽIM S VÝREZOM. Build mapy si dlaždice hľadá podľa mena
    # (`N49E020.tif`) a to meno je sľub: „tento celý stupeň je tu". Keby sa
    # pod ním v release ocitol len prienik s bboxom, ďalší beh by kontrolou
    # prešiel („dlaždica tam je“) a tieňovanie by ticho končilo v polovici
    # mapy. Okno sa preto rozširuje na celé stupne – čítať sa musí celá
    # dlaždica, nie len to, čo dnes treba.
    tiles_out = bbox is None or args.tiles
    if bbox is not None and args.tiles:
        w, s, e, n = bbox
        bbox = (float(math.floor(w)), float(math.floor(s)),
                float(math.ceil(e)), float(math.ceil(n)))
        deg = int((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        area_name += (f" → celé stupne {bbox[0]:g},{bbox[1]:g}…{bbox[2]:g},"
                      f"{bbox[3]:g} ({deg} dlaždíc)")

    log(f"Územie: {area_name}, cieľová mriežka {args.grid_m:g} m")

    if bbox is None:
        box = (info["geoTransform"][0], info["geoTransform"][3]
               + info["geoTransform"][5] * info["size"][1],
               info["geoTransform"][0] + info["geoTransform"][1] * info["size"][0],
               info["geoTransform"][3])
    else:
        box = src_window(bbox, wkt_file, info, env)
    parts, (nx, ny) = blocks(box, args.grid_m, args.jobs)

    km_x, km_y = (box[2] - box[0]) / 1000, (box[3] - box[1]) / 1000
    area_m2 = (box[2] - box[0]) * (box[3] - box[1])
    cells = area_m2 / args.grid_m ** 2

    # ČO SA BUDE ČÍTAŤ, NIE ČO VYPADNE. Cena je počet pixelov, ktoré prídu
    # z Drive, a tie nie sú bunky cieľa: `gdal_translate -ovr AUTO` siahne po
    # najhrubšej pyramíde, ktorá je ešte jemnejšia než cieľ. Pri cieli 5 m to
    # je úroveň 4 m, čiže (5/4)² = 1,6× viac pixelov, než má výstup – a to je
    # presne ten rozdiel medzi „13 minút" a „21 minút na stupeň".
    read_m = native_m
    for o in info["bands"][0].get("overviews", []):
        r = native_m * info["size"][0] / o["size"][0]
        if read_m < r <= args.grid_m + 1e-9:
            read_m = r
    src_px = area_m2 / read_m ** 2

    # Rýchlosť je meraná pri `--jobs=12`; pri inom počte vlákien sa škáluje,
    # ale nie donekonečna – nad ~16 vláknami Drive začne odpovedať 403.
    rate = PX_PER_MIN * min(args.jobs, 16) / 12.0
    est_min = src_px / max(rate, 1.0)
    est_gb = src_px * BYTES_PER_PX / 1e9
    asset = args.asset or f"ugkk-{args.area}.tif"

    print("── Plán čítania z Drive ─────────────────────────────")
    print(f"  územie          {area_name}")
    print(f"  okno            {km_x:.1f} × {km_y:.1f} km "
          f"({km_x * km_y:.0f} km²) v EPSG:{SRC_EPSG}")
    print(f"  cieľová mriežka {args.grid_m:g} m → {cells / 1e6:.1f} mil. buniek")
    print(f"  číta sa z       {read_m:g} m "
          + ("(plné rozlíšenie)" if read_m == native_m else "(pyramída)")
          + f" → {src_px / 1e6:.1f} mil. px")
    print(f"  blokov          {len(parts)} ({nx}×{ny}), {args.jobs} naraz")
    print(f"  odhad           ~{est_min:.0f} min, ~{est_gb:.2f} GB z Drive")
    print("  výstup          " + (f"1° dlaždice do {args.out}/" if tiles_out
                                  else f"{args.out}/{asset}"))
    print("  výšky           " + ("EGM2008 (≈ Bpv)" if args.geoid == "egm2008"
                                  else "elipsoidické ETRS89"))
    print("─────────────────────────────────────────────────────", flush=True)
    if est_min > 120:
        print(f"::warning::Odhad čítania je ~{est_min / 60:.1f} h. Kratšie to "
              f"ide s menším územím alebo hrubšou mriežkou (--grid-m).")

    state = {
        "area": args.area,
        "area_name": area_name,
        # Ako sa k dátam pristupovalo, patrí do súhrnu – prepnutie na verejný
        # odkaz (zmazaný secret) sa inak nemá kde ukázať a zistilo by sa až
        # tým, že Drive prestane púšťať. Rovnaký dôvod ako `dem-source.txt`:
        # nesie sa, čo sa NAOZAJ použilo.
        "drive_auth": auth.describe(creds),
        "bbox": list(bbox) if bbox is not None else None,
        "box": list(box),
        "blocks": [list(p) for p in parts],
        "grid_m": args.grid_m,
        "native_m": native_m,
        "tiles": tiles_out,
        "geoid": args.geoid,
        "asset": asset,
        "src_px": list(info["size"]),
        "cells": cells,
        "est_min": est_min,
    }

    # ROZČÍTANÉ BLOKY SÚ DOBRÉ, LEN KEĎ SEDIA NA TENTO PLÁN. Fáza `read`
    # pozná blok podľa poradového čísla v mene (`blok-0007.tif`), takže po
    # zmene územia či mriežky by pod tým istým menom ležal úplne iný kus zeme
    # a mozaika by bola poskladaná z dvoch rôznych zadaní. Rovnaký plán =
    # opakovaný krok dopočíta zvyšok; iný plán = začína sa odznova.
    old = None
    if os.path.exists(state_path(args.work)):
        with open(state_path(args.work)) as f:
            old = json.load(f)
    same = old is not None and all(old.get(k) == state[k]
                                   for k in ("box", "blocks", "grid_m", "geoid"))
    stale = [f for f in os.listdir(args.work)
             if f.startswith("blok-") and f.endswith((".tif", ".part"))]
    if stale and not same:
        for f in stale:
            os.remove(os.path.join(args.work, f))
        log(f"  plán sa zmenil – zahodených {len(stale)} blokov z predošlého")
    elif stale:
        log(f"  {len(stale)} blokov z predošlého pokusu sedí na tento plán "
            f"a znova sa čítať nebudú")

    if same and old.get("t_start"):
        state["t_start"] = old["t_start"]
        state["drive_bytes"] = old.get("drive_bytes", 0)
        state["drive_requests"] = old.get("drive_requests", 0)
        state["log"] = old.get("log", [])
    else:
        state["t_start"] = time.time()
    drive_totals(state, stats)
    save_state(args.work, state)
    return state


# ---------- fáza 2: čítanie ----------

def stage_read(args, state):
    """Bloky z Drive na disk. Jediná fáza, ktorá siaha na sieť – a tá dlhá."""
    src, env, _info, _native, stats, creds = open_source(args)
    state["drive_auth"] = auth.describe(creds)
    parts = [tuple(p) for p in state["blocks"]]
    log(f"  {len(parts)} blokov, {args.jobs} naraz, cieľová mriežka "
        f"{state['grid_m']:g} m")
    read_blocks(src, parts, state["grid_m"], args.work, args.jobs, env,
                state["native_m"])
    got, req = drive_totals(state, stats)
    log(f"Z Drive doteraz {got / 1e9:.2f} GB v {req:,} požiadavkách")
    save_state(args.work, state)
    return state


# ---------- fáza 3: zloženie výstupu ----------

def stage_finish(args, state):
    """Bloky na disku → COG alebo 1° dlaždice. Na sieť sa už nesiaha.

    Výnimka je mriežka geoidu, ktorú si PROJ stiahne z CDN – pár MB, nie Drive.
    """
    env = drive.gdal_env()
    if state["geoid"] == "egm2008":
        env["PROJ_NETWORK"] = "ON"
    parts = sorted(os.path.join(args.work, f)
                   for f in os.listdir(args.work)
                   if f.startswith("blok-") and f.endswith(".tif"))
    if not parts:
        raise SystemExit(f"::error::V {args.work} nie je ani jeden blok – "
                         f"fáza `read` nebežala alebo spadla.")
    if len(parts) != len(state["blocks"]):
        raise SystemExit(
            f"::error::Na disku je {len(parts)} blokov, plán ich má "
            f"{len(state['blocks'])}. Mozaika s dierou by sa doplnila nulami "
            f"a z nuly je v mape more – spusti fázu `read` znova.")
    log(f"Skladám {len(parts)} blokov, "
        f"{sum(os.path.getsize(p) for p in parts) / 1048576:.0f} MB na disku")

    if state["tiles"]:
        country_tiles(parts, args.out, args.work, env, state["geoid"])
        made = sorted(f for f in os.listdir(args.out) if f.endswith(".tif"))
        log(f"Hotovo: {len(made)} dlaždíc v {args.out}")
    else:
        dest = to_wgs84(parts, os.path.join(args.out, state["asset"]),
                        state["bbox"], state["grid_m"], args.work, env,
                        state["geoid"])
        made = [os.path.basename(dest)]

    # Bloky až teraz: kým výstup nie je hotový, sú to jediné prečítané dáta
    # a opakovaný `read` by ich musel stiahnuť znova.
    for p in parts:
        os.remove(p)
    state["made"] = made
    save_state(args.work, state)
    return state


def write_summary(path, state):
    got = state.get("drive_bytes", 0)
    req = state.get("drive_requests", 0)
    made = state.get("made", [])
    with open(path, "w") as f:
        f.write("## DMR 5.0 (ETRS89) z Drive\n\n")
        f.write("| vec | hodnota |\n|---|---|\n")
        f.write(f"| územie | {state['area_name']} |\n")
        f.write(f"| mriežka | {state['grid_m']:g} m |\n")
        f.write(f"| okno | {(state['box'][2] - state['box'][0]) / 1000:.1f} × "
                f"{(state['box'][3] - state['box'][1]) / 1000:.1f} km, "
                f"{len(state['blocks'])} blokov |\n")
        f.write(f"| zdroj | {state['src_px'][0]:,}×{state['src_px'][1]:,} px "
                f"@ {state['native_m']:g} m, EPSG:{SRC_EPSG} |\n")
        f.write(f"| výšky | {'EGM2008 (≈ Bpv)' if state['geoid'] == 'egm2008' else 'elipsoidické ETRS89'} |\n")
        f.write(f"| z Drive | {got / 1e9:.2f} GB / {req:,} požiadaviek |\n")
        f.write(f"| prístup | {state.get('drive_auth', '?')} |\n")
        f.write(f"| trvanie | {(time.time() - state['t_start']) / 60:.1f} min "
                f"(odhad bol {state['est_min']:.0f} min) |\n")
        f.write(f"| výstup | {', '.join(f'`{m}`' for m in made[:12]) or '–'} |\n")
        f.write("\n<details><summary>Log</summary>\n\n```\n"
                + "\n".join(state.get("log", []) + LOG[_LOG_SAVED:])
                + "\n```\n\n</details>\n")


# ---------- beh ----------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--area", default="cele_slovensko",
                    help="kľúč z workers/areas.json, `cele_slovensko`, alebo bbox W,S,E,N")
    ap.add_argument("--grid-m", type=float, default=1.0)
    ap.add_argument("--out", default="out")
    ap.add_argument("--work", default="drive-work")
    ap.add_argument("--asset", default=None,
                    help="meno výsledku pri výreze; predvolene ugkk-<area>.tif. "
                         "Pri bboxe v --area je povinné – `ugkk-20,49,21,50.tif` "
                         "si build vypýtať nevie.")
    ap.add_argument("--jobs", type=int, default=12,
                    help="koľko blokov sa číta naraz; nad ~16 začne Drive "
                         "odpovedať 403 a čakanie zožerie viac, než sa získa")
    ap.add_argument("--geoid", choices=("egm2008", "elipsoid"), default="egm2008")
    ap.add_argument("--tiles", action="store_true",
                    help="výstup sú 1° dlaždice (dem-dmr5) aj pri zadanom "
                         "výreze – okno sa rozšíri na celé stupne. Bez toho "
                         "je z výrezu jeden COG (dem-ugkk).")
    ap.add_argument("--stage", choices=("all", "plan", "read", "finish"),
                    default="all",
                    help="ktorú fázu spustiť; stav si podávajú cez --work")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--probe-only", action="store_true",
                    help="len otvor zdroj a vypíš, čo v ňom je")
    ap.add_argument("--auth-check", action="store_true",
                    help="povedz, ktorým účtom sa bude z Drive čítať a či "
                         "na oba súbory vidí; nič sa nečíta")
    ap.add_argument("--summary", default=None)
    args = ap.parse_args()

    if args.auth_check:
        return auth_check()

    if args.probe_only:
        _src, _env, info, native_m, _stats, _creds = open_source(args)
        log(f"  CRS: {(info.get('coordinateSystem') or {}).get('wkt', '')[:80]}…")
        log(f"  origin: {info['geoTransform'][0]}, {info['geoTransform'][3]}")
        for i, o in enumerate(info["bands"][0].get("overviews", [])):
            w, h = o["size"]
            log(f"    úroveň {i}: {w:,} × {h:,} px = "
                f"{native_m * info['size'][0] / w:.0f} m")
        return 0

    # Fázy sa reťazia zhora nadol; `all` je všetky tri v jednom procese.
    state = None
    if args.stage in ("all", "plan"):
        state = stage_plan(args)
    if args.stage in ("all", "read"):
        state = stage_read(args, state or load_state(args.work))
    if args.stage in ("all", "finish"):
        state = stage_finish(args, state or load_state(args.work))
        got, req = state.get("drive_bytes", 0), state.get("drive_requests", 0)
        log(f"Z Drive prišlo {got / 1e9:.2f} GB v {req:,} požiadavkách, "
            f"celý beh {(time.time() - state['t_start']) / 60:.1f} min")

    if args.summary:
        write_summary(args.summary, state or load_state(args.work))
    return 0


if __name__ == "__main__":
    sys.exit(main())

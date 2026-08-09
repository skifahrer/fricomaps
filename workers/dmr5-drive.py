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

Použitie:
    python3 workers/dmr5-drive.py --area=vysoke_tatry --grid-m=1 \\
        --out=out --asset=ugkk-vysoke_tatry.tif
    python3 workers/dmr5-drive.py --area=cele_slovensko --grid-m=5 --out=out
    python3 workers/dmr5-drive.py --area=20,49,21,50 --grid-m=5 --tiles --out=out
    python3 workers/dmr5-drive.py --probe-only
"""
import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))

# Drive file id. Sú to verejné odkazy „ktokoľvek s odkazom", nie tajomstvo –
# preto smú byť v repozitári a nie v secrets.
TIF_ID = "1A4q6T-S8IZbODMDsowGr_DihzcQf22wI"
OVR_ID = "1p07TFZwG6LzbdkWdK3gV_ccvOubd2Xqi"
TIF_NAME = "dmr5_etrs89.tif"

SRC_EPSG = 3046           # ETRS89 / TM zone N34, priamo z GeoTIFF tagov
# Elipsoidické výšky nad GRS80 (ETRS89) → ortometrické (EGM2008 ≈ Bpv).
SRC_VERT = 4937           # ETRS89 (3D, elipsoidické výšky)
DST_VERT = 3855           # EGM2008 height


def load(name, path):
    """workers/*.py sa kvôli pomlčke v mene nedajú `import`-núť normálne."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drive = load("drive_serve", "drive-serve.py")
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


def read_blocks(src, box, grid_m, work, jobs, env, native_m=1.0):
    """Bloky sa čítajú SÚBEŽNE – latencia Drive sa inak nedá prekonať.

    Vracia zoznam hotových súborov. Prázdne bloky (samé nodata) sa
    nezahadzujú: diera v mozaike by sa v ďalšom kroku doplnila nulami.
    """
    os.makedirs(work, exist_ok=True)
    parts, (nx, ny) = blocks(box, grid_m, jobs)
    log(f"  {len(parts)} blokov ({nx}×{ny}), {jobs} naraz")

    resample = [] if abs(grid_m - native_m) < 1e-9 else ["-r", "average"]
    tr = [] if abs(grid_m - native_m) < 1e-9 else ["-tr", repr(grid_m), repr(grid_m)]
    done = []
    t0 = time.time()

    def one(idx_part):
        idx, (bw, bs, be, bn) = idx_part
        dest = os.path.join(work, f"blok-{idx:04d}.tif")
        cmd = ["gdal_translate", "-q",
               "-projwin", repr(bw), repr(bn), repr(be), repr(bs),
               *tr, *resample, "-ovr", "AUTO",
               "-of", "GTiff", "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3",
               "-co", "TILED=YES", "-co", "BIGTIFF=YES",
               src, dest]
        subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)
        return dest

    with raster.Heartbeat(f"čítanie {len(parts)} blokov z Drive"):
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            for dest in ex.map(one, enumerate(parts)):
                done.append(dest)
    mb = sum(os.path.getsize(p) for p in done) / 1048576
    log(f"  bloky hotové za {(time.time() - t0) / 60:.1f} min, {mb:.0f} MB na disku")
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
                    help="meno výsledku pri výreze; predvolene ugkk-<area>.tif")
    ap.add_argument("--jobs", type=int, default=12,
                    help="koľko blokov sa číta naraz; nad ~16 začne Drive "
                         "odpovedať 403 a čakanie zožerie viac, než sa získa")
    ap.add_argument("--geoid", choices=("egm2008", "elipsoid"), default="egm2008")
    ap.add_argument("--tiles", action="store_true",
                    help="výstup sú 1° dlaždice (dem-dmr5) aj pri zadanom "
                         "výreze – okno sa rozšíri na celé stupne. Bez toho "
                         "je z výrezu jeden COG (dem-ugkk).")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--probe-only", action="store_true",
                    help="len otvor zdroj a vypíš, čo v ňom je")
    ap.add_argument("--summary", default=None)
    args = ap.parse_args()

    t_all = time.time()
    log("Otváram DMR 5.0 (ETRS89) na Drive cez lokálny shim…")
    base, sizes, stats = drive.serve(
        {TIF_NAME: TIF_ID, TIF_NAME + ".ovr": OVR_ID}, args.port)
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
    native_m = abs(info["geoTransform"][1])

    if args.probe_only:
        log(f"  CRS: {(info.get('coordinateSystem') or {}).get('wkt', '')[:80]}…")
        log(f"  origin: {info['geoTransform'][0]}, {info['geoTransform'][3]}")
        for i, (w, h) in enumerate(ov):
            log(f"    úroveň {i}: {w:,} × {h:,} px = "
                f"{native_m * info['size'][0] / w:.0f} m")
        return 0

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
    parts = read_blocks(src, box, args.grid_m, args.work, args.jobs, env, native_m)

    if bbox is None or args.tiles:
        country_tiles(parts, args.out, args.work, env, args.geoid)
        made = sorted(f for f in os.listdir(args.out) if f.endswith(".tif"))
        log(f"Hotovo: {len(made)} dlaždíc v {args.out}")
    else:
        asset = args.asset or f"ugkk-{args.area}.tif"
        dest = to_wgs84(parts, os.path.join(args.out, asset), bbox,
                        args.grid_m, args.work, env, args.geoid)
        made = [os.path.basename(dest)]

    for p in parts:
        os.remove(p)
    with stats["lock"]:
        req, got = stats["requests"], stats["bytes"]
    log(f"Z Drive prišlo {got / 1e9:.2f} GB v {req:,} požiadavkách, "
        f"celý beh {(time.time() - t_all) / 60:.1f} min")

    if args.summary:
        with open(args.summary, "w") as f:
            f.write("## DMR 5.0 (ETRS89) z Drive\n\n")
            f.write("| vec | hodnota |\n|---|---|\n")
            f.write(f"| územie | {area_name} |\n")
            f.write(f"| mriežka | {args.grid_m:g} m |\n")
            f.write(f"| zdroj | {info['size'][0]:,}×{info['size'][1]:,} px "
                    f"@ {native_m:g} m, EPSG:{SRC_EPSG} |\n")
            f.write(f"| výšky | {'EGM2008 (≈ Bpv)' if args.geoid == 'egm2008' else 'elipsoidické ETRS89'} |\n")
            f.write(f"| z Drive | {got / 1e9:.2f} GB / {req:,} požiadaviek |\n")
            f.write(f"| trvanie | {(time.time() - t_all) / 60:.1f} min |\n")
            f.write(f"| výstup | {', '.join(f'`{m}`' for m in made[:12])} |\n")
            f.write("\n<details><summary>Log</summary>\n\n```\n"
                    + "\n".join(LOG) + "\n```\n\n</details>\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
DMR 5.0 z Drive: okno, bloky a výstupy – teda „ktorý kus a v akom tvare".

ČO JE TU. Geometria a zápis: z WGS84 bboxu okno v projekcii zdroja, rozdelenie
na bloky, ich súbežné čítanie z Drive a nakoniec dva možné výstupy – jeden COG
vo WGS84 (výrez na pohorie) alebo 1°×1° dlaždice (celá krajina). Prihlásenie,
sondu, fázy behu a CLI rieši `workers/drive/dmr5.py`, ktorý si tento modul
berie.

PREČO ZVLÁŠŤ. `dmr5-drive.py` mal 888 riadkov a v jednom takom súbore sa nedá
rýchlo nájsť, čo sa zmenilo (pravidlo 5 v CLAUDE.md, strop 800 stráži
`Lint workflows`). Rez je tam, kde sa mení otázka: tam „ako sa k tým dátam
dostať a čo to bude stáť", tu „ktorý kus zeme a v akom tvare z nich urobiť".

DENNÍK JE TU, NIE VEDĽA. `LOG`, `log()` a `run()` používajú obe strany, a keby
mala každá svoju kópiu `LOG`, súhrn behu by mal polovicu riadkov a nikde by
nebolo vidieť prečo (pravidlo 1: jedna otázka, jedno miesto). `dmr5-drive.py`
si ich preto berie odtiaľto.

Spúšťa sa ako modul, nie z príkazovej riadky:
    cut = load("dmr5_cut", "dmr5-cut.py")
"""
import importlib.util
import math
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
# Priečinok = job, súbor = krok; spoločné veci ležia o úroveň vyššie.
_WORKERS = os.path.dirname(_HERE)          # workers/
_DATA = os.path.join(_WORKERS, "data")     # číselníky (areas, regions, zdroje)

# PROJEKCIA A VÝŠKY ZDROJA. Sú tu, a nie vedľa, lebo ich potrebuje `gdalwarp`
# v tomto module – teda to jediné miesto, kde sa naozaj prevádza. `dmr5-drive.py`
# si `SRC_EPSG` berie odtiaľto na výpis do plánu a súhrnu.
SRC_EPSG = 3046           # ETRS89 / TM zone N34, priamo z GeoTIFF tagov
# Elipsoidické výšky nad GRS80 (ETRS89) → ortometrické (EGM2008 ≈ Bpv).
SRC_VERT = 4937           # ETRS89 (3D, elipsoidické výšky)
DST_VERT = 3855           # EGM2008 height


def load(name, path):
    """workers/*.py sa kvôli pomlčke v mene nedajú `import`-núť normálne."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Heartbeat a `run_live`: dlhý gdalwarp musí hovoriť, ako ďaleko je.
raster = load("dmr5_raster", "dmr5-raster.py")

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


def country_tiles(parts, out_dir, work, env, geoid, window=None):
    """Mozaika → dlaždice 1°×1° vo WGS84, ako ich čaká build mapy.

    Používa sa na celé Slovensko aj na `--tiles` s výrezom. Okno sa pred
    čítaním rozširuje na celé stupne a TO ISTÉ OKNO sa podáva do `dem/tiles.py`
    – rez podľa rozsahu rastra totiž nestačí: prevod do WGS84 okno vydúva
    (`21,49…22,50` vyšlo ako `21.000,48.996 … 22.013,49.628`), takže z neho
    vypadli aj tri cudzie stupne po pár set metroch. Uložili sa pod menami
    `N48E021.tif`, `N48E022.tif`, `N49E020.tif`, ktoré tvrdia, že tam je celý
    stupeň – a vrstevnice Prešovského kraja potom skončili v jednom štvorci
    (behy 31476448895 → 31484544154). `window=None` = celé Slovensko, kde je
    pravdivá každá dlaždica z rastra.
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
    cmd = ["python3", os.path.join(_WORKERS, "dem", "tiles.py"),
           "--out", out_dir, merged]
    if window is not None:
        cmd.append("--window=" + ",".join(f"{v:g}" for v in window))
    run(cmd, env, label="krájanie na 1° dlaždice")
    return merged

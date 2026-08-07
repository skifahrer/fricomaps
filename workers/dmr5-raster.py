#!/usr/bin/env python3
"""
DMR 5.0 ako JEDEN GeoTIFF vo vzdialenom ZIPe – čítaný cez /vsizip//vsicurl/.

ČO JE V ARCHÍVE (zmerané behom 31184095104, `mode: len plán`):

    dmr5_0/dmr5_jtsk03.tif        151,43 GB   celé Slovensko, 1 m, jeden raster
    dmr5_0/dmr5_jtsk03.tif.ovr     46,28 GB   prehľadové úrovne (pyramídy)
    dmr5_0/dmr5_jtsk03.tfw                    world file
    dmr5_0/dmr5_jtsk03.tif.aux.xml / .xml     metadáta
    INFO_*.txt, 4× PDF, prehlad_lokalit_*.shp licencie a prehľad lokalít

Čakali sme textové výškové body po blokoch. Nie sú. Je to jeden súvislý
raster, takže sa nedá deliť po položkách archívu – a celé rozdeľovanie na
časti (workers/dmr5-chunk.py) tu nemá čo deliť.

ZATO SA DÁ ČÍTAŤ PRIAMO. GDAL vie `/vsizip//vsicurl/URL/cesta.tif`: ZIP číta
cez HTTP Range a GeoTIFF je dlaždicovaný, takže si vypýta len tie dlaždice,
ktoré potrebuje. Žiadne sťahovanie 151 GB na disk, žiadne medzivýsledky.

JEDNA VEC O TOM ALE PLATÍ A URČUJE CELÝ NÁVRH: položka v ZIPe je uložená
deflate-om (ÚGKK ju tak zabalil), a v deflate prúde sa nedá skočiť dopredu –
dá sa doň len rozbaliť od začiatku. Cena čítania je preto úmerná tomu, AKO
ĎALEKO V SÚBORE dáta ležia. Zmerané na napodobenine (44 MB ZIP, dlaždicovaný
DEFLATE GeoTIFF, HTTP server s Range):

    výrez na začiatku rastra      0,5 MB    1 % archívu
    výrez na konci rastra        44,1 MB  100 % archívu
    celý raster 1 m → 5 m        37,8 MB   (s .ovr, 1,1 s)
    to isté bez .ovr             46,1 MB   (2,7 s)

Z toho plynú dve pravidlá, podľa ktorých je tento script napísaný:

  1. JEDEN PRECHOD, NIE VIAC. Prevzorkovanie celej krajiny sa robí jedným
     `gdal_translate -tr`, nie po dlaždiciach – N výrezov by stálo N× cestu
     od začiatku súboru. Dlaždice 1°×1° sa krájajú až z hotového malého
     rastra.
  2. SIDECARY SA NESMÚ SCHOVAŤ. `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`
     síce šetrí požiadavky, ale skryje `.ovr` aj `.tfw` – a práve `.ovr` je
     to, čo z prevzorkovania celej krajiny robí tretinovú prácu.

Použitie:
    python3 workers/dmr5-raster.py --url=URL --area=cele --grid-m=5 --out=tiles
    python3 workers/dmr5-raster.py --url=URL --area=vysoke_tatry --grid-m=1 \\
        --out=out --asset=ugkk-vysoke_tatry.tif
    python3 workers/dmr5-raster.py --url=URL --probe-only
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

# Prípony, ktoré vieme otvoriť ako raster. `.ovr` a `.aux.xml` sú sidecary –
# tie sa NEotvárajú samostatne, GDAL si ich nájde sám vedľa hlavného súboru.
RASTER_EXT = (".tif", ".tiff", ".img", ".vrt", ".dem")
SIDECAR = (".ovr", ".aux.xml", ".xml", ".tfw", ".prj", ".rrd")

GDAL_ENV = {
    **os.environ,
    # Bez PAM by si gdalinfo -stats odkladal .aux.xml vedľa výstupov a tie by
    # sa viezli do releasu ako smetie.
    "GDAL_PAM_ENABLED": "NO",
    # Vyrovnávacia pamäť na dlaždice: čím väčšia, tým menej sa to isté číta
    # dvakrát. Runner má 16 GB, 2 GB je bezpečné.
    "GDAL_CACHEMAX": os.environ.get("GDAL_CACHEMAX", "2048"),
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": os.environ.get("VSI_CACHE_SIZE", str(256 * 1024 * 1024)),
    "GDAL_NUM_THREADS": "ALL_CPUS",
    # ZÁMERNE NEnastavujeme GDAL_DISABLE_READDIR_ON_OPEN – viď hlavička.
}


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True,
                          env=GDAL_ENV, **kw)


def run_live(cmd):
    """Dlhé kroky idú do logu naživo – hodinu tichého behu sa nedá odlíšiť
    od zaseknutého behu."""
    return subprocess.run(cmd, check=True, env=GDAL_ENV)


def vsi_path(url, member):
    """`/vsizip//vsicurl/<url>/<cesta v archíve>`."""
    return f"/vsizip//vsicurl/{url}/{member}"


def pick_member(plan_path, explicit):
    """Ktorý súbor v archíve je ten raster. Sidecary sa preskakujú."""
    if explicit:
        return explicit
    plan = json.load(open(plan_path))
    best = None
    for e in plan["entries"]:
        low = e["name"].lower()
        if any(low.endswith(s) for s in SIDECAR):
            continue
        if not low.endswith(RASTER_EXT):
            continue
        if best is None or e["usize"] > best["usize"]:
            best = e
    if not best:
        raise SystemExit("::error::V pláne nie je ani jeden raster – pozri "
                         "inventár v súhrne behu a zadaj --member ručne.")
    return best["name"]


def probe(vsi, log):
    """Hlavička rastra. Číta pár stoviek kB, aj keď má súbor 151 GB."""
    t0 = time.time()
    try:
        info = json.loads(run(["gdalinfo", "-json", vsi]).stdout)
    except subprocess.CalledProcessError as exc:
        log("::error::Raster sa nedá otvoriť cez /vsizip//vsicurl/: "
            f"{(exc.stderr or '').strip()[:400]}")
        return None
    band = info["bands"][0]
    gt = info["geoTransform"]
    wkt = (info.get("coordinateSystem") or {}).get("wkt", "")
    ovr = [o.get("size") for o in band.get("overviews", [])]
    out = {
        "size": info["size"],
        "pixel": [abs(gt[1]), abs(gt[5])],
        "type": band["type"],
        "block": band.get("block"),
        "nodata": band.get("noDataValue"),
        "compression": (info.get("metadata", {}).get("IMAGE_STRUCTURE", {})
                        .get("COMPRESSION")),
        "crs": wkt.split('"')[1] if '"' in wkt else "?",
        "overviews": ovr,
        "seconds": round(time.time() - t0, 1),
    }
    log(f"Raster: {out['size'][0]}×{out['size'][1]} px, "
        f"mriežka {out['pixel'][0]}×{out['pixel'][1]}, {out['type']}")
    log(f"  CRS {out['crs']}, kompresia {out['compression']}, "
        f"dlaždica {out['block']}, nodata {out['nodata']}")
    note = ovr if ovr else "(žiadne – prevzorkovanie prečíta plnú veľkosť)"
    log(f"  prehľadových úrovní: {len(ovr)} {note}")
    log(f"  hlavička prečítaná za {out['seconds']} s")
    return out


def degrees_per_metre(lat):
    """Krok mriežky v stupňoch pre daný krok v metroch na tejto šírke."""
    return (1.0 / (111320 * math.cos(math.radians(lat))), 1.0 / 110540)


def whole_country(vsi, grid_m, work, out_dir, log):
    """Celá krajina: JEDEN prechod na hrubšiu mriežku, potom dlaždice.

    Krájať 1° dlaždice priamo zo zdroja by znamenalo prejsť ten deflate prúd
    toľkokrát, koľko je dlaždíc. Preto sa najprv prevzorkuje (sekvenčne, raz)
    a až malý výsledok sa krája.
    """
    os.makedirs(work, exist_ok=True)
    small = os.path.join(work, "dmr5-national.tif")
    t0 = time.time()
    log(f"Prevzorkovanie celej krajiny na {grid_m} m – jeden prechod "
        f"cez celý raster, toto je tá dlhá časť…")
    run_live(["gdal_translate", "-tr", str(grid_m), str(grid_m),
              "-r", "average", "-of", "GTiff",
              "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3",
              "-co", "TILED=YES", "-co", "BIGTIFF=YES",
              "-co", "NUM_THREADS=ALL_CPUS", vsi, small])
    mb = os.path.getsize(small) / 1048576
    log(f"  hotovo za {time.time() - t0:.0f} s, {mb:.0f} MB")

    log("Krájanie na dlaždice 1°×1° vo WGS84…")
    run_live(["python3", os.path.join(_HERE, "dem-tiles.py"), "--out", out_dir, small])
    return small


def area_cut(vsi, bbox_wgs, grid_m, dest, log):
    """Výrez: gdalwarp si vypýta len okno, ktoré potrebuje.

    Cena je úmerná tomu, ako ďaleko v súbore ten výrez leží – sever je lacný,
    juh drahý. Nedá sa s tým nič robiť, deflate v ZIPe sa preskakovať nedá.
    """
    dx, dy = degrees_per_metre((bbox_wgs[1] + bbox_wgs[3]) / 2)
    t0 = time.time()
    log(f"Výrez {bbox_wgs} pri {grid_m} m "
        f"({grid_m * dx:.7f}° × {grid_m * dy:.7f}°)…")
    run_live(["gdalwarp", "-overwrite",
         "-t_srs", "EPSG:4326",
         "-te", *[str(v) for v in bbox_wgs],
         "-tr", repr(grid_m * dx), repr(grid_m * dy),
         "-r", "bilinear", "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
         "-of", "COG", "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3",
         "-co", "RESAMPLING=BILINEAR", "-co", "NUM_THREADS=ALL_CPUS",
         vsi, dest])
    mb = os.path.getsize(dest) / 1048576
    log(f"  hotovo za {time.time() - t0:.0f} s, {mb:.0f} MB")
    return dest


def resolve_area(area, areas_path):
    key = (area or "cele").strip()
    if key.lower() in ("", "cele", "cele_slovensko", "all", "vsetko"):
        return "celé Slovensko", None
    if "," in key:
        vals = [float(v) for v in key.split(",")]
        if len(vals) != 4:
            raise SystemExit(f"::error::bbox musí mať 4 čísla: {key}")
        return f"bbox {key}", tuple(vals)
    areas = json.load(open(areas_path))
    if key not in areas:
        known = ", ".join(k for k in areas if not k.startswith("_"))
        raise SystemExit(f"::error::neznámy výrez „{key}“. Známe: {known}")
    return areas[key]["name"], tuple(areas[key]["bbox"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--plan", default="plan.json",
                    help="z neho sa vyberie najväčší raster v archíve")
    ap.add_argument("--member", default="", help="cesta v archíve natvrdo")
    ap.add_argument("--area", default="cele")
    ap.add_argument("--areas", default=os.path.join(_HERE, "areas.json"))
    ap.add_argument("--grid-m", type=float, default=5.0)
    ap.add_argument("--out", default="out")
    ap.add_argument("--work", default="raster-work")
    ap.add_argument("--asset", default="", help="meno súboru pri výreze")
    ap.add_argument("--probe-only", action="store_true",
                    help="len prečítať hlavičku a skončiť")
    ap.add_argument("--summary", default="")
    ap.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = ap.parse_args()

    lines = []

    def log(m):
        print(m, flush=True)
        lines.append(m)

    member = pick_member(args.plan, args.member)
    vsi = vsi_path(args.url, member)
    log(f"Položka v archíve: {member}")
    log(f"Cesta pre GDAL:    {vsi}")

    info = probe(vsi, log)
    if info is None:
        return 3
    if args.github_output:
        with open(args.github_output, "a") as f:
            f.write(f"member={member}\n")
            f.write(f"px={info['size'][0]}x{info['size'][1]}\n")
            f.write(f"cell_m={info['pixel'][0]}\n")
            f.write(f"overviews={len(info['overviews'])}\n")

    if not info["overviews"]:
        log("::warning::Raster nemá prehľadové úrovne dostupné cez GDAL – "
            "prevzorkovanie prečíta plnú veľkosť. Ak je v archíve `.ovr`, "
            "znamená to, že sa k nemu GDAL nedostal (nezakazuj readdir).")

    if args.probe_only:
        log("Len sonda – nič sa nesťahovalo okrem hlavičky.")
        area_name = None
    else:
        area_name, bbox = resolve_area(args.area, args.areas)
        os.makedirs(args.out, exist_ok=True)
        if bbox is None:
            whole_country(vsi, args.grid_m, args.work, args.out, log)
        else:
            asset = args.asset or "ugkk-vyrez.tif"
            area_cut(vsi, bbox, args.grid_m, os.path.join(args.out, asset), log)
        made = sorted(f for f in os.listdir(args.out) if f.endswith(".tif"))
        total = sum(os.path.getsize(os.path.join(args.out, f)) for f in made)
        log(f"Hotovo: {len(made)} súborov, {total / 1048576:.0f} MB")
        if args.github_output:
            with open(args.github_output, "a") as f:
                f.write(f"files={len(made)}\n")

    if args.summary:
        with open(args.summary, "w") as f:
            f.write("## Raster priamo z archívu\n\n")
            f.write("| vec | hodnota |\n|---|---|\n")
            f.write(f"| položka | `{member}` |\n")
            f.write(f"| veľkosť | {info['size'][0]}×{info['size'][1]} px |\n")
            f.write(f"| mriežka zdroja | {info['pixel'][0]} m |\n")
            f.write(f"| CRS | {info['crs']} |\n")
            f.write(f"| kompresia | {info['compression']} |\n")
            f.write(f"| dlaždica | {info['block']} |\n")
            f.write(f"| prehľadové úrovne | {len(info['overviews'])} |\n")
            if area_name:
                f.write(f"| územie | {area_name} |\n")
                f.write(f"| cieľová mriežka | {args.grid_m} m |\n")
            f.write("\n<details><summary>Log</summary>\n\n```\n"
                    + "\n".join(lines) + "\n```\n\n</details>\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

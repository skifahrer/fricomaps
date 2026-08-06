#!/usr/bin/env python3
"""
ÚGKK otvorené dáta: jeden ZIP s DMR pre celé Slovensko → 1°×1° dlaždice.

PREČO TOTO A NIE DMR 5.0: 1 m LiDAR (DMR 5.0) sa z GitHub runnera stiahnuť
nedá – `zbgis.skgeodesy.sk` aj `zbgisws.skgeodesy.sk` timeoutujú aj pri 30 s
(zmerané v behoch 31072215798, 31075806874, 31096745697). `opendata.skgeodesy.sk`
je iný stroj a iná infraštruktúra: statické súbory, žiadny ArcGIS, žiadny WAF
pred mapovým klientom. Ak je dostupný, je to prvý model od ÚGKK, ktorý sa dá
zobrať priamo v pipeline.

MODEL JE HRUBŠÍ NEŽ DMR 5.0 A TO JE V PORIADKU: DMR 3.5 je starší a redší,
ale keď má mriežku 10 m, je aj tak **dvakrát jemnejší než Sonny (20 m)** –
a mriežka zdroja je to jediné, čo stropuje skutočný detail skál. `rock_res:
auto` si to zoberie sám: jeho dolný strop je desatina bunky DEM, takže z 2 m
(pri Sonnym) spadne na 1 m. Žiadna zmena v rock-areas.py netreba.

Aká je mriežka naozaj, sa NEHÁDA – script ju po rozbalení zmeria a vypíše.
Meno súboru (`dmr3_5-10.zip`) na 10 m ukazuje, ale meno nie je dôkaz.

ZRKADLÍ SA AKO SONNY: výsledok sú 1°×1° dlaždice `N49E019.tif`, takže si ich
build sťahuje presne tou istou cestou (`workers/fetch-dem.sh`) a nič ďalšie
sa učiť nemusí. Celé Slovensko pri 10 m je ~490 mil. buniek, čo je vo Float32
necelé 2 GB – po dlaždiciach a s DEFLATE sa to do release assetov zmestí
pohodlne.

Použitie:
    python3 workers/fetch-dem-open.py --out=tiles --bbox=16.8,47.7,22.6,49.7
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# Rastre, ktoré môžu byť v ZIPe. ÚGKK nikde nepíše, čo presne posiela, tak
# sa berie všetko, čo GDAL prečíta ako raster.
RASTER_EXT = (".tif", ".tiff", ".asc", ".xyz", ".img", ".dem", ".grd", ".vrt")


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def load_probe():
    """`smart_get` a `host_reachable` zo sondy – nekopírovať profily znova."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "probe_dem_source", os.path.join(_HERE, "probe-dem-source.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def download(url, dest, timeout):
    """Stiahne ZIP. Veľký súbor ide curl-om, nie do pamäte."""
    probe = load_probe()
    ok, why = probe.host_reachable(url, timeout=timeout)
    if not ok:
        print(f"::warning::{url}: hostiteľ neodpovedá ({why})")
        return False
    print(f"  hostiteľ odpovedá ({why}), sťahujem…", flush=True)
    ua = probe.BROWSERS[0][1]["User-Agent"]
    cmd = ["curl", "-sSL", "--fail", "--retry", "3", "--retry-delay", "5",
           "--max-time", "3600", "-A", ua, "-o", dest, url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dest):
        print(f"::warning::curl zlyhal (rc={r.returncode}): {r.stderr[:200]}")
        return False
    mb = os.path.getsize(dest) / 1048576
    print(f"  stiahnuté {mb:.0f} MB")
    # Prázdna alebo chybová odpoveď vyzerá ako súbor, kým sa nepozrieš dnu.
    with open(dest, "rb") as f:
        head = f.read(4)
    if head[:2] != b"PK":
        print(f"::warning::{dest} nie je ZIP (začína {head!r}) – server "
              f"pravdepodobne vrátil chybovú stránku.")
        return False
    return True


def unpack(zip_path, out_dir):
    """Rozbalí a vráti zoznam rastrov vnútri."""
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        print(f"  v archíve {len(names)} položiek")
        z.extractall(out_dir)
    rasters = []
    for root, _, files in os.walk(out_dir):
        for f in files:
            if f.lower().endswith(RASTER_EXT):
                rasters.append(os.path.join(root, f))
    # `.asc` a `.xyz` bez hlavičky GDAL neprečíta – nech je vidieť, čo tam je.
    if not rasters:
        sample = sorted({os.path.splitext(f)[1].lower()
                         for _, _, fs in os.walk(out_dir) for f in fs})
        print(f"::warning::V archíve nie je ani jeden známy raster. "
              f"Prípony vnútri: {', '.join(sample) or '(žiadne)'}")
    return sorted(rasters)


def describe(path):
    """Mriežka a CRS – to je to, čo rozhoduje, či sa model oplatí."""
    try:
        info = json.loads(run(["gdalinfo", "-json", path]).stdout)
    except subprocess.CalledProcessError as exc:
        return None, f"gdalinfo zlyhal: {(exc.stderr or '')[:120]}"
    gt = info.get("geoTransform") or [0, 0, 0, 0, 0, 0]
    wkt = (info.get("coordinateSystem") or {}).get("wkt", "")
    dx, dy = abs(gt[1]), abs(gt[5])
    if wkt.startswith("GEOGCRS") or wkt.startswith("GEOGCS"):
        # V stupňoch: prepočítať na metre pre naše šírky (~49° s. š.)
        dx_m = dx * 111320 * math.cos(math.radians(49))
        dy_m = dy * 110540
    else:
        dx_m, dy_m = dx, dy
    m = re.search(r'ID\["EPSG",(\d+)\]\s*\]\s*$', wkt) or re.search(
        r'"EPSG",\s*(\d+)', wkt[::-1])
    return {"cell_x_m": dx_m, "cell_y_m": dy_m,
            "size": info.get("size"), "crs": wkt.split('"')[1] if '"' in wkt else "?"}, None


def cut_tiles(vrt, out_dir, bbox):
    """Rozreže mozaiku na 1°×1° dlaždice N49E019.tif (konvencia SRTM).

    Tá istá pomenúvacia schéma ako Sonny, takže build nepotrebuje vedieť,
    z ktorého modelu dlaždice sú – rieši to len meno releasu.
    """
    w, s, e, n = bbox
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for lat in range(math.floor(s), math.ceil(n)):
        for lon in range(math.floor(w), math.ceil(e)):
            name = (f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"
                    f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}")
            dest = os.path.join(out_dir, f"{name}.tif")
            try:
                run(["gdalwarp", "-q", "-overwrite",
                     "-te", str(lon), str(lat), str(lon + 1), str(lat + 1),
                     "-te_srs", "EPSG:4326", "-t_srs", "EPSG:4326",
                     "-of", "COG", "-co", "COMPRESS=DEFLATE",
                     "-co", "PREDICTOR=3", "-co", "RESAMPLING=BILINEAR",
                     vrt, dest])
            except subprocess.CalledProcessError as exc:
                print(f"  – {name}: {(exc.stderr or '')[:100].strip()}")
                continue
            # Dlaždica mimo územia vyjde prázdna – nemá zmysel ju ukladať.
            try:
                stats = run(["gdalinfo", "-stats", "-json", dest]).stdout
                band = json.loads(stats)["bands"][0]
                if band.get("maximum") is None:
                    os.remove(dest)
                    continue
            except Exception:
                pass
            mb = os.path.getsize(dest) / 1048576
            print(f"  ✓ {name}.tif  {mb:.1f} MB", flush=True)
            made.append(dest)
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="prebije URL z dem-sources.json")
    ap.add_argument("--source", default="dmr35", help="kľúč v dem-sources.json")
    ap.add_argument("--sources", default=os.path.join(_HERE, "dem-sources.json"))
    ap.add_argument("--out", default="tiles", help="kam dať hotové dlaždice")
    ap.add_argument("--bbox", default="16.8,47.7,22.6,49.7",
                    help="rozsah, na ktorý sa krája (default Slovensko)")
    ap.add_argument("--work", default="", help="pracovný adresár")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--stats", default="", help="kam zapísať zistené (key=value)")
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args()

    src = json.load(open(args.sources)).get(args.source) or {}
    url = args.url or src.get("url", "")
    if not url:
        print(f"::error::Zdroj „{args.source}“ nemá `url` v {args.sources} "
              f"a --url nie je zadané.")
        return 2

    bbox = tuple(float(v) for v in args.bbox.split(","))
    work = args.work or os.path.join(os.path.dirname(args.out) or ".", "open-work")
    os.makedirs(work, exist_ok=True)
    zip_path = os.path.join(work, "dem.zip")

    print(f"Zdroj: {src.get('label', args.source)}")
    print(f"URL:   {url}")
    if not download(url, zip_path, args.timeout):
        # Kód 3 = „nedá sa stiahnuť", nie „všetko je zle" – volajúci sa podľa
        # neho vie rozhodnúť, či spadnúť alebo ísť na Sonnyho.
        print("::error::Nepodarilo sa stiahnuť otvorené dáta ÚGKK.")
        return 3

    print("Rozbaľujem…")
    rasters = unpack(zip_path, os.path.join(work, "unz"))
    if not rasters:
        return 3
    print(f"  rastrov: {len(rasters)}")

    meta, why = describe(rasters[0])
    if not meta:
        print(f"::error::Prvý raster sa nedá prečítať: {why}")
        return 3
    print(f"  mriežka ~{meta['cell_x_m']:.1f}×{meta['cell_y_m']:.1f} m, "
          f"{meta['size']}, CRS {meta['crs']}")
    cell = max(meta["cell_x_m"], meta["cell_y_m"])
    if cell > 20:
        print(f"::warning::Mriežka {cell:.0f} m je hrubšia alebo rovnaká ako "
              f"Sonny (20 m) – tento model nič nezlepší.")

    vrt = os.path.join(work, "all.vrt")
    run(["gdalbuildvrt", "-q", "-resolution", "highest", vrt] + rasters)

    print("Krájam na 1°×1° dlaždice…")
    tiles = cut_tiles(vrt, args.out, bbox)
    if not tiles:
        print("::error::Nevznikla ani jedna dlaždica.")
        return 3
    total_mb = sum(os.path.getsize(t) for t in tiles) / 1048576
    print(f"Hotovo: {len(tiles)} dlaždíc, {total_mb:.0f} MB")

    if args.stats:
        with open(args.stats, "w") as f:
            f.write(f"tiles={len(tiles)}\nmb={total_mb:.0f}\n")
            f.write(f"cell_m={cell:.1f}\ncrs={meta['crs']}\nurl={url}\n")
    if not args.keep_temp:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

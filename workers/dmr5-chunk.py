#!/usr/bin/env python3
"""
Jedna časť DMR 5.0: stiahnuť úsek archívu, rozbaliť ho za behu a urobiť raster.

Toto beží v matrix jobe workflowu „Rozobrať DMR 5.0“ – toľkokrát, koľko častí
naplánoval `dmr5-plan.py`. Každý beh siahne LEN na svoj súvislý úsek bajtov
(jeden HTTP prenos s Range) a nikdy nemá na disku viac než jeden rozbalený
blok naraz.

PREČO SA RASTRUJE UŽ TU, A NIE AŽ NA KONCI: 184 GB komprimovaného textu je
rozbalených okolo 700 GB. To sa nemá kam uložiť ani odovzdať ďalej. Ten istý
terén ako mriežka Float32 je o rád menší (a pri 5 m o dva), takže časť
odovzdáva rastre – z nich sa vrstevnice, skaly aj tieňovanie počítajú presne
tak isto ako z ktoréhokoľvek iného DEM.

JEDEN RASTER NA BLOK, NIE JEDEN NA ČASŤ. Položky idú do častí v poradí
offsetov, takže časť môže byť dlhý pás cez pol Slovenska. Jeden raster na
taký pás by bol z 95 % prázdny. Raster na blok (to, čo je jeden súbor
v archíve) je presne to, čo v dátach naozaj je.

Súradnice sú natívne S-JTSK (EPSG:8353) – bez prevzorkovania. Do WGS84 to
prepočíta až posledný job, jedným warpom, takže sa terén interpoluje raz
a nie dvakrát.

Použitie:
    python3 workers/dmr5-chunk.py --plan=plan.json --chunk=000 --out=out \\
        --mode=dem --grid-m=1
"""
import argparse
import gzip
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import sjtsk  # noqa: E402

NODATA = -9999.0
MB = 1024 * 1024
TEXT_EXT = (".txt", ".xyz", ".csv", ".pts", ".dat", ".asc")
CLOUD_EXT = (".las", ".laz")
RASTER_EXT = (".tif", ".tiff", ".img", ".dem")


def load_remote_zip():
    spec = importlib.util.spec_from_file_location(
        "zip_remote", os.path.join(_HERE, "zip-remote.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


# ---------- text → čísla ----------

def sniff(line):
    """Z prvého riadku: oddeľovač a či je desatinná čiarka."""
    s = line.decode("utf-8", "replace") if isinstance(line, bytes) else line
    if ";" in s:
        return ";", ("," in s.replace(";", ""))
    if "\t" in s:
        return "\t", ("," in s)
    return None, False           # biele znaky


def parse_text(buf, sep, comma):
    """Kus textu → pole (n, 3) float64. Neúplný posledný riadok sa vracia späť."""
    cut = buf.rfind(b"\n")
    if cut < 0:
        return None, buf
    head, rest = buf[:cut], buf[cut + 1:]
    txt = head.decode("utf-8", "replace")
    if comma:
        txt = txt.replace(",", ".")
    if sep:
        txt = txt.replace(sep, " ")
    try:
        vals = np.array(txt.split(), dtype=np.float64)
    except ValueError:
        # Hlavička alebo poznámka medzi číslami – riadok po riadku, pomalšie,
        # ale nezhodí to celý blok kvôli jednému riadku.
        rows = []
        for ln in txt.split("\n"):
            v = sjtsk.parse_numbers(ln)
            if len(v) >= 3:
                rows.append(v[:3])
        return (np.array(rows, dtype=np.float64) if rows else None), rest
    ncols = 3
    if vals.size % ncols:
        # Riadky nemajú 3 stĺpce – zisti koľko ich má prvý.
        first = txt.split("\n", 1)[0].split()
        ncols = max(len(first), 3)
        if vals.size % ncols:
            return None, rest
    return vals.reshape(-1, ncols)[:, :3], rest


# ---------- mriežka ----------

class Grid:
    """Priebežné binovanie bodov do pravidelnej mriežky v EPSG:8353."""

    def __init__(self, bbox_en, cell):
        w, s, e, n = bbox_en
        self.cell = float(cell)
        self.w = np.floor(w / self.cell) * self.cell
        self.s = np.floor(s / self.cell) * self.cell
        self.nx = max(1, int(np.ceil((e - self.w) / self.cell)))
        self.ny = max(1, int(np.ceil((n - self.s) / self.cell)))
        self.sum = np.zeros(self.nx * self.ny, dtype=np.float64)
        self.cnt = np.zeros(self.nx * self.ny, dtype=np.int32)

    def add(self, pts):
        e, n, h = pts[:, 0], pts[:, 1], pts[:, 2]
        col = ((e - self.w) / self.cell).astype(np.int64)
        row = ((n - self.s) / self.cell).astype(np.int64)
        ok = (col >= 0) & (col < self.nx) & (row >= 0) & (row < self.ny)
        if not ok.all():
            col, row, h = col[ok], row[ok], h[ok]
        if col.size == 0:
            return 0
        idx = row * self.nx + col
        self.sum += np.bincount(idx, weights=h, minlength=self.sum.size)
        self.cnt += np.bincount(idx, minlength=self.cnt.size).astype(np.int32)
        return int(col.size)

    def raster(self):
        """Priemer na bunku, otočené na sever hore (ako to chce GeoTIFF)."""
        out = np.full(self.sum.size, NODATA, dtype=np.float32)
        hit = self.cnt > 0
        out[hit] = (self.sum[hit] / self.cnt[hit]).astype(np.float32)
        return out.reshape(self.ny, self.nx)[::-1], int(hit.sum())


def write_tif(arr, west, north, cell, path):
    """Pole → GeoTIFF v EPSG:8353 cez surový VRT (bez rasterio/GDAL Pythonu)."""
    tmp = path + ".dat"
    arr.astype("<f4").tofile(tmp)
    vrt = path + ".vrt"
    with open(vrt, "w") as f:
        f.write(
            f'<VRTDataset rasterXSize="{arr.shape[1]}" rasterYSize="{arr.shape[0]}">\n'
            f'  <SRS>EPSG:{sjtsk.EN_EPSG}</SRS>\n'
            f'  <GeoTransform>{west}, {cell}, 0, {north}, 0, -{cell}</GeoTransform>\n'
            f'  <VRTRasterBand dataType="Float32" band="1" subClass="VRTRawRasterBand">\n'
            f'    <SourceFilename relativeToVRT="1">{os.path.basename(tmp)}</SourceFilename>\n'
            f'    <ByteOrder>LSB</ByteOrder>\n'
            f'    <NoDataValue>{NODATA}</NoDataValue>\n'
            f'  </VRTRasterBand>\n'
            f'</VRTDataset>\n')
    run(["gdal_translate", "-q", "-of", "GTiff", "-a_nodata", str(NODATA),
         "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3", "-co", "TILED=YES",
         vrt, path])
    os.remove(tmp)
    os.remove(vrt)


# ---------- spracovanie jednej položky ----------

def raw_name(entry):
    """Meno pôvodného súboru v artefakte – s poradovým číslom, lebo v archíve
    môžu byť dva rovnako pomenované súbory v rôznych podadresároch."""
    return f"b{entry['i']:06d}_{os.path.basename(entry['name'])}.gz"


def block_bbox(entry, cell):
    """Rozsah bloku z plánu, roztiahnutý o jednu bunku hore a doprava.

    Blok siaha od svojho rohu po roh susedného a body presne na hornej hranici
    môžu byť v oboch súboroch. Bez tej jednej bunky navyše by vypadli; dolný
    ľavý roh sa nehýbe, aby susedné bloky sedeli na tú istú mriežku.
    """
    b = entry.get("en")
    if not b:
        return None
    return (b[0], b[1], b[2] + cell, b[3] + cell)


def rasterize_text(stream, entry, cell, orient, out_dir, stem):
    """Textové výškové body → jeden GeoTIFF. Nič sa nedrží celé v pamäti."""
    bbox = block_bbox(entry, cell)
    spill = None                 # keď rozsah bloku nepoznáme, body sa odložia
    grid = Grid(bbox, cell) if bbox else None
    buf = b""
    sep, comma, sniffed = None, False, False
    total = 0
    while True:
        piece = stream.read(8 * MB)
        if not piece:
            break
        buf += piece
        if not sniffed:
            nl = buf.find(b"\n")
            if nl < 0:
                continue
            sep, comma = sniff(buf[:nl])
            sniffed = True
        pts, buf = parse_text(buf, sep, comma)
        if pts is None or not len(pts):
            continue
        pts[:, 0], pts[:, 1] = orient(pts[:, 0].copy(), pts[:, 1].copy())
        total += len(pts)
        if grid is not None:
            grid.add(pts)
        else:
            if spill is None:
                spill = tempfile.NamedTemporaryFile(suffix=".f8", delete=False)
            spill.write(pts.astype("<f8").tobytes())
    if buf.strip():
        pts, _ = parse_text(buf + b"\n", sep, comma)
        if pts is not None and len(pts):
            pts[:, 0], pts[:, 1] = orient(pts[:, 0].copy(), pts[:, 1].copy())
            total += len(pts)
            if grid is not None:
                grid.add(pts)
            elif spill is not None:
                spill.write(pts.astype("<f8").tobytes())

    if grid is None:
        # Rozsah sa nedal vziať z plánu – druhý prechod cez odložené body.
        if spill is None:
            return None, 0, 0
        spill.close()
        raw = np.fromfile(spill.name, dtype="<f8").reshape(-1, 3)
        os.unlink(spill.name)
        if not len(raw):
            return None, 0, 0
        grid = Grid((raw[:, 0].min(), raw[:, 1].min(),
                     raw[:, 0].max() + cell, raw[:, 1].max() + cell), cell)
        grid.add(raw)

    arr, hit = grid.raster()
    if not hit:
        return None, total, 0
    # Poradové číslo položky v mene je poistka proti zhode názvov: `assemble`
    # sype bloky zo VŠETKÝCH častí do jedného adresára a v archíve môžu byť
    # dva rovnako pomenované súbory v rôznych podadresároch. Bez toho by si
    # jeden blok ticho prepísal druhý.
    base = os.path.splitext(os.path.basename(entry["name"]))[0]
    path = os.path.join(out_dir, f"{stem}_{base}.tif" if base else f"{stem}.tif")
    write_tif(arr, grid.w, grid.s + grid.ny * grid.cell, cell, path)
    return path, total, hit


def rasterize_cloud(src, cell, out_dir, stem, log):
    """LAS/LAZ cez PDAL, keď je k dispozícii."""
    if not shutil.which("pdal"):
        log(f"::warning::{os.path.basename(src)}: PDAL nie je nainštalovaný, "
            f"mračno bodov sa preskakuje (režim `raw` ho odovzdá tak, ako je).")
        return None
    dest = os.path.join(
        out_dir, f"{stem}_{os.path.splitext(os.path.basename(src))[0]}.tif")
    pipe = {
        "pipeline": [
            src,
            {"type": "filters.range", "limits": "Classification[2:2]"},
            {"type": "writers.gdal", "filename": dest, "resolution": cell,
             "output_type": "mean", "nodata": NODATA,
             "gdaldriver": "GTiff",
             "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=3,TILED=YES"},
        ]
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(pipe, f)
        pl = f.name
    try:
        run(["pdal", "pipeline", pl])
    except subprocess.CalledProcessError as exc:
        log(f"::warning::PDAL zlyhal na {os.path.basename(src)}: "
            f"{(exc.stderr or '')[:200]}")
        return None
    finally:
        os.unlink(pl)
    return dest if os.path.exists(dest) else None


def copy_raster(src, out_dir, stem, log):
    """Hotový raster v archíve – len prebaliť, nič neprepočítavať."""
    dest = os.path.join(
        out_dir, f"{stem}_{os.path.splitext(os.path.basename(src))[0]}.tif")
    try:
        run(["gdal_translate", "-q", "-of", "GTiff", "-co", "COMPRESS=DEFLATE",
             "-co", "PREDICTOR=3", "-co", "TILED=YES", src, dest])
    except subprocess.CalledProcessError as exc:
        log(f"::warning::{os.path.basename(src)} sa nedá prečítať ako raster: "
            f"{(exc.stderr or '')[:160]}")
        return None
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--chunk", required=True, help="id časti, napr. 000")
    ap.add_argument("--out", default="out")
    ap.add_argument("--mode", default="dem", choices=["dem", "raw", "both"])
    ap.add_argument("--grid-m", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--stats", default="", help="kam zapísať stats.json")
    args = ap.parse_args(argv)

    def log(m):
        print(m, flush=True)

    plan = json.load(open(args.plan))
    chunk = next((c for c in plan["chunks"] if c["id"] == args.chunk), None)
    if chunk is None:
        log(f"::error::V pláne nie je časť „{args.chunk}“.")
        return 2
    by_i = {e["i"]: e for e in plan["entries"]}
    ents = [by_i[i] for i in chunk["idx"] if i in by_i]

    orient = dict(sjtsk.ORIENTATIONS).get(plan.get("orientation") or "")
    if orient is None:
        log("::error::Plán nemá orientáciu stĺpcov – bez nej sa body nedajú "
            "umiestniť. Pusti plán znova s vyšším `--calibrate`.")
        return 2

    dem_dir = os.path.join(args.out, "dem")
    raw_dir = os.path.join(args.out, "raw")
    want_dem = args.mode in ("dem", "both")
    want_raw = args.mode in ("raw", "both")
    for d in ((dem_dir,) if want_dem else ()) + ((raw_dir,) if want_raw else ()):
        os.makedirs(d, exist_ok=True)

    log(f"Časť {chunk['id']}: {chunk['n']} položiek, "
        f"úsek {chunk['span'] / MB:.0f} MB, mriežka {args.grid_m} m")

    zr = load_remote_zip()
    rz = zr.RemoteZip(plan["url"], timeout=args.timeout)
    work = tempfile.mkdtemp(prefix="dmr5-")
    t0 = time.time()
    stats = {"chunk": chunk["id"], "entries": len(ents), "points": 0,
             "rasters": 0, "cells": 0, "skipped": 0, "grid_m": args.grid_m}

    def handle(entry, stream):
        name = entry["name"]
        low = name.lower()
        if want_raw and not want_dem:
            # Bez rastrovania sa prúd len prepíše na disk (a ešte raz zbalí,
            # aby artefakt nebol niekoľkonásobne väčší než zdroj).
            dest = os.path.join(raw_dir, raw_name(entry))
            with gzip.open(dest, "wb", compresslevel=6) as f:
                shutil.copyfileobj(stream, f, 8 * MB)
            return
        if want_raw:
            # Pri `both` musí prúd prejsť dvakrát – odloží sa na disk a číta
            # sa z neho, aby sa úsek nesťahoval druhýkrát.
            tmp = os.path.join(work, os.path.basename(name))
            with open(tmp, "wb") as f:
                shutil.copyfileobj(stream, f, 8 * MB)
            with open(tmp, "rb") as f, \
                 gzip.open(os.path.join(raw_dir, raw_name(entry)),
                           "wb", compresslevel=6) as g:
                shutil.copyfileobj(f, g, 8 * MB)
            src = open(tmp, "rb")
        else:
            src = stream
            tmp = None

        try:
            if low.endswith(TEXT_EXT):
                path, pts, hit = rasterize_text(src, entry, args.grid_m,
                                                orient, dem_dir,
                                                f"b{entry['i']:06d}")
                stats["points"] += pts
                if path:
                    stats["rasters"] += 1
                    stats["cells"] += hit
                else:
                    stats["skipped"] += 1
                    log(f"  – {os.path.basename(name)}: žiadne body v mriežke")
                return
            # Ostatné formáty potrebujú súbor na disku.
            if tmp is None:
                tmp = os.path.join(work, os.path.basename(name))
                with open(tmp, "wb") as f:
                    shutil.copyfileobj(src, f, 8 * MB)
            if low.endswith(".zip"):
                n = 0
                with zipfile.ZipFile(tmp) as z:
                    members = [m for m in z.infolist() if not m.is_dir()
                               and m.filename.lower().endswith(TEXT_EXT)]
                    # Rozsah z plánu platí pre CELÝ vnorený archív. Ak je
                    # vnútri viac blokov, každý si ho musí zistiť z dát sám,
                    # inak by všetky pristáli v tom istom obdĺžniku.
                    en = entry.get("en") if len(members) == 1 else None
                    for k, m in enumerate(members):
                        inner = dict(entry, name=m.filename, en=en)
                        with z.open(m) as f:
                            path, pts, hit = rasterize_text(
                                f, inner, args.grid_m, orient, dem_dir,
                                f"b{entry['i']:06d}-{k:04d}")
                        stats["points"] += pts
                        if path:
                            stats["rasters"] += 1
                            stats["cells"] += hit
                            n += 1
                if not n:
                    stats["skipped"] += 1
            elif low.endswith(CLOUD_EXT):
                if rasterize_cloud(tmp, args.grid_m, dem_dir, f"b{entry['i']:06d}", log):
                    stats["rasters"] += 1
                else:
                    stats["skipped"] += 1
            elif low.endswith(RASTER_EXT):
                if copy_raster(tmp, dem_dir, f"b{entry['i']:06d}", log):
                    stats["rasters"] += 1
                else:
                    stats["skipped"] += 1
            else:
                stats["skipped"] += 1
        finally:
            if src is not stream:
                src.close()
            if tmp and os.path.exists(tmp):
                os.remove(tmp)

    try:
        rz.extract_span(ents, handle)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    stats["seconds"] = round(time.time() - t0)
    if want_dem:
        stats["out_bytes"] = sum(
            os.path.getsize(os.path.join(dem_dir, f))
            for f in os.listdir(dem_dir))
    if want_raw:
        stats["raw_bytes"] = sum(
            os.path.getsize(os.path.join(raw_dir, f))
            for f in os.listdir(raw_dir))
    log(f"Hotovo za {stats['seconds']} s: {stats['rasters']} rastrov "
        f"z {stats['points']} bodov"
        + (f", {stats.get('out_bytes', 0) / MB:.0f} MB" if want_dem else ""))

    dest = args.stats or os.path.join(args.out, "stats.json")
    with open(dest, "w") as f:
        json.dump(stats, f)
    if want_dem and not stats["rasters"]:
        log("::error::Z tejto časti nevznikol ani jeden raster.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

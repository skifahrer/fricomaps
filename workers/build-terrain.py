#!/usr/bin/env python3
"""
DEM → dlaždice `raster-dem` (kódovanie terrarium) pre tieňovanie reliéfu
a 3D terén.

PREČO VLASTNÉ DLAŽDICE: MapLibre nevie čítať výšky z GeoTIFFu – potrebuje
pyramídu PNG dlaždíc, kde je nadmorská výška zakódovaná do farby. Verejné
AWS Terrain Tiles sú *povrchový* model (Copernicus/SRTM vrátane stromov),
takže by 3D terén a tieňovanie hovorili niečo iné než vrstevnice a skaly,
ktoré počítame z LiDAR terénu. Tento skript preto vyrobí dlaždice z toho
istého DEM ako zvyšok pipeline.

Kódovanie terrarium (rovnaké, aké čakal doterajší zdroj):
    výška [m] = (R * 256 + G + B / 256) − 32768
B necháme na nule, teda výška je zaokrúhlená na celé metre. Merané na
Vysokých Tatrách: oproti plnej presnosti (0,004 m) sú dlaždice 2,9× menšie
a rozdiel vo vykreslenom tieňovaní je priemerne 0,5 z 255 odtieňov, teda
okom neviditeľný – posledný bajt kódovania je z väčšiny šum, ktorý sa nedá
skomprimovať.

Dlaždice sa nekreslia zmenšovaním hotových dlaždíc, ale pre každý zoom sa
DEM prevzorkuje nanovo priemerom (`gdalwarp -r average`). Priemerovať sa
totiž musí *výška*, nie zakódovaná farba – priemer bajtov R/G je nezmysel.

Použitie:
    python3 workers/build-terrain.py --dem=dem/all.vrt \\
        --bbox=16.8,47.7,22.6,49.6 --maxzoom=12 --out=terrain-out
"""
import argparse
import math
import os
import struct
import subprocess
import sys
import zlib

import numpy as np

R_EARTH = 6378137.0
ORIGIN = math.pi * R_EARTH  # 20037508.342789244
TILE = 256


def merc_x(lon):
    return math.radians(lon) * R_EARTH


def merc_y(lat):
    lat = max(min(lat, 85.05112878), -85.05112878)
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R_EARTH


def tile_range(z, w, s, e, n):
    """Rozsah dlaždíc XYZ, ktoré pokrývajú bbox na danom zoome."""
    count = 2**z
    size = 2 * ORIGIN / count
    x0 = int((merc_x(w) + ORIGIN) // size)
    x1 = int((merc_x(e) + ORIGIN) // size)
    y0 = int((ORIGIN - merc_y(n)) // size)
    y1 = int((ORIGIN - merc_y(s)) // size)
    clamp = lambda v: max(0, min(count - 1, v))
    return clamp(x0), clamp(x1), clamp(y0), clamp(y1)


# ---------- minimálny zapisovač PNG ----------
# Pillow tu nie je: jediné, čo potrebujeme, je bezstratový RGB PNG, a to je
# zlib + pár hlavičiek. Filtre skúšame všetky a pre každý riadok berieme ten
# s najmenším súčtom absolútnych odchýlok (štandardná heuristika) – bez nich
# by boli dlaždice zbytočne veľké.
def _filter_rows(raw):
    h, stride = raw.shape
    bpp = 3
    out = np.empty((h, stride + 1), np.uint8)
    prev = np.zeros(stride, np.uint8)
    for i in range(h):
        line = raw[i].astype(np.int16)
        left = np.zeros(stride, np.int16)
        left[bpp:] = line[:-bpp]
        up = prev.astype(np.int16)
        upleft = np.zeros(stride, np.int16)
        upleft[bpp:] = up[:-bpp]

        cands = [
            (0, line),
            (1, line - left),
            (2, line - up),
            (3, line - ((left + up) // 2)),
        ]
        # Paeth
        p = left + up - upleft
        pa, pb, pc = np.abs(p - left), np.abs(p - up), np.abs(p - upleft)
        pred = np.where((pa <= pb) & (pa <= pc), left, np.where(pb <= pc, up, upleft))
        cands.append((4, line - pred))

        best = min(cands, key=lambda c: int(np.abs(c[1].astype(np.int8)).sum()))
        out[i, 0] = best[0]
        out[i, 1:] = best[1].astype(np.uint8)
        prev = raw[i]
    return out


def png_rgb(arr):
    h, w, _ = arr.shape
    raw = np.ascontiguousarray(arr).reshape(h, w * 3)
    data = _filter_rows(raw).tobytes()

    def chunk(kind, payload):
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(data, 9))
        + chunk(b"IEND", b"")
    )


def warp_level(dem, path, minx, miny, maxx, maxy, width, height):
    """Prevzorkuje DEM do mriežky presne zarovnanej na dlaždice daného zoomu."""
    subprocess.run(
        ["gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:3857",
         "-te", *map(repr, (minx, miny, maxx, maxy)),
         "-ts", str(width), str(height),
         "-r", "average", "-ot", "Int16", "-dstnodata", "0",
         "-of", "ENVI", dem, path],
        check=True,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dem", required=True, help="vstupný DEM (.vrt/.tif)")
    ap.add_argument("--bbox", required=True, help="west,south,east,north")
    ap.add_argument("--maxzoom", type=int, default=12)
    ap.add_argument("--minzoom", type=int, default=0)
    ap.add_argument("--out", required=True, help="adresár s dlaždicami {z}/{x}/{y}.png")
    args = ap.parse_args()

    w, s, e, n = (float(v) for v in args.bbox.split(","))
    total_bytes = 0
    total_tiles = 0

    for z in range(args.minzoom, args.maxzoom + 1):
        x0, x1, y0, y1 = tile_range(z, w, s, e, n)
        size = 2 * ORIGIN / (2**z)
        nx, ny = x1 - x0 + 1, y1 - y0 + 1

        # Po pásoch, nech pamäť nerastie s veľkosťou územia.
        rows_per_strip = max(1, 512 // max(1, nx))
        zbytes = 0
        for ry in range(y0, y1 + 1, rows_per_strip):
            ry_end = min(ry + rows_per_strip - 1, y1)
            minx = -ORIGIN + x0 * size
            maxx = -ORIGIN + (x1 + 1) * size
            maxy = ORIGIN - ry * size
            miny = ORIGIN - (ry_end + 1) * size
            width = nx * TILE
            height = (ry_end - ry + 1) * TILE
            warp_level(args.dem, "/tmp/level.raw", minx, miny, maxx, maxy, width, height)
            grid = np.fromfile("/tmp/level.raw", dtype="<i2").reshape(height, width)

            # terrarium: v = výška + 32768, R = v >> 8, G = v & 255, B = 0
            v = (grid.astype(np.int32) + 32768).clip(0, 65535)
            rgb = np.zeros((height, width, 3), np.uint8)
            rgb[..., 0] = (v >> 8).astype(np.uint8)
            rgb[..., 1] = (v & 255).astype(np.uint8)

            for ty in range(ry, ry_end + 1):
                for tx in range(x0, x1 + 1):
                    tile = rgb[
                        (ty - ry) * TILE : (ty - ry + 1) * TILE,
                        (tx - x0) * TILE : (tx - x0 + 1) * TILE,
                    ]
                    d = os.path.join(args.out, str(z), str(tx))
                    os.makedirs(d, exist_ok=True)
                    data = png_rgb(tile)
                    with open(os.path.join(d, f"{ty}.png"), "wb") as f:
                        f.write(data)
                    zbytes += len(data)
                    total_tiles += 1
        total_bytes += zbytes
        print(f"z{z}: {nx}×{ny} dlaždíc, {zbytes / 1048576:.1f} MB", flush=True)

    print(f"Spolu: {total_tiles} dlaždíc, {total_bytes / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

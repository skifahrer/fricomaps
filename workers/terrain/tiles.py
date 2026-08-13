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
    python3 workers/terrain/tiles.py --dem=dem/all.vrt \\
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


def load_mask(poly, bbox):
    """Maska kraja z `workers/lib/region-mask.py`, alebo `None` bez polygónu.

    Modul má v mene pomlčku, takže `import` naň nefunguje – naťahuje sa cez
    `importlib` presne tak, ako to robí zvyšok pipeline (`load("rock_plan", …)`).
    """
    if not poly or not os.path.exists(poly):
        return None
    import importlib.util
    lib = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "lib", "region-mask.py")
    spec = importlib.util.spec_from_file_location("region_mask", lib)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, mod.mask_from_file(poly, bbox)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dem", required=True, help="vstupný DEM (.vrt/.tif)")
    ap.add_argument("--bbox", required=True, help="west,south,east,north")
    ap.add_argument("--poly", default="",
                    help="GeoJSON kraja – dlaždice mimo neho sa nekreslia")
    ap.add_argument("--grow", type=float, default=0.5,
                    help="o koľko svojej strany smie dlaždica prečnievať za kraj")
    ap.add_argument("--maxzoom", type=int, default=12)
    ap.add_argument("--minzoom", type=int, default=0)
    ap.add_argument("--out", required=True, help="adresár s dlaždicami {z}/{x}/{y}.png")
    ap.add_argument("--budget-mb", type=float, default=0,
                    help="koľko MB smú dlaždice zabrať (0 = bez stropu)")
    args = ap.parse_args()

    w, s, e, n = (float(v) for v in args.bbox.split(","))
    # ORIENTAČNÝ OREZ NA KRAJ. Bbox kraja je oveľa väčší než kraj sám (pri
    # Prešovskom 16 107 km² proti 10 184 km², teda 37 % mimo), takže bez tohto
    # sa tretina dlaždíc kreslila do susedných krajov a za hranicu – a práve
    # tam je DMR 5.0 prázdne, takže z nich boli biele dlaždice s rovnou hranou.
    maska = load_mask(args.poly, (w, s, e, n))
    rm, mask = maska if maska else (None, None)
    if mask:
        print(f"Orez na kraj: v kraji je {mask.pct:.0f} % bboxu "
              f"(maska {mask.nx}×{mask.ny}); dlaždica smie prečnievať "
              f"{args.grow:g} svojej strany.", flush=True)
    else:
        print("::warning::Polygón kraja nie je – kreslí sa celý bbox regiónu, "
              "teda aj mimo kraj. (`--poly` nedostal súbor.)", flush=True)
    total_bytes = 0
    total_tiles = 0
    skipped = 0
    made = args.minzoom - 1

    # ---------- plán ----------
    # Každý zoom navyše je ŠTVORNÁSOBOK dlaždíc, takže rozdiel medzi z13
    # a z15 nie je „o kúsok viac", ale šestnásťnásobok. Bez tohto výpisu to
    # bolo vidieť až podľa toho, že job bežal hodinu a stránka sa nezmestila
    # do rozpočtu – teda po celej práci.
    plan = []
    for z in range(args.minzoom, args.maxzoom + 1):
        x0, x1, y0, y1 = tile_range(z, w, s, e, n)
        vsetkych = (x1 - x0 + 1) * (y1 - y0 + 1)
        if mask:
            v_kraji = sum(1 for tx in range(x0, x1 + 1) for ty in range(y0, y1 + 1)
                          if rm.tile_touches(mask, z, tx, ty, args.grow))
        else:
            v_kraji = vsetkych
        plan.append((z, v_kraji, vsetkych))
    mimo = sum(v - k for _, k, v in plan)
    print("Plán: " + ", ".join(f"z{z} {k} dl." for z, k, _ in plan)
          + f"  (spolu {sum(k for _, k, _ in plan)} dlaždíc"
          + (f", {mimo} mimo kraja sa vynechá" if mimo else "")
          + ")"
          + (f", strop {args.budget_mb:.0f} MB" if args.budget_mb else ""),
          flush=True)

    for z in range(args.minzoom, args.maxzoom + 1):
        x0, x1, y0, y1 = tile_range(z, w, s, e, n)
        # ---------- strop veľkosti ----------
        # Odhad na ďalší zoom sa NEBERIE z konštanty, ale z toho, čo práve
        # vyšlo o zoom nižšie: dlaždica z toho istého územia a modelu má na
        # každom zoome podobnú veľkosť. Zoom, ktorý by sa nezmestil, sa preto
        # ani nezačne počítať – inak by sa hodina práce vyhodila.
        if args.budget_mb and total_tiles:
            per_tile = total_bytes / total_tiles
            want = next(k for zz, k, _ in plan if zz == z) * per_tile
            if (total_bytes + want) / 1048576 > args.budget_mb:
                print(f"::warning::Výškové dlaždice končia na z{made}: z{z} by "
                      f"pridal ~{want / 1048576:.0f} MB a rozpočet na "
                      f"tieňovanie je {args.budget_mb:.0f} MB. Pre jemnejší "
                      f"reliéf zmenši územie (input `area`, voľba "
                      f"`crop_bbox`), alebo zdvihni `size_limit_mb` "
                      f"či podiel BUDGET_TERRAIN_PCT.")
                break
        size = 2 * ORIGIN / (2**z)
        nx, ny = x1 - x0 + 1, y1 - y0 + 1
        skipped_before = skipped

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
                    # MIMO KRAJA SA NEZAPISUJE. Kontroluje sa tu a nie pred
                    # warpom zámerne: warp beží na celý pás naraz a v tom páse
                    # sú aj dlaždice v kraji, takže sa celý vynechať nedá.
                    # Ušetrí sa zápis, veľkosť stránky a hlavne biele dlaždice
                    # z prázdneho DEM za hranicou.
                    if mask and not rm.tile_touches(mask, z, tx, ty, args.grow):
                        skipped += 1
                        continue
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
        made = z
        print(f"z{z}: {nx}×{ny} dlaždíc, {zbytes / 1048576:.1f} MB"
              + (f" (mimo kraja vynechaných {skipped - skipped_before})"
                 if mask and skipped > skipped_before else ""), flush=True)

    if made < args.minzoom:
        print("::error::Nevznikla ani jedna dlaždica tieňovania.",
              file=sys.stderr)
        return 1
    # Skutočne vyrobený maxzoom, nie ten želaný. Píše ho ten, kto dlaždice
    # naozaj vyrobil – meno assetu v sklade aj štýl si ho odtiaľto berú, takže
    # sa nemá ako stať, že mapa pýta z15 a na Pages je z13.
    with open(os.path.join(args.out, "maxzoom.txt"), "w") as f:
        f.write(f"{made}\n")
    print(f"Spolu: {total_tiles} dlaždíc, {total_bytes / 1048576:.1f} MB, "
          f"maxzoom z{made}"
          + (f"; mimo kraja vynechaných {skipped} dlaždíc "
             f"({100 * skipped / (total_tiles + skipped):.0f} %)"
             if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

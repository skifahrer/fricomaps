#!/usr/bin/env python3
"""
Stiahne 1 m LiDAR od ÚGKK (DMR 5.0) cez ArcGIS ImageServer do dlaždíc + VRT.

Na rozdiel od Sonnyho, ktorý máme nazrkadlený v releasi ako 1°×1° dlaždice,
sa ÚGKK pýta po výrezoch: `exportImage` vráti GeoTIFF presne toho kúska, ktorý
si vypýtaš. Územie sa preto krája na dlaždice po `--tile-px` pixeloch a lepí
sa `gdalbuildvrt`-om.

KTORÁ SLUŽBA: názvy služieb v ÚGKK adresári nie sú zdokumentované, takže
`workers/dem-sources.json` má zoznam kandidátov a skript vezme prvého, ktorý
odpovie a má mriežku ≤ 2 m. Ktorý to je, zistí workflow *Check DEM source* –
a keď neodpovie ani jeden, tento skript to povie hneď a odkáže naň.

POZOR NA VEĽKOSŤ: 1 m je 400× viac buniek než Sonnyho 20 m. Celý kraj by mal
16 miliárd buniek (64 GB vo Float32), takže sa to používa **len s výrezom**
(input `area`). Strop je preto natvrdo a hlási sa dopredu.

Použitie:
    python3 workers/fetch-dem-ugkk.py --bbox=W,S,E,N --out=dem
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib.machinery import SourceFileLoader  # noqa: E402

_probe = SourceFileLoader(
    "probe", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "probe-dem-source.py")).load_module()

UA = _probe.UA


def hms(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def pick_service(sources_path):
    """Prvý kandidát, ktorý odpovie a má mriežku ≤ 2 m."""
    src = json.load(open(sources_path))["ugkk"]
    for url in src["candidates"]:
        meta = _probe.probe_image_server(url)
        if meta["ok"] and (meta.get("pixel_m") or 99) <= 2:
            print(f"Služba: {url} (pixel {meta['pixel_m']} m, {meta['type']})")
            return url, meta
        why = meta.get("why") if not meta["ok"] else f"pixel {meta.get('pixel_m')} m"
        print(f"  – {url}: {why}")
    return None, None


def download(url, path, timeout=180):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", required=True, help="west,south,east,north v stupňoch")
    ap.add_argument("--out", default="dem", help="adresár na dlaždice a VRT")
    ap.add_argument("--sources", default="workers/dem-sources.json")
    ap.add_argument("--tile-px", type=int, default=4000,
                    help="strana dlaždice v pixeloch (ArcGIS má strop okolo 4096)")
    ap.add_argument("--max-cells", type=float, default=1.5e9,
                    help="strop buniek celého územia (0 = bez stropu)")
    ap.add_argument("--steps-tsv", default="")
    args = ap.parse_args()

    t0 = time.time()
    w, s, e, n = (float(v) for v in args.bbox.split(","))
    # Metre na stupeň v našich šírkach – na odhad počtu pixelov to stačí,
    # presnú geometriu rieši ArcGIS na svojej strane.
    lat = (s + n) / 2
    mx = 111320 * math.cos(math.radians(lat))
    width_m, height_m = (e - w) * mx, (n - s) * 110540
    cells = width_m * height_m
    print(f"Územie {width_m/1000:.1f}×{height_m/1000:.1f} km → "
          f"{cells/1e6:.0f} mil. buniek pri 1 m")

    if args.max_cells and cells > args.max_cells:
        print(f"::error::1 m LiDAR pre toto územie znamená {cells/1e9:.2f} miliardy "
              f"buniek ({cells*4/1e9:.0f} GB vo Float32), strop je "
              f"{args.max_cells/1e9:.2f} mld. Metrový model má zmysel len na výrez – "
              f"nastav input `area` (napr. vysoke_tatry) alebo použi dem_source: sonny.")
        return 2

    service, meta = pick_service(args.sources)
    if not service:
        print("::error::Ani jedna ÚGKK služba neodpovedala použiteľne. Spusti "
              "workflow 'Check DEM source', ktorý vypíše, čo v ich ArcGIS adresári "
              "naozaj je – a podľa neho oprav workers/dem-sources.json. Dovtedy "
              "použi dem_source: sonny.")
        return 2

    tiles_dir = os.path.join(args.out, "tiles")
    os.makedirs(tiles_dir, exist_ok=True)
    nx = max(1, math.ceil(width_m / args.tile_px))
    ny = max(1, math.ceil(height_m / args.tile_px))
    dx, dy = (e - w) / nx, (n - s) / ny
    print(f"Sťahujem {nx}×{ny} = {nx*ny} dlaždíc po {args.tile_px} px", flush=True)

    tiles = []
    for iy in range(ny):
        for ix in range(nx):
            tw, ts = w + ix * dx, s + iy * dy
            te, tn = tw + dx, ts + dy
            px = max(1, round((te - tw) * mx))
            py = max(1, round((tn - ts) * 110540))
            out = os.path.join(tiles_dir, f"ugkk-{iy:03d}-{ix:03d}.tif")
            try:
                d = _probe.fetch(service + "/exportImage", {
                    "f": "json", "bbox": f"{tw},{ts},{te},{tn}",
                    "bboxSR": "4326", "imageSR": "4326",
                    "format": "tiff", "pixelType": "F32",
                    "size": f"{px},{py}",
                })
                if "href" not in d:
                    print(f"::warning::dlaždica {iy}/{ix} bez href: {str(d)[:80]}")
                    continue
                download(d["href"], out)
                tiles.append(out)
            except Exception as exc:
                print(f"::warning::dlaždica {iy}/{ix} zlyhala: {type(exc).__name__}: {exc}")
            done = iy * nx + ix + 1
            el = time.time() - t0
            print(f"  [{done}/{nx*ny}] {hms(el)} za sebou, "
                  f"zostáva ~{hms(el / done * (nx*ny - done))}", flush=True)

    if not tiles:
        print("::error::Nestiahla sa ani jedna dlaždica z ÚGKK.")
        return 1

    vrt = os.path.join(args.out, "all.vrt")
    subprocess.run(["gdalbuildvrt", "-q", "-resolution", "highest", vrt] + tiles,
                   check=True)
    mb = sum(os.path.getsize(t) for t in tiles) / 1048576
    print(f"ÚGKK DMR 5.0: {len(tiles)} dlaždíc, {mb:.0f} MB → {vrt}")

    if args.steps_tsv:
        with open(args.steps_tsv, "a") as f:
            f.write(f"20\tDEM dlaždice (ÚGKK 1 m)\t{int(time.time() - t0)}\t"
                    f"{len(tiles)} dlaždíc, {mb:.0f} MB\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

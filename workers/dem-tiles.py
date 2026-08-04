#!/usr/bin/env python3
"""
Ľubovoľný výškový raster → dlaždice 1°×1° v EPSG:4326, pomenované podľa
juhozápadného rohu (N49E019.tif), teda tak, ako ich čaká build mapy.

PREČO: Sonny distribuuje viac produktov a každý inak. Modely 1″ a 3″ sú
.hgt súbory presne po 1° dlaždiciach vo WGS84, ale modely „20m" a „50m" sú
GeoTIFFy – jedným súborom môže byť celá krajina a môžu byť v metrickej
projekcii. Build mapy pritom potrebuje jednotné dlaždice, aby si vedel
stiahnuť len tie, ktoré pokrývajú jeho bbox, a aby ich `gdalbuildvrt` zlepil
(rôzne projekcie v jednom VRT nefungujú).

Zvislé rozlíšenie sa zámerne nezaokrúhľuje: 20m model má krok 0,1 m a práve
ten rozhoduje o tom, či sklon vyjde hladký, alebo schodíkovitý.

Viac vstupov sa najprv zlepí do jedného virtuálneho rastra (VRT), takže
prekrývajúce sa súbory nevyrobia dlaždicu dvakrát.

Použitie:
    python3 workers/dem-tiles.py --out tiles/ Slovakia_20m.tif [ďalšie.tif …]
"""
import argparse
import json
import math
import os
import subprocess
import sys


def gdalinfo(path):
    out = subprocess.run(
        ["gdalinfo", "-json", path], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


def wgs84_bounds(info):
    """Rozsah rastra v stupňoch – aj keď je sám v metrickej projekcii."""
    ext = info.get("wgs84Extent")
    if not ext or not ext.get("coordinates"):
        raise SystemExit("Raster nemá zistiteľný rozsah vo WGS84 (chýba projekcia?).")
    pts = []
    def walk(node):
        if isinstance(node[0], (int, float)):
            pts.append(node)
        else:
            for n in node:
                walk(n)
    walk(ext["coordinates"])
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def is_geographic(info):
    """Zemepisná (stupne) alebo projektovaná (metre) sústava? Rozhoduje typ
    v WKT – hľadať jednotku „degree" v texte je krehké, lebo sa líši podľa
    verzie GDALu aj podľa toho, či ide o VRT."""
    wkt = (info.get("coordinateSystem") or {}).get("wkt", "")
    return wkt.strip().upper().startswith(("GEOGCRS", "GEOGCS", "BASEGEOGCRS"))


def pixel_degrees(info, lat):
    """Veľkosť pixela v stupňoch. Pri metrickej projekcii sa prepočíta –
    po dĺžke cez cos(šírky), inak by na severe vyšla mriežka hrubšia."""
    gt = info["geoTransform"]
    px, py = abs(gt[1]), abs(gt[5])
    if is_geographic(info):
        return px, py
    return px / (111320 * math.cos(math.radians(lat))), py / 110540


def tile_name(lon, lat):
    ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    return f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="+", help="vstupné rastre")
    ap.add_argument("--out", required=True)
    # bilineárne, nie kubicky: pri prakticky rovnakej mierke je to rovnako
    # dobré a na okrajoch dát ani pri dierach nič „neprestrelí" mimo rozsah
    # skutočných výšok.
    ap.add_argument("--resampling", default="bilinear")
    args = ap.parse_args()

    src = args.src[0]
    if len(args.src) > 1:
        # jeden VRT nad všetkými vstupmi – rieši aj prekryvy
        src = os.path.join(args.out or ".", "_dem-tiles.vrt")
        os.makedirs(args.out, exist_ok=True)
        subprocess.run(["gdalbuildvrt", "-q", "-resolution", "highest", src, *args.src],
                       check=True)
        print(f"Zlepené do VRT: {len(args.src)} rastrov")

    info = gdalinfo(src)
    w, s, e, n = wgs84_bounds(info)
    dtype = info["bands"][0]["type"]
    predictor = "3" if dtype.startswith("Float") else "2"
    lat_mid = (s + n) / 2
    dlon, dlat = pixel_degrees(info, lat_mid)
    nodata = info["bands"][0].get("noDataValue")
    same_grid = is_geographic(info)
    print(
        f"{os.path.basename(src)}: {w:.3f},{s:.3f} … {e:.3f},{n:.3f}, "
        f"{dtype}, mriežka {dlon * 3600:.2f}″ × {dlat * 3600:.2f}″"
        f"{'' if same_grid else ' (prepočítané z metrov)'}"
    )

    os.makedirs(args.out, exist_ok=True)
    made = []
    for lat in range(math.floor(s), math.ceil(n)):
        for lon in range(math.floor(w), math.ceil(e)):
            # Dlaždica má zmysel, len ak sa s dátami prekrýva aspoň o pár
            # pixelov. Zdroje často presahujú celý stupeň o polpixel (tak sú
            # robené .hgt aj Copernicus dlaždice) – bez tejto podmienky by
            # vedľa nich vznikali prázdne dlaždice.
            over_x = min(lon + 1, e) - max(lon, w)
            over_y = min(lat + 1, n) - max(lat, s)
            if over_x <= 2 * dlon or over_y <= 2 * dlat:
                continue
            name = tile_name(lon, lat)
            dst = os.path.join(args.out, f"{name}.tif")
            cmd = [
                "gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:4326",
                "-te", str(lon), str(lat), str(lon + 1), str(lat + 1),
                "-tr", repr(dlon), repr(dlat),
                "-r", "near" if same_grid else args.resampling,
                "-co", "COMPRESS=DEFLATE", "-co", f"PREDICTOR={predictor}",
                "-co", "TILED=YES", "-multi",
            ]
            if nodata is not None:
                cmd += ["-dstnodata", repr(nodata)]
            subprocess.run(cmd + [src, dst], check=True)
            made.append(name)
            print(f"  ✓ {name}")

    if len(args.src) > 1 and os.path.exists(src):
        os.remove(src)
    if not made:
        raise SystemExit("Raster nepokrýva ani jednu celú 1° dlaždicu.")
    print(f"{len(made)} dlaždíc: {' '.join(sorted(set(made)))}")


if __name__ == "__main__":
    sys.exit(main())

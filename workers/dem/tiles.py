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
    python3 workers/dem/tiles.py --out tiles/ Slovakia_20m.tif [ďalšie.tif …]
"""
import argparse
import json
import math
import os
import subprocess
import sys


# GDAL_PAM_ENABLED=NO: bez neho si `gdalinfo -stats` odkladá štatistiky do
# súborov .aux.xml, ktoré by sa potom viezli do releasu ako smetie.
NO_PAM = {**os.environ, "GDAL_PAM_ENABLED": "NO"}


def gdalinfo(path, stats=False):
    cmd = ["gdalinfo", "-json"] + (["-approx_stats"] if stats else []) + [path]
    out = subprocess.run(
        cmd, capture_output=True, text=True, check=True, env=NO_PAM
    ).stdout
    return json.loads(out)


def elevation_range(path):
    """(min, max) výšok, alebo None, keď v rastri nie je ani jeden platný pixel."""
    try:
        b = gdalinfo(path, stats=True)["bands"][0]
        return b["minimum"], b["maximum"]
    except Exception:
        return None


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
    verzie GDALu aj podľa toho, či ide o VRT.

    COMPOUNDCRS je tu preto, že raster s prevedenými výškami (`-t_srs
    EPSG:4326+3855`) má vodorovnú zložku v stupňoch, ale WKT začína
    `COMPOUNDCRS[` – a bez tohto riadku vyzeral ako metrický. Veľkosť pixela
    (0,00008°) sa potom delila 111 320, vyšlo 8·10⁻¹⁰° a `gdalwarp` mal
    z jedného stupňa vyrobiť dlaždicu širokú 1,2 miliardy pixelov:

        ERROR 6: File too large regarding tile size. This would result in
        a file with tile arrays larger than 2GB

    Zhodilo to beh 31310604408. Týkalo sa to KAŽDÉHO krájania na dlaždice
    s predvoleným geoidom, teda aj `dmr5-drive.py --area=cele_slovensko`.
    """
    wkt = (info.get("coordinateSystem") or {}).get("wkt", "")
    wkt = wkt.strip().upper()
    if wkt.startswith("COMPOUNDCRS"):
        # Vodorovná zložka je prvá vnorená CRS – zaujíma nás len ona.
        inner = wkt.split("[", 1)[1] if "[" in wkt else ""
        inner = inner.split(",", 1)[1].lstrip() if "," in inner else ""
        return inner.startswith(("GEOGCRS", "GEOGCS", "BASEGEOGCRS"))
    return wkt.startswith(("GEOGCRS", "GEOGCS", "BASEGEOGCRS"))


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

    temps = []
    src = args.src[0]
    if len(args.src) > 1:
        # jeden VRT nad všetkými vstupmi – rieši aj prekryvy
        src = os.path.join(args.out or ".", "_dem-tiles.vrt")
        os.makedirs(args.out, exist_ok=True)
        subprocess.run(["gdalbuildvrt", "-q", "-resolution", "highest", src, *args.src],
                       check=True)
        temps.append(src)
        print(f"Zlepené do VRT: {len(args.src)} rastrov")

    info = gdalinfo(src)

    # Výšky uložené ako celé čísla so škálou (napr. decimetre so scale=0.1)
    # by sa bez rozbalenia dostali do mapy desaťkrát väčšie – a sklon by potom
    # ukázal skalu na každom poli. gdalwarp škálu sám neuplatňuje, preto sa
    # raster najprv prepíše na skutočné metre.
    band = info["bands"][0]
    scale, offset = band.get("scale", 1) or 1, band.get("offset", 0) or 0
    if scale != 1 or offset != 0:
        print(f"Výšky sú škálované (scale={scale}, offset={offset}) – rozbaľujem na metre")
        os.makedirs(args.out, exist_ok=True)
        unscaled = os.path.join(args.out, "_dem-tiles-unscaled.tif")
        subprocess.run(
            ["gdal_translate", "-q", "-unscale", "-ot", "Float32",
             "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3", src, unscaled],
            check=True,
        )
        temps.append(unscaled)
        src = unscaled
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

    # Keby boli výšky v iných jednotkách (decimetre, stopy) alebo by sa
    # nerozbalila škála, prejaví sa to tu – a nie až tak, že mapa bude samá
    # skala, lebo sklon vyjde desaťkrát väčší.
    rng = elevation_range(src)
    if rng:
        lo, hi = rng
        print(f"Výšky v zdroji: {lo:.1f} … {hi:.1f} m")
        if lo < -500 or hi > 9000:
            print("::warning::Rozsah výšok nevyzerá ako metre nad morom – "
                  "skontroluj jednotky zdroja (decimetre? stopy?).")

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
            if elevation_range(dst) is None:
                # celá dlaždica je nodata – do releasu nemá čo pridať
                os.remove(dst)
                continue
            made.append(name)
            print(f"  ✓ {name}")

    for t in temps:
        if os.path.exists(t):
            os.remove(t)
    if not made:
        raise SystemExit("Raster nepokrýva ani jednu celú 1° dlaždicu.")

    print(f"{len(made)} dlaždíc: {' '.join(sorted(set(made)))}")


if __name__ == "__main__":
    sys.exit(main())

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

`--window` JE TO, ČO DRŽÍ MENO DLAŽDICE PRAVDIVÝM (pravidlo 2 v CLAUDE.md).
Volajúci ním hovorí „toto územie som NAOZAJ prečítal" a platia potom dve veci:

  * stupeň, ktorý do okna nepadne celý, sa NEUKLADÁ. Prevod do WGS84 totiž
    okno vydúva – z okna `21,49…22,50` v EPSG:3046 vyšel raster
    `21.000,48.996 … 22.013,49.628`, takže sa doň zmestili štyri stupne
    a tri z nich pár set metrov (5 MB vedľa 253 MB). Uložili sa pod menami
    `N48E021.tif`, `N48E022.tif`, `N49E020.tif` – a tie tvrdia, že tam je celý
    stupeň. Ďalší beh podľa mena usúdil, že model má, a vrstevnice Prešovského
    kraja skončili v jednom štvorci (behy 31476448895 → 31484544154).
  * stupeň, ktorý do okna padne celý a nie je v ňom ani jedna platná výška,
    sa uloží PRÁZDNY. Nie je to lož: meno hovorí „celý tento stupeň je tu"
    a on tu je, len bez terénu (rohy bboxu za hranicou, napr. N47E016
    v Maďarsku). Je to ZÁZNAM, ŽE SA TAM POZERALO – bez neho by kontrola
    v `workers/dem/check.sh` pýtala doplnenie takého stupňa navždy a každý
    build by platil hodinu čítania za prázdno.

Bez `--window` sa nič nemení: vstupom je celý produkt (Sonny 20 m má jeden
GeoTIFF na krajinu), takže je pravdivá každá dlaždica, ktorú z neho vyrežeme,
a prázdne sa zahadzujú ako doteraz.

Použitie:
    python3 workers/dem/tiles.py --out tiles/ Slovakia_20m.tif [ďalšie.tif …]
    python3 workers/dem/tiles.py --out tiles/ --window=21,49,22,50 nation.tif
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


def plan_tiles(bounds, window, dlon, dlat):
    """Ktoré stupne sa idú písať – JEDNA odpoveď pre celý súbor.

    `bounds` je rozsah rastra (W,S,E,N), `window` okno, ktoré volajúci naozaj
    prečítal (alebo None), `dlon`/`dlat` veľkosť pixela v stupňoch.

    Vracia `(write, partial)`:
      write    zoznam `(lon, lat, name, has_data)` – čo warpnúť; `has_data`
               False znamená „stupeň v okne, ktorý raster vôbec nepretína",
               teda prázdna dlaždica ako záznam, že sa tam pozeralo
      partial  mená stupňov, ktoré okno neprečítalo celé – tie sa NEUKLADAJÚ,
               lebo meno dlaždice je sľub o celom stupni (pravidlo 2)

    Bez okna je to to, čo tu bolo vždy: stupne, ktoré raster pretína viac než
    o pár pixelov. S oknom sa mriežka berie z OKNA, nie z rastra – prevod do
    WGS84 okno vydúva (`21,49…22,50` → `21.000,48.996…22.013,49.628`) a tie
    presahy sú cudzie stupne, nie naše.
    """
    w, s, e, n = bounds
    lat_range = range(math.floor(s), math.ceil(n))
    lon_range = range(math.floor(w), math.ceil(e))
    if window is not None:
        # ÚNIA okna a rastra. Okno preto, že stupeň bez výšok sa musí uložiť
        # prázdny; raster preto, že presahy prevodu sa majú dať vypísať – „kde
        # je N48E021" je prvá otázka, ktorú si pri tom človek položí.
        lat_range = range(min(lat_range.start, math.floor(window[1])),
                          max(lat_range.stop, math.ceil(window[3])))
        lon_range = range(min(lon_range.start, math.floor(window[0])),
                          max(lon_range.stop, math.ceil(window[2])))

    write, partial = [], []
    for lat in lat_range:
        for lon in lon_range:
            # Prekryv s dátami. Zdroje presahujú celý stupeň o polpixel (tak sú
            # robené .hgt aj Copernicus dlaždice), takže „aspoň pár pixelov".
            over_x = min(lon + 1, e) - max(lon, w)
            over_y = min(lat + 1, n) - max(lat, s)
            thin = over_x <= 2 * dlon or over_y <= 2 * dlat
            name = tile_name(lon, lat)
            if window is None:
                if not thin:
                    write.append((lon, lat, name, True))
                continue
            # Tolerancia je pixel: okno sa rozširuje na celé stupne, takže sa
            # hranica môže rozísť len o zaokrúhlenie prevodu.
            if (lon < window[0] - dlon or lon + 1 > window[2] + dlon
                    or lat < window[1] - dlat or lat + 1 > window[3] + dlat):
                if not thin:
                    partial.append(name)
                continue
            write.append((lon, lat, name, not thin))
    return write, partial


def empty_tile(dst, lon, lat, dtype, nodata, px=60):
    """Prázdna dlaždica pre celý stupeň – „pozerali sme sa a nič tu nie je".

    Píše sa cez VRT bez zdroja (vrstva bez zdrojov číta svoje `NoDataValue`
    všade), takže vznikne bez ohľadu na to, kam raster dosiahol, a má kilobajty
    namiesto stoviek – mriežka je zámerne hrubá, v celej dlaždici aj tak nie je
    ani jedna výška.

    A OVERÍ SA, ŽE JE NAOZAJ PRÁZDNA. Keby VRT bez zdrojov vrátil nuly namiesto
    nodaty, ležala by v sklade dlaždica s výškou 0 m po celom stupni – a nula je
    v mape more. To je presne ten tichý omyl, proti ktorému je celý tento súbor,
    tak sa radšej dlaždica nevyrobí a povie sa prečo. Vracia True, keď vznikla.
    """
    nd = nodata if nodata is not None else (
        -9999.0 if dtype.startswith("Float") else -32768)
    vrt = dst + ".vrt"
    with open(vrt, "w") as f:
        f.write(
            f'<VRTDataset rasterXSize="{px}" rasterYSize="{px}">'
            f'<SRS>EPSG:4326</SRS>'
            f'<GeoTransform>{lon}, {1.0 / px}, 0, {lat + 1}, 0, {-1.0 / px}'
            f'</GeoTransform>'
            f'<VRTRasterBand dataType="{dtype}" band="1">'
            f'<NoDataValue>{nd!r}</NoDataValue></VRTRasterBand></VRTDataset>')
    try:
        subprocess.run(
            ["gdal_translate", "-q", "-of", "GTiff", "-a_nodata", repr(nd),
             "-co", "COMPRESS=DEFLATE", vrt, dst],
            check=True, env=NO_PAM)
    except subprocess.CalledProcessError as exc:
        print(f"::warning::Prázdnu dlaždicu {os.path.basename(dst)} sa "
              f"nepodarilo vyrobiť ({exc}) – doplnenie sa na ten stupeň bude "
              f"pýtať znova.")
        return False
    finally:
        os.remove(vrt)
    if elevation_range(dst) is not None:
        os.remove(dst)
        print(f"::warning::Prázdna dlaždica {os.path.basename(dst)} nevyšla "
              f"prázdna (GDAL do nej dal hodnoty namiesto nodaty) – radšej ju "
              f"neukladám, nula v mape je more.")
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="+", help="vstupné rastre")
    ap.add_argument("--out", required=True)
    # bilineárne, nie kubicky: pri prakticky rovnakej mierke je to rovnako
    # dobré a na okrajoch dát ani pri dierach nič „neprestrelí" mimo rozsah
    # skutočných výšok.
    ap.add_argument("--resampling", default="bilinear")
    ap.add_argument("--window", default="",
                    help="W,S,E,N – okno, ktoré volajúci NAOZAJ prečítal. "
                         "Stupeň, ktorý doň nepadne celý, sa neuloží; stupeň "
                         "bez výšok sa uloží prázdny (rozpis v hlavičke).")
    args = ap.parse_args()

    window = None
    if args.window.strip():
        vals = [float(v) for v in args.window.split(",")]
        if len(vals) != 4:
            raise SystemExit(f"::error::--window chce W,S,E,N: „{args.window}“")
        window = tuple(vals)

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
    empty = []
    write, partial = plan_tiles((w, s, e, n), window, dlon, dlat)
    for lon, lat, name, has_data in write:
        dst = os.path.join(args.out, f"{name}.tif")
        if not has_data:
            # Stupeň v okne, ktorý raster vôbec nepretína (model nekončí na
            # hranici obdĺžnika, ale na hranici krajiny). Prázdna dlaždica
            # sa píše bez warpu – gdalwarp s `-te` mimo zdroja je zbytočná
            # gigabajtová operácia pre výsledok, ktorý je celý nodata.
            if empty_tile(dst, lon, lat, dtype, nodata):
                empty.append(name)
                print(f"  ○ {name} (v okne, ale model tam nedosahuje)")
            continue
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
            if window is None:
                # celá dlaždica je nodata – do skladu nemá čo pridať
                os.remove(dst)
                continue
            # S oknom je prázdna dlaždica ODPOVEĎ: „tento stupeň sme prečítali
            # celý a terén v ňom nie je". Ostáva v sklade, nech sa naň
            # doplnenie nepýta v každom builde znova – a prepíše sa za hrubú,
            # nech nezaberá stovky megabajtov nodaty.
            os.remove(dst)
            if empty_tile(dst, lon, lat, dtype, nodata):
                empty.append(name)
                print(f"  ○ {name} (prečítaný celý, výšky v ňom nie sú)")
            continue
        made.append(name)
        print(f"  ✓ {name}")

    for t in temps:
        if os.path.exists(t):
            os.remove(t)
    if partial:
        # Nie warning: je to správny výsledok správne prečítaného okna. Ale
        # musí byť v logu, inak sa „prečo tam nie je N48E021" hľadá v sklade.
        print(f"Mimo okna, neukladá sa: {' '.join(sorted(set(partial)))} – "
              f"okno {args.window} tie stupne neprečítalo celé a meno "
              f"dlaždice je sľub o celom stupni.")
    if not made and not empty:
        raise SystemExit("Raster nepokrýva ani jednu celú 1° dlaždicu.")

    print(f"{len(made)} dlaždíc: {' '.join(sorted(set(made)))}"
          + (f" (+ {len(empty)} prázdnych: {' '.join(sorted(set(empty)))})"
             if empty else ""))


if __name__ == "__main__":
    sys.exit(main())

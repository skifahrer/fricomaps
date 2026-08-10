#!/usr/bin/env python3
"""
Zaoblí obrys plôch AJ priebeh čiar (Chaikinovo orezávanie rohov) – aby
skaly ani vrstevnice neboli pri najvyššom zoome zubaté.

JEDEN SÚBOR NA OBE, lebo je to jedna otázka: „ako sa zaobľuje izolínia nad
rastrom". Skala je izolínia sklonu, vrstevnica izolínia výšky; obe chodia
po hranách buniek a obe sa pred kreslením zjednodušujú. Dve kópie Chaikina
by sa raz rozišli a jedna vrstva by bola hladká inak než druhá. Čo je
plocha a čo čiara, sa zistí zo samotnej geometrie – volajúci to nemusí
hovoriť (`rock-areas.py`, `shading-vector.py`, `contours-build.sh`).

PREČO TO TREBA: obrys skaly je izolínia sklonu nad rastrom, čiže chodí po
hranách buniek – veľmi veľa krátkych segmentov, ktoré sa občas zlomia
o 90°. Keď sa to zmenší Douglas–Peuckerom, počet bodov klesne 8×, lenže
tie lomy sa nasčítajú do ostrých rohov: priemerný lom vyskočí zo 4,6° na
28,5°. Práve to je tá zubatosť, ktorú vidno pri max zoome – a spôsobuje ju
zjednodušenie, nie raster. Vrstevnica je na tom rovnako, len sa u nej
schodíky vidia aj bez zjednodušenia: pri 1 m DEM je jeden schodík meter
a to je pri z16 (1,57 m na pixel) presne ten „zúbok" na čiare.

RIEŠENIE: zjednodušiť a rohy potom zaobliť. Chaikin každý roh nahradí dvomi
bodmi v 1/4 a 3/4 hrany, takže sa jeden lom rozdelí na dva polovičné; dva
prechody dajú štvrtinové. Namerané na tom istom území (326 plôch, mriežka
4 m, prah 50°):

    bez úprav                     640 021 bodov, priemerný lom  4,6°, >60° 0,1 %
    simplify 0,5 m                 91 256 bodov, priemerný lom 28,5°, >60° 0,9 %  ← zubaté
    simplify 0,5 m + chaikin 1    181 975 bodov, priemerný lom 14,3°, >60° 0,4 %
    simplify 0,5 m + chaikin 2    363 341 bodov, priemerný lom  7,7°, >60° 0,1 %  ← toto

Dva prechody teda dajú hladší obrys než pôvodný raster (0,1 % ostrých lomov
namiesto 0,1 % pri 6× menej ostrých rohoch) a stále o 43 % menej bodov než
nezjednodušený originál.

ČO SA NESKÚŠALO NASLEPO: vyhladzovanie samotného rastra sklonu (priemer 3×3
pred vektorizáciou) obrys síce zjemní, ale zníži špičky sklonu a okolo prahu
z toho vznikne množstvo drobných úlomkov – z 326 plôch bolo naraz 1668.
Preto sa hladí až hotová geometria, nie raster.

Diery ostávajú dierami: zaobľuje sa každý prstenec zvlášť, vnútorné aj
vonkajší, a poradie prstencov sa nemení.

ČIARA SA ZAOBĽUJE INAK NEŽ PRSTENEC – a je to podstatné. Prstenec je
uzavretý, takže sa oreže každý roh vrátane toho medzi posledným a prvým
bodom. Otvorená čiara má dva konce, ktoré rohmi nie sú: keby sa orezali,
vrstevnica by sa pri každom prechode skrátila o štvrtinu krajnej hrany a na
hranici dlaždice by medzi dvomi kusmi tej istej čiary vznikla medzera.
Konce preto ostávajú, kde sú. Uzavretá vrstevnica (a tých je väčšina –
vrchol, kotlina) sa pozná podľa toho, že prvý bod je posledný, a ide cez
prstencovú vetvu.

Ide to prúdom cez GeoJSONSeq (jeden útvar na riadok), aby sa nikdy nedržala
v pamäti celá vrstva – kraj má státisíce plôch. GDAL python bindings netreba,
stačí `ogr2ogr` z gdal-bin.

Použitie:
    python3 workers/contours-rocks/smooth-shapes.py --in=rock.gpkg --out=rock-smooth.gpkg \\
        --layer=rock --passes=2
    python3 workers/contours-rocks/smooth-shapes.py --in=raw.gpkg --out=contours.gpkg \\
        --layer=contours --passes=1
"""
import argparse
import json
import os
import subprocess
import sys


def chaikin_ring(ring, passes):
    """Chaikinovo orezávanie rohov na uzavretom prstenci."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else list(ring)
    # Trojuholník sa orezávať nemá zmysel a kratší prstenec je odpad.
    if len(pts) < 4:
        return list(ring)
    for _ in range(passes):
        out = []
        n = len(pts)
        for i in range(n):
            (x0, y0), (x1, y1) = pts[i][:2], pts[(i + 1) % n][:2]
            out.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            out.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        pts = out
    return pts + [pts[0]]


def chaikin_line(line, passes):
    """To isté na otvorenej čiare – ale s konzervovanými koncami.

    Krajné body sa neorezávajú: sú to konce čiary, nie rohy. Bez toho by sa
    vrstevnica pri každom prechode skrátila o štvrtinu krajnej hrany a dva
    kusy tej istej čiary by na hranici dlaždice prestali na seba sadnúť.
    Uzavretá čiara (prvý bod = posledný) ide cez prstencovú vetvu, kde je
    naopak orezanie každého rohu správne.
    """
    pts = list(line)
    if len(pts) > 2 and pts[0] == pts[-1]:
        return chaikin_ring(pts, passes)
    if len(pts) < 3:
        return pts
    for _ in range(passes):
        out = [pts[0]]
        for i in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[i][:2], pts[i + 1][:2]
            out.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            out.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        out.append(pts[-1])
        pts = out
    return pts


# Ktoré typy geometrie sa hladia – a čím. Zoznam je tu raz, nech sa
# `smooth_geometry`, `count_points` aj výber `-nlt` na výstupe nemôžu
# rozísť v tom, čo tento skript vlastne vie.
POLYGONS = ("Polygon", "MultiPolygon")
LINES = ("LineString", "MultiLineString")


def smooth_geometry(geom, passes):
    if not geom:
        return geom
    t = geom.get("type")
    if t in POLYGONS:
        parts = [geom["coordinates"]] if t == "Polygon" else geom["coordinates"]
        new = [[chaikin_ring(ring, passes) for ring in poly] for poly in parts]
        geom["coordinates"] = new if t == "MultiPolygon" else new[0]
    elif t in LINES:
        parts = [geom["coordinates"]] if t == "LineString" else geom["coordinates"]
        new = [chaikin_line(line, passes) for line in parts]
        geom["coordinates"] = new if t == "MultiLineString" else new[0]
    return geom


def count_points(geom):
    t = geom["type"]
    if t in POLYGONS:
        parts = ([geom["coordinates"]] if t == "Polygon"
                 else geom["coordinates"])
        return sum(len(r) for p in parts for r in p)
    parts = ([geom["coordinates"]] if t == "LineString"
             else geom["coordinates"])
    return sum(len(line) for line in parts)


def layer_srs(path, layer):
    """(`EPSG:kód`, je_projektovaná) zdrojovej vrstvy.

    Treba to, lebo ovládač GeoJSON prepočítava VŽDY do WGS84, nech si o tom
    človek myslí čokoľvek – aj s `RFC7946=NO`. Metrická vrstva by teda po
    prechode cez GeoJSONSeq vyšla v stupňoch a `ST_Area` by potom vracala
    nezmysly (namerané: „spolu 0,00 km²" na území, kde sú hektáre). Tu sa
    preto zistí pôvodné CRS, na výstupe sa vrstve LEN PRIRADÍ `EPSG:4326`
    (súradnice sa nemenia) a pri čítaní späť sa priradí zase to pôvodné.
    """
    try:
        info = json.loads(subprocess.run(
            ["ogrinfo", "-json", "-so", path, layer],
            capture_output=True, text=True, check=True).stdout)
        cs = info["layers"][0]["geometryFields"][0]["coordinateSystem"]
        pj = cs.get("projjson") or {}
        ident = pj.get("id") or {}
        code = ident.get("code")
        auth = ident.get("authority", "EPSG")
        projected = pj.get("type") == "ProjectedCRS"
        if code:
            return f"{auth}:{code}", projected
    except (subprocess.CalledProcessError, ValueError, KeyError, IndexError):
        pass
    return "", False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--layer", default="rock")
    ap.add_argument("--passes", type=int, default=2,
                    help="koľkokrát orezať rohy (0 = vypnuté)")
    args = ap.parse_args()

    if args.passes <= 0:
        subprocess.run(["ogr2ogr", "-f", "GPKG", args.dst, args.src,
                        "-nln", args.layer, "-overwrite"], check=True)
        print("  zaoblenie: vypnuté (passes=0)", flush=True)
        return 0

    srs, projected = layer_srs(args.src, args.layer)
    seq = args.dst + ".seq.json"
    tmp = seq + ".sm"
    for f in (seq, tmp):
        if os.path.exists(f):
            os.remove(f)
    # GeoJSONSeq = jeden útvar na riadok, takže sa dá čítať aj písať prúdom.
    # `-a_srs EPSG:4326` NEprepočítava, len prekryje značku CRS – bez toho by
    # ovládač metrickú vrstvu prehnal do stupňov. Milimeter stačí: mriežka
    # sklonu má metre a Chaikin robí štvrtiny hrán, nie mikróny.
    export = ["ogr2ogr", "-f", "GeoJSONSeq", seq, args.src, args.layer]
    if projected:
        export += ["-a_srs", "EPSG:4326", "-lco", "COORDINATE_PRECISION=3"]
    subprocess.run(export, check=True)

    # Čo to je – plochy alebo čiary – hovorí sama geometria, nie prepínač.
    # Volajúci by ho musel držať v súlade s tým, čo do skriptu naozaj posiela,
    # a to je presne to miesto, kde sa dve pravdy raz rozídu.
    n, pts_in, pts_out, kind = 0, 0, 0, ""
    with open(seq) as fi, open(tmp, "w") as fo:
        for line in fi:
            line = line.strip()
            if not line:
                continue
            feat = json.loads(line)
            g = feat.get("geometry")
            n += 1
            if g and g.get("type") in POLYGONS + LINES:
                kind = "plocha" if g["type"] in POLYGONS else "čiara"
                pts_in += count_points(g)
                feat["geometry"] = smooth_geometry(g, args.passes)
                pts_out += count_points(feat["geometry"])
            fo.write(json.dumps(feat, separators=(",", ":")) + "\n")
    os.remove(seq)

    if os.path.exists(args.dst):
        os.remove(args.dst)
    # `-makevalid`: Chaikin je konvexná kombinácia susedných bodov, takže sa
    # obrys sám do seba nezareže – okrem veľmi tenkých ostňov, kde sa dva
    # zaoblené okraje môžu dotknúť. Tie sa tu opravia, nech do dlaždíc nejde
    # neplatný polygón. Na čiare nemá čo opravovať (čiara sa smie krížiť),
    # takže sa tam ani nevolá – ušetrí to priechod nad státisícmi vrstevníc.
    lines = kind == "čiara"
    cmd = ["ogr2ogr", "-f", "GPKG", args.dst, tmp, "-nln", args.layer]
    # Prázdna vrstva nepovedala, čo v nej malo byť – vtedy sa typ NEVNUCUJE.
    # Dosadiť sem MULTIPOLYGON „aby tam niečo bolo" by zo vstupu s vypnutou
    # vrstvou spravilo vrstvu nesprávneho typu a schéma by ju zahodila.
    if kind:
        cmd += ["-nlt", "MULTILINESTRING" if lines else "MULTIPOLYGON"]
    if kind and not lines:
        cmd += ["-makevalid"]
    if projected and srs:
        # Súradnice sú stále v metroch, len sa tvárili ako stupne – tu sa
        # vrstve vráti jej skutočné CRS. Zase bez prepočtu.
        cmd += ["-a_srs", srs]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        print("::warning::-makevalid nefunguje (starý GDAL) – zaoblené skaly "
              "idú bez kontroly platnosti.")
        subprocess.run([c for c in cmd if c != "-makevalid"], check=True)
    os.remove(tmp)

    grew = pts_out / pts_in if pts_in else 1.0
    print(f"  zaoblenie: {args.passes}× orezanie rohov, {n} "
          f"{'čiar' if lines else 'plôch'}, "
          f"bodov {pts_in} → {pts_out} ({grew:.2f}×)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

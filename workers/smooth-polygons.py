#!/usr/bin/env python3
"""
Zaoblí obrys polygónov (Chaikinovo orezávanie rohov) – aby skaly neboli
pri najvyššom zoome zubaté.

PREČO TO TREBA: obrys skaly je izolínia sklonu nad rastrom, čiže chodí po
hranách buniek – veľmi veľa krátkych segmentov, ktoré sa občas zlomia
o 90°. Keď sa to zmenší Douglas–Peuckerom, počet bodov klesne 8×, lenže
tie lomy sa nasčítajú do ostrých rohov: priemerný lom vyskočí zo 4,6° na
28,5°. Práve to je tá zubatosť, ktorú vidno pri max zoome – a spôsobuje ju
zjednodušenie, nie raster.

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

Ide to prúdom cez GeoJSONSeq (jeden útvar na riadok), aby sa nikdy nedržala
v pamäti celá vrstva – kraj má státisíce plôch. GDAL python bindings netreba,
stačí `ogr2ogr` z gdal-bin.

Použitie:
    python3 workers/smooth-polygons.py --in=rock.gpkg --out=rock-smooth.gpkg \\
        --layer=rock --passes=2
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


def smooth_geometry(geom, passes):
    if not geom:
        return geom
    t = geom.get("type")
    if t == "Polygon":
        parts = [geom["coordinates"]]
    elif t == "MultiPolygon":
        parts = geom["coordinates"]
    else:
        return geom
    new = [[chaikin_ring(ring, passes) for ring in poly] for poly in parts]
    geom["coordinates"] = new if t == "MultiPolygon" else new[0]
    return geom


def count_points(geom):
    parts = ([geom["coordinates"]] if geom["type"] == "Polygon"
             else geom["coordinates"])
    return sum(len(r) for p in parts for r in p)


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

    n, pts_in, pts_out = 0, 0, 0
    with open(seq) as fi, open(tmp, "w") as fo:
        for line in fi:
            line = line.strip()
            if not line:
                continue
            feat = json.loads(line)
            g = feat.get("geometry")
            n += 1
            if g and g.get("type") in ("Polygon", "MultiPolygon"):
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
    # neplatný polygón.
    cmd = ["ogr2ogr", "-f", "GPKG", args.dst, tmp, "-nln", args.layer,
           "-nlt", "MULTIPOLYGON", "-makevalid"]
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
    print(f"  zaoblenie obrysu: {args.passes}× orezanie rohov, {n} plôch, "
          f"bodov {pts_in} → {pts_out} ({grew:.2f}×)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

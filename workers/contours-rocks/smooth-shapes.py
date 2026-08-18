#!/usr/bin/env python3
"""
Zaoblí obrys plôch AJ priebeh čiar – aby skaly ani vrstevnice neboli zubaté.

JEDEN SÚBOR NA OBE, lebo je to jedna otázka: „ako sa zaobľuje izolínia nad
rastrom". Skala je izolínia sklonu, vrstevnica izolínia výšky; obe chodia
po hranách buniek a obe sa pred kreslením zjednodušujú. Dve kópie by sa raz
rozišli a jedna vrstva by bola hladká inak než druhá. Čo je plocha a čo čiara,
sa zistí zo samotnej geometrie – volajúci to nemusí hovoriť (`rock-areas.py`,
`rocks-shading/vector.py`, `contours-rocks/build.sh`).

PREČO TO TREBA: obrys je izolínia nad rastrom, čiže chodí po hranách buniek –
veľmi veľa krátkych segmentov, ktoré sa občas zlomia o 90°. Keď sa to zmenší
Douglas–Peuckerom, počet bodov klesne 8×, lenže tie lomy sa nasčítajú do
ostrých rohov: priemerný lom vyskočí zo 4,6° na 28,5°. Práve to je tá
zubatosť, ktorú vidno pri max zoome – a spôsobuje ju zjednodušenie, nie raster.

RIEŠENIE: zjednodušiť a rohy potom zaobliť. Zaobľuje sa KVADRATICKÝM
B-SPLINOM nad zjednodušenou čiarou – teda presne tou krivkou, ku ktorej
Chaikinovo orezávanie rohov konverguje – a vzorkuje sa rovno tak husto, ako to
dlaždica unesie.

PREČO NIE DVA PRECHODY CHAIKINA, AKO TO BOLO. Chaikin sa k tej krivke len
BLÍŽI a robí to LOKÁLNE: jeden prechod nahradí roh dvomi bodmi v štvrtine
a troch štvrtinách hrany, takže sa lom rozpolí, ale nezmizne. Po dvoch
prechodoch ostane zo 120° rohu ešte vyše 30° – a to je zub. Rozostup tých
zubov je rozostup vrcholov po Douglas–Peuckerovi, čiže PRAVIDELNÝ; na
vrstevniciach z hotovej mapy (Bratislavský kraj, beh 143, 3 220 km čiar
z `bratislavsky-contours.pmtiles`) vychádza 14,7 lomu nad 30° na kilometer,
teda jeden zhruba každých 68 m, a pri z16 je to zlom každých ~40 px. Že za ne
môže zaoblenie a nie terén, ukázal test na vzorec Chaikina: po dvoch
prechodoch má zvyškový roh v každej čiare pevnú pozíciu (každý štvrtý bod),
a ostré lomy na nej naozaj sedia častejšie, než by vyšlo náhodou (0,50 proti
0,39 pri náhodnom výbere z tej istej čiary).

Tretí prechod tie rohy dorovná, ale zdvojnásobí počet bodov – a to je horšie
než zuby: dlaždica má súradnice na celočíselnej mriežke `extent` (4096,
Planetiler ju meniť nevie), takže body hustejšie než jej krok sa zlepia
a z hladkej čiary sa stanú schodíky. Namerané po mriežke z14: 2× Chaikin
11,5 % lomov nad 30°, 3× Chaikin 34,6 %. Preto sa nedá „pridať prechod".

AKO HUSTO SA VZORKUJE: podľa PRIEHYBU TETIVY, nie podľa dĺžky. Oblúk medzi
vrcholmi a–b–c má od svojej tetivy priehyb |2b − a − c| / 8 a rozdelenie na
`n` dielov ho zmenší n²-krát, takže `n = ⌈√(priehyb / tolerancia)⌉`. Tolerancia
je zlomok kroku mriežky dlaždice (`--sag`, v štvrtinách): jemnejší detail
mriežka aj tak zahodí a schodíky z neho len pribudnú. Rovná časť čiary tak
dostane jeden bod, zákruta toľko, koľko treba – a počet bodov ide dole.

Namerané na tom istom modeli terénu ako doteraz (okno 3×3, simplify ¼ bunky,
`measure-smoothing.py --maxzoom=16`, čo je maxzoom, ktorý vrstevnice na
hotových mapách naozaj majú). „Zub/km" sú ostré lomy TVARU – čiara sa pred
meraním prevzorkuje rovnomerne, inak by sa porovnávala hustota bodov, nie
zubatosť. Posledné stĺpce sú tá istá čiara po zaokrúhlení na mriežku
dlaždice, teda to, čo naozaj skončí v `.pmtiles`:

    zaoblenie                  bodov  zub/km  tvar 12 m │ po mriežke z16
    1× Chaikin                   430    31,9      65 %  │ 32,9
    2× Chaikin (doteraz)         860    10,0      63 %  │ 11,0
    3× Chaikin                  1720     4,0      63 %  │  5,0
    limitná, priehyb ¼ mriežky   767     5,0      62 %  │  6,0
    limitná, priehyb ½ mriežky   586     5,0      62 %  │  8,1   ← teraz

A to isté celou cestou cez skutočný `gdal_contour` (5 m model, 700×700 buniek,
mikroreliéf σ 0,25 m, interval 5 m, 89 čiar; `--simplify` ¼ bunky ako v behu):

    zaoblenie                     bodov │ zub/km po mriežke z16   z14
    2× Chaikin (doteraz)         56 792 │        0,7             3,0
    3× Chaikin                  113 580 │        0,1            16,2
    limitná, priehyb ¼ mriežky   76 933 │        0,1             4,2
    limitná, priehyb ½ mriežky   56 421 │        0,1             1,6   ← teraz

POL KROKU MRIEŽKY, nie štvrtina, a rozhoduje o tom druhá tabuľka: pri hrubšom
modeli (5 m bunka, teda `-simplify` 1,25 m) je štvrtina o 35 % viac bodov za
rovnaký počet zubov. Väčšia vrstva pritom nie je len väčšia – rozpočet stránky
by jej mohol zobrať celý jeden zoom, a to je proti zubatosti oveľa horšie než
polovičný priehyb. Pol kroku je aj presne toľko, koľko spraví samo
zaokrúhlenie do mriežky (±pol kroku), takže sa vzorkovaním neplatí za detail,
ktorý dlaždica aj tak zahodí.

Chaikin oproti tomu násobí počet bodov ŠTYRMI bez ohľadu na to, aká hrubá je
vstupná čiara – dva prechody sú vždy 4×, tri vždy 8×. Práve preto sa tretí
prechod nedá „len pridať": pri 5 m modeli je to 113 580 bodov a po mriežke z14
16,2 zuba/km, teda päťnásobok toho, čo má limitná krivka za polovicu bodov.

ČO SA NESKÚŠALO NASLEPO: vyhladzovanie samotného rastra sklonu (priemer 3×3
pred vektorizáciou) obrys síce zjemní, ale zníži špičky sklonu a okolo prahu
z toho vznikne množstvo drobných úlomkov – z 326 plôch bolo naraz 1668.
Preto sa hladí až hotová geometria, nie raster.

Diery ostávajú dierami: zaobľuje sa každý prstenec zvlášť, vnútorné aj
vonkajší, a poradie prstencov sa nemení.

ČIARA SA ZAOBĽUJE INAK NEŽ PRSTENEC – a je to podstatné. Prstenec je
uzavretý, takže sa zaoblí každý roh vrátane toho medzi posledným a prvým
bodom. Otvorená čiara má dva konce, ktoré rohmi nie sú: keby sa orezali,
vrstevnica by sa skrátila a na hranici dlaždice by medzi dvomi kusmi tej istej
čiary vznikla medzera. Konce preto ostávajú PRESNE tam, kde boli (krajný
riadiaci bod je zdvojený, čo B-spline v koncovom bode „pribije"). Uzavretá
vrstevnica (a tých je väčšina – vrchol, kotlina) sa pozná podľa toho, že prvý
bod je posledný, a ide cez prstencovú vetvu.

Ide to prúdom cez GeoJSONSeq (jeden útvar na riadok), aby sa nikdy nedržala
v pamäti celá vrstva – kraj má státisíce plôch. GDAL python bindings netreba,
stačí `ogr2ogr` z gdal-bin.

Použitie:
    python3 workers/contours-rocks/smooth-shapes.py --in=rock.gpkg \\
        --out=rock-smooth.gpkg --layer=rock --maxzoom=16 --sag=1
    python3 workers/contours-rocks/smooth-shapes.py --in=raw.gpkg \\
        --out=contours.gpkg --layer=contours --maxzoom=14 --sag=1
"""
import argparse
import json
import math
import os
import subprocess
import sys

# Krok mriežky dlaždice pozná `lib/cell.py` – to isté miesto, ktoré hovorí,
# koľko je pixel dlaždice v metroch (pravidlo 1 v CLAUDE.md).
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import cell  # noqa: E402

# Strop počtu vzoriek na jeden oblúk. Pri rozumnom vstupe sa nedosiahne
# (priehyb je zlomok metra), je to poistka proti riadiacemu polygónu
# s kilometrovými hranami a pravým uhlom – z toho by inak vypadli desaťtisíce
# bodov na jeden oblúk.
MAX_SAMPLES = 64


def _arc(a, b, c, tol, out):
    """Jeden oblúk kvadratického B-splinu (a–b–c) – body pre t ∈ (0, 1].

    Oblúk ide od stredu hrany a–b do stredu hrany b–c; `b` je roh, ktorý sa
    zaobľuje. Priehyb od tetivy klesá s n², tak sa `n` z tolerancie priamo
    dopočíta. Rovná časť (priehyb 0) dostane jeden bod, čiže žiadne body
    navyše.
    """
    (x0, y0), (x1, y1), (x2, y2) = a[:2], b[:2], c[:2]
    # Priehyb sa meria KOLMO na tetivu, nie ako dĺžka rozdielu stredov. Pri
    # zdvojenom krajnom bode (koniec čiary) je oblúk rovný, ale stredy sa aj
    # tak nekryjú – posun je POZDĹŽ tetivy, teda parametrizácia, nie tvar.
    # Nekolmá miera by tam natlačila desiatky bodov do priamky.
    sx, sy = (x0 + x1) / 2, (y0 + y1) / 2          # začiatok oblúka
    ex, ey = (x1 + x2) / 2, (y1 + y2) / 2          # koniec oblúka
    mx, my = (x0 + 6 * x1 + x2) / 8, (y0 + 6 * y1 + y2) / 8   # stred oblúka
    dx, dy = ex - sx, ey - sy
    d = math.hypot(dx, dy)
    if d > 0:
        sag = abs((mx - sx) * dy - (my - sy) * dx) / d
    else:
        sag = math.hypot(mx - sx, my - sy)
    # Rozdelenie oblúka na `n` dielov zmenší priehyb n²-krát.
    n = 1 if sag <= tol else min(MAX_SAMPLES,
                                 int(math.ceil(math.sqrt(sag / tol))))
    for k in range(1, n + 1):
        t = k / n
        w0, w1, w2 = 0.5 * (1 - t) ** 2, 0.5 + t - t * t, 0.5 * t * t
        out.append((w0 * x0 + w1 * x1 + w2 * x2,
                    w0 * y0 + w1 * y1 + w2 * y2))


def curve_ring(ring, tol):
    """Limitná krivka nad uzavretým prstencom – zaoblí sa každý roh."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else list(ring)
    # Trojuholník zaobľovať nemá zmysel a kratší prstenec je odpad.
    if len(pts) < 4:
        return list(ring)
    n = len(pts)
    first = ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2)
    out = [first]
    for i in range(n):
        _arc(pts[i], pts[(i + 1) % n], pts[(i + 2) % n], tol, out)
    # Posledný oblúk končí presne v prvom bode – prstenec je tým uzavretý.
    out[-1] = first
    return out


def curve_line(line, tol):
    """To isté na otvorenej čiare – ale s konzervovanými koncami.

    Krajné body sa nehýbu: sú to konce čiary, nie rohy. Bez toho by sa
    vrstevnica skrátila a dva kusy tej istej čiary by na hranici dlaždice
    prestali na seba sadnúť. Robí to zdvojenie krajného riadiaceho bodu –
    kvadratický B-spline vtedy koncom prechádza presne.
    """
    pts = list(line)
    if len(pts) > 2 and pts[0] == pts[-1]:
        return curve_ring(pts, tol)
    if len(pts) < 3:
        return pts
    ctrl = [pts[0], pts[0]] + pts[1:-1] + [pts[-1], pts[-1]]
    out = [tuple(pts[0][:2])]
    for a, b, c in zip(ctrl, ctrl[1:], ctrl[2:]):
        _arc(a, b, c, tol, out)
    out[-1] = tuple(pts[-1][:2])
    return out


# Ktoré typy geometrie sa hladia – a čím. Zoznam je tu raz, nech sa
# `smooth_geometry`, `count_points` aj výber `-nlt` na výstupe nemôžu
# rozísť v tom, čo tento skript vlastne vie.
POLYGONS = ("Polygon", "MultiPolygon")
LINES = ("LineString", "MultiLineString")


def smooth_geometry(geom, tol):
    if not geom:
        return geom
    t = geom.get("type")
    if t in POLYGONS:
        parts = [geom["coordinates"]] if t == "Polygon" else geom["coordinates"]
        new = [[curve_ring(ring, tol) for ring in poly] for poly in parts]
        geom["coordinates"] = new if t == "MultiPolygon" else new[0]
    elif t in LINES:
        parts = [geom["coordinates"]] if t == "LineString" else geom["coordinates"]
        new = [curve_line(line, tol) for line in parts]
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


def tolerance(srs, projected, maxzoom, sag):
    """Dovolený priehyb tetivy V JEDNOTKÁCH VRSTVY (stupne alebo metre).

    Zadáva sa v štvrtinách kroku mriežky dlaždice – to je jednotka, v ktorej
    o tom rozhoduje dlaždica, nie vrstva. Prevod je tu preto, že každá vrstva
    chodí do tohto skriptu v inom CRS: vrstevnice v EPSG:4326 (stupne), skaly
    zo sklonu v EPSG:3035 (metre v teréne) a skaly z tieňovania v EPSG:3857
    (metre Mercatora, ktoré sú u nás ~1,5× dlhšie než na zemi).
    """
    m = cell.tile_grid_m(maxzoom) * sag / 4.0
    if not projected:
        # Delí sa DLHŠÍM stupňom (po šírke), takže na zemi tolerancia nikdy
        # nevyjde väčšia, než sa žiadalo – po dĺžke je u nás stupeň kratší.
        return m / cell.M_PER_DEG_LAT, f"{m:.3f} m"
    if srs == "EPSG:3857":
        return m / math.cos(math.radians(cell.DEFAULT_LAT)), f"{m:.3f} m"
    return m, f"{m:.3f} m"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--layer", default="rock")
    ap.add_argument("--maxzoom", type=int, default=16,
                    help="maxzoom dlaždíc tejto vrstvy – podľa neho sa určí "
                         "krok mriežky, a teda hustota vzoriek")
    ap.add_argument("--sag", type=float, default=1.0,
                    help="dovolený priehyb tetivy v ŠTVRTINÁCH kroku mriežky "
                         "dlaždice (0 = zaoblenie vypnuté)")
    args = ap.parse_args()

    if args.sag <= 0:
        subprocess.run(["ogr2ogr", "-f", "GPKG", args.dst, args.src,
                        "-nln", args.layer, "-overwrite"], check=True)
        print("  zaoblenie: vypnuté (sag=0)", flush=True)
        return 0

    srs, projected = layer_srs(args.src, args.layer)
    tol, tol_m = tolerance(srs, projected, args.maxzoom, args.sag)
    seq = args.dst + ".seq.json"
    tmp = seq + ".sm"
    for f in (seq, tmp):
        if os.path.exists(f):
            os.remove(f)
    # GeoJSONSeq = jeden útvar na riadok, takže sa dá čítať aj písať prúdom.
    # `-a_srs EPSG:4326` NEprepočítava, len prekryje značku CRS – bez toho by
    # ovládač metrickú vrstvu prehnal do stupňov. Milimeter stačí: mriežka
    # sklonu má metre a priehyb tetivy je zlomok kroku dlaždice, nie mikrón.
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
                feat["geometry"] = smooth_geometry(g, tol)
                pts_out += count_points(feat["geometry"])
            fo.write(json.dumps(feat, separators=(",", ":")) + "\n")
    os.remove(seq)

    # PRÁZDNA VRSTVA SA NEPREPISUJE CEZ GeoJSONSeq. Nula útvarov = nula
    # riadkov = súbor s nulovou dĺžkou, a ten ovládač neotvorí („Unable to
    # open datasource") – krok by spadol na vrstve, v ktorej nie je čo
    # zaobľovať. Vypnutá vrstva pritom nie je chyba: `build.sh` ju vyrába
    # naschvál (`make_empty_gpkg`), lebo schéma na ňu odkazuje vždy.
    if n == 0:
        os.remove(tmp)
        subprocess.run(["ogr2ogr", "-f", "GPKG", args.dst, args.src,
                        "-nln", args.layer, "-overwrite"], check=True)
        print("  zaoblenie: vrstva je prázdna, niet čo zaobľovať", flush=True)
        return 0

    if os.path.exists(args.dst):
        os.remove(args.dst)
    # `-makevalid`: každý bod krivky je konvexnou kombináciou susedných
    # riadiacich bodov, takže sa obrys sám do seba nezareže – okrem veľmi
    # tenkých ostňov, kde sa dva zaoblené okraje môžu dotknúť. Tie sa tu
    # opravia, nech do dlaždíc nejde neplatný polygón. Na čiare nemá čo
    # opravovať (čiara sa smie krížiť), takže sa tam ani nevolá – ušetrí to
    # priechod nad státisícmi vrstevníc.
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
    print(f"  zaoblenie: limitná krivka, priehyb do {args.sag / 4:.2f}× kroku "
          f"mriežky z{args.maxzoom} ({tol_m}), {n} "
          f"{'čiar' if lines else 'plôch'}, "
          f"bodov {pts_in} → {pts_out} ({grew:.2f}×)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

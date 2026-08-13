#!/usr/bin/env python3
"""
Pokrýva stiahnutá mozaika DEM to územie, na ktorom sa má počítať?

PREČO EXISTUJE. Dlaždica je sľub o celom stupni (pravidlo 2 v CLAUDE.md) a
`workers/dem/check.sh` vie o sklade len MENÁ – čiže sa spolieha na to, že sľub
platí. Keď neplatil, build to nepovedal: v sklade `dem-dmr5` ležali
`N48E021.tif`, `N48E022.tif` a `N49E020.tif` s pár set metrami dát (5 MB vedľa
253 MB), lebo ich do skladu poslal presah prevodu do WGS84. Kontrola videla
šesť dlaždíc z ôsmich, doplnenie sa nespustilo, `gdal_contour` prešiel po
mozaike, v ktorej boli naozaj dva stupne – a vrstevnice Prešovského kraja
skončili v jednom štvorci. Beh bol zelený (31484544154).

ROZHODUJE ROZSAH DLAŽDICE, NIE POČET PLATNÝCH BUNIEK. Poctivá dlaždica má
rozsah presne celého stupňa aj vtedy, keď je v nej terén len na pätine plochy
(pohraničný stupeň, alebo prázdna dlaždica = „pozerali sme sa a nič tu nie
je"). Lož má rozsah pár pixelov. Rozsah teda tie dva prípady oddelí presne,
kým „koľko je v nej nodaty" ich zlieva.

DRUHÝ DRUH LŽI: PRÁZDNA DLAŽDICA OD KONTROLY, KTOREJ UŽ NEVERÍME. Rozsah má
poctivý (celý stupeň), a predsa je to nepravda – `N48E016.tif` z behu
31526268289 tvrdila, že v stupni s Devínom a Záhorím nie je terén, lebo ho
vzorkovaná štatistika prehliadla. Vrstevnice, skaly aj tieňovanie Bratislavského
kraja preto skončili rovnou líniou na 17. poludníku a pokrytie tu vyšlo 100 %.
Prázdna dlaždica sa odvtedy podpisuje verziou kontroly (`EMPTY_CHECK`
v `workers/dem/tiles.py`); nepodpísaná – alebo podpísaná verziou, ktorú sme
zahodili – je LOŽ ako každá iná a zo skladu ide preč.

Použitie:
    python3 workers/dem/coverage.py --bbox=19.865,48.745,22.585,49.48 \\
        --dir=dem/dmr5/tiles [--min-pct=95] [--out=cov.txt]

Vypisuje `key=value` (aj do `--out`):
    covered_pct=97.5          koľko z bboxu pokrývajú rozsahy dlaždíc
    liars=N48E021.tif …       dlaždice, ktoré nesplnili, čo sľubuje ich meno
    empty=N47E016 …           stupne, kde sa pozeralo a terén v nich nie je
    missing=N48E019 …         stupne bboxu, na ktoré nie je ani jedna dlaždica
Návratový kód 1 = pokrytie je pod `--min-pct` (volajúci sa rozhodne, čo s tým).
"""
import argparse
import json
import math
import os
import subprocess
import sys

# Ako vyzerá prázdna dlaždica a ktorá kontrola ju smela vyhlásiť za prázdnu,
# vie JEDNO miesto – ten, kto ju píše. Druhá predstava o tom istom by sa raz
# rozišla a tu by z toho bolo „mažem zo skladu všetko" alebo „neverím ničomu".
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tiles  # noqa: E402

# Koľko zo svojho stupňa musí dlaždica pokrývať, aby jej meno nebolo lož.
# Poctivá dlaždica má 100 % (píše ju `workers/dem/tiles.py` s `-te` na celý
# stupeň); tolerancia je na polpixel a na zaokrúhlenie v hlavičke.
HONEST_PCT = 99.0


def tile_info(path):
    """`gdalinfo -json` dlaždice, alebo None keď sa nedá prečítať.

    Jedno volanie na dlaždicu: rozsah aj podpis prázdnoty sú v tej istej
    odpovedi a druhý `gdalinfo` by za ne len znova zaplatil.
    """
    try:
        return json.loads(subprocess.run(
            ["gdalinfo", "-json", path], capture_output=True, text=True,
            check=True, env={**os.environ, "GDAL_PAM_ENABLED": "NO"}).stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return None


def empty_stamp(info):
    """Podpis prázdnej dlaždice, alebo None keď to prázdna dlaždica nie je.

    Prázdnu dlaždicu poznať po mriežke: `workers/dem/tiles.py` jej dáva
    zámerne hrubých `EMPTY_PX` pixelov na stranu, kým skutočná 1° dlaždica má
    tisíce (Sonny 20 m ~5 500, DMR 5.0 na 5 m ~18 000). Čo je také malé, je
    záznam „pozerali sme sa", nie výškový model – a vtedy sa pýtame na podpis.
    Bez podpisu vráti prázdny reťazec, teda „napísala to kontrola v1".
    """
    if (info.get("size") or [])[:2] != [tiles.EMPTY_PX, tiles.EMPTY_PX]:
        return None
    return ((info.get("metadata") or {}).get("", {})).get(tiles.EMPTY_TAG, "")


def extent_of(info):
    """(w, s, e, n) dlaždice v stupňoch, alebo None keď sa to nedá zistiť."""
    ext = info.get("wgs84Extent") or {}
    pts = []

    def walk(node):
        if node and isinstance(node[0], (int, float)):
            pts.append(node)
        else:
            for child in node or []:
                walk(child)

    walk(ext.get("coordinates"))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def degree_of(name):
    """`N49E020.tif` → (20, 49). Nesúvisiace meno vráti None."""
    base = os.path.basename(name).split(".")[0].upper()
    if len(base) != 7 or base[0] not in "NS" or base[3] not in "EW":
        return None
    try:
        lat, lon = int(base[1:3]), int(base[4:7])
    except ValueError:
        return None
    return (-lon if base[3] == "W" else lon, -lat if base[0] == "S" else lat)


def covers_own_degree(extent, degree):
    """Koľko percent svojho stupňa dlaždica pokrýva (podľa rozsahu)."""
    lon, lat = degree
    w = max(0.0, min(extent[2], lon + 1) - max(extent[0], lon))
    h = max(0.0, min(extent[3], lat + 1) - max(extent[1], lat))
    return 100.0 * w * h


def covered_pct(bbox, extents, cells=400):
    """Koľko z bboxu pokrýva únia rozsahov – meraná na pravidelnej mriežke.

    Mriežka, nie geometria: 400×400 buniek je na túto otázku („chýba pol kraja,
    alebo pol pixela?") presnosť 0,25 ‰ plochy a nepotrebuje na to shapely.
    """
    w, s, e, n = bbox
    if e <= w or n <= s or not extents:
        return 0.0
    dx, dy = (e - w) / cells, (n - s) / cells
    hit = 0
    for j in range(cells):
        y = s + (j + 0.5) * dy
        row = [x for x in extents if x[1] <= y <= x[3]]
        if not row:
            continue
        for i in range(cells):
            x = w + (i + 0.5) * dx
            if any(t[0] <= x <= t[2] for t in row):
                hit += 1
    return 100.0 * hit / (cells * cells)


def data_pct(path, region=None):
    """Koľko percent dlaždice má NAOZAJ výšky (nie nodata).

    PREČO TO NESTAČÍ MERAŤ ROZSAHOM. Doteraz sa pokrytie počítalo z rozsahov
    dlaždíc – „mozaika sa dotýka celého bboxu" – a to prejde aj vtedy, keď je
    dlaždica takmer prázdna. Presne to sa stalo v behu 31635772047: v sklade
    `dem-dmr5` mala `N49E020.tif` (Vysoké Tatry, čiže STRED Prešovského kraja)
    5 MB, kým susedná `N49E021.tif` 265 MB. Kontrola vypísala „Pokrytie územia
    100.0 % z 8 dlaždíc" a beh pokračoval – tieňovanie potom skončilo rovnou
    hranou na 21° a skalám z hrany dát vyšlo 13 403 km² falošných stien.
    Veľkosť súboru pritom stačila na to, aby to bolo vidieť; nikto sa nepozeral.

    `STATISTICS_VALID_PERCENT` počíta GDAL sám pri `-stats`. Je to PRESNÝ
    priechod, nie vzorkovanie (`-approx_stats` už raz odrezalo pol kraja, viď
    `tiles.py`), ale beží nad hotovým súborom na disku a je ich osem.
    """
    r = subprocess.run(["gdalinfo", "-json", "-stats", path],
                       capture_output=True, text=True, check=False)
    if r.returncode:
        return None
    try:
        info = json.loads(r.stdout)
    except ValueError:
        return None
    for band in info.get("bands") or []:
        md = (band.get("metadata") or {}).get("") or {}
        pct = md.get("STATISTICS_VALID_PERCENT")
        if pct is not None:
            try:
                return float(pct)
            except ValueError:
                return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", required=True, help="W,S,E,N územia, čo sa počíta")
    ap.add_argument("--dir", default="", help="adresár s dlaždicami")
    ap.add_argument("tiles", nargs="*", help="alebo priamo súbory")
    ap.add_argument("--min-pct", type=float, default=95.0,
                    help="pod týmto pokrytím je návratový kód 1")
    ap.add_argument("--out", default="", help="kam zapísať key=value")
    ap.add_argument("--data-pct", type=float, default=0.0,
                    help="dlaždica pod týmto podielom skutočných výšok "
                         "sa hlási ako podozrivá (0 = nekontrolovať)")
    args = ap.parse_args()

    bbox = tuple(float(v) for v in args.bbox.split(","))
    if len(bbox) != 4:
        raise SystemExit(f"::error::--bbox chce W,S,E,N: „{args.bbox}“")

    paths = list(args.tiles)
    if args.dir and os.path.isdir(args.dir):
        paths += [os.path.join(args.dir, f) for f in sorted(os.listdir(args.dir))
                  if f.lower().endswith(".tif")]
    if not paths:
        print("::error::coverage.py nedostal ani jednu dlaždicu.")
        return 2

    good, liars, empty = [], [], []
    for p in sorted(set(paths)):
        name = os.path.basename(p)
        info = tile_info(p)
        ext = extent_of(info) if info else None
        deg = degree_of(name)
        stamp = empty_stamp(info) if info else None
        if stamp is not None and stamp != tiles.EMPTY_CHECK:
            # Prázdna dlaždica od kontroly, ktorej už neveríme. Rozsah má
            # v poriadku, takže inak by prešla – a stupeň, ktorý v skutočnosti
            # terén má, by sa už nikdy neprečítal (beh 31526268289).
            liars.append(name)
            print(f"  ✗ {name} je prázdna dlaždica z kontroly "
                  f"„{stamp or 'v1 (nepodpísaná)'}“, dnes platí "
                  f"„{tiles.EMPTY_CHECK}“ – ten stupeň sa musí prečítať znova")
            continue
        if stamp is not None:
            empty.append(name)
        if ext is None:
            # Rozsah sa nedá zistiť (gdalinfo zlyhal, chýba projekcia). Beriem
            # ju ako platnú – a to znamená aj započítať ju do pokrytia podľa
            # MENA, nie ju len preskočiť: inak by z „nedá sa overiť" vyšlo
            # „chýba" a beh by padol na dlaždici, ktorá je možno v poriadku.
            print(f"  ? {name} – rozsah sa nedá zistiť, beriem ju ako platnú")
            if deg is not None:
                good.append((float(deg[0]), float(deg[1]),
                             float(deg[0] + 1), float(deg[1] + 1)))
            continue
        if deg is None:
            good.append(ext)
            continue
        pct = covers_own_degree(ext, deg)
        if pct < HONEST_PCT:
            liars.append(name)
            print(f"  ✗ {name} pokrýva len {pct:.2f} % svojho stupňa "
                  f"({ext[0]:.4f},{ext[1]:.4f} … {ext[2]:.4f},{ext[3]:.4f}) – "
                  f"meno tvrdí celý stupeň")
        else:
            good.append((float(deg[0]), float(deg[1]),
                         float(deg[0] + 1), float(deg[1] + 1)))

    # ---------- majú dlaždice aj DÁTA, nie len rozsah? ----------
    # Toto je tá kontrola, ktorá v behu 31635772047 chýbala. Rozsahy sedeli,
    # pokrytie vyšlo 100 % – a pol kraja bolo bez terénu, lebo jedna dlaždica
    # v jeho STREDE bola takmer prázdna. Nie je to chyba behu (dlaždica môže
    # byť prázdna právom – stupeň, ktorý je celý za hranicou Slovenska), preto
    # varovanie a nie pád; hlavné je, že to už nie je ticho.
    chudobne = []
    if args.data_pct > 0:
        print(f"  dáta v dlaždiciach (podiel skutočných výšok, prah "
              f"{args.data_pct:g} %):")
        for p_ in sorted(set(paths)):
            name = os.path.basename(p_)
            if name in liars:
                continue
            d = data_pct(p_)
            mb = os.path.getsize(p_) / 1e6 if os.path.exists(p_) else 0
            if d is None:
                print(f"    ? {name} – podiel dát sa nedá zistiť ({mb:.0f} MB)")
                continue
            znak = "✓" if d >= args.data_pct else "✗"
            print(f"    {znak} {name}: {d:.1f} % výšok, {mb:.0f} MB")
            if d < args.data_pct and name not in empty:
                chudobne.append((name, d, mb))
        if chudobne:
            zoznam = ", ".join(f"{n} ({d:.1f} % výšok, {mb:.0f} MB)"
                               for n, d, mb in chudobne)
            print(f"::warning::Dlaždice s takmer žiadnymi výškami: {zoznam}. "
                  f"Rozsah majú v poriadku, takže pokrytie vyšlo v poriadku – "
                  f"ale v mape z nich bude PRÁZDNE MIESTO s rovnou hranou na "
                  f"celom stupni a skalám z hrany dát vyjdú falošné steny. "
                  f"Keď ten stupeň má byť plný, zmaž ho zo skladu a nechaj "
                  f"doplniť znova (`store.py --rm`); keď je celý za hranicou "
                  f"Slovenska, je to v poriadku.")

    # Ktoré stupne bboxu neprikrýva ani jedna poctivá dlaždica.
    missing = []
    for lat in range(math.floor(bbox[1]), math.floor(bbox[3]) + 1):
        for lon in range(math.floor(bbox[0]), math.floor(bbox[2]) + 1):
            if not any(t[0] <= lon + 0.5 <= t[2] and t[1] <= lat + 0.5 <= t[3]
                       for t in good):
                ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
                missing.append(f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}")

    pct = covered_pct(bbox, good)
    lines = [f"covered_pct={pct:.1f}",
             "liars=" + " ".join(liars),
             "empty=" + " ".join(empty),
             "missing=" + " ".join(missing)]
    print(f"Pokrytie územia {args.bbox}: {pct:.1f} % z {len(good)} dlaždíc"
          + (f", {len(liars)} nepoctivých" if liars else ""))
    if missing:
        print(f"  bez dlaždice: {' '.join(missing)}")
    if empty:
        # Prázdna dlaždica sa do pokrytia počíta (stupeň JE prečítaný), takže
        # inak by o nej v logu nebolo ani slovo – a „prečo tam nie sú
        # vrstevnice" by sa hľadalo v mape, nie v behu. Preto sa vypisuje vždy.
        print(f"  prečítané a bez terénu: {' '.join(empty)}")
    text = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    sys.stdout.write(text)
    return 1 if pct < args.min_pct else 0


if __name__ == "__main__":
    sys.exit(main())

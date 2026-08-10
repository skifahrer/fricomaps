#!/usr/bin/env python3
"""
`gdal_contour -p` po blokoch – aby to dobehlo aj nad veľkým územím.

PREČO. Skladanie prstencov v režime plôch NIE JE lineárne v počte buniek: čím
viac rozpracovaných prstencov GDAL drží, tým drahšie je pridať ďalší segment.
Nad zrnitým sklonom to znamená, že beh začne rýchlo a potom sa zadrháva.
Namerané v behu 31418794845 (689 km², sklad 1 m), tempo po krokoch po 2,5 %:
2 %/min do 17,5 %, potom 1,07, 0,36 a 0,26 %/min – každý ďalší krok ~1,4×
dlhší než predošlý. Žiadny beh skál nad celým výrezom takto nikdy nedobehol.

Blok je malý raster: prstence sa v ňom poskladajú rýchlo, pamäť je zhora
ohraničená a hlavne – ČO JE HOTOVÉ, JE NA DISKU. Zrušený beh teda nezahodí
prácu a ďalší dopočíta len zvyšok (`.part` + premenovanie, ako sklad sklonu).

ZA ČO SA TO PLATÍ A AKO SA TO VRACIA. Plocha cez hranicu bloku vypadne ako dva
polygóny a diera preseknutá hranicou ako zárez v okraji oboch polovíc.
`zlep_svy()` ich spojí späť: `ST_Union` nad tými útvarmi, ktoré sa hranice
NAOZAJ dotýkajú (`sev=1`), po triedach. Keď sa spoja obe polovice, zárezy do
seba zapadnú a diera sa zase uzavrie. Únia beží nad zlomkom plôch, takže to
nie je tá istá drahá fáza, ktorej sme sa blokmi zbavovali.

Bez spatialite sa švy zlepiť nedajú – vtedy beh POKRAČUJE s rozseknutými
plochami a povie to. Rozseknutá skala je horšia mapa, nie žiadna mapa.

Používa to `contours-rocks/rock-areas.py` (skaly zo sklonu). Tú istú vec robí
zatiaľ vlastným kódom aj `rocks-shading/vector.py` (skaly z tieňovania) – ten
je starší a mal by sa sem presťahovať, nech to nie sú dve implementácie
jedného (pravidlo 1).
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from watch import hms, run_watched  # noqa: E402


def raster_size(vrt):
    """(šírka, výška) rastra v pixeloch."""
    try:
        info = json.loads(subprocess.run(["gdalinfo", "-json", vrt],
                                         check=True, capture_output=True,
                                         text=True).stdout)
        return info["size"][0], info["size"][1]
    except (subprocess.CalledProcessError, KeyError, ValueError):
        return 0, 0


def plan(w_px, h_px, blok_px):
    """Ľavé horné rohy blokov. Blok je štvorec `blok_px`, posledný je menší."""
    return [(bx, by)
            for by in range(0, h_px, blok_px)
            for bx in range(0, w_px, blok_px)]


def oznac_svy(src, dst, na_hranici):
    """Prepíše GeoJSONSeq a útvarom na hranici bloku pridá `"sev":1`.

    Rozhoduje sa podľa toho, či sa súradnice útvaru dotýkajú okraja bloku –
    `na_hranici(geometry)` vráti True/False. Len tie idú potom do únie.
    """
    n = 0
    with open(src) as fi, open(dst, "w") as fo:
        for line in fi:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if na_hranici(obj.get("geometry") or {}):
                obj.setdefault("properties", {})["sev"] = 1
                n += 1
            fo.write(json.dumps(obj, separators=(",", ":")) + "\n")
    return n


def _dotyka_sa(geom, x0, y0, x1, y1, tol):
    """Siaha geometria na okraj okna (v súradniciach rastra)?"""
    def body(g):
        t = g.get("type")
        c = g.get("coordinates")
        if t == "Polygon":
            for ring in c or []:
                yield from ring
        elif t == "MultiPolygon":
            for poly in c or []:
                for ring in poly:
                    yield from ring
    for x, y in body(geom):
        if (abs(x - x0) <= tol or abs(x - x1) <= tol
                or abs(y - y0) <= tol or abs(y - y1) <= tol):
            return True
    return False


def po_blokoch(vrt, out_dir, urovne, atributy, blok_px, geo, *,
               heartbeat=30, max_rss_mb=0, label="gdal_contour"):
    """Obrysy po blokoch do `out_dir/b*.geojsonl`. Vráti (priečinok, počet).

    `urovne`   – zoznam prahov pre `-fl`
    `atributy` – napr. `["-amin", "smin", "-amax", "smax"]`
    `geo`      – (ox, oy, res): ľavý horný roh a veľkosť bunky v metroch,
                 aby sa okno dalo prepočítať na súradnice
    """
    w_px, h_px = raster_size(vrt)
    if not w_px:
        raise RuntimeError(f"z {vrt} sa nedá prečítať rozmer rastra")
    ox, oy, res = geo
    bloky = plan(w_px, h_px, blok_px)
    os.makedirs(out_dir, exist_ok=True)
    hotovych = sum(1 for i in range(len(bloky))
                   if os.path.exists(os.path.join(out_dir, f"b{i:05d}.geojsonl")))
    print(f"  blok {blok_px}×{blok_px} px, {len(bloky)} blokov"
          + (f", {hotovych} už hotových z predošlého behu" if hotovych else ""),
          flush=True)

    t0 = time.time()
    spravene = 0
    for i, (bx, by) in enumerate(bloky):
        cesta = os.path.join(out_dir, f"b{i:05d}.geojsonl")
        if os.path.exists(cesta):
            continue
        bw, bh = min(blok_px, w_px - bx), min(blok_px, h_px - by)
        okno = os.path.join(out_dir, "okno.vrt")
        # `-of VRT` je len XML nad tým istým rastrom – výrez bloku nestojí
        # ani jeden prepísaný bajt dát.
        subprocess.run(["gdal_translate", "-q", "-of", "VRT",
                        "-srcwin", str(bx), str(by), str(bw), str(bh),
                        vrt, okno], check=True)
        part = cesta + ".part"
        if os.path.exists(part):
            os.remove(part)
        subprocess.run(["gdal_contour", "-p", "-q", "-fl", *urovne, *atributy,
                        "-f", "GeoJSONSeq", "-nln", "band", okno, part],
                       check=True)
        # Súradnice sú v metroch výrezu; hranica bloku je jeho okraj.
        x0, y0 = ox + bx * res, oy - by * res
        x1, y1 = x0 + bw * res, y0 - bh * res
        oznac_svy(part, cesta, lambda g: _dotyka_sa(g, x0, y1, x1, y0, res))
        os.remove(part)
        spravene += 1
        el = time.time() - t0
        # Postup po blokoch je jediné, čo o dlhej fáze niečo povie – a na
        # rozdiel od percent `gdal_contour` sa nezasekne, lebo blok buď je,
        # alebo nie je hotový.
        if spravene and (i % max(1, len(bloky) // 50) == 0 or i == len(bloky) - 1):
            zvysok = el / spravene * (len(bloky) - i - 1)
            print(f"  … obrysy: blok {i + 1}/{len(bloky)}, beží {hms(el)}, "
                  f"zostáva ~{hms(zvysok)}", flush=True)
    return out_dir, len(bloky)


def zlep_svy(seq, tmp, *, klucovy_atribut="smin", heartbeat=30, label="švy"):
    """Spojí plochy rozseknuté hranicou bloku. Vráti cestu k výsledku.

    Unionuje LEN útvary s `sev=1`, po triedach – inak by sa stena zlepila so
    svahom. Keď spatialite chýba alebo príkaz zlyhá, beh pokračuje
    s rozseknutými plochami a povie to nahlas.
    """
    svy = os.path.join(tmp, "svy.geojsonl")
    zvysok = os.path.join(tmp, "bez-svov.geojsonl")
    n_sev = n_ok = 0
    with open(seq) as fi, open(svy, "w") as fs, open(zvysok, "w") as fz:
        for line in fi:
            if not line.strip():
                continue
            if '"sev":1' in line.replace(" ", ""):
                fs.write(line)
                n_sev += 1
            else:
                fz.write(line)
                n_ok += 1
    if not n_sev:
        print("  švy: žiadna plocha nesiaha na hranicu bloku", flush=True)
        return zvysok

    print(f"  švy: {n_sev} plôch na hranici bloku, {n_ok} mimo – "
          f"zlepujem tie prvé", flush=True)
    zlep = os.path.join(tmp, "zlepene.geojsonl")
    try:
        run_watched(["ogr2ogr", "-f", "GeoJSONSeq", zlep, svy,
                     "-dialect", "SQLITE", "-explodecollections",
                     "-sql", f"SELECT {klucovy_atribut}, "
                             f"ST_Union(geometry) AS geometry "
                             f"FROM svy GROUP BY {klucovy_atribut}"],
                    label, tmp=zlep, every=heartbeat)
    except Exception as exc:
        print(f"::warning::Švy sa nepodarilo zlepiť ({type(exc).__name__}) – "
              f"plochy cez hranicu bloku ostanú rozseknuté a diery na tej "
              f"hranici otvorené. Chýba spatialite? Mapa bude, len s rovnými "
              f"rezmi v skalách.", flush=True)
        return seq

    spolu = os.path.join(tmp, "zlepene-spolu.geojsonl")
    with open(spolu, "w") as fo:
        for src in (zvysok, zlep):
            if os.path.exists(src):
                with open(src) as fi:
                    for line in fi:
                        if line.strip():
                            fo.write(line)
    return spolu


def zlej(out_dir, dst):
    """Zlepí bloky do jedného GeoJSONSeq (v poradí, nech je beh opakovateľný)."""
    n = 0
    with open(dst, "w") as fo:
        for meno in sorted(os.listdir(out_dir)):
            if not meno.endswith(".geojsonl"):
                continue
            with open(os.path.join(out_dir, meno)) as fi:
                for line in fi:
                    if line.strip():
                        fo.write(line)
                        n += 1
    return n

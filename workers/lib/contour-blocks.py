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
import re
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


def _suradnice(geom):
    """Body geometrie – bez ohľadu na to, či je to Polygon alebo MultiPolygon."""
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "Polygon":
        for ring in c or []:
            yield from ring
    elif t == "MultiPolygon":
        for poly in c or []:
            for ring in poly:
                yield from ring


def _plocha(geom):
    """Plocha geometrie v m² (shoelace nad metrickými súradnicami).

    Bez GDAL a bez závislostí – potrebuje sa len na porovnanie „koľko plochy
    išlo do únie a koľko z nej vyšlo". Diery sa odčítajú, takže to zhruba
    sedí aj na plochy s vnútornými prstencami.
    """
    def ring(body):
        s = 0.0
        for i in range(len(body) - 1):
            x0, y0 = body[i][0], body[i][1]
            x1, y1 = body[i + 1][0], body[i + 1][1]
            s += x0 * y1 - x1 * y0
        return abs(s) / 2.0

    t, c = geom.get("type"), geom.get("coordinates")
    if t == "Polygon":
        prst = c or []
        return ring(prst[0]) - sum(ring(r) for r in prst[1:]) if prst else 0.0
    if t == "MultiPolygon":
        spolu = 0.0
        for poly in c or []:
            if poly:
                spolu += ring(poly[0]) - sum(ring(r) for r in poly[1:])
        return spolu
    return 0.0


def plocha_suboru(path):
    """Súčet plôch všetkých útvarov v GeoJSONSeq (m²)."""
    spolu = 0.0
    try:
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    spolu += _plocha(json.loads(line).get("geometry") or {})
                except ValueError:
                    continue
    except FileNotFoundError:
        return 0.0
    return spolu


def _dotyka_sa(geom, x0, y0, x1, y1, tol):
    """Siaha geometria na okraj okna (v súradniciach rastra)?"""
    for x, y in _suradnice(geom):
        if (abs(x - x0) <= tol or abs(x - x1) <= tol
                or abs(y - y0) <= tol or abs(y - y1) <= tol):
            return True
    return False


def skontroluj_metricke(seq, minimum=1000.0, vzoriek=200):
    """Sú súradnice v metroch, alebo sa niekde stratili do stupňov?

    Rozdiel nevidno na ničom inom: beh dobehne, výstup existuje a je zelený –
    len každá plocha má rádovo 1e-9 m² a filter najmenšej plochy ju vyhodí.
    Preto sa to kontroluje rovno pri zdroji a je to CHYBA, nie varovanie.

    Rozhoduje NAJVÄČŠIA súradnica zo vzorky, nie prvá: jeden bod môže byť
    blízko nuly aj v metroch. V EPSG:3035 má Slovensko rádovo 4,8e6 / 3,0e6,
    v stupňoch je to do 180 – tie dva svety sa nemajú ako pomýliť.
    """
    najvacsia = 0.0
    videl = False
    try:
        with open(seq) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                for x, y in _suradnice(obj.get("geometry") or {}):
                    videl = True
                    najvacsia = max(najvacsia, abs(x), abs(y))
                    vzoriek -= 1
                    if vzoriek <= 0:
                        break
                if vzoriek <= 0:
                    break
    except FileNotFoundError:
        return True
    if videl and najvacsia < minimum:
        raise ValueError(
            f"súradnice vyzerajú ako stupne (najväčšia {najvacsia:.6f}), nie "
            f"ako metre – z okna bloku sa nevyhodil `<SRS>` a GDAL ich "
            f"prepočítal do WGS84. Plocha by potom vyšla rádovo 1e-9 m² "
            f"a filter najmenšej plochy by vyhodil VŠETKY skaly, pričom beh "
            f"by ostal zelený (behy 31245134321 a 31426542010).")
    return True


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
        # A TERAZ TO DÔLEŽITÉ: z okna sa vyhodí <SRS>.
        #
        # Ovládač GeoJSON prepočítava do WGS84 vždy, keď zdroj vie, v čom je.
        # `gdal_contour` nad rastrom s EPSG:3035 by teda vypísal STUPNE – a kto
        # z toho počíta plochu ako z metrov, tomu vyjde každá skala rádovo
        # 1e-9 m² a spadne pod `min_area`. Von potom ide NULA plôch a beh je
        # pritom zelený. Stalo sa to dvakrát: v tieňovacej ceste (beh
        # 31245134321, 976 725 plôch → 0 ponechaných) a znova tu, keď sa
        # bloky písali pre sklon a tento riadok sa nepreniesol (beh
        # 31426542010). Bez SRS nemá GDAL čo prepočítať a súradnice ostanú
        # metrické.
        with open(okno) as f:
            xml = f.read()
        with open(okno, "w") as f:
            f.write(re.sub(r"\s*<SRS[^>]*>.*?</SRS>", "", xml, flags=re.S))
        part = cesta + ".part"
        if os.path.exists(part):
            os.remove(part)
        subprocess.run(["gdal_contour", "-p", "-q", "-fl", *urovne, *atributy,
                        "-f", "GeoJSONSeq", "-nln", "band",
                        # Súradnice sú metrické, dve desatiny = centimeter.
                        "-lco", "COORDINATE_PRECISION=2", okno, part],
                       check=True)
        # STRÁŽCA: prvý blok sa pozrie, či sú súradnice naozaj metrické.
        # Keď sa sem raz vráti prepočet do stupňov, nespadne nič – len
        # z filtra plochy vypadne všetko a mapa bude ticho bez skál.
        if spravene == 0:
            skontroluj_metricke(part)
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


def zlep_svy(seq, tmp, *, klucovy_atribut="smin", srs=None, heartbeat=30,
             label="švy"):
    """Spojí plochy rozseknuté hranicou bloku. Vráti cestu k výsledku.

    Unionuje LEN útvary s `sev=1`, po triedach – inak by sa stena zlepila so
    svahom.

    ÚNIA SA MÔŽE NEPODARIŤ A NEPOVIE TO NÁVRATOVÝM KÓDOM. `ST_Union` nad
    obrysmi z `gdal_contour` padá na neplatných geometriách („TopologyException:
    unable to assign free hole to a shell") – ogr2ogr pritom skončí ÚSPECHOM
    a napíše prázdny súbor. Kým sa výsledok nekontroloval, zmizli s ním všetky
    plochy, ktoré sa dotýkali hranice bloku: v behu 31434520563 to bolo 22
    z 24 útvarov a z celých Vysokých Tatier ostalo 44 plôch so súhrnnou
    plochou 0,00 km². Beh bol zelený a mapa bez skál.

    Preto sa tu robia tri veci navyše:
      * `ST_MakeValid` pred úniou – tá topologická chyba je práve o tom,
      * výsledok sa PREPOČÍTA a keď je prázdny, únia sa zahodí,
      * pri zahodení sa vracajú PÔVODNÉ útvary. Rozseknutá skala je horšia
        mapa; žiadna skala je rozbitá mapa.
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
    # `-a_srs`: súradnice sú metrické, ale GeoJSONSeq o tom nevie (z okna bloku
    # sa `<SRS>` zámerne vyhodil). Bez tohto to GEOS počíta ako stupne a hlási
    # `No SRS set on layer`.
    srs_args = ["-a_srs", srs] if srs else []
    chyba = None
    try:
        run_watched(["ogr2ogr", "-f", "GeoJSONSeq", zlep, svy, *srs_args,
                     "-dialect", "SQLITE", "-explodecollections",
                     "-sql", f"SELECT {klucovy_atribut}, "
                             f"ST_Union(ST_MakeValid(geometry)) AS geometry "
                             f"FROM svy GROUP BY {klucovy_atribut}"],
                    label, tmp=zlep, every=heartbeat)
    except Exception as exc:
        chyba = f"{type(exc).__name__}"

    # ÚSPECH OGR2OGR NESTAČÍ – a nerozhoduje ani POČET útvarov: zlepiť 22
    # kúskov do jedného je práve zmysel únie. Rozhoduje PLOCHA. Únia smie
    # plochu mierne zmenšiť (prekryvy sa spoja), ale nikdy nie zmiesť.
    n_zlep = 0
    if not chyba and os.path.exists(zlep):
        with open(zlep) as f:
            n_zlep = sum(1 for line in f if line.strip())
    plocha_pred = plocha_suboru(svy)
    plocha_po = plocha_suboru(zlep) if n_zlep else 0.0
    stratene = (plocha_pred > 0 and plocha_po < plocha_pred * 0.5)
    if chyba or not n_zlep or stratene:
        preco = (f"({chyba})" if chyba else
                 "(únia skončila prázdna – hľadaj v logu `TopologyException`)"
                 if not n_zlep else
                 f"(z {plocha_pred/1e6:.2f} km² ostalo {plocha_po/1e6:.2f} km²)")
        print(f"::warning::Zlepenie švov stratilo plochu {preco}"
              + f". Vraciam {n_sev} pôvodných plôch nezlepených: budú rozseknuté "
              f"na hraniciach blokov a diery na nich otvorené, ale BUDÚ. "
              f"Chýba spatialite?", flush=True)
        return seq

    print(f"  švy: {n_sev} plôch zlepených na {n_zlep} "
          f"({plocha_pred/1e6:.2f} → {plocha_po/1e6:.2f} km²)", flush=True)
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

#!/usr/bin/env python3
"""
„V tomto stupni terén nie je" sa nesmie povedať od oka ani bez podpisu.

PREČO TO TU JE. Prázdna dlaždica je doživotná odpoveď: kým leží v sklade,
`workers/dem/check.sh` vidí jej meno, usúdi, že model ten stupeň má, a nikto
ho už neprečíta. Keď tú odpoveď vysloví vzorkovaná štatistika, je to tichý
omyl v najhoršej podobe – beh je zelený a v mape chýba pol kraja.

Presne to sa stalo v behu 31526268289: `gdalinfo -approx_stats` číta len každý
n-tý blok, v štvorcovej dlaždici teda uhlopriečku, a v `N48E016.tif` (Devín,
Záhorie – Slovensko zaberá 7,8 % stupňa pri jeho východnom okraji) netrafilo
ani jeden platný pixel. Dlaždica s 25 miliónmi platných buniek sa zahodila,
do skladu išla prázdna a vrstevnice, skaly aj tieňovanie Bratislavského kraja
skončili rovnou líniou na 17. poludníku.

ČO SA KONTROLUJE:

  1. `dem/tiles.py` pozná `EMPTY_PX`, `EMPTY_TAG` aj `EMPTY_CHECK` – teda vie,
     ako prázdna dlaždica vyzerá a ktorá kontrola ju smela vyhlásiť.
  2. prázdna dlaždica sa naozaj PODPÍŠE (`-mo EMPTY_CHECK=…`). Bez podpisu sa
     nedá odlíšiť poctivá odpoveď od omylu starej kontroly.
  3. o zahodení hotovej dlaždice nerozhoduje vzorkovanie: `elevation_range(dst`
     sa v `tiles.py` nevolá, ide sa cez `has_elevations(`, ktorá „nič tu nie je"
     overí presným priechodom.
  4. `dem/coverage.py` si prázdnu dlaždicu nedefinuje druhýkrát – obe
     konštanty berie z `dem/tiles.py` (pravidlo 1: jedna otázka, jedno miesto).
  5. KONTROLA SA PÝTA TO ISTÉ, ČO STIAHNUTIE. `dem/check.sh` sa dlho pýtal len
     „je to meno v sklade?", kým `coverage.py` meria, čo v tých súboroch je –
     a v behu 31781263921 sa to rozišlo: `dem-dmr5` mal `N48E016.tif` s nulovou
     veľkosťou (prázdna dlaždica ešte od kontroly „v1"), kontrola povedala
     „2 z 2 → doplniť: false" a ostrý build Bratislavského kraja spadol
     o minútu neskôr na pokrytí 75,8 %. Preto sa kontroluje, že `check.sh`
     púšťa `dem/trust.py` a že `trust.py` posudzuje podpis TOU ISTOU funkciou
     (`coverage.empty_stamp`), a nie vlastným nápadom.

Spustenie (aj lokálne, bez GDALu – je to statická kontrola):
    python3 workers/lint/dem-empty.py
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# Priečinok = job, súbor = krok; susedné joby ležia o úroveň vyššie.
_WORKERS = os.path.dirname(_HERE)
TILES = os.path.join(_WORKERS, "dem", "tiles.py")
COVERAGE = os.path.join(_WORKERS, "dem", "coverage.py")
CHECK = os.path.join(_WORKERS, "dem", "check.sh")
TRUST = os.path.join(_WORKERS, "dem", "trust.py")


def main():
    bad = []
    tiles = open(TILES, encoding="utf-8").read()
    coverage = open(COVERAGE, encoding="utf-8").read()

    for const in ("EMPTY_PX", "EMPTY_TAG", "EMPTY_CHECK"):
        if not re.search(rf"^{const}\s*=", tiles, re.M):
            bad.append(f"workers/dem/tiles.py nemá konštantu {const} – prázdna "
                       f"dlaždica sa potom nedá ani podpísať, ani spoznať.")

    if 'f"{EMPTY_TAG}={EMPTY_CHECK}"' not in tiles:
        bad.append("workers/dem/tiles.py nepodpisuje prázdnu dlaždicu "
                   "(`gdal_translate -mo EMPTY_CHECK=…`). Bez podpisu sa "
                   "odpoveď starej kontroly tvári ako dnešná a stupeň "
                   "s terénom ostane v sklade navždy prázdny.")

    # Zakázaná je VZORKOVANÁ podoba nad hotovou dlaždicou, teda volanie bez
    # `exact=True`. `elevation_range(dst, exact=True)` je v poriadku – tak si
    # `empty_tile()` overuje, že prázdna dlaždica naozaj vyšla prázdna.
    if re.search(r"elevation_range\(\s*dst\s*\)", tiles):
        bad.append("workers/dem/tiles.py rozhoduje o dlaždici priamo cez "
                   "`elevation_range(dst)`. Vzorkovaná štatistika smie "
                   "povedať len „výšky sú“; jej „nie sú“ znamená zahodiť "
                   "hotovú dlaždicu, a to musí prejsť cez `has_elevations()` "
                   "(presný priechod, beh 31526268289).")

    if "def has_elevations(" not in tiles:
        bad.append("workers/dem/tiles.py nemá `has_elevations()` – práve ona "
                   "overuje „prázdny stupeň“ presným priechodom.")

    for const in ("EMPTY_PX", "EMPTY_TAG", "EMPTY_CHECK"):
        if re.search(rf"^{const}\s*=", coverage, re.M):
            bad.append(f"workers/dem/coverage.py si definuje vlastné {const}. "
                       f"Ako vyzerá prázdna dlaždica vie ten, kto ju píše "
                       f"(workers/dem/tiles.py) – dve predstavy o tom istom sa "
                       f"raz rozídu.")
    if "tiles.EMPTY_TAG" not in coverage or "tiles.EMPTY_PX" not in coverage:
        bad.append("workers/dem/coverage.py neberie podpis prázdnej dlaždice "
                   "z workers/dem/tiles.py – nepoctivú prázdnu dlaždicu potom "
                   "zo skladu nevyhodí a stupeň sa už nikdy neprečíta.")

    # ---- kontrola sa musí pýtať to isté, čo stiahnutie ----
    try:
        check = open(CHECK, encoding="utf-8").read()
        trust = open(TRUST, encoding="utf-8").read()
    except OSError as exc:
        bad.append(f"{exc} – bez `dem/trust.py` sa kontrola skladu vráti "
                   f"k „meno v sklade stačí“ (beh 31781263921).")
        check = trust = ""

    if check and "trust.py" not in check:
        bad.append("workers/dem/check.sh nepúšťa workers/dem/trust.py – "
                   "kontrola by opäť verila menu súboru a prázdna dlaždica "
                   "od starej kontroly by prešla ako hotový model.")
    if trust and "empty_stamp" not in trust:
        bad.append("workers/dem/trust.py neposudzuje podpis cez "
                   "`coverage.empty_stamp` – tretia predstava o tom, čo je "
                   "prázdna dlaždica, sa raz rozíde s tými dvoma.")
    if trust and "EMPTY_MAX_BYTES" not in trust:
        bad.append("workers/dem/trust.py nemá prah `tiles.EMPTY_MAX_BYTES` – "
                   "bez neho by otváral aj skutočné dlaždice (stovky MB) "
                   "a kontrola skladu by sťahovala celý sklad.")

    for m in bad:
        print(f"::error::{m}")
    print(f"Prázdna dlaždica: {'chyby ' + str(len(bad)) if bad else 'v poriadku ✓'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

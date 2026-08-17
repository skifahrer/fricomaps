#!/usr/bin/env python3
"""
Koľko metrov je jedna bunka – a čo z toho plynie.

Bunka výškového modelu a pixel dlaždice sú dve čísla a porovnanie tých dvoch
rozhoduje o troch veciach naraz: **ktorý maxzoom** má zmysel počítať, **ktorým
resamplingom** sa na daný zoom ide a **aký zvislý krok** znesie kódovanie
výšky. Sú to tri otázky, ale odpoveď na všetky stojí na tom istom prevode
„stupne → metre" – a keby sa dve z nich rozišli, tieňovanie by sa počítalo
na inej mriežke, než akú mu vybral plán. Preto sú tu spolu (pravidlo 1
v CLAUDE.md). Pýtajú sa ich:

  `plan/options.py`             ktorý maxzoom vyjde na tento model
                                (`terrain_maxzoom: auto`)
  `contours-rocks/rock-plan.py` aká je NAOZAJ mriežka toho rastra
  `terrain/tiles.py`            zväčšuje sa DEM alebo zmenšuje, a koľko
                                zlomkových bitov výšky treba

BEZ NUMPY, a je to zámer. Tieto funkcie sú čistá aritmetika a `lint/terrain.py`
ich musí vedieť spustiť – lintovací job má len `checkout` a holý `python3`,
žiadne `pip install`. Keby tu numpy bolo, kontrola by sa buď musela ticho
preskakovať (zelená, ktorá sa nepozrela na nič), alebo by lint pri každom pushi
visel na sieti. Práca nad poliami (kódovanie do RGB, test roviny) preto ostáva
vo `terrain/tiles.py`.

Použitie ako modul:
    sys.path.insert(0, os.path.join(_WORKERS, "lib"))
    from cell import tile_m_per_px, terrain_zoom_for, dem_cell_metres
"""
import json
import math
import subprocess

# Stred Slovenska. Mriežka Web Mercatora je v metroch na rovníku, takže sa
# každý rozmer musí prepočítať na našu šírku – a keď sa nevie, ktorá to je,
# platí táto. Rozdiel medzi 47,7° a 49,6° je na tomto asi 4 %, čo o zoome
# ani o resamplingu nerozhoduje.
DEFAULT_LAT = 49.0

# Zoom 0 má na rovníku 156 543,03 m na pixel (256 px na 40 075 km).
EQUATOR_M_PER_PX = 156543.03


def tile_m_per_px(z, lat=DEFAULT_LAT):
    """Koľko metrov v teréne je jeden pixel dlaždice na danom zoome.

    Číslo je tu raz, nech sa výber zoomu, voľba resamplingu a to, čo o nich
    hovorí log, nemôžu rozísť.
    """
    return EQUATOR_M_PER_PX * math.cos(math.radians(lat)) / (2 ** z)


def terrain_zoom_for(cell_m, lo=8, hi=16):
    """Najnižší zoom, na ktorom je pixel dlaždice jemnejší než bunka modelu.

    Vyššie už dlaždice nesú detail, ktorý v modeli nie je – len štvornásobok
    súborov na každý ďalší zoom. Sonny (20 m) → z13, DMR 3.5 (10 m) → z14,
    DMR 5.0 (5 m) → z15.
    """
    for z in range(lo, hi + 1):
        if tile_m_per_px(z) <= cell_m:
            return z
    return hi


# Sklon, ktorý sa v tieňovaní už nedá odlíšiť od roviny. Pri svetle pod 45°
# mení sklon σ jas asi o 0,7·σ, takže 2 % sú ~3,6 z 255 odtieňov – a v štýle
# to ide ešte cez `hillshade-exaggeration` 0,25–0,4, čiže pod jeden odtieň.
#
# JEDNO ČÍSLO, DVE POUŽITIA, a obe hovoria to isté („pod týmto nie je čo
# tieňovať"): vyberá zvislý krok kódovania (`frac_bits`) a rozhoduje, ktorá
# dlaždica je rovina a nemusí vzniknúť (`je_rovina` v `terrain/tiles.py`).
# Keby to boli dve čísla, raz by sa rozišli a jedno by tvrdilo, že tam nič
# nie je, kým druhé by tam platilo bity za presnosť.
SLOPE_EPS = 0.02

# Jemnejšie než 1/64 m nemá čo pridať: taký krok je pod šumom každého modelu,
# ktorý sem chodí, a v PNG je to už len nestlačiteľný bajt navyše.
MAX_FRAC_BITS = 6


def frac_bits(px_m):
    """Koľko zlomkových bitov výšky (bajt B) treba pri pixeli `px_m` metrov.

    Krok kódovania je 2^-bits metra a má ostať pod `SLOPE_EPS × pixel` – teda
    tak, aby falošný sklon z kvantizácie nebolo vidieť. Rozpis aj namerané
    čísla sú v hlavičke `workers/terrain/tiles.py`. Nula znamená celé metre,
    teda presne to, čo sa zapisovalo doteraz; na nízkych zoomoch teda dlaždice
    nerastú vôbec.
    """
    want = SLOPE_EPS * px_m
    if not (want > 0) or want >= 1.0:
        return 0
    return min(MAX_FRAC_BITS, int(math.ceil(-math.log2(want))))


def resampling(px_m, cell_m):
    """`average` keď sa DEM zmenšuje, `cubicspline` keď sa zväčšuje.

    Pri zmenšovaní sa výšky musia priemerovať. Pri ZVÄČŠOVANÍ ale `average`
    zdegeneruje na najbližšieho suseda a z každej bunky modelu vypadne
    štvorček rovnakých pixelov – a z jeho hrán spraví hillshade mriežku.

    Bez známej bunky modelu ostáva `average`: je to doterajšie správanie
    a pri zmenšovaní je správne.
    """
    if not cell_m or px_m >= cell_m:
        return "average"
    return "cubicspline"


def dem_cell_metres(dem, lat=DEFAULT_LAT):
    """Rozmer bunky zdrojového DEM v metroch – zmeraný z rastra, nie z mena.

    `data/dem-sources.json` má pri každom modeli `cell_m`, ale to je hodnota
    zo zadania: `dmr5` je 5 m na región a 1 m na výrez, a `sonny1` má mriežku
    nesúmernú (20,3 × 30,9 m). Kto potrebuje vedieť, na čom naozaj počíta,
    má sa spýtať rastra.

    Vracia `(dx, dy)`, alebo `(None, None)`, keď sa raster nedá prečítať –
    volajúci si vtedy musí vybrať bezpečnú vetvu sám.
    """
    try:
        out = subprocess.run(["gdalinfo", "-json", dem], check=True,
                             capture_output=True, text=True).stdout
        info = json.loads(out)
        gt = info["geoTransform"]
        wkt = info.get("coordinateSystem", {}).get("wkt", "")
        dx, dy = abs(gt[1]), abs(gt[5])
        if wkt.startswith("GEOGCRS") or wkt.startswith("GEOGCS"):
            return dx * 111320 * math.cos(math.radians(lat)), dy * 110540
        return dx, dy
    except Exception:
        return None, None

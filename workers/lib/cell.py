#!/usr/bin/env python3
"""
Koľko metrov je jedna bunka: pixel dlaždice a bunka výškového modelu.

JEDNA OTÁZKA, JEDNO MIESTO (pravidlo 1 v CLAUDE.md). Ten istý prevod
„stupne → metre" si počítali dve miesta a každé kvôli inej otázke:

  `plan/options.py`             ktorý maxzoom vyjde na tento model
                                (`terrain_maxzoom: auto`)
  `contours-rocks/rock-plan.py` aká je NAOZAJ mriežka toho rastra

a tretie sa naň chystalo: `terrain/tiles.py` sa musí pri každom zoome
rozhodnúť, či DEM zväčšuje alebo zmenšuje. Sú to tri otázky, ale odpoveď na
všetky tri stojí na tom istom čísle – a keby sa dve z nich rozišli, tieňovanie
by sa počítalo na inej mriežke, než akú mu vybral plán. Presne ten druh tichej
chyby, pri ktorej obe strany vyzerajú samy o sebe správne.

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

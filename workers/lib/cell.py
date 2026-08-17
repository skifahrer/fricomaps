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

# O KOĽKO POD HRANICU VIDITEĽNOSTI. `SLOPE_EPS` je sklon, ktorý sa v JEDNOM
# mieste stratí – lenže kvantizácia ho nerobí v jednom mieste, robí ho
# PRAVIDELNE cez celú dlaždicu. Krok presne na `SLOPE_EPS × pixel` teda
# posadí falošný sklon tesne POD hranicu viditeľnosti a nechá ho tam na
# každom zoome rovnako; oko z toho číta mriežku, lebo je pravidelná (a na
# hranách schodíkov ju druhá derivácia ešte zvýrazní – Machove pásy).
#
# Je to tá istá chyba, ktorú už raz popísala hlavička `terrain/tiles.py`:
# „merala sa VEĽKOSŤ odchýlky, nie jej TVAR". Prvá oprava ju popísala
# správne a potom si krok aj tak vybrala presne na tú hranicu.
#
# Namerané celou cestou cez `terrain/tiles.py` na hladkom umelom teréne
# (mriežka = stredná |Laplacián| tieňovania ×10⁻³). Kvantizácia pridáva na
# KAŽDOM zoome to isté, lebo krok ide s pixelom – preto je „+0" na oboch
# zoomoch rovnaké číslo:
#
#     bity navyše   z12 krok  mriežka  kB/dl.   z13 krok  mriežka  kB/dl.
#     +0 (doteraz)    1/2      10,47    20,3     1/4        9,12    21,2
#     +1              1/4       6,39    26,6     1/8        4,62    30,8
#     +2              1/8       4,68    35,3     1/16       2,33    38,7
#     +3 (dnes)       1/16      4,07    43,7     1/32       1,17    47,4
#
# Každý bit stojí ~30 % veľkosti dlaždice. Na z13 tri bity mriežku zrazia
# 7,8× a je po nej; na z12 sa zastaví na 4,07, lebo tam už nedrží kvantizácia,
# ale samotné prevzorkovanie pri pomere 1,25 (blízko Nyquista) – a to sa
# bitmi nedá kúpiť. Dlaždice sú za to ~2,2× väčšie a to je celá cena, ktorú
# `--budget-mb` môže zaplatiť jedným zoomom navrch.
FRAC_BITS_MARGIN = 3


def frac_bits(px_m):
    """Koľko zlomkových bitov výšky (bajt B) treba pri pixeli `px_m` metrov.

    Krok kódovania je 2^-bits metra a má ostať `FRAC_BITS_MARGIN` bitov POD
    `SLOPE_EPS × pixel` – teda nie tesne na hranici viditeľnosti, ale
    bezpečne pod ňou, lebo falošný sklon z kvantizácie je pravidelný.
    Rozpis aj namerané čísla sú pri `FRAC_BITS_MARGIN` a v hlavičke
    `workers/terrain/tiles.py`. Nula znamená celé metre; na najhrubších
    zoomoch teda dlaždice nerastú vôbec.
    """
    want = SLOPE_EPS * px_m
    if not (want > 0):
        return 0
    # Hranica viditeľnosti posunutá o `FRAC_BITS_MARGIN` bitov nadol. Strop
    # `MAX_FRAC_BITS` platí ďalej – na najvyšších zoomoch preto vyjde margin
    # menší, a je to v poriadku: krok 1/64 m je pri pixeli 3 m falošný sklon
    # 0,5 %, čiže aj tak štvrtina `SLOPE_EPS`.
    target = want / (2 ** FRAC_BITS_MARGIN)
    return max(0, min(MAX_FRAC_BITS, int(math.ceil(-math.log2(target)))))


# Od akého NÁSOBKU bunky sa smie priemerovať. `average` je box filter cez tie
# bunky, ktoré cieľový pixel prekryje – priemerovať teda má čo až vtedy, keď
# ich prekryje aspoň dve. Hneď nad bunkou (pomer 1,0–2,0) mu ich padne raz
# jedna, raz dve, čo je striedavo najbližší sused a priemer dvojice; z toho
# vznikne rytmus plošiniek a hillshade (derivácia) z ich hrán spraví tú istú
# mriežku ako pri zväčšovaní. Je to jedna chyba, len na druhej strane hranice.
#
# Namerané na umelom hladkom teréne (bunka 20 m, mriežka = stredná |Laplacián|
# tieňovania ×10⁻³). Najprv SAMOTNÝ WARP, bez kódovania – tam je rozdiel vidieť
# najčistejšie:
#
#     zoom   pixel    pomer   average   cubicspline
#     z11    50,1 m    2,51     0,65       0,58      ← tu sú si rovné
#     z12    25,1 m    1,25     5,36       0,53      ← 10× a doteraz `average`
#     z13    12,5 m    0,63    34,34       0,04      ← opravené v PR #149
#
# A to isté celou cestou cez `terrain/tiles.py` (z12, krok 1/16 m v oboch):
# `average` 5,45 proti `cubicspline` 4,07. Menej než na samotnom warpe, lebo
# pri pomere 1,25 je zvyšok blízko Nyquista a ten sa prevzorkovaním nedá
# odstrániť – ale je to zadarmo, dlaždica z toho nerastie ani o bajt.
#
# Nad pomerom 2 sa už nelíšia (a `average` je tam lacnejší aj poctivejší –
# je to skutočný priemer, nie vzorka splajnu), pod ním je `average` horší.
AVERAGE_RATIO = 2.0


def resampling(px_m, cell_m):
    """`average` až keď je pixel aspoň `AVERAGE_RATIO`× hrubší než bunka.

    Priemerovať sa oplatí len tam, kde je čo priemerovať. Pri ZVÄČŠOVANÍ
    `average` zdegeneruje na najbližšieho suseda a z každej bunky modelu
    vypadne štvorček rovnakých pixelov – a z jeho hrán spraví hillshade
    mriežku. V PÁSME TESNE NAD BUNKOU je to to isté, len slabšie: pixel
    prekryje raz jednu bunku, raz dve. Preto hranica nie je `px_m >= cell_m`,
    ale dvojnásobok – rozpis a namerané čísla sú pri `AVERAGE_RATIO`.

    Bez známej bunky modelu ostáva `average`: je to doterajšie správanie
    a pri poctivom zmenšovaní je správne.
    """
    if not cell_m or px_m >= AVERAGE_RATIO * cell_m:
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

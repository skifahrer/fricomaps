#!/usr/bin/env python3
"""
Tieňovanie nesmie ticho stratiť presnosť, ktorou stojí a padá.

PREČO TO EXISTUJE. Výškové dlaždice vyzerali roky správne a neboli: výška sa
zaokrúhľovala na celé metre (`B = 0`) a na maxzoome sa DEM zväčšoval
priemerom, ktorý pri zväčšovaní zdegeneruje na najbližšieho suseda. Hillshade
je derivácia výšky, takže z metrových plošiniek a zo štvorčekov po najbližšom
susedovi spravil PRAVIDELNÚ TKANINU cez celú mapu. Nič nespadlo, nič nezčervenalo
– dlaždice vznikli, mapa sa nasadila a bolo to vidieť len okom. Presne to,
čo je v CLAUDE.md pravidlo 8.

Oprava je v troch číslach a každé z nich sa dá „zjednodušiť" späť bez toho,
aby to čokoľvek povedalo. Preto sa strážia:

  1. ZVISLÝ KROK IDE ZA PIXELOM. `frac_bits` musí pre každý zoom vrátiť toľko
     bitov, aby krok kódovania ostal pod `SLOPE_EPS × pixel` – kým sa nenarazí
     na `MAX_FRAC_BITS`. Bez toho je krok na vysokých zoomoch znova metrový.
  2. PRIEMER LEN NADOL. `resampling` smie vrátiť `average` iba vtedy, keď je
     pixel dlaždice hrubší než bunka modelu. Pri zväčšovaní musí ísť
     `cubicspline`.
  3. WARP MUSÍ NIESŤ ZLOMOK. `-ot Int16` vo `warp_level` by zlomok zahodil
     ešte pred kódovaním a body 1 a 2 by boli zbytočné – krok by bol metrový
     bez ohľadu na to, koľko bitov mu potom dáme.

  4. A ŠTVRTÁ, INÁ VEC: podoba kódovania je v mene assetu v sklade
     (`workers/terrain/build.sh`) aj v kľúči cache
     (`workers/plan/cache-keys.sh`). Sú to dve miesta a jedna otázka („sú
     tieto hotové dlaždice ešte tie, ktoré by dnes vznikli?"). Keď sa rozídu,
     build si z jedného z nich vytiahne staré dlaždice a bude zelený.

Spustiť sa dá aj lokálne: `python3 workers/lint/terrain.py`.
"""
import importlib.util
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKERS = os.path.dirname(_HERE)
TILES = os.path.join(_WORKERS, "terrain", "tiles.py")
BUILD = os.path.join(_WORKERS, "terrain", "build.sh")
KEYS = os.path.join(_WORKERS, "plan", "cache-keys.sh")

# Zoomy, na ktorých tieňovanie naozaj beží: `terrain/build.sh` púšťa
# `tiles.py` od z5 a `terrain_maxzoom: auto` dá najviac z16.
ZOOMS = range(5, 17)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    bad = []
    t = load("terrain_tiles", TILES)

    # ---------- 1. zvislý krok ide za pixelom ----------
    # BEZ ÚĽAVY NA `MAX_FRAC_BITS`: ten strop je poistka proti nezmyselne
    # jemnému kroku (1/64 m je pod šumom každého modelu), nie povolenie
    # nechať krok hrubý. Na zoomoch, ktoré tieňovanie používa, sa naň nemá
    # ako naraziť – a keby ho niekto stiahol dole „na ušetrenie bajtov",
    # mriežka sa vráti a nikto to nepovie.
    for z in ZOOMS:
        px = t.tile_m_per_px(z)
        bits = t.frac_bits(px)
        krok = 2.0 ** -bits
        strop = t.SLOPE_EPS * px
        if krok > strop:
            bad.append(f"z{z}: pixel {px:.1f} m znesie krok najviac "
                       f"{strop:.3f} m, ale `frac_bits` dala {bits} bitov, "
                       f"teda {krok:g} m"
                       + (f" (zaráža strop MAX_FRAC_BITS={t.MAX_FRAC_BITS})"
                          if bits >= t.MAX_FRAC_BITS else "")
                       + ". Z plošiniek spraví hillshade pravidelnú tkaninu.")
    # A druhá strana toho istého: na hrubých zoomoch sa nemá platiť za nič.
    if t.frac_bits(t.tile_m_per_px(5)) != 0:
        bad.append("z5: taký hrubý pixel znesie celý meter, ale `frac_bits` "
                   "si pýta zlomkové bity – to je bajt navyše na dlaždicu "
                   "za presnosť, ktorú tam nikto neuvidí.")

    # ---------- 1b. kóduje sa zaokrúhlením, nie orezaním ----------
    # Maskovanie spodných bitov je o riadok kratšie a je to `floor`: posunulo
    # by KAŽDÚ výšku nadol až o celý krok. Systematicky, takže nie ako šum, ale
    # ako schod na hranici zoomov – v 3D teréne terén pri priblížení nadskočí.
    import numpy as np
    vzorka = np.linspace(100.0, 101.0, 4096).reshape(64, 64)
    for bits in (0, 2, 4, 6):
        rgb = t.terrarium(vzorka, bits)
        v = (rgb[..., 0].astype(np.int64) << 16 | rgb[..., 1].astype(np.int64) << 8
             | rgb[..., 2].astype(np.int64))
        spat = v / 256.0 - 32768.0
        krok = 2.0 ** -bits
        chyba = float(np.abs(spat - vzorka).max())
        if chyba > krok / 2 + 1e-9:
            bad.append(f"`terrarium` pri {bits} bitoch posúva výšku až o "
                       f"{chyba:g} m, čiže viac než pol kroku ({krok / 2:g} m) "
                       f"– to nie je zaokrúhlenie, ale orezanie nadol.")
        hodnoty = np.unique(spat)
        if len(hodnoty) > 1:
            rozostup = float(np.diff(hodnoty).min())
            if abs(rozostup - krok) > 1e-9:
                bad.append(f"`terrarium` pri {bits} bitoch dáva krok "
                           f"{rozostup:g} m namiesto {krok:g} m.")

    # ---------- 2. priemer len nadol ----------
    for cell in (1.0, 5.0, 10.0, 20.0, 31.0):
        for z in ZOOMS:
            px = t.tile_m_per_px(z)
            r = t.resampling(px, cell)
            if px < cell and r == "average":
                bad.append(f"model {cell:g} m, z{z} (pixel {px:.1f} m): DEM sa "
                           f"ZVÄČŠUJE, ale prevzorkúva sa `average` – ten pri "
                           f"zväčšovaní zdegeneruje na najbližšieho suseda "
                           f"a z každej bunky modelu vypadne štvorček.")
            if px >= cell and r != "average":
                bad.append(f"model {cell:g} m, z{z} (pixel {px:.1f} m): DEM sa "
                           f"zmenšuje, tam sa musí priemerovať (`average`), "
                           f"nie `{r}`.")
    # Bez známej mriežky sa nesmie hádať – ostáva doterajšie správanie.
    if t.resampling(10.0, 0.0) != "average":
        bad.append("Bez známej mriežky modelu musí `resampling` ostať pri "
                   "`average` – to je doterajšie správanie a pri zmenšovaní "
                   "je správne.")

    # ---------- 3. warp musí niesť zlomok ----------
    src = open(TILES).read()
    warp = src[src.index("def warp_level"):]
    warp = warp[:warp.index("\ndef ")] if "\ndef " in warp[1:] else warp
    if re.search(r'"-ot",\s*"Int16"', warp):
        bad.append("`warp_level` warpuje do Int16 – zlomok výšky sa zahodí "
                   "ešte pred kódovaním a zvislý krok je metrový bez ohľadu "
                   "na `frac_bits`.")
    elif not re.search(r'"-ot",\s*"Float32"', warp):
        bad.append("`warp_level` nemá `-ot Float32`; skontroluj, či zlomok "
                   "výšky prežije až po kódovanie.")

    # ---------- 4. podoba kódovania: sklad aj cache ----------
    build = open(BUILD).read()
    keys = open(KEYS).read()
    v_asset = set(re.findall(r"terrain-\$\{REGION_KEY\}-\$\{TDEM\}-"
                             r"z[^-\s]*-v(\d+)\\?\.pmtiles", build))
    v_cache = set(re.findall(r'echo "terrain=terrain-v(\d+)-', keys))
    if len(v_asset) != 1:
        bad.append(f"Vo `workers/terrain/build.sh` sa nedá prečítať jedna "
                   f"verzia kódovania z mena assetu (našlo sa {sorted(v_asset)}). "
                   f"Meno sa skladá aj pri hľadaní v sklade aj pri ukladaní – "
                   f"obe musia hovoriť to isté.")
    elif not v_cache:
        bad.append("V `workers/plan/cache-keys.sh` nie je verzia v kľúči "
                   "`terrain=` – bez nej vráti cache staré dlaždice.")
    elif v_asset != v_cache:
        bad.append(f"Podoba kódovania sa rozišla: sklad hovorí v{v_asset.pop()}, "
                   f"cache v{v_cache.pop()}. Jedno z tých dvoch miest vráti "
                   f"dlaždice spočítané po starom a build bude zelený.")
    else:
        print(f"  ✓ podoba kódovania v{v_cache.pop()} v sklade aj v cache")

    if bad:
        for b in bad:
            print(f"::error::{b}")
        return 1
    print("Tieňovanie: zvislý krok ide za pixelom, priemeruje sa len nadol, "
          "warp nesie zlomok ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

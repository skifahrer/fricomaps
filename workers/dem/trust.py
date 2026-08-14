#!/usr/bin/env python3
"""
Ktorej malej dlaždici v sklade sa NEDÁ veriť – a musí sa teda prečítať znova.

PREČO TO EXISTUJE. Kontrola (`workers/dem/check.sh`) sa pýtala „je to meno
v sklade?", kým sťahovanie (`workers/dem/fetch.sh` → `coverage.py`) meria, čo
v tých súboroch naozaj je. To sú dve odpovede na jednu otázku – a raz sa
rozišli: v behu 31781263921 mal `dem-dmr5` dlaždicu `N48E016.tif` s nulovou
veľkosťou (prázdna dlaždica ešte od kontroly „v1", ktorej dnes už neveríme).
Kontrola povedala „2 z 2 → doplniť: false", doplnenie sa nespustilo, a job
s tieňovaním o minútu neskôr zistil pokrytie 75,8 %, prešiel na Sonnyho
a spadol, lebo ten sklad je prázdny. Ostrý build Bratislavského kraja tak
zomrel na niečom, čo sa dalo vedieť na začiatku.

AKO TO ROBÍ, ABY TO NEBOLA TRETIA PRAVDA. Neposudzuje nič sám – pýta sa
`coverage.empty_stamp`, teda TEJ ISTEJ funkcie, ktorou sa neskôr riadi
sťahovanie. Rozdiel je len v tom, ČO SA OTVORÍ: skutočná 1° dlaždica má
v 5 m mriežke stovky MB a sťahovať ju kvôli kontrole by bola hlúposť, kým
prázdna dlaždica je `EMPTY_PX`×`EMPTY_PX` pixelov, teda pár kilobajtov.
Preto sa otvárajú LEN podozrivo malé súbory (`tiles.EMPTY_MAX_BYTES`) –
veľkosť je vo výpise skladu, takže sa na to netreba nikoho pýtať.

Prázdna dlaždica je platná odpoveď („pozerali sme sa tam a terén tam nie
je") a znova sa nečíta – ale len dovtedy, kým nesie podpis kontroly, ktorá
dnes platí. Bez podpisu, alebo so starým, je to odpoveď z pravidiel, ktorým
už neveríme (rozpis pri `EMPTY_CHECK` vo `workers/dem/tiles.py`).

Chce `gdalinfo` – volajúci ho doinštaluje, až keď je čo otvárať (býva to
nula súborov).

Použitie (na stdin ide `meno:veľkosť`, teda výstup `store.py --index`):
    python3 workers/drive/store.py --index --store=dem-dmr5 \\
      | python3 workers/dem/trust.py --store=dem-dmr5 --names="N48E016.tif …"

Na stdout idú mená, ktorým sa veriť nedá (jedno na riadok); vysvetlenie ide
na stderr, aby sa dalo výstup rovno použiť v skripte.
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKERS = os.path.dirname(_HERE)


def load(name, filename, where=_HERE):
    """Modul s pomlčkou v mene sa nedá importovať – načíta sa cestou."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(where, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tiles = load("dem_tiles", "tiles.py")
coverage = load("dem_coverage", "coverage.py")


def podozrive(index_riadky, chce):
    """Mená z `chce`, ktoré sú v sklade, ale sú podozrivo malé.

    `index_riadky` je výstup `store.py --index` (`meno:veľkosť`). Veľkosť je
    tam preto, aby sa z nej dal počítať otlačok cache – tu sa tá istá hodnota
    použije druhýkrát a nemusí sa na ňu nikoho pýtať.
    """
    out = []
    for riadok in index_riadky:
        meno, _, velkost = riadok.strip().rpartition(":")
        if not meno or meno not in chce:
            continue
        try:
            if int(velkost) <= tiles.EMPTY_MAX_BYTES:
                out.append(meno)
        except ValueError:
            continue
    return out


def stiahni(store, mena, kam):
    """Stiahne podozrivé dlaždice do `kam`. Vracia tie, ktoré sa podarili."""
    subprocess.run(
        [sys.executable, os.path.join(_WORKERS, "drive", "store.py"), "--get",
         "--store", store, "--dir", kam, "--missing-ok",
         "--name", " ".join(mena)],
        check=False, stdout=sys.stderr, stderr=sys.stderr)
    return [m for m in mena if os.path.exists(os.path.join(kam, m))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, help="sklad, z ktorého sťahovať")
    ap.add_argument("--names", default="",
                    help="mená dlaždíc, o ktoré ide (oddelené medzerou)")
    ap.add_argument("--only-suspect", action="store_true",
                    help="len vypíš, ktoré sú podozrivo malé (bez GDALu "
                         "a bez sťahovania) – podľa toho sa volajúci "
                         "rozhodne, či sa oplatí GDAL vôbec inštalovať")
    args = ap.parse_args()

    chce = set(args.names.split())
    if not chce:
        return 0
    male = podozrive(sys.stdin.readlines(), chce)
    if not male:
        return 0
    if args.only_suspect:
        for meno in male:
            print(meno)
        return 0

    print(f"Podozrivo malé dlaždice v sklade {args.store} "
          f"(≤ {tiles.EMPTY_MAX_BYTES // 1024} kB, čiže nie výškový model, "
          f"ale záznam „pozerali sme sa tam“): {' '.join(male)}",
          file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        for meno in stiahni(args.store, male, tmp):
            info = coverage.tile_info(os.path.join(tmp, meno))
            stamp = coverage.empty_stamp(info) if info else None
            if stamp is None:
                # Malá, ale nie je to prázdna dlaždica podľa mriežky – teda
                # niečo, čo `coverage.py` posúdi až podľa rozsahu. Sem to
                # nepatrí; nech o tom rozhodne on, nie odhad z veľkosti.
                print(f"  ? {meno}: malá, ale nie je to prázdna dlaždica – "
                      f"nechávam ju na coverage.py", file=sys.stderr)
                continue
            if stamp == tiles.EMPTY_CHECK:
                print(f"  ✓ {meno}: prázdna dlaždica s dnešným podpisom "
                      f"„{stamp}“ – ten stupeň sa už čítať nemusí",
                      file=sys.stderr)
                continue
            print(f"  ✗ {meno}: prázdna dlaždica z kontroly "
                  f"„{stamp or 'v1 (nepodpísaná)'}“, dnes platí "
                  f"„{tiles.EMPTY_CHECK}“ – doplní sa", file=sys.stderr)
            print(meno)
    return 0


if __name__ == "__main__":
    sys.exit(main())

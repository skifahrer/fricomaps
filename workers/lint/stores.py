#!/usr/bin/env python3
"""
Kontrola: každý sklad, ktorý si pipeline pýta, je v zozname známych skladov.

PREČO. „Ktoré sklady existujú" si hovoria TRI miesta a musia si odpovedať
rovnako (pravidlo 1):

    workers/drive/store.py         KNOWN – čo pipeline pozná a pustí
    workers/data/dem-sources.json  `store` / `store_area` pri každom zdroji
    .github/workflows/*.yml        `env:` – `DEM_STORE`, `SONNY1_STORE`, …

A KEĎ SA ROZÍDU, PADÁ TO AŽ PO PRÁCI. `dem-sonny1` v `KNOWN` chýbal od chvíle,
čo pribudol zdroj `sonny1`. Doplnenie výšok stiahlo z Drive 12 dlaždíc, rozuzlilo
skratky, prevzorkovalo ich – a spadlo na poslednom kroku, nahratí do skladu,
lebo `known_or_die` to meno nepoznal. Hotová práca sa zahodila a joby, ktoré na
ten sklad čakali (vrstevnice, skaly, tieňovanie), spadli za ním: štyri padnuté
joby z jedného chýbajúceho riadku (beh 31533988137).

Táto kontrola to povie pri pushi, nie po hodine čítania z Drive.

Spustiť sa dá aj lokálne:
    python3 workers/lint/stores.py
"""
import glob
import json
import re
import sys

STORE_PY = "workers/drive/store.py"
SOURCES = "workers/data/dem-sources.json"

bad = []

# KNOWN sa číta zo zdrojáku regulárnym výrazom a nie importom: `store.py` si pri
# načítaní vyrobí prihlásenie na Drive a stiahne pol sveta modulov, čo kontrola
# pri pushi nepotrebuje.
text = open(STORE_PY, encoding="utf-8").read()
blok = re.search(r"^KNOWN = \{(.*?)^\}", text, re.S | re.M)
if not blok:
    print(f"::error::V {STORE_PY} sa nedá nájsť `KNOWN = {{…}}` – kontrola "
          f"skladov nemá čo porovnávať. Keď sa ten zoznam presunul, uprav aj "
          f"`workers/lint/stores.py`.")
    sys.exit(1)
known = set(re.findall(r'"([^"]+)"\s*:', blok.group(1)))
print(f"{STORE_PY}: pozná {len(known)} skladov")

# 1. Zdroje výšok: `store` (dlaždice) aj `store_area` (výrez v plnom rozlíšení).
zdroje = json.load(open(SOURCES, encoding="utf-8"))
for key, meta in zdroje.items():
    if key.startswith("_") or not isinstance(meta, dict):
        continue
    for pole in ("store", "store_area"):
        sklad = meta.get(pole)
        if sklad and sklad not in known:
            bad.append(f"{SOURCES}: zdroj `{key}` chce sklad `{sklad}` "
                       f"({pole}), ktorý v KNOWN vo {STORE_PY} nie je. Doplň ho "
                       f"tam – inak beh spadne až pri nahrávaní, keď je práca "
                       f"hotová (beh 31533988137).")

# 2. `env:` vo workflowoch: `…_STORE: dem-…`. Berie sa hodnota, nie meno
# premennej – práve tá ide do `--store=`.
for path in sorted(glob.glob(".github/workflows/*.yml")):
    for premenna, hodnota in re.findall(r"^\s*([A-Z0-9_]*STORE):\s*(\S+)\s*$",
                                        open(path, encoding="utf-8").read(),
                                        re.M):
        hodnota = hodnota.strip("'\"")
        # Premenná, ktorá nesie priečinok Drive alebo výraz `${{ … }}`, nie je
        # meno skladu – tá sa kontrolovať nedá a ani nemá.
        if hodnota.startswith("${{") or "/" in hodnota:
            continue
        if hodnota not in known:
            bad.append(f"{path}: `{premenna}: {hodnota}` – taký sklad "
                       f"{STORE_PY} nepozná. Buď je v mene preklep, alebo ho "
                       f"treba dopísať do KNOWN.")

for b in bad:
    print(f"::error::{b}")
print(f"sklady: {len(bad)} chýb")
sys.exit(1 if bad else 0)

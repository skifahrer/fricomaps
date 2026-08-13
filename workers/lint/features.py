#!/usr/bin/env python3
"""
Kontrola: čo si schéma krajinných prvkov vyžiada, to jej predfilter pustí.

PREČO. Job `features` čítá PBF DVAKRÁT za sebou:

    región.osm.pbf --[osmium tags-filter, workers/features/filter.txt]-->
        features.osm.pbf --[Planetiler, workers/features/features.yml]--> dlaždice

Predfilter je hrubé sito (bez neho by Planetiler prechádzal 380 MB Slovenska
druhýkrát a job by bežal desiatky minút namiesto minút), presné rozhodnutie
robí schéma. Tým sa ale to isté rozhodnutie ocitlo na DVOCH miestach – a keď
sa rozídu, je to TICHÉ: do schémy pridáš triedu, filter o nej nevie, Planetiler
dostane PBF, v ktorom tie objekty vôbec nie sú, a vyrobí dlaždice bez nich.
Beh je zelený, súbor vznikne, v mape tá vrstva len nie je. Presne to hovorí
varovanie v hlavičke oboch súborov – „inak sa v dlaždiciach ticho neobjaví" –
a doteraz to nikto nekontroloval, iba si to musel pamätať.

ČO SA KONTROLUJE: každý `include_when` v schéme musí mať v predfiltri buď holý
kľúč (`nwr/embankment`), alebo kľúč so svojou hodnotou (`nwr/power=line,…`).
Naopak to neplatí a je to zámer: filter SMIE byť širší.

Spustiť sa dá aj lokálne:
    python3 workers/lint/features.py
"""
import os
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKERS = os.path.dirname(_HERE)
SCHEMA = os.path.join(_WORKERS, "features", "features.yml")
FILTER = os.path.join(_WORKERS, "features", "filter.txt")

bad = []


def filter_tags(path):
    """`{kľúč: {hodnoty} alebo None}` z `osmium tags-filter --expressions`.

    `None` znamená „celý kľúč" (`nwr/embankment`) – vtedy je jedno, akú má
    hodnotu. Typy objektov (`nwr/`, `w/`) sa zahodia: schéma si geometriu
    vyberá sama a filter je aj tak širší.
    """
    out = {}
    with open(path) as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if "/" in line.split("=", 1)[0]:
                line = line.split("/", 1)[1]
            key, _, values = line.partition("=")
            key = key.strip()
            if not values:
                out[key] = None
                continue
            if out.get(key, "x") is None:
                continue                      # celý kľúč už prešiel
            out.setdefault(key, set()).update(
                v.strip() for v in values.split(",") if v.strip())
    return out


def schema_tags(path):
    """`[(kľúč, hodnota, kde)]` zo všetkých `include_when` v schéme."""
    with open(path) as f:
        data = yaml.safe_load(f)
    out = []
    for layer in data.get("layers") or []:
        for feat in layer.get("features") or []:
            when = feat.get("include_when")
            if not isinstance(when, dict):
                continue
            for key, value in when.items():
                values = value if isinstance(value, list) else [value]
                for v in values:
                    out.append((key, v, layer.get("id", "?")))
    return out


tags = filter_tags(FILTER)
for key, value, layer_id in schema_tags(SCHEMA):
    if key in tags and tags[key] is None:
        continue                              # holý kľúč pustí všetko
    # `true` nie je hodnota tagu, ale „tag je prítomný" (`embankment=yes`,
    # `cutting=yes`, `mountain_pass=yes`) – stačí, že filter pozná kľúč.
    if value is True:
        if key not in tags:
            bad.append(f"schéma (vrstva `{layer_id}`) chce prítomnosť tagu "
                       f"`{key}`, ale predfilter ten kľúč vôbec nepozná – "
                       f"objekty s ním sa do PBF pre Planetiler nedostanú "
                       f"a v dlaždiciach ticho nebudú.")
        continue
    if key not in tags:
        bad.append(f"schéma (vrstva `{layer_id}`) chce `{key}={value}`, ale "
                   f"predfilter kľúč `{key}` vôbec nepozná – v dlaždiciach "
                   f"tá trieda ticho nebude. Dopíš ho do "
                   f"workers/features/filter.txt.")
    elif value not in tags[key]:
        bad.append(f"schéma (vrstva `{layer_id}`) chce `{key}={value}`, ale "
                   f"predfilter pri `{key}` púšťa len "
                   f"{', '.join(sorted(tags[key]))} – tá trieda bude "
                   f"v dlaždiciach ticho chýbať.")

for b in bad:
    print(f"::error file=workers/features/filter.txt::{b}")
print(f"krajinné prvky: {len(bad)} chýb "
      f"({len(schema_tags(SCHEMA))} požiadaviek schémy proti predfiltru)")
sys.exit(1 if bad else 0)

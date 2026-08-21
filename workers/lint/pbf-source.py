#!/usr/bin/env python3
"""
PBF regiónu sa sťahuje PRIAMO z osm.fr – a nesmie sa vrátiť obchádzka cez
rodičovský extrakt.

PREČO TO EXISTUJE. osm.fr reže svoje extrakty po skutočných administratívnych
hraniciach a robí to denne, takže každý kraj má vlastný
`{kraj}-latest.osm.pbf` (36–63 MB). Chvíľu sa namiesto neho sťahovalo celé
Slovensko (`europe/slovakia-latest.osm.pbf`, 373 MB) a kraj sa z neho rezal
`osmium extract -s smart --polygon`. Bolo to o krok dlhšie, o 300 MB drahšie
a stálo to na `osmfr.parent`, ktorý sa dal ticho pokaziť poradím krokov:
bez `data/region.poly` skript spadol späť na priame sťahovanie a beh bol pri
tom zelený. Sťahuje sa teda ten región, ktorý sa stavia (pravidlo 7 z
CLAUDE.md), a táto kontrola stráži, že to tak ostane.

ČO SA KONTROLUJE:

  1. každý región s `osmfr` má `dir` aj neprázdne `slugs` – to sú jediné dva
     údaje, z ktorých sa URL na osm.fr skladá,
  2. v `regions.json` neostal `osmfr.parent`: je to mŕtve nastavenie, ktoré už
     nikto nečíta, takže by sľuboval rez, čo sa nekoná,
  3. `workers/plan/pbf.sh` naozaj sťahuje z `$OSMFR_BASE/$DIR/$SLUG.osm.pbf`,
  4. a nereže región z iného extraktu (`osmium extract --polygon`) – orez, ktorý
     v skripte ostáva, je `crop_bbox` na bbox, čiže `-b`.

Spustiť sa dá aj lokálne:
    python3 workers/lint/pbf-source.py
"""
import json
import re
import sys

REGIONS = "workers/data/regions.json"
PBF = "workers/plan/pbf.sh"

bad = []

# ---- 1. a 2. číselník regiónov ----
with open(REGIONS, encoding="utf-8") as f:
    regions = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

for key, reg in regions.items():
    osmfr = reg.get("osmfr") or {}
    if not osmfr:
        continue
    if not osmfr.get("dir") or not osmfr.get("slugs"):
        bad.append(
            f"Región `{key}` má `osmfr`, ale nie `dir` a neprázdne `slugs`. "
            f"URL sa skladá ako `<base>/<dir>/<slug>.osm.pbf`, takže bez nich "
            f"sa PBF nemá odkiaľ stiahnuť.")
    if "parent" in osmfr:
        bad.append(
            f"Región `{key}` má `osmfr.parent` = `{osmfr['parent']}`, ale kraj "
            f"sa už z rodičovského extraktu nereže – sťahuje sa priamo jeho "
            f"vlastný súbor z osm.fr. Zmaž to, nech číselník nesľubuje krok, "
            f"ktorý sa nekoná.")

# ---- 3. a 4. čím to sťahuje ----
with open(PBF, encoding="utf-8") as f:
    text = f.read()
kod = "\n".join(r for r in text.splitlines() if not r.lstrip().startswith("#"))

if not re.search(r'\$OSMFR_BASE/\$DIR/\$SLUG\.osm\.pbf', kod):
    bad.append(
        f"{PBF} neskladá URL regiónu ako `$OSMFR_BASE/$DIR/$SLUG.osm.pbf` – "
        f"kraj sa má sťahovať priamo z osm.fr podľa `osmfr.dir` a "
        f"`osmfr.slugs` z {REGIONS}.")

for volanie in re.findall(r"osmium extract((?:[^\n]*\\\n)*[^\n]*)", kod):
    if "--polygon" in volanie:
        bad.append(
            f"{PBF}: `osmium extract --polygon` – to je rez regiónu z väčšieho "
            f"extraktu a ten sa zrušil: sťahuje sa rovno súbor regiónu z "
            f"osm.fr. Jediný orez, ktorý tu má čo robiť, je `crop_bbox` "
            f"(`osmium extract -b`).")

for b in bad:
    print(f"::error::{b}")
print(f"PBF regiónu priamo z osm.fr: {len(bad)} chýb")
sys.exit(1 if bad else 0)

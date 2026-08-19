#!/usr/bin/env python3
"""
PBF kraja sa reže z rodiča – a poradie krokov to nesmie ticho vypnúť.

PREČO TO EXISTUJE. Hotový `presovsky-latest.osm.pbf` z osm.fr je orezaný NA
TVRDO: ceste, rieke alebo lesu, ktorý pokračuje do vedľajšieho kraja, v ňom
chýbajú uzly za hranicou, a viacpolygónovej ploche (`type=multipolygon`) celé
členské cesty. Planetiler z takého objektu geometriu nepostaví a ZAHODÍ HO
CELÝ – nie presah, celý objekt:

    Error constructing line for OsmWay[…]: Missing location for node: …
    Error constructing polygon for OsmRelation[…]: error building multipolygon

Namerané na Bratislavskom kraji (maxzoom 12, schéma na `highway`/`waterway`/
`landuse`/`natural`): hotový extrakt 97 zahodených čiar, 66 plôch a 19
viacpolygónových plôch; kraj vyrezaný z Slovenska cez `osmium extract -s smart`
5, 6 a 1. Rozdiel je presne to, čo v stiahnutom kraji chýbalo na jeho hranici.

A JE TO TICHÉ Z OBOCH STRÁN. Keď sa orez nespraví, beh je zelený a mapa vyzerá
celá – len jej na hranici chýbajú objekty. Vypnúť ho pritom stačí poradím
krokov: `workers/plan/pbf.sh` reže podľa `data/region.poly` a ten sťahuje krok
`Polygón kraja`. Keby sa dostal ZA krok s PBF (alebo do iného jobu), skript by
polygón nenašiel, spadol by späť na hotový extrakt – a v logu by o tom bol
jeden riadok medzi tisíckami.

ČO SA KONTROLUJE:

  1. každý región, ktorý v `osmfr.dir` leží vnútri iného extraktu (dir má
     lomku, napr. `europe/slovakia`), má `osmfr.parent`,
  2. ten `parent` je kľúč, ktorý v `regions.json` naozaj je, nie je to on sám
     a má `osmfr.dir` aj `osmfr.slugs` (inak sa z neho nedá stiahnuť nič),
  3. každý krok, ktorý púšťa `workers/plan/pbf.sh`, má v `env:`
     `PBF_NEEDS_GEOMETRY` (`true` = kreslí sa z toho mapa, `false` = čítajú sa
     len tagy) – a keď je `true`, krok vyrábajúci `data/region.poly` je PRED
     ním v tom istom jobe,
  4. `pbf.sh` reže stratégiou `smart` a na `--polygon` – `simple` (predvolená)
     by z rodiča vyrobila presne tú dieravú podobu, kvôli ktorej to vzniklo.

Spustiť sa dá aj lokálne:
    python3 workers/lint/pbf-parent.py
"""
import glob
import json
import re
import sys

import yaml

REGIONS = "workers/data/regions.json"
PBF = "workers/plan/pbf.sh"

bad = []

# ---- 1. a 2. rodič v číselníku ----
with open(REGIONS, encoding="utf-8") as f:
    regions = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

for key, reg in regions.items():
    osmfr = reg.get("osmfr") or {}
    if not osmfr:
        continue
    parent = osmfr.get("parent", "")
    # Lomka v `dir` znamená „ležím vnútri iného extraktu" – `europe/slovakia`
    # je priečinok kraja pod Slovenskom, `europe` je priečinok Slovenska.
    if "/" in (osmfr.get("dir") or "") and not parent:
        bad.append(
            f"Región `{key}` má `osmfr.dir` = `{osmfr.get('dir')}`, čiže leží "
            f"vnútri iného extraktu, ale nemá `osmfr.parent`. Hotový extrakt "
            f"z osm.fr je orezaný na tvrdo a Planetiler z neho zahodí každý "
            f"objekt, ktorý cez hranicu prechádza – dopíš `\"parent\": "
            f"\"<kľúč nadradeného regiónu>\"`.")
    if not parent:
        continue
    if parent == key:
        bad.append(f"Región `{key}` má `osmfr.parent` sám na seba.")
        continue
    rodic = regions.get(parent)
    if rodic is None:
        bad.append(
            f"Región `{key}` má `osmfr.parent` = `{parent}`, ale taký kľúč "
            f"v {REGIONS} nie je. Známe: {', '.join(sorted(regions))}.")
        continue
    prodic = rodic.get("osmfr") or {}
    if not prodic.get("dir") or not prodic.get("slugs"):
        bad.append(
            f"Rodič `{parent}` (regiónu `{key}`) nemá `osmfr.dir` a "
            f"`osmfr.slugs`, takže sa z neho nedá stiahnuť PBF, z ktorého by "
            f"sa kraj vyrezal.")

# ---- 3. poradie krokov vo workflowoch ----
# `data/region.poly` vyrába `region-poly.py --poly-out=…`; hľadá sa to, čo
# krok naozaj spúšťa, nie zmienka v komentári.
JE_PBF = re.compile(r"(^|[\s;&|])workers/plan/pbf\.sh")
# Príkaz sa píše na viac riadkov (`\` na konci), takže sa hľadá oboje v tom
# istom `run:` bloku, nie na jednom riadku.
JE_POLY = re.compile(r"region-poly\.py")
JE_POLY_OUT = re.compile(r"--poly-out")


def bez_komentarov(text):
    return "\n".join(r for r in (text or "").splitlines()
                     if not r.lstrip().startswith("#"))


for wf in sorted(glob.glob(".github/workflows/*.yml")):
    with open(wf, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    for job_id, job in (data.get("jobs") or {}).items():
        kroky = job.get("steps") or []
        i_pbf = i_poly = None
        for i, st in enumerate(kroky):
            run = bez_komentarov(st.get("run") or "")
            if i_pbf is None and JE_PBF.search(run):
                i_pbf = i
            if (i_poly is None and JE_POLY.search(run)
                    and JE_POLY_OUT.search(run)):
                i_poly = i
        if i_pbf is None:
            continue
        krok = kroky[i_pbf]
        treba = str((krok.get("env") or {}).get("PBF_NEEDS_GEOMETRY", "")).strip()
        if treba not in ("true", "false"):
            bad.append(
                f"{wf}: krok „{krok.get('name', i_pbf)}“ v jobe `{job_id}` "
                f"púšťa {PBF}, ale nemá v `env:` `PBF_NEEDS_GEOMETRY` "
                f"(prišlo {treba!r}). Napíš `true`, keď sa z toho PBF kreslí "
                f"mapa (Planetiler stavia geometriu, kraj sa musí vyrezať "
                f"z rodiča), alebo `false`, keď sa z neho čítajú len tagy.")
            continue
        if treba == "false":
            continue
        if i_poly is None:
            bad.append(
                f"{wf}: job `{job_id}` púšťa {PBF} s "
                f"`PBF_NEEDS_GEOMETRY: true`, ale v žiadnom kroku nevyrába "
                f"`data/region.poly` (`region-poly.py --poly-out=…`). Bez "
                f"polygónu sa kraj z rodiča nevyreže a mapa ticho príde "
                f"o objekty na hranici.")
        elif i_poly > i_pbf:
            bad.append(
                f"{wf}: job `{job_id}` vyrába `data/region.poly` až v kroku "
                f"„{kroky[i_poly].get('name', i_poly)}“, teda ZA krokom "
                f"„{krok.get('name', i_pbf)}“, ktorý púšťa {PBF}. Skript "
                f"polygón nenájde, spadne späť na hotový extrakt kraja "
                f"a mapa ticho príde o objekty na hranici – daj krok "
                f"s polygónom pred neho.")

# ---- 4. čím sa reže ----
with open(PBF, encoding="utf-8") as f:
    pbf = bez_komentarov(f.read())

extract = re.search(r"osmium extract[^\n]*(\\\n[^\n]*)*", pbf)
if not extract:
    bad.append(f"{PBF} nespúšťa `osmium extract` – kraj sa z rodiča nevyreže.")
else:
    volania = re.findall(r"osmium extract((?:[^\n]*\\\n)*[^\n]*)", pbf)
    if not any("--polygon" in v for v in volania):
        bad.append(
            f"{PBF} volá `osmium extract`, ale ani raz s `--polygon` – kraj "
            f"sa má rezať na svoj polygón, nie na obdĺžnik.")
    for v in volania:
        if "--polygon" not in v:
            continue
        if not re.search(r"(-s|--strategy)[= ]smart", v):
            bad.append(
                f"{PBF}: `osmium extract --polygon` bez `-s smart`. "
                f"Predvolená stratégia (`simple`) nechá ceste cez hranicu "
                f"chýbajúce uzly a viacpolygónovej ploche chýbajúce členské "
                f"cesty – Planetiler taký objekt zahodí celý, čiže presne to, "
                f"kvôli čomu sa z rodiča reže.")

for b in bad:
    print(f"::error::{b}")
print(f"PBF kraja z rodiča: {len(bad)} chýb")
sys.exit(1 if bad else 0)

#!/usr/bin/env python3
"""
Kraj sa REŽE Z RODIČOVSKÉHO EXTRAKTU – hotový export kraja sa nesmie vrátiť.

PREČO TO EXISTUJE, A PREČO OPAČNE, NEŽ PREDTÝM. Táto kontrola kedysi strážila
pravý opak: že sa kraj sťahuje priamo z osm.fr a nereže sa z 373 MB Slovenska.
Bolo to o krok kratšie a o 337 MB lacnejšie a vyzeralo to ako pravidlo 7
(„nikdy nesťahuj viac, než treba"). Lenže hotový `{kraj}-latest.osm.pbf` NIE JE
referenčne úplný: plocha, ktorá pokračuje do susedného kraja, v ňom nemá
všetkých členov a Planetiler ju ZAHODÍ CELÚ – aj tú časť, čo v kraji leží.

Namerané na `bratislavsky-latest.osm.pbf`: z 3075 plošných relácií malo 250
chýbajúceho člena, z toho 49 krajinnej pokrývky a ochrany prírody. Medzi nimi
CHKO Malé Karpaty, CHKO Záhorie, CHKO Dunajské luhy, NPR Aluvium Moravy, les
Záhoria (1011 členov) a Zdrž Hrušov. V mape kraja jednoducho neboli – a build
bol pri tom zelený. To sťahovanie teda TREBA, takže pravidlo 7 porušené nie je;
porušené bolo pravidlo 8 (tichý omyl je horší než pád).

Po reze z rodiča ostane z tých 49 päť a všetky sú na hranici so zahraničím.
Rozpis aj s číslami je v hlavičke `workers/plan/pbf.sh`; koľko ich v konkrétnom
behu je, počíta `workers/plan/pbf-areas.py` a píše to do súhrnu.

ČO SA KONTROLUJE:

  1. každý región s `osmfr` má `dir` aj neprázdne `slugs` – to sú jediné dva
     údaje, z ktorých sa URL na osm.fr skladá,
  2. každý kraj (`admin_level` > 2) má `osmfr.parent` a ten ukazuje na región,
     ktorý v číselníku naozaj je a má vlastný `osmfr`,
  3. `workers/plan/pbf.sh` reže rodiča `osmium extract -s smart --polygon`,
  4. a má pri tom `-S types=multipolygon,boundary`: predvolene `smart` dopĺňa
     členov len reláciám `type=multipolygon`, kým CHKO je `type=boundary` –
     bez toho prepínača ostanú všetky tri CHKO rozbité aj po reze z rodiča
     (namerané: 9 zvyšných plôch namiesto 5). Je to jedno slovo, ktoré sa dá
     pri úprave stratiť, a nespadne po ňom nič.
  5. rez sa nesmie ticho preskočiť, keď chýba hranica – práve na tom padla
     prvá verzia tohto kroku: bez `data/region.poly` sa vrátil k priamemu
     sťahovaniu kraja a beh bol zelený.

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

    parent = osmfr.get("parent")
    if (reg.get("admin_level") or 0) > 2 and not parent:
        bad.append(
            f"Región `{key}` je kraj, ale nemá `osmfr.parent`. Kraj sa reže "
            f"z rodičovského extraktu – hotový `{key}-latest.osm.pbf` z osm.fr "
            f"nie je referenčne úplný a plochy presahujúce do susedného kraja "
            f"(CHKO, veľké lesy) by z mapy ticho zmizli celé.")
    if parent and parent not in regions:
        bad.append(
            f"Región `{key}` má `osmfr.parent` = `{parent}`, ale taký región "
            f"v {REGIONS} nie je. Rodič je KĽÚČ regiónu, nie URL.")
    elif parent and not (regions[parent].get("osmfr") or {}).get("dir"):
        bad.append(
            f"Rodič `{parent}` regiónu `{key}` nemá `osmfr.dir` – nedá sa "
            f"z neho zložiť adresa, z ktorej sa má rezať.")
    if parent == key:
        bad.append(f"Región `{key}` je rodičom sám sebe.")

# ---- 3. až 5. čím to reže ----
with open(PBF, encoding="utf-8") as f:
    text = f.read()
kod = "\n".join(r for r in text.splitlines() if not r.lstrip().startswith("#"))

rezy = re.findall(r"osmium extract((?:[^\n]*\\\n)*[^\n]*)", kod)
rez_rodica = [v for v in rezy if "--polygon" in v]

if not rez_rodica:
    bad.append(
        f"{PBF} nereže kraj z rodičovského extraktu (`osmium extract "
        f"--polygon`). Hotový export kraja z osm.fr sa použiť nesmie: nemá "
        f"členov plôch, čo presahujú do susedného kraja, a Planetiler ich "
        f"zahodí CELÉ – z mapy zmizne CHKO aj s tou časťou, čo v kraji leží.")

# `-S types=` musí byť pri KAŽDOM reze, nie len pri tom z rodiča: orez na
# `crop_bbox` aj na štvorec rýchleho testu majú ten istý problém, len sa
# prejaví ešte skôr – zo 4 km² vytŕča skoro každá plocha.
for volanie in rezy:
    if "-s smart" not in volanie:
        bad.append(
            f"{PBF}: `osmium extract` bez `-s smart`. Bez neho sa členovia "
            f"relácií nedopĺňajú a rez nespraví nič navyše oproti hotovému "
            f"exportu.")
    if "types=multipolygon,boundary" not in volanie:
        bad.append(
            f"{PBF}: `osmium extract -s smart` bez `-S types=multipolygon,boundary`. "
            f"Predvolene `smart` dopĺňa členov len reláciám "
            f"`type=multipolygon`, kým CHKO je `type=boundary` – bez toho "
            f"prepínača ostanú CHKO Malé Karpaty, Záhorie aj Dunajské luhy "
            f"rozbité a v mape ich nebude. Nespadne po tom nič.")

if not re.search(r'\$OSMFR_BASE/\$PDIR/\$SLUG\.osm\.pbf', kod):
    bad.append(
        f"{PBF} neskladá URL rodiča ako `$OSMFR_BASE/$PDIR/$SLUG.osm.pbf` – "
        f"adresa sa má brať z `osmfr.dir` a `osmfr.slugs` toho regiónu, na "
        f"ktorý ukazuje `osmfr.parent` v {REGIONS}.")

# 5. chýbajúca hranica musí byť PÁD, nie návrat k priamemu sťahovaniu
if not re.search(r'if \[ ! -s "\$POLY" \]; then\n[^\n]*::error::', kod):
    bad.append(
        f"{PBF} nepadne, keď chýba `.poly` regiónu. Presne tam sa predošlá "
        f"verzia vrátila k priamemu sťahovaniu kraja – beh bol zelený a v mape "
        f"zase chýbali CHKO (pravidlo 8). Chýbajúca hranica musí byť "
        f"`::error::` a `exit 1`.")

for b in bad:
    print(f"::error::{b}")
print(f"Kraj sa reže z rodičovského extraktu: {len(bad)} chýb")
sys.exit(1 if bad else 0)

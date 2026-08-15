#!/usr/bin/env python3
"""
Mapa sveta: štýl kreslí presne tie vrstvy a od tých zoomov, ktoré schéma robí.

PREČO TO EXISTUJE. Je to ten istý pár čísel na dvoch miestach, aký pri
vrstevniciach stráži `workers/lint/zoom-floor.py`, len o mape sveta:

    workers/world/world.yml    `min_zoom`  – ČO SA VYROBÍ (Planetiler)
    workers/world/style.mjs    `minzoom`   – ČO SA NAKRESLÍ (MapLibre)

A k tomu tretia vec, ktorá je ešte tichšia: MENO VRSTVY. Keď v štýle stojí
`source-layer: downloads` a schéma vrstvu volá `download`, MapLibre nepovie
nič – v mape jednoducho nebudú regióny sťahovania. Vyzerá to ako chýbajúce
dáta, hoci v `.pmtiles` sú.

ČO SA KONTROLUJE:

  1. každý `source-layer` v štýle je vrstva schémy (preklep = prázdno v mape),
  2. každá vrstva schémy je v štýle aspoň raz nakreslená (inak sa platí za
     dlaždice, ktoré nikto nevidí),
  3. najnižší `minzoom` v štýle sa pri každej vrstve rovná najnižšiemu
     `min_zoom` v schéme – nižší štýl znamená dieru v mape, vyšší dlaždice
     pre zoomy, ktoré nikto nekreslí.

Bod 3 je zámerne o DNE vrstvy, nie o každej triede zvlášť: v schéme je
staging po triedach (`level`, `rank`) a v štýle po vrstvách a filtroch, takže
priradiť „táto trieda = táto vrstva štýlu" by kontrola musela hádať. Dno chytí
to, čo naozaj bolí – že sa vrstva začne kresliť skôr, než vôbec vznikne.

Vedľajší zisk: kontrola štýl SPUSTÍ, takže chyba v `style.mjs` sa nájde tu
a nie až v behu, ktorý predtým pol hodiny počítal dlaždice.

Spustiť sa dá aj lokálne:
    python3 workers/lint/world.py
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

SCHEMA = "workers/world/world.yml"
STYLE = "workers/world/style.mjs"

bad = []

doc = yaml.safe_load(open(SCHEMA, encoding="utf-8"))
# Dno vrstvy v schéme: najnižší `min_zoom` spomedzi jej prvkov.
schema_min = {}
for lay in doc.get("layers") or []:
    zoomy = [f.get("min_zoom", 0) for f in (lay.get("features") or [])]
    schema_min[lay["id"]] = min(zoomy) if zoomy else 0
print(f"{SCHEMA}: vrstvy {', '.join(f'{k} od z{v}' for k, v in sorted(schema_min.items()))}")

with tempfile.TemporaryDirectory() as tmp:
    hotovo = subprocess.run(
        ["node", STYLE, f"--out={tmp}"],
        capture_output=True, text=True, check=False)
    if hotovo.returncode != 0:
        print(f"::error file={STYLE}::štýl sa nedá vygenerovať: "
              f"{hotovo.stderr.strip() or hotovo.stdout.strip()}")
        sys.exit(1)
    subory = sorted(f for f in os.listdir(tmp) if f.endswith(".json"))
    if not subory:
        print(f"::error file={STYLE}::nevyrobil ani jeden štýl.")
        sys.exit(1)

    for meno in subory:
        with open(os.path.join(tmp, meno), encoding="utf-8") as f:
            styl = json.load(f)
        style_min = {}
        for lay in styl.get("layers") or []:
            src = lay.get("source-layer")
            if not src:
                continue        # `background` nemá zdroj
            z = lay.get("minzoom", 0)
            style_min[src] = min(style_min.get(src, z), z)

        for src, z in sorted(style_min.items()):
            if src not in schema_min:
                bad.append(
                    f"{meno}: vrstva štýlu má `source-layer: {src}`, ktorý "
                    f"schéma nepozná (má {sorted(schema_min)}). MapLibre na to "
                    f"nepovie nič – tá vrstva v mape jednoducho nebude.")
            elif z != schema_min[src]:
                preco = ("mapa má v tých zoomoch dieru"
                         if z < schema_min[src]
                         else "platia sa dlaždice, ktoré nikto nekreslí")
                bad.append(
                    f"{meno}: `{src}` sa kreslí od z{z}, ale schéma ju robí od "
                    f"z{schema_min[src]} – {preco}. Zrovnaj `minzoom` v "
                    f"{STYLE} s `min_zoom` v {SCHEMA}.")
        for src in schema_min:
            if src not in style_min:
                bad.append(
                    f"{meno}: schéma robí vrstvu `{src}`, ale štýl ju nekreslí "
                    f"ani raz. Buď ju dokresli, alebo ju zo schémy vyhoď – "
                    f"inak sú to dlaždice, za ktoré nikto nič nedostane.")

for b in bad:
    print(f"::error::{b}")
print(f"mapa sveta (schéma × štýl): {len(bad)} chýb")
sys.exit(1 if bad else 0)

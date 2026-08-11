#!/usr/bin/env python3
"""
Kontrola: katalóg `maps.json` drží tvar a nikto ho neobchádza.

PREČO. `maps.json` je jediný zoznam toho, ktoré mapy sú hotové a kde na Drive
ležia. Je to súbor v repozitári, ktorý dopisuje BEH – a to je presne ten druh
veci, ktorá sa rozíde ticho:

  * mená v katalógu prestanú sedieť s menami balíkov, ktoré publikovanie
    naozaj vyrába (`<kraj>[-<výsek>][-testNkm2]{,-vrstevnice-skaly,-tienovanie}
    .zip`), a odkazy potom ukazujú na súbory, ktoré na Drive nie sú;
  * katalóg sa zapíše aj vtedy, keď publikovanie zlyhalo – zoznam by tvrdil,
    že mapa je hotová;
  * build stratí právo zapisovať (`contents: write`) a katalóg sa ticho
    prestane dopĺňať.

Spustiť sa dá aj lokálne:
    python3 workers/lint/catalog.py
"""
import json
import re
import sys

import yaml

DRUHY = {"mapa", "vrstevnice-skaly", "tienovanie"}
# Meno balíka: `<kraj>[-<výsek>][-testNkm2]` + prípona druhu. Sedí to s
# `zaklad()` a `meno()` vo `workers/deploy/publish-map.py`.
MENO = re.compile(r"^[a-z0-9_]+(-[a-z0-9_]+)*(-test[0-9.]+km2)?"
                  r"(-vrstevnice-skaly|-tienovanie)?\.zip$")
CATALOG = "maps.json"
WORKFLOW = ".github/workflows/build-map.yml"

bad = []


def polozky(node, kde):
    """Rekurzívne prejde katalóg a vráti (cesta, položka s mapami)."""
    out = []
    if isinstance(node, dict) and isinstance(node.get("maps"), dict):
        out.append((kde, node))
    for kluc in ("countries", "regions", "subregions"):
        for k, v in (node.get(kluc) or {}).items() if isinstance(node, dict) else []:
            out += polozky(v, f"{kde}/{k}" if kde else k)
    return out


try:
    with open(CATALOG) as f:
        data = json.load(f)
except FileNotFoundError:
    bad.append(f"{CATALOG} v repozitári nie je – build ho dopisuje, ale musí "
               f"existovať aspoň prázdny (`{{\"countries\": {{}}}}`), inak sa "
               f"prvý zápis nemá o čo oprieť.")
    data = None
except ValueError as exc:
    bad.append(f"{CATALOG} nie je platný JSON ({exc}) – build ho číta a dopisuje, "
               f"takže na rozbitom súbore prestane katalóg vznikať.")
    data = None

if data is not None:
    if not isinstance(data.get("countries"), dict):
        bad.append(f"{CATALOG} nemá objekt `countries` – štruktúra je "
                   f"krajina → kraj (`regions`) → výsek (`subregions`).")
    for kde, p in polozky(data, ""):
        for druh, m in p["maps"].items():
            if druh not in DRUHY:
                bad.append(f"{CATALOG}: {kde} má balík `{druh}`, ktorý "
                           f"publikovanie nevyrába (pozná {sorted(DRUHY)}).")
            if not isinstance(m, dict) or not m.get("file") or not m.get("link"):
                bad.append(f"{CATALOG}: {kde}/{druh} nemá `file` a `link` – "
                           f"zoznam bez odkazu je na nič.")
                continue
            if not MENO.match(m["file"]):
                bad.append(f"{CATALOG}: {kde}/{druh} má meno `{m['file']}`, "
                           f"ktoré nesedí s tým, čo vyrába "
                           f"`workers/deploy/publish-map.py`.")

try:
    wf = yaml.safe_load(open(WORKFLOW))
    text = open(WORKFLOW).read()
except (OSError, ValueError) as exc:
    print(f"::error::{WORKFLOW} sa nedá prečítať: {exc}")
    sys.exit(1)

deploy = (wf.get("jobs") or {}).get("deploy") or {}
if (deploy.get("permissions") or {}).get("contents") != "write":
    bad.append(f"{WORKFLOW}: job `deploy` nemá `contents: write`, takže katalóg "
               f"{CATALOG} nemá ako commitnúť – a prestal by sa dopĺňať bez "
               f"jediného slova.")

kroky = deploy.get("steps") or []
katalog = [s for s in kroky if str(s.get("run", "")).find("deploy/catalog.sh") >= 0]
if not katalog:
    bad.append(f"{WORKFLOW}: v jobe `deploy` nie je krok, ktorý pustí "
               f"`workers/deploy/catalog.sh` – katalóg by sa zapísal na runner "
               f"a stratil sa s ním.")
else:
    for s in katalog:
        # Katalóg nesmie vzniknúť po neúspešnom publikovaní: ukazoval by na
        # súbory, ktoré na Drive nie sú.
        if "steps.publish.outcome == 'success'" not in str(s.get("if", "")):
            bad.append(f"{WORKFLOW}: krok „{s.get('name')}“ nemá podmienku "
                       f"`steps.publish.outcome == 'success'` – katalóg by "
                       f"ukazoval aj na balíky, ktoré sa nenahrali.")
if "--maps=" not in text:
    bad.append(f"{WORKFLOW}: `publish-map.py` sa volá bez `--maps=`, takže "
               f"katalóg nikto nedopíše.")

for b in bad:
    print(f"::error::{b}")
print(f"katalóg máp: {len(bad)} chýb")
sys.exit(1 if bad else 0)

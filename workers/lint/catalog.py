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

DRUHY = {"mapa", "vrstevnice-skaly", "tienovanie", "wikipedia"}
# Meno balíka: `<kraj>[-<výsek>][-testNkm2]` + prípona druhu. Sedí to s
# `zaklad()` a `meno()` vo `workers/deploy/publish-map.py`.
MENO = re.compile(r"^[a-z0-9_]+(-[a-z0-9_]+)*(-test[0-9.]+km2)?"
                  r"(-vrstevnice-skaly|-tienovanie|-wikipedia)?\.zip$")
CATALOG = "maps.json"
WORKFLOW = ".github/workflows/build-map.yml"
# Články z Wikipédie majú vlastnú pipeline a tá do katalógu zapisuje tiež –
# tým istým skriptom, len s `--only=wikipedia`. Platia na ňu tie isté dve
# pravidlá: musí mať právo commitnúť a nesmie zapísať po neúspešnom nahratí.
WIKI_WORKFLOW = ".github/workflows/wiki.yml"
PUBLISH_MAP = "workers/deploy/publish-map.py"
# Čo sa do katalógu zapíše, skladá vedľajší modul – `publish-map.py` prerástol
# strop 800 riadkov a rezalo sa tam, kde sa mení otázka.
CATALOG_PY = "workers/deploy/catalog.py"

bad = []


def polozky(node, kde):
    """Rekurzívne prejde katalóg a vráti (cesta, položka s mapami)."""
    out = []
    if not isinstance(node, dict):
        return out
    if isinstance(node.get("maps"), dict):
        out.append((kde, node))
    for kluc in ("regions", "subregions"):
        for k, v in (node.get(kluc) or {}).items():
            out += polozky(v, f"{kde}/{k}" if kde else k)
    return out


def krajiny(data):
    """Krajiny sú kľúče v KORENI – metadáta katalógu začínajú podčiarkovníkom.

    Tá istá konvencia ako vo `workers/data/areas.json` (`_comment` medzi kľúčmi
    pohorí). Kto to číta, preskočí `_*`; kontrola musí robiť to isté, inak by
    `_comment` hlásila ako krajinu bez máp.
    """
    return {k: v for k, v in data.items()
            if not k.startswith("_") and isinstance(v, dict)}


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
    if not isinstance(data, dict):
        bad.append(f"{CATALOG} nie je objekt – hlavný kľúč je KRAJINA "
                   f"(`slovensko`), pod ňou `regions` a `subregions`.")
        data = None
    elif [k for k in data if not k.startswith("_") and not isinstance(data[k], dict)]:
        bad.append(f"{CATALOG}: v koreni je kľúč, ktorý nie je ani krajina "
                   f"(objekt), ani metadáta (`_…`). Hlavný kľúč je krajina.")
    for kde, p in polozky({"regions": krajiny(data or {})}, ""):
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

# ---- wiki: samostatná pipeline zapisuje do toho istého katalógu ----
try:
    wwf = yaml.safe_load(open(WIKI_WORKFLOW))
    wtext = open(WIKI_WORKFLOW, encoding="utf-8").read()
except (OSError, ValueError) as exc:
    bad.append(f"{WIKI_WORKFLOW} sa nedá prečítať: {exc}")
    wwf, wtext = {}, ""
for job, jd in ((wwf.get("jobs") or {})).items():
    if (jd.get("permissions") or {}).get("contents") != "write":
        bad.append(f"{WIKI_WORKFLOW}: job `{job}` nemá `contents: write`, "
                   f"takže {CATALOG} nemá ako commitnúť – balík by sa nahral "
                   f"na Drive a v katalógu by po ňom nezostalo nič.")
    kat = [s for s in (jd.get("steps") or [])
           if "deploy/catalog.sh" in str(s.get("run", ""))]
    if not kat:
        bad.append(f"{WIKI_WORKFLOW}: job `{job}` nepúšťa "
                   f"`workers/deploy/catalog.sh` – katalóg by sa zapísal na "
                   f"runner a stratil sa s ním.")
    for s in kat:
        if "steps.publish.outcome == 'success'" not in str(s.get("if", "")):
            bad.append(f"{WIKI_WORKFLOW}: krok „{s.get('name')}“ nemá "
                       f"podmienku `steps.publish.outcome == 'success'` – "
                       f"katalóg by ukazoval aj na balík, ktorý sa nenahral.")
if wtext and "--only=wikipedia" not in wtext:
    bad.append(f"{WIKI_WORKFLOW}: `publish-map.py` sa volá bez "
               f"`--only=wikipedia`. Bez toho by publikovanie chcelo celý web "
               f"(`_site`), ktorý táto pipeline nerobí, a katalóg by prepísalo "
               f"položkou bez máp.")

# `--only` musí katalóg DOPĹŇAŤ, nie prepisovať: samostatná pipeline vie len
# o svojom balíku a prepis by zmazal odkazy na mapu, o ktorej nič nevie.
try:
    pmap = open(PUBLISH_MAP, encoding="utf-8").read()
    kmap = open(CATALOG_PY, encoding="utf-8").read()
except OSError as exc:
    bad.append(f"{PUBLISH_MAP} / {CATALOG_PY} sa nedá prečítať: {exc}")
    pmap = kmap = ""
if kmap and "def zapis_katalog(path, parts, regions, baliky, man, iba=" not in kmap:
    bad.append(f"{CATALOG_PY}: `zapis_katalog` nepozná režim „doplň jeden "
               f"balík“ (parameter `iba`). Samostatná pipeline by položku "
               f"regiónu prepísala a odkazy na mapu by zmizli.")

# RÝCHLY TEST SA ZAPISUJE, ALE DO VLASTNÉHO UZLA. Zapisovať sa musí – balík
# `…-test4km2.zip` na Drive je inak jediný, o ktorom sa bez tokenu nedá
# dozvedieť. Sadnúť na uzol ostrej mapy ale nesmie: terén je v ňom na pár km²
# a kto si ho stiahne podľa katalógu, dostane mapu s dierou. Preto sa
# kontroluje, že cesta v katalógu vzniká `cesta_katalog()` a podáva sa ako
# `kat=` – bez toho by test ostrú mapu prepísal.
if pmap:
    if "def cesta_katalog(" not in pmap:
        bad.append(f"{PUBLISH_MAP}: chýba `cesta_katalog()` – rýchly test by "
                   f"sa zapísal na miesto ostrej mapy toho istého kraja "
                   f"a katalóg by o mape zo 4 km² tvrdil, že je to kraj.")
    if "kat=kat" not in pmap:
        bad.append(f"{PUBLISH_MAP}: `zapis_katalog` sa volá bez `kat=`, takže "
                   f"uzol testu a uzol ostrej mapy sú ten istý.")
    if "nezapisujem" in pmap:
        bad.append(f"{PUBLISH_MAP}: katalóg sa pri niektorom behu preskakuje. "
                   f"Rýchly test má vlastný uzol (`cesta_katalog`), takže "
                   f"preskakovať ho netreba – a balík, ktorý v zozname nie je, "
                   f"nikto nenájde.")

for b in bad:
    print(f"::error::{b}")
print(f"katalóg máp: {len(bad)} chýb")
sys.exit(1 if bad else 0)

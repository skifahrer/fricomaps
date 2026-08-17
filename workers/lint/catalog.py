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

DRUHY = {"mapa", "vrstevnice-skaly", "tienovanie", "wikipedia", "search"}
# Meno balíka: `<kraj>[-<výsek>][-testNkm2]` + prípona druhu. Sedí to s
# `zaklad()` a `meno()` vo `workers/deploy/publish-map.py`.
MENO = re.compile(r"^[a-z0-9_]+(-[a-z0-9_]+)*(-test[0-9.]+km2)?"
                  r"(-vrstevnice-skaly|-tienovanie|-wikipedia|-search)?\.zip$")
CATALOG = "maps.json"
WORKFLOW = ".github/workflows/build-map.yml"
# Samostatné pipeline, ktoré do TOHO ISTÉHO katalógu zapisujú tiež – tým istým
# skriptom (`publish-map.py`, pri článkoch s `--only=wikipedia`). Platia na ne
# tie isté dve pravidlá: musia mať právo commitnúť a nesmú zapísať po
# neúspešnom nahratí. Zoznam je preto zoznam a nie jedno meno: pribudla k nemu
# mapa sveta a bez toho by na ňu tieto kontroly ticho nedosiahli – čiže presne
# to, čomu sa tento súbor venuje.
WIKI_WORKFLOW = ".github/workflows/wiki.yml"
WORLD_WORKFLOW = ".github/workflows/world-map.yml"
PIPELINE = (WIKI_WORKFLOW, WORLD_WORKFLOW)
PUBLISH_MAP = "workers/deploy/publish-map.py"
# Čo sa do katalógu zapíše, skladá vedľajší modul – `publish-map.py` prerástol
# strop 800 riadkov a rezalo sa tam, kde sa mení otázka.
CATALOG_PY = "workers/deploy/catalog.py"
# A `catalog.sh` ten súbor commitne – na ČERSTVÚ vetvu, nie na SHA, s ktorou
# beh začal (inak druhý zapisujúci job v tom istom behu vždy skončí
# konfliktom; beh 31782846262).
CATALOG_SH = "workers/deploy/catalog.sh"

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

# ---- samostatné pipeline zapisujú do toho istého katalógu ----
for wf_path in PIPELINE:
    try:
        wwf = yaml.safe_load(open(wf_path))
        wtext = open(wf_path, encoding="utf-8").read()
    except (OSError, ValueError) as exc:
        bad.append(f"{wf_path} sa nedá prečítať: {exc}")
        continue
    for job, jd in ((wwf.get("jobs") or {})).items():
        if (jd.get("permissions") or {}).get("contents") != "write":
            bad.append(f"{wf_path}: job `{job}` nemá `contents: write`, "
                       f"takže {CATALOG} nemá ako commitnúť – balík by sa "
                       f"nahral na Drive a v katalógu by po ňom nezostalo nič.")
        kat = [s for s in (jd.get("steps") or [])
               if "deploy/catalog.sh" in str(s.get("run", ""))]
        if not kat:
            bad.append(f"{wf_path}: job `{job}` nepúšťa "
                       f"`workers/deploy/catalog.sh` – katalóg by sa zapísal "
                       f"na runner a stratil sa s ním.")
        for s in kat:
            if "steps.publish.outcome == 'success'" not in str(s.get("if", "")):
                bad.append(f"{wf_path}: krok „{s.get('name')}“ nemá "
                           f"podmienku `steps.publish.outcome == 'success'` – "
                           f"katalóg by ukazoval aj na balík, ktorý sa "
                           f"nenahral.")
    if wf_path == WIKI_WORKFLOW and "--only=wikipedia" not in wtext:
        bad.append(f"{WIKI_WORKFLOW}: `publish-map.py` sa volá bez "
                   f"`--only=wikipedia`. Bez toho by publikovanie chcelo celý "
                   f"web (`_site`), ktorý táto pipeline nerobí, a katalóg by "
                   f"prepísalo položkou bez máp.")
    # Mapa sveta naopak publikuje CELÚ mapu (`--site=_site`), takže položku
    # svojho uzla prepisuje – ale musí povedať, čo v nej je. Bez `MAP_LAYERS`
    # by si `publish-map.py` vypýtal vrstvy mapy kraja a do katalógu aj do
    # `obsah.json` by napísal „bez_vrstevnic, bez_skal, bez_tienovania“:
    # znie to ako mapa kraja s vypnutým terénom, a to táto mapa nie je.
    if wf_path == WORLD_WORKFLOW and "MAP_LAYERS" not in wtext:
        bad.append(f"{WORLD_WORKFLOW}: nikde nenastavuje `MAP_LAYERS`, takže "
                   f"katalóg by o mape sveta tvrdil, že je to mapa kraja bez "
                   f"vrstevníc, skál a tieňovania.")

# `--only` musí katalóg DOPĹŇAŤ, nie prepisovať: samostatná pipeline vie len
# o svojom balíku a prepis by zmazal odkazy na mapu, o ktorej nič nevie.
try:
    pmap = open(PUBLISH_MAP, encoding="utf-8").read()
    kmap = open(CATALOG_PY, encoding="utf-8").read()
except OSError as exc:
    bad.append(f"{PUBLISH_MAP} / {CATALOG_PY} sa nedá prečítať: {exc}")
    pmap = kmap = ""
# ČO SI ČITATEĽ NESMIE ODVODIŤ. Meno súboru s dlaždicami sa z kľúča uzla
# poskladať NEDÁ – uzol je `bratislavsky_test4km2`, balík `bratislavsky-test4km2
# .zip` a dlaždice v ňom `tiles/bratislavsky_test4-…`; pri výreze sa dokonca
# volajú podľa kraja, nie podľa výseku. Kto by si cestu odvodil, dostane súbor,
# ktorý v balíku nie je, a vrstva sa ticho nenačíta. A strop zoomu, ktorý
# v katalógu nie je, si čitateľ dosadí z `maxzoom` mapy – trasy (z14) a prvky
# (z15) by tak nad svojím stropom pýtali neexistujúce dlaždice a zmizli by
# práve tie dve vrstvy, ktoré sa vyberajú ťuknutím do mapy.
for kluc, preco in (
        ("tiles_paths", "cesty k `.pmtiles` sa do položky nezapisujú, takže "
                        "si ich čitateľ musí odvodiť z kľúča – a ten meno "
                        "súboru nie je"),
        ("trails_maxzoom", "strop zoomu značených trás (z14) v položke nie je"),
        ("features_maxzoom", "strop zoomu krajinných prvkov (z15) v položke "
                             "nie je"),
        ("rock_source", "z ktorého modelu sú SKALY, v položke nie je "
                        "(`dem_source` je zdroj vrstevníc)"),
        ("terrain_source", "z ktorého modelu je TIEŇOVANIE, v položke nie je "
                           "– pri prechode na náhradný model by atribúcia "
                           "tvrdila DMR 5.0 nad reliéfom zo Sonnyho")):
    if kmap and kluc not in kmap:
        bad.append(f"{CATALOG_PY}: {preco}. Doplň to z `manifest.json` – "
                   f"pozná to, lebo podľa toho číta dlaždice aj viewer.")

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

try:
    csh = open(CATALOG_SH, encoding="utf-8").read()
except OSError as exc:
    bad.append(f"{CATALOG_SH} sa nedá prečítať: {exc}")
    csh = ""
if csh and "reset --mixed" not in csh:
    bad.append(f"{CATALOG_SH}: commit sa nerobí na čerstvú vetvu "
               f"(`git fetch` + `git reset --mixed FETCH_HEAD`). Druhý "
               f"zapisujúci job v tom istom behu – `.aar` po `deploy` – by "
               f"niesol aj cudzí zápis, rebase by ho pridával druhýkrát "
               f"a katalóg by sa zakaždým ticho zahodil.")

# ---- balík z INEJ pipeline prežije build mapy ----
# Toto sa staticky prečítať nedá, a bola to presne tá tichá chyba: `wikipedia`
# robí `wiki.yml`, ale položku regiónu prepisuje Build map – a ten ju pri
# každom builde (teda pri každej zmene štýlu) z katalógu zmazal, hoci
# `…-wikipedia.zip` na Drive ležal ďalej, lebo TAM sa balík, o ktorom beh
# nerozhoduje, nemaže. Katalóg sa preto skúša naostro: zapíše sa mapa, doplní
# sa cudzí balík a mapa sa zapíše znova.
def _skuska_katalogu():
    """Vráti zoznam chýb – prázdny, keď sa katalóg správa, ako má."""
    import contextlib
    import importlib.util
    import io
    import tempfile

    spec = importlib.util.spec_from_file_location("_lint_catalog", CATALOG_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_lint_catalog"] = mod
    spec.loader.exec_module(mod)

    regions = {"bratislavsky": {"name": "Bratislavský kraj",
                                "country": "slovensko"}}
    man = {"default_region": "bratislavsky",
           "regions": {"bratislavsky": {"bbox": [16.8, 48.0, 17.5, 48.6],
                                        "maxzoom": 16}}}
    parts = ["slovensko", "bratislavsky"]
    mapove = ["mapa", "vrstevnice-skaly", "tienovanie"]
    mapa_baliky = [("", "bratislavsky.zip", 1, "id1", "zip"),
                   ("vrstevnice-skaly", "bratislavsky-vrstevnice-skaly.zip",
                    1, "id2", "zip"),
                   ("tienovanie", "bratislavsky-tienovanie.zip", 1, "id3", "zip")]

    def maps_v(path):
        with open(path) as f:
            return json.load(f)["slovensko"]["regions"]["bratislavsky"]["maps"]

    chyby = []
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/maps.json"
        with contextlib.redirect_stdout(io.StringIO()):
            mod.zapis_katalog(path, parts, regions, mapa_baliky, man,
                              spravuje=mapove)
            # `wiki.yml`: `--only=wikipedia`, teda doplnenie jedného balíka
            mod.zapis_katalog(path, parts, regions,
                              [("wikipedia", "bratislavsky-wikipedia.zip",
                                1, "id4", "zip")], {}, iba="wikipedia")
            po_wiki = maps_v(path)
            # a teraz ďalší build mapy – o `wikipedia` nerozhoduje
            mod.zapis_katalog(path, parts, regions, mapa_baliky, man,
                              spravuje=mapove)
            po_mape = maps_v(path)
            # beh, ktorý o `wikipedia` ROZHODUJE a nemá ju (články sa vypli):
            # ten ju zmazať MUSÍ – na Drive sa starý balík maže tiež
            mod.zapis_katalog(path, parts, regions, mapa_baliky, man,
                              spravuje=mapove + ["wikipedia"])
            po_vypnuti = maps_v(path)
    if "wikipedia" not in po_wiki:
        chyby.append(f"{CATALOG_PY}: `--only=wikipedia` balík do položky "
                     f"nedoplní – pipeline článkov by nahrala ZIP na Drive "
                     f"a v katalógu by po ňom nezostalo nič.")
    elif "wikipedia" not in po_mape:
        chyby.append(f"{CATALOG_PY}: build mapy zmazal z položky balík "
                     f"`wikipedia`, o ktorom nerozhoduje (nemá ho v "
                     f"`spravuje`). Na Drive ten ZIP ostáva ležať, takže "
                     f"katalóg by o ňom mlčal po každej zmene štýlu.")
    if "wikipedia" in po_vypnuti:
        chyby.append(f"{CATALOG_PY}: balík, o ktorom beh ROZHODUJE a "
                     f"nevyrobil ho, ostal v katalógu – odkazoval by na "
                     f"súbor, ktorý ten istý beh na Drive zmazal.")
    return chyby


try:
    bad += _skuska_katalogu()
except SystemExit as exc:
    bad.append(f"{CATALOG_PY}: zápis katalógu spadol ({exc}) – skúška je "
               f"volanie, aké robí `publish-map.py`.")
except Exception as exc:                      # noqa: BLE001 – čokoľvek je chyba
    bad.append(f"{CATALOG_PY} sa nedá skúšobne spustiť ({exc!r}).")

for b in bad:
    print(f"::error::{b}")
print(f"katalóg máp: {len(bad)} chýb")
sys.exit(1 if bad else 0)

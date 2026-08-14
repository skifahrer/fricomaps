#!/usr/bin/env python3
"""
Katalóg máp `maps.json` – čo sa doň píše a kam.

PREČO ZVLÁŠŤ OD `publish-map.py`: ten odpovedá na „čo sa publikuje, ako sa to
volá a ako sa to nahrá na Drive"; toto na „čo o tom vie zoznam v repozitári".
Sú to dve otázky a súbor prerástol strop 800 riadkov práve tam, kde sa
stretli – rezalo sa teda tam, kde sa mení otázka. Vedľa leží `catalog.sh`:
ten ten istý súbor commitne, keď ho tento modul zapísal.

Volá to `publish-map.py` (a cezeň aj pipeline článkov z Wikipédie
s `--only=wikipedia`), samostatne to zmysel nemá.
"""
import importlib.util
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKERS = os.path.dirname(_HERE)
_DATA = os.path.join(_WORKERS, "data")     # číselníky (areas, regions)
_DRIVE = os.path.join(_WORKERS, "drive")


def _load(name, path):
    """workers/*.py sa kvôli pomlčke v mene nedajú `import`-núť normálne."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


folder = _load("drive_folder", os.path.join(_DRIVE, "folder.py"))


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def log(msg):
    print(msg, flush=True)


def rozdel_test(key):
    """`vysoke_tatry_test4km2` → (`vysoke_tatry`, `4`), inak `(key, "")`.

    Opak `cesta_katalog` v `publish-map.py`. Prípona je tá istá, akú nesú
    mená balíkov – aby sa uzol katalógu a súbor na Drive dali prečítať ako
    jedna vec.
    """
    cut = key.rfind("_test")
    if cut < 0 or not key.endswith("km2"):
        return key, ""
    stred = key[cut + 5:-3]
    return (key[:cut], stred) if stred.replace(".", "").isdigit() else (key, "")


def region_entry(man):
    """Položka regiónu z `manifest.json` – zoomy, bbox, zdroje výšok."""
    key = man.get("default_region")
    return ((man.get("regions") or {}).get(key) or {}) if key else {}


# ---------- katalóg máp v repozitári ----------
# `maps.json` je JEDINÝ zoznam toho, ktoré mapy sú hotové a kde ležia. Na Drive
# sa to inak nedá zistiť bez tokenu a bez klikania: priečinky sú tri úrovne
# hlboko a mená balíkov si človek nepamätá. Preto ho zapisuje ten, kto tie
# súbory práve nahral – vie ich id, veľkosť aj to, čo v nich je.
#
# ŠTRUKTÚRA SEDÍ S CESTOU NA DRIVE, a to zámerne: `krajina → kraj → výsek` je tá
# istá odpoveď na otázku „čoho sa tá mapa týka", akú dáva `cesta()`. Dve rôzne
# hierarchie tých istých máp by sa raz rozišli (pravidlo 1).
#
# KRAJINA JE HLAVNÝ KĽÚČ – rovno v koreni, bez obálky:
#
#   slovensko.regions.presovsky.maps                          celý kraj
#   slovensko.regions.presovsky.subregions.vysoke_tatry.maps  výsek
#
# Metadáta katalógu ležia vedľa nich a poznať ich je po čom: začínajú
# podčiarkovníkom (`_comment`, `_updated_at`). Je to tá istá konvencia ako
# vo `workers/data/areas.json`, kde sú kľúče pohorí tiež v koreni a `_comment`
# medzi nimi – kto katalóg číta, preskočí kľúče na `_`.
#
# ZÁPIS JE „NAHRAĎ CELÚ POLOŽKU". Keď mapa v zozname nie je, pridá sa; keď je,
# prepíše sa celá – vrátane balíkov, ktoré tento build nevyrobil, aby v nej
# nezostal odkaz na súbor, ktorý sa medzitým zmazal.

def katalog_meno(regions, key, kind):
    """Ľudské meno kraja/výseku/krajiny – z číselníkov, nie vymyslené.

    Uzol rýchleho testu má v kľúči príponu, ktorá v číselníkoch nie je –
    meno sa hľadá bez nej a to, že je to test, sa dopíše. Bez toho by
    v katalógu stálo holé `vysoke_tatry_test4km2` a vyzeralo by to ako
    ďalšie pohorie.
    """
    key, test = rozdel_test(key)
    chvost = f" – rýchly test {test} km²" if test else ""
    if kind == "area":
        try:
            with open(os.path.join(_DATA, "areas.json")) as f:
                meno = (json.load(f).get(key) or {}).get("name") or key
        except (OSError, ValueError):
            meno = key
        return meno + chvost
    r = regions.get(key) or {}
    return (r.get("name") or key) + chvost


def zapis_balik(mapy, kind, name, velkost, fid, fmt, kedy=""):
    """Jeden balík v jednom formáte do `maps` položky katalógu.

    JEDNO MIESTO PRE OBE CESTY. Zapisujú sa tu dve vetvy – celý build
    (nahradenie položky) aj samostatná pipeline (`--only`, doplnenie jedného
    balíka) – a keby si každá skladala ten zápis sama, raz sa rozídu: jedna by
    vedela o formátoch a druhá nie.

    `formats` je to podstatné; `file`/`size`/`link`/`download` na úrovni
    balíka ukazujú na ZIP, aby starší čitateľ katalógu nemusel o formátoch
    vedieť. `.aar` ich preto NEPREPISUJE – prepísal by odkaz, ktorý sa
    doteraz čítal ako „stiahni mapu".
    """
    zaznam = {
        "file": name,
        "size": velkost,
        "link": folder.file_link(fid),
        "download": folder.download_link(fid),
        # Z KTORÉHO BEHU je práve TENTO balík. Články z Wikipédie robí iná
        # pipeline než mapu, takže `run` pri kraji (= beh, čo vyrobil mapu)
        # o nich nič nepovie – a naopak: keby si ho tá pipeline prepísala na
        # svoj, katalóg by tvrdil, že mapu vyrobil beh, ktorý stiahol len
        # články. Beh 1 workflowu „Wikipédia k mape" tak v katalógu prepísal
        # beh 110 Build map. Preto je pôvod pri balíku, nie len pri kraji.
        "run": env("GITHUB_RUN_NUMBER"),
    }
    if kedy:
        zaznam["updated_at"] = kedy
    polozka = mapy.setdefault(kind or "mapa", {})
    polozka.setdefault("formats", {})[fmt] = zaznam
    if fmt == "zip":
        polozka.update(zaznam)


def zapis(path, data, popis):
    """Zapíše katalóg, keď sa naozaj zmenil. True = súbor je iný."""
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with open(path) as f:
            if f.read() == text:
                log(f"{path}: to isté ako doteraz – bez zmeny.")
                return False
    except OSError:
        pass
    with open(path, "w") as f:
        f.write(text)
    log(f"{path}: {popis}")
    return True


def zapis_katalog(path, parts, regions, baliky, man, iba="", merge=False,
                  kat=None, layers=None):
    """Doplň (alebo prepíš) položku v `maps.json`. Vracia True, keď sa zmenil.

    `baliky` je zoznam `(druh, meno, veľkosť, id, formát)` – to, čo sa naozaj
    nahralo.

    `kat` je cesta v KATALÓGU, keď sa líši od cesty na Drive (`parts`) –
    presne to je prípad rýchleho testu (viď `cesta_katalog`). `drive` v
    položke sa preto píše z `parts`: uzol je iný, priečinok ten istý.

    `layers` sú tie isté kúsky, aké nesie meno balíka (`vrstvy()` v
    `publish-map.py`) – podávajú sa, aby o tom, čo v mape je, nerozhodovali
    dve miesta.

    `merge=True` znamená „tento beh nahral LEN ĎALŠÍ FORMÁT toho istého, čo
    tam už je" – vtedy sa balíky doplnia k existujúcim namiesto nahradenia.
    Tak to má job na macOS, ktorý dobalí `.aar` po tom, čo `deploy` nahral
    ZIPy: keby prepisoval, katalóg by o ZIPoch prestal vedieť. Pri bežnom
    behu (`merge=False`) sa naopak MUSÍ nahradiť – inak by v katalógu ostali
    odkazy na balíky, ktoré tento build nevyrobil.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault("_comment",
                    "Katalóg hotových máp na Google Drive – ktoré sú a kde. "
                    "Hlavný kľúč je krajina, pod ňou `regions` (kraj) a "
                    "`subregions` (výsek); kľúče na `_` sú metadáta katalógu, "
                    "nie krajiny. Dopisuje ho na konci buildu "
                    "workers/deploy/publish-map.py (krok „Zapíš mapu do "
                    "maps.json“); ručne sa needituje. Odkazy otvorí ten, kto "
                    "má prístup k priečinku s mapami.")
    data["_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    kat = kat or parts
    krajina = data.setdefault(kat[0], {})
    krajina.setdefault("name", katalog_meno(regions, kat[0], "region"))
    uzol = krajina                      # build celej krajiny končí tu
    if len(kat) > 1:
        regs = krajina.setdefault("regions", {})
        uzol = regs.setdefault(kat[1], {})
        uzol.setdefault("name", katalog_meno(regions, kat[1], "region"))
    if len(kat) > 2:
        subs = uzol.setdefault("subregions", {})
        uzol = subs.setdefault(kat[2], {})
        uzol.setdefault("name", katalog_meno(regions, kat[2], "area"))

    reg = region_entry(man)
    if iba:
        # DOPLNENIE, NIE PREPIS. Samostatná pipeline vie len o svojom balíku;
        # keby prepísala celú položku, zmazala by odkazy na mapu, zoomy aj
        # zdroje výšok, o ktorých nič nevie – a v katalógu by po nich nezostalo
        # nič. Preto sa mení len ten jeden balík a čas.
        uzol.setdefault("name", katalog_meno(regions, kat[-1], "region"))
        uzol.setdefault("drive", "/".join(parts))
        # `updated_at` a `run` pri kraji hovoria, KTORÝ BEH VYROBIL MAPU – to
        # táto pipeline nespravila a nesmie si to prisvojiť (`setdefault` je
        # tu na prvý zápis, keď mapa v katalógu ešte nie je). Kedy pribudol
        # tento balík, nesie balík sám (viď `zapis_balik`).
        uzol.setdefault("updated_at", data["_updated_at"])
        uzol.setdefault("run", env("GITHUB_RUN_NUMBER"))
        mapy = uzol.setdefault("maps", {})
        for kind, name, velkost, fid, fmt in baliky:
            zapis_balik(mapy, kind, name, velkost, fid, fmt,
                        kedy=data["_updated_at"])
        return zapis(path, data,
                     f"doplnený balík {iba} k {'/'.join(kat)}")
    polozka = {
        "name": uzol.get("name"),
        "drive": "/".join(parts),
        "updated_at": data["_updated_at"],
        "run": env("GITHUB_RUN_NUMBER"),
        "layers": list(layers or []),
        # Balík × formát. `formats` je to podstatné (ZIP otvorí čokoľvek,
        # `.aar` rozbalí iOS a macOS systémovo); `file`/`size`/`link` na
        # úrovni balíka ostávajú a ukazujú na ZIP, aby starší čitateľ
        # katalógu nemusel vedieť o formátoch.
        "maps": {},
    }
    # Čo o mape treba vedieť pri výbere, nie až po rozbalení. Zoomy a zdroje
    # nesie manifest, tak sa berú z neho. `bbox` je bbox MAPY, teda celého
    # regiónu – aj pri builde na výrez, lebo mapa je celý región a orezané sú
    # len vrstvy z výškového modelu. Práve preto je pri výreze vedľa neho aj
    # `area_bbox`: to je to, kde v tej mape vrstevnice a skaly naozaj sú.
    for k in ("bbox", "maxzoom", "contours_maxzoom", "contour_interval",
              "rocks_maxzoom", "rock_slope", "dem_source"):
        if reg.get(k) is not None:
            polozka[k] = reg[k]
    area_bbox = env("AREA_BBOX")
    test_km2 = env("TEST_KM2", "0")
    if test_km2 not in ("", "0"):
        # Pri teste to je to najdôležitejšie číslo v celej položke: mapa je
        # celý kraj, ale vrstevnice, skaly a tieňovanie sú len v tom štvorci.
        # Preto sa píše aj vtedy, keď sa nebeží na výrez – vtedy `area_bbox`
        # nižšie nesie práve ten testovací štvorec.
        polozka["test_km2"] = test_km2
    if area_bbox and (len(parts) > 2 or test_km2 not in ("", "0")):
        try:
            polozka["area_bbox"] = [float(v) for v in area_bbox.split(",")]
        except ValueError:
            pass
    stare_maps = uzol.get("maps") or {}
    polozka["maps"] = {k: dict(v) for k, v in stare_maps.items()} if merge else {}
    if merge:
        # ČO TENTO BEH NEVIE, TO NESMIE ZMAZAŤ. Položka sa zapisuje celá
        # (`uzol.clear()`), takže beh, ktorý pridáva len ďalší formát, by
        # z katalógu vyhodil všetko, čo sám nedopočítal – bbox, zoomy, zdroj
        # výšok. Tie sa berú z manifestu a ten sem chodí z iného jobu, takže
        # stačí, aby raz nedorazil. Pri `merge` je preto východiskom to, čo
        # v katalógu už je, a nové hodnoty idú navrch.
        zaklad = {k: v for k, v in uzol.items()
                  if k not in ("maps", "regions", "subregions")}
        zaklad.update(polozka)
        polozka = zaklad
    for kind, name, velkost, fid, fmt in baliky:
        zapis_balik(polozka["maps"], kind, name, velkost, fid, fmt,
                    kedy=data["_updated_at"])

    # `subregions` patria uzlu, nie tejto mape – nahradenie položky ich nesmie
    # zmazať (build Vysokých Tatier neruší mapu celého kraja a naopak).
    zachovaj = {k: uzol[k] for k in ("regions", "subregions") if k in uzol}
    uzol.clear()
    uzol.update(polozka)
    uzol.update(zachovaj)

    return zapis(path, data, f"zapísaná mapa {'/'.join(kat)} "
                             f"({len(polozka['maps'])} balíkov, "
                             f"priečinok {'/'.join(parts)})")

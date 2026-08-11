#!/usr/bin/env python3
"""
Hotová mapa na Google Drive – TRI ZIPy so stálym menom.

ČO TO ROBÍ. Z `_site` (celý web: dlaždice, štýly, vrstevnice, skaly,
tieňovanie, fonty a sprity) sa zabalia tri balíky a nahrajú na Drive do
priečinka podľa toho, čoho sa mapa týka:

    <koreň>/slovensko/presovsky/vysoke_tatry/
        presovsky-vysoke_tatry.zip                    celá mapa
        presovsky-vysoke_tatry-vrstevnice-skaly.zip   len tie dve vrstvy
        presovsky-vysoke_tatry-tienovanie.zip         len výškové dlaždice

Úrovne cesty, ktoré nedávajú zmysel, sa vynechajú: build celej krajiny nemá
kraj a build celého kraja nemá výsek. Chýbajúce priečinky sa vyrobia.

MENO JE STÁLE – rovnaký kraj (a rovnaký výsek) má vždy to isté meno, takže
ďalší build starý balík PREPÍŠE a v priečinku je jeden aktuálny súbor namiesto
histórie behov. Poradie je „najprv nahraj, potom zmaž starý" (`folder.
upload_clobber`): Drive dovolí dva súbory s tým istým menom vedľa seba, takže
„najprv zmaž" by po spadnutom nahrávaní nenechalo ani nové, ani staré.

ČO V TOM BALÍKU JE, HOVORÍ `obsah.json` V ŇOM. Kým bolo meno jedinečné, nieslo
zoom, vrstvy a ich zdroje (`…-z16-vrstevnice_dmr5_5m-skaly_dmr5-…-r73.zip`);
stále meno to nesie ako súbor vnútri – dátum, číslo behu, výrez, zoomy, zdroje
výšok, prah sklonu. Vrstva, ktorá v mape NIE JE, je tam napísaná tiež
(`bez_skal`): mlčanie sa dá čítať aj ako „zabudlo sa to dopísať".

BALÍK VRSTVY, KTORÚ TENTO BUILD NEVYROBIL, SA ZMAŽE. Inak by vedľa novej mapy
ostal starý `-tienovanie.zip` z iného behu a na súbore by to nikto nepoznal –
tá istá trieda tichého omylu ako dlaždica, ktorá sľubuje celý stupeň.

TESTOVACÍ BEH TO MUSÍ POVEDAŤ. Rýchly test počíta terén len na pár km² zo
stredu výrezu; mapa z neho vyzerá ako každá iná, len jej väčšina chýba. V mene
je preto `test4km2` – a je to nutné dvakrát: aby sa nedalo pomýliť, a aby test
NEPREPÍSAL ostrú mapu (meno je sľub o rozsahu, ako pri assetoch DEM).

Použitie (hodnoty berie z prostredia, tak ako ostatné workery):
    REGION_KEY=presovsky AREA_KEY=vysoke_tatry TILES_MAXZOOM=16 \\
        python3 workers/deploy/publish-map.py --site=_site
    python3 workers/deploy/publish-map.py --site=_site --dry-run   # len mená a cesta
"""
import argparse
import importlib.util
import json
import os
import sys
import time
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
# Priečinok = job, súbor = krok; spoločné veci ležia o úroveň vyššie.
_WORKERS = os.path.dirname(_HERE)          # workers/
_DATA = os.path.join(_WORKERS, "data")     # číselníky (areas, regions, zdroje)
_DRIVE = os.path.join(_WORKERS, "drive")   # prihlásenie a nahrávanie na Drive


def load(name, path):
    """workers/*.py sa kvôli pomlčke v mene nedajú `import`-núť normálne."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


auth = load("drive_auth", os.path.join(_DRIVE, "auth.py"))        # kto sme na Drive
folder = load("drive_folder", os.path.join(_DRIVE, "folder.py"))  # priečinky a nahrávanie

# Priečinok, do ktorého sa mapy publikujú. Ako pri DMR 5.0 a cache platí, že
# tajomstvo to nie je – id chodí v zdieľanom odkaze; tajomstvom je token.
FOLDER_ID = "1pvrw7CGUkQLwg8Ql8xbKA4HhQHvPl8_7"

# Balí sa `deflate` na najnižší stupeň. Obsah `_site` je z veľkej časti už
# komprimovaný (PMTiles nesú gzip-nuté dlaždice, tieňovanie sú PNG), takže
# vyšší stupeň stojí minúty a ušetrí percentá.
ZIP_LEVEL = 1


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def log(msg):
    print(msg, flush=True)


def safe(text):
    """Kus mena súboru: bez diakritiky, medzier a lomítok."""
    prevod = {"á": "a", "ä": "a", "č": "c", "ď": "d", "é": "e", "í": "i",
              "ĺ": "l", "ľ": "l", "ň": "n", "ó": "o", "ô": "o", "ŕ": "r",
              "š": "s", "ť": "t", "ú": "u", "ý": "y", "ž": "z"}
    out = []
    for ch in text.strip().lower():
        ch = prevod.get(ch, ch)
        out.append(ch if (ch.isalnum() and ch.isascii()) or ch in "._-" else "_")
    return "".join(out).strip("_") or "bez_mena"


def bez_testu(key):
    """`presovsky_test4` → `presovsky`.

    Kľúč výrezu aj regiónu nesie pri rýchlom teste príponu `_test<N>`, aby si
    testovací výsledok nesadol na miesto ostrého. Do CESTY ale patrí to
    pohorie, o ktoré ide – že je to test, povie meno súboru.
    """
    base = key
    while True:
        cut = base.rfind("_test")
        if cut < 0 or not base[cut + 5:].replace(".", "").isdigit():
            return base
        base = base[:cut]


# ---------- kam to patrí ----------

def krajina_z_url(url):
    """Krajina z odkazu na osm.fr export.

    `…/extracts/europe/austria/tirol-latest.osm.pbf` → `austria`. Vlastný PBF
    je jediný prípad, keď región nie je v `workers/data/regions.json`, takže sa
    krajina nemá odkiaľ inak dozvedieť. Keď sa z odkazu vyčítať nedá, ide to
    do `ostatne` – nie do `slovensko`, kde nepatrí.
    """
    cesta = url.split("/extracts/", 1)[-1] if "/extracts/" in url else url
    kusy = [k for k in cesta.split("/") if k]
    if len(kusy) >= 2:
        return safe(kusy[-2])
    return "ostatne"


def cesta(regions):
    """Priečinky pod koreňom: [krajina, kraj?, výsek?]."""
    region_key = bez_testu(env("REGION_KEY"))
    custom_url = env("CUSTOM_PBF_URL")
    area_key = bez_testu(env("AREA_KEY"))

    if custom_url:
        # Vlastný PBF: v `regions.json` nie je, kraj je to, čo si človek
        # pomenoval sám (alebo slug z odkazu).
        kraj = safe(env("CUSTOM_NAME") or region_key
                    or custom_url.rsplit("/", 1)[-1].split(".")[0])
        parts = [krajina_z_url(custom_url), kraj]
    else:
        r = regions.get(region_key) or {}
        krajina = safe(r.get("country") or region_key or "ostatne")
        parts = [krajina]
        # Celá krajina nemá nadradený kraj – `admin_level` 2 je štát.
        if r.get("admin_level") != 2 and region_key:
            parts.append(safe(region_key))
    # `cely` znamená „celý región", teda žiadny výrez – vlastnú úroveň
    # nedostane, inak by v každom kraji ležal priečinok `cely`.
    if area_key and area_key != "cely":
        parts.append(safe(area_key))
    return parts


# ---------- ako sa to volá ----------

def vrstvy():
    """Kúsky mena, ktoré hovoria, čo je v mape a z čoho.

    Vrstva sa do mena zapíše aj vtedy, keď v mape NIE JE (`bez_vrstevnic`).
    Mlčanie by sa dalo čítať dvoma spôsobmi – „nie sú" aj „zabudlo sa to
    dopísať" – a to je presne ten rozdiel, kvôli ktorému sa mená píšu.
    """
    out = []
    if env("CONTOURS_ENABLED") == "true":
        interval = env("CONTOUR_INTERVAL", "10")
        out.append(f"vrstevnice_{safe(env('CONTOURS_SOURCE', '?'))}_{safe(interval)}m")
    else:
        out.append("bez_vrstevnic")

    if env("ROCKS_ENABLED") == "true":
        out.append(f"skaly_{safe(env('ROCKS_SOURCE', '?'))}")
    else:
        out.append("bez_skal")

    if env("TERRAIN_ENABLED") == "true":
        out.append(f"tienovanie_{safe(env('TERRAIN_SOURCE', '?'))}")
    else:
        out.append("bez_tienovania")

    # Trasy a prvky sa píšu, len keď sú – nie sú to vrstvy z výškového modelu
    # a meno by bez toho narástlo o dve „bez_" na každom behu.
    if env("TRAILS_ENABLED") == "true":
        out.append("trasy")
    if env("FEATURES_ENABLED") == "true":
        out.append("prvky")
    return out


def zaklad():
    """Stále meno bez prípony: `<kraj>[-<výsek>][-testNkm2]`.

    Zoom, vrstvy, ich zdroje, dátum ani číslo behu v ňom NIE SÚ – práve preto
    je stále a ďalší build ten istý súbor prepíše. Všetko to nesie `obsah.json`
    vnútri balíka.
    """
    region = bez_testu(env("REGION_KEY")) or "mapa"
    area = bez_testu(env("AREA_KEY"))
    kusy = [safe(region)]
    if area and area != "cely":
        kusy.append(safe(area))
    test_km2 = env("TEST_KM2", "0")
    if test_km2 not in ("", "0"):
        # Rýchly test má terén len na pár km². Bez tohto by mapa vyzerala ako
        # ostrá, chýbala by jej väčšina – a PREPÍSALA by tú ostrú.
        kusy.append(f"test{safe(test_km2)}km2")
    return "-".join(kusy)


def meno(kind=""):
    """Meno ZIPu jedného balíka: základ + druh (`` = celá mapa)."""
    return zaklad() + (f"-{kind}" if kind else "") + ".zip"


# ---------- čo je v ktorom balíku ----------
# Tri balíky, lebo sa aj inak používajú: celú mapu si človek rozbalí a otvorí,
# vrstevnice so skalami sú to, čo sa nosí do inej mapy, a tieňovanie je jedna
# pyramída PNG. Vrstevnice a skaly sú SPOLU zámerne – sú z toho istého výpočtu
# nad tým istým DEM a jedna bez druhej sa nepoužíva.

def manifest_data(site):
    """`_site/tiles/manifest.json` – jediné miesto, ktoré vie, čo v mape je.

    Berie sa odtiaľ, a nie z ďalších premenných prostredia: manifest skladá
    `workers/deploy/site.sh` a vrstva, ktorá v ňom nie je, v mape nie je
    (pravidlo 1 – jedna otázka, jedna odpoveď, jedno miesto).
    """
    path = os.path.join(site, "tiles", "manifest.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        log(f"::warning::{path} sa nedá prečítať ({exc}) – balíky vrstiev sa "
            f"skladajú podľa mien súborov v `_site`.")
        return {}


def region_entry(man):
    key = man.get("default_region")
    return ((man.get("regions") or {}).get(key) or {}) if key else {}


def vrstvy_subory(site, man):
    """Súbory balíka `vrstevnice-skaly` – z manifestu, inak podľa mena."""
    reg = region_entry(man)
    rel = [reg[k] for k in ("contours", "rocks") if reg.get(k)]
    if not rel:
        tiles = os.path.join(site, "tiles")
        rel = [os.path.join("tiles", n) for n in sorted(os.listdir(tiles))
               if n.endswith(("-contours.pmtiles", "-rocks.pmtiles"))] \
            if os.path.isdir(tiles) else []
    return [os.path.join(site, p) for p in rel
            if os.path.exists(os.path.join(site, p))]


def tienovanie_subory(site, man):
    """Súbory balíka `tienovanie` – pyramída výškových PNG dlaždíc.

    Balí sa len vlastná pyramída v `_site/terrain`. Keď sa tieňovanie
    nevyrobilo, štýl padá na cudzie dlaždice (AWS Terrain Tiles) a tie do
    nášho balíka nepatria – nie sú naše a nie sú v `_site`.
    """
    base = os.path.join(site, "terrain")
    out = []
    for root, _dirs, names in os.walk(base):
        out += [os.path.join(root, n) for n in names]
    return out


def obsah(kind, man):
    """`obsah.json` do balíka – to, čo kedysi nieslo meno súboru."""
    reg = region_entry(man)
    return {
        "balik": kind or "mapa",
        "subor": meno(kind),
        "region": bez_testu(env("REGION_KEY")),
        "vyrez": bez_testu(env("AREA_KEY")) or "cely",
        "test_km2": env("TEST_KM2", "0"),
        "tiles_maxzoom": env("TILES_MAXZOOM"),
        "vrstvy": vrstvy(),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run": env("GITHUB_RUN_NUMBER"),
        "run_id": env("GITHUB_RUN_ID"),
        # Čo o mape hovorí manifest: bbox, zoomy vrstiev, zdroje výšok, prah
        # sklonu, testovací štvorec. Neopisuje sa – kopíruje sa.
        "manifest": {"dem": man.get("dem"),
                     "dem_maxzoom": man.get("dem_maxzoom"),
                     "dem_source": man.get("dem_source"),
                     "region": reg},
    }


# ---------- balenie ----------

def vsetky_subory(site):
    subory = []
    for root, _dirs, names in os.walk(site):
        for n in names:
            subory.append(os.path.join(root, n))
    return subory


def zabal(site, dest, koren, subory, info=None):
    """Súbory → jeden ZIP, v ktorom je všetko pod priečinkom `koren`.

    Ten priečinok navyše je zámerný: rozbalenie do stiahnutých súborov inak
    vysype dvadsať položiek do `~/Downloads`. Volá sa rovnako ako ZIP, takže
    je po rozbalení vidieť, ktorá mapa to je. Cesty vnútri ostávajú tie z
    `_site`, takže `-vrstevnice-skaly.zip` má dlaždice v `tiles/` – rovnako
    ako celá mapa, nech sa dá rozbaliť jeden cez druhý.
    """
    surovo = sum(os.path.getsize(p) for p in subory)
    log(f"Balím {len(subory)} súborov ({folder.human(surovo)}) do {dest}")

    t0 = time.time()
    hotovo = 0
    posledny = t0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=ZIP_LEVEL) as z:
        if info is not None:
            # Čo kedysi nieslo meno súboru. Ide dovnútra ako prvý, nech je po
            # rozbalení hneď na očiach.
            z.writestr(os.path.join(koren, "obsah.json"),
                       json.dumps(info, ensure_ascii=False, indent=2) + "\n")
        for p in sorted(subory):
            z.write(p, os.path.join(koren, os.path.relpath(p, site)))
            hotovo += os.path.getsize(p)
            # Tep raz za pol minúty: `_site` má aj gigabajt a ticho v logu sa
            # nedá odlíšiť od zaseknutého kroku.
            if time.time() - posledny >= 30:
                posledny = time.time()
                log(f"  [{(posledny - t0) / 60:.1f} min] "
                    f"{folder.human(hotovo)} z {folder.human(surovo)}")
    velkost = os.path.getsize(dest)
    log(f"  hotovo za {time.time() - t0:.0f} s: {folder.human(velkost)} "
        f"({velkost * 100 // max(surovo, 1)} % pôvodnej veľkosti)")
    return velkost


# ---------- beh ----------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="_site", help="čo sa balí")
    ap.add_argument("--folder", default=FOLDER_ID,
                    help="priečinok na Drive (URL alebo id)")
    ap.add_argument("--out", default="", help="kam odložiť ZIP (default RUNNER_TEMP)")
    ap.add_argument("--keep-zip", action="store_true",
                    help="nechať ZIP na disku aj po nahratí")
    ap.add_argument("--dry-run", action="store_true",
                    help="povedz mená a cestu, ale nič nebaľ ani nenahrávaj")
    ap.add_argument("--zip-only", action="store_true",
                    help="zabaľ do --out a na Drive nesiahaj (lokálna skúška)")
    ap.add_argument("--summary", default="", help="kam dopísať súhrn")
    args = ap.parse_args()

    with open(os.path.join(_DATA, "regions.json")) as f:
        regions = json.load(f)

    parts = cesta(regions)
    man = manifest_data(args.site)
    # Tri balíky v jednom zozname: druh, čo do neho patrí, a popis do logu.
    # Zoznam preto, že sa s nimi robí to isté – zabaliť, nahrať, prepísať
    # starý – a tri kópie toho istého by sa raz rozišli.
    baliky = [
        ("", "celá mapa – web tak, ako sa nasadil",
         vsetky_subory(args.site)),
        ("vrstevnice-skaly", "vrstevnice a skalné plochy (.pmtiles)",
         vrstvy_subory(args.site, man)),
        ("tienovanie", "výškové dlaždice pre tieňovanie a 3D terén (PNG)",
         tienovanie_subory(args.site, man)),
    ]
    if not baliky[0][2]:
        raise SystemExit(f"::error::V {args.site} nie je ani jeden súbor – nie je "
                         f"čo publikovať. (Zbehol job `deploy` až po zloženie "
                         f"webu?)")
    for kind, popis, subory in baliky:
        stav = (f"{len(subory)} súborov, "
                f"{folder.human(sum(os.path.getsize(p) for p in subory))}"
                if subory else "NIE JE V TOMTO BUILDE – starý balík sa zmaže")
        log(f"  {meno(kind):<48} {popis} – {stav}")
    log(f"Priečinok na Drive: {'/'.join(parts)}")
    if args.dry_run:
        return 0

    if args.zip_only:
        # Lokálna skúška: to isté balenie, len bez Drive – nech sa dá pozrieť,
        # čo v balíkoch je, bez tokenu a bez nahrávania.
        out = args.out or os.environ.get("RUNNER_TEMP", "/tmp")
        for kind, popis, subory in baliky:
            if not subory:
                log(f"{meno(kind)}: {popis} v tomto builde nie je – vynechávam.")
                continue
            dest = os.path.join(out, meno(kind))
            zabal(args.site, dest, meno(kind)[:-4], subory,
                  info=obsah(kind, man))
        return 0

    creds = auth.from_env()
    if creds is None:
        raise SystemExit(
            "::error::Publikovanie mapy na Drive potrebuje token vlastníka, "
            "ale v prostredí nie je. Doplň secret GDRIVE_CREDENTIALS (alebo "
            "premennú DRIVE_CLIENT a secrety DRIVE_SECRET / DRIVE_REFRESH) "
            "a podaj ho jobu cez `env:` – vyrobí ich workflow „Prihlásenie "
            "na Drive (jednorazové)“.")
    # Rozsah PRED balením: readonly token nič nenahrá, tak nech sa kvôli nemu
    # nebalí gigabajt. Tá istá lacná otázka ako pri cache.
    if auth.can_write(creds) is False:
        raise SystemExit(f"::error::Mapa sa nepublikovala: {auth.scope_hint()}")

    root = folder.folder_id(args.folder)
    fid = folder.ensure_path(creds, root, parts)
    hotove = []
    for kind, popis, subory in baliky:
        name = meno(kind)
        if not subory:
            # Vrstva v tomto builde nie je. Starý balík toho istého mena by
            # vedľa novej mapy tvrdil, že je – a na súbore by to nikto
            # nepoznal.
            kolko = folder.delete_named(creds, fid, name)
            if kolko:
                log(f"::warning::{popis} v tomto builde nie je – zmazal som "
                    f"{kolko}× starý {name}, aby v priečinku nezostal balík "
                    f"z iného behu.")
            else:
                log(f"{name}: {popis} v tomto builde nie je – nepublikujem.")
            continue
        dest = os.path.join(args.out or os.environ.get("RUNNER_TEMP", "/tmp"),
                            name)
        velkost = zabal(args.site, dest, name[:-4], subory,
                        info=obsah(kind, man))
        try:
            log(f"Nahrávam {name} ({folder.human(velkost)}) …")
            t0 = time.time()
            prepisane = folder.upload_clobber(
                creds, dest, name, fid, f"{'/'.join(parts)}/{name}")
            el = max(time.time() - t0, 1e-6)
            log(f"  hotovo za {el / 60:.1f} min "
                f"({velkost / el / 1e6:.1f} MB/s)"
                + (" – starý súbor toho mena prepísaný" if prepisane else ""))
        finally:
            if not args.keep_zip and os.path.exists(dest):
                os.remove(dest)
        hotove.append((name, popis, velkost, prepisane))
    log(f"Hotovo: {len(hotove)} balíkov v {folder.folder_link(fid)}")

    if args.summary:
        with open(args.summary, "a") as f:
            f.write("## Mapa na Google Drive\n\n")
            f.write(f"Priečinok [{'/'.join(parts)}]({folder.folder_link(fid)}) – "
                    f"mená sú stále, takže ďalší build tie isté súbory prepíše. "
                    f"Čo je v balíku, hovorí `obsah.json` v ňom.\n\n")
            f.write("| balík | čo je v ňom | veľkosť | starý |\n|---|---|--:|---|\n")
            for name, popis, velkost, prepisane in hotove:
                f.write(f"| `{name}` | {popis} | {folder.human(velkost)} | "
                        f"{'prepísaný' if prepisane else '–'} |\n")
            f.write("\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except auth.AuthError as exc:
        # Text tých hlášok už nesie, čo s nimi.
        print(f"::error::{exc}")
        sys.exit(1)
    except RuntimeError as exc:
        print(f"::error::{exc}")
        sys.exit(1)

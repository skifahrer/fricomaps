#!/usr/bin/env python3
"""
Hotová mapa ako ZIP do priečinka na Google Drive.

ČO TO ROBÍ. Vezme `_site` (celý web: dlaždice, štýly, vrstevnice, skaly,
tieňovanie, fonty a sprity), zabalí ho do jedného ZIPu a nahrá na Drive do
priečinka podľa toho, čoho sa mapa týka:

    <koreň>/slovensko/presovsky/vysoke_tatry/<mapa>.zip
             krajina  kraj      výsek

Úrovne, ktoré nedávajú zmysel, sa vynechajú: build celej krajiny nemá kraj
a build celého kraja nemá výsek. Chýbajúce priečinky sa vyrobia.

MENO NESIE, ČO V TEJ MAPE JE – a to je celý zmysel. Do jedného priečinka
padajú desiatky behov s rôznymi nastaveniami a „mapa.zip" o žiadnom z nich
nehovorí nič:

    presovsky-vysoke_tatry-z16-vrstevnice_dmr5_10m-skaly_dmr5-
    tienovanie_sonny-trasy-prvky-20260810-0748-r73.zip

Je v ňom výrez, zoom dlaždíc, KTORÉ vrstvy sú vnútri a Z ČOHO sú spočítané,
dátum, čas a číslo behu. Posledné tri robia meno jedinečným, takže sa dva behy
nikdy neprepíšu.

TESTOVACÍ BEH TO MUSÍ POVEDAŤ. Rýchly test počíta terén len na pár km² zo
stredu výrezu; mapa z neho vyzerá ako každá iná, len jej väčšina chýba. V mene
je preto `test2km2` – to isté pravidlo ako pri assetoch výškového modelu
(meno je sľub o rozsahu, viď `docs/pipeline.md`).

Použitie (hodnoty berie z prostredia, tak ako ostatné workery):
    REGION_KEY=presovsky AREA_KEY=vysoke_tatry TILES_MAXZOOM=16 \\
        python3 workers/publish-map.py --site=_site
    python3 workers/publish-map.py --site=_site --dry-run   # len povie meno a cestu
"""
import argparse
import importlib.util
import json
import os
import sys
import time
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, path):
    """workers/*.py sa kvôli pomlčke v mene nedajú `import`-núť normálne."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


auth = load("drive_auth", "drive-auth.py")        # kto sme na Drive
folder = load("drive_folder", "drive-folder.py")  # priečinky a nahrávanie

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
    """`presovsky_test2` → `presovsky`.

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
    je jediný prípad, keď región nie je v `workers/regions.json`, takže sa
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


def meno():
    """Meno ZIPu. Jedinečné (dátum, čas a číslo behu) a hovoriace."""
    region = bez_testu(env("REGION_KEY")) or "mapa"
    area = bez_testu(env("AREA_KEY"))
    kusy = [safe(region)]
    if area and area != "cely":
        kusy.append(safe(area))
    test_km2 = env("TEST_KM2", "0")
    if test_km2 not in ("", "0"):
        # Rýchly test má terén len na pár km². Bez tohto by mapa vyzerala ako
        # ostrá a chýbala by jej väčšina.
        kusy.append(f"test{safe(test_km2)}km2")
    kusy.append("z" + safe(env("TILES_MAXZOOM", "?")))
    kusy += vrstvy()
    kusy.append(time.strftime("%Y%m%d-%H%M", time.gmtime()))
    run = env("GITHUB_RUN_NUMBER")
    if run:
        kusy.append("r" + safe(run))
    return "-".join(kusy) + ".zip"


# ---------- balenie ----------

def zabal(site, dest, koren):
    """`_site` → jeden ZIP, v ktorom je všetko pod priečinkom `koren`.

    Ten priečinok navyše je zámerný: rozbalenie do stiahnutých súborov inak
    vysype dvadsať položiek do `~/Downloads`. Volá sa rovnako ako ZIP, takže
    je po rozbalení vidieť, ktorá mapa to je.
    """
    subory = []
    for root, _dirs, names in os.walk(site):
        for n in names:
            subory.append(os.path.join(root, n))
    if not subory:
        raise SystemExit(f"::error::V {site} nie je ani jeden súbor – nie je čo "
                         f"publikovať. (Zbehol job `deploy` až po zloženie webu?)")
    surovo = sum(os.path.getsize(p) for p in subory)
    log(f"Balím {len(subory)} súborov ({folder.human(surovo)}) do {dest}")

    t0 = time.time()
    hotovo = 0
    posledny = t0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=ZIP_LEVEL) as z:
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
                    help="povedz meno a cestu, ale nič nebaľ ani nenahrávaj")
    ap.add_argument("--summary", default="", help="kam dopísať súhrn")
    args = ap.parse_args()

    with open(os.path.join(_HERE, "regions.json")) as f:
        regions = json.load(f)

    parts = cesta(regions)
    name = meno()
    log(f"Mapa sa publikuje ako:  {'/'.join(parts)}/{name}")
    if args.dry_run:
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

    dest = os.path.join(args.out or os.environ.get("RUNNER_TEMP", "/tmp"), name)
    velkost = zabal(args.site, dest, name[:-4])
    try:
        root = folder.folder_id(args.folder)
        log(f"Priečinok na Drive: {'/'.join(parts)}")
        fid = folder.ensure_path(creds, root, parts)
        log(f"Nahrávam {folder.human(velkost)} …")
        t0 = time.time()
        folder.upload(creds, dest, name, fid,
                      f"{'/'.join(parts)}/{name}")
        el = max(time.time() - t0, 1e-6)
        log(f"Hotovo za {el / 60:.1f} min ({velkost / el / 1e6:.1f} MB/s): "
            f"{folder.folder_link(fid)}")
    finally:
        if not args.keep_zip and os.path.exists(dest):
            os.remove(dest)

    if args.summary:
        with open(args.summary, "a") as f:
            f.write("## Mapa na Google Drive\n\n")
            f.write("| vec | hodnota |\n|---|---|\n")
            f.write(f"| priečinok | [{'/'.join(parts)}]({folder.folder_link(fid)}) |\n")
            f.write(f"| súbor | `{name}` |\n")
            f.write(f"| veľkosť | {folder.human(velkost)} |\n\n")
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

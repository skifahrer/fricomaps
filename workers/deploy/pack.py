#!/usr/bin/env python3
"""
Ako sa balík zabalí – ZIP a Apple Archive.

PREČO ZVLÁŠŤ OD `publish-map.py`: ten odpovedá na „čo sa publikuje, ako sa to
volá a čo o tom povieme katalógu"; toto na „ako sa z priečinka stane jeden
súbor". Sú to dve otázky a súbor prerástol strop 800 riadkov práve tam, kde
sa stretli. Rezalo sa teda tam, kde sa mení otázka, nie v polovici.

DVA FORMÁTY, TEN ISTÝ OBSAH. ZIP otvorí čokoľvek; `.aar` (Apple Archive,
LZFSE) rozbalí iOS aj macOS systémovo, bez tretej knižnice v aplikácii.
`.aar` je NAVYŠE, nie namiesto – a `aa` je súčasť macOS, takže mimo neho sa
vyrobiť nedá a musí to byť počuť.

Použitie (volá to `publish-map.py`, samostatne nemá zmysel):
    from pack import BALICE
    BALICE["zip"](site, dest, koren, subory, info=...)
"""
import importlib.util
import json
import os
import subprocess
import shutil
import sys
import tempfile
import time
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_DRIVE = os.path.join(os.path.dirname(_HERE), "drive")


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

# Kompresia ZIPu: 1, nie 9. Dlaždice, PNG aj fonty sú už komprimované, takže
# vyšší stupeň minie minúty a ušetrí promile.
ZIP_LEVEL = 1


def log(*a):
    print(*a, flush=True)


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


def aa_je():
    """Je tu Apple Archive CLI (`aa`)? Je len na macOS – inde sa `.aar` nedá
    vyrobiť a musí to byť POČUŤ, nie ticho preskočené."""
    from shutil import which
    return which("aa") is not None


def zabal_aar(site, dest, koren, subory, info=None):
    """Súbory → jeden Apple Archive (`.aar`, LZFSE).

    PREČO NAVYŠE K ZIPU: `.aar` vie iOS aj macOS rozbaliť systémovo
    (framework AppleArchive) – bez tretej knižnice v aplikácii a s LZFSE,
    ktoré je na Apple hardvéri výrazne rýchlejšie než deflate. ZIP ostáva,
    lebo ten otvorí čokoľvek; toto je druhá podoba TOHO ISTÉHO obsahu, nie
    náhrada.

    `aa` berie ADRESÁR, nie zoznam súborov, takže sa najprv postaví strom
    z TVRDÝCH ODKAZOV. Kopírovať by znamenalo druhýkrát zapísať gigabajt na
    disk runnera; odkaz stojí nič a `aa` cez neho číta ten istý obsah.
    Priečinok `koren` navyše je ten istý zámer ako pri ZIPe – rozbalenie
    nevysype dvadsať položiek do stiahnutých súborov.
    """
    surovo = sum(os.path.getsize(p) for p in subory)
    log(f"Balím {len(subory)} súborov ({folder.human(surovo)}) do {dest}")
    t0 = time.time()
    stage = tempfile.mkdtemp(prefix="aar-", dir=os.path.dirname(dest) or None)
    try:
        koren_dir = os.path.join(stage, koren)
        os.makedirs(koren_dir, exist_ok=True)
        if info is not None:
            with open(os.path.join(koren_dir, "obsah.json"), "w") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
                f.write("\n")
        for p in sorted(subory):
            cieľ = os.path.join(koren_dir, os.path.relpath(p, site))
            os.makedirs(os.path.dirname(cieľ), exist_ok=True)
            try:
                os.link(p, cieľ)
            except OSError:
                # Iný filesystém alebo plný počet odkazov – vtedy kópia.
                shutil.copy2(p, cieľ)
        if os.path.exists(dest):
            os.remove(dest)
        subprocess.run(["aa", "archive", "-a", "lzfse", "-d", stage,
                        "-o", dest], check=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    velkost = os.path.getsize(dest)
    log(f"  hotovo za {time.time() - t0:.0f} s: {folder.human(velkost)} "
        f"({velkost * 100 // max(surovo, 1)} % pôvodnej veľkosti)")
    return velkost


# Formát → funkcia, ktorá ho zabalí. Jedno miesto, kde sú vymenované: keby
# pribudol tretí, pridá sa sem a nikde inde.
BALICE = {"zip": zabal, "aar": zabal_aar}

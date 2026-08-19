#!/usr/bin/env python3
"""
DMR 5.0 (ETRS89) z Google Drive → výškový model do releasu.

ČO JE ZDROJ. Dva súbory v jednom priečinku na Drive (`FOLDER_ID` nižšie), oba
čítané cez HTTP Range, nič sa nesťahuje celé:

    dmr5_etrs89.tif       145,39 GiB   423 518 × 207 589 px, mriežka 1 m
    dmr5_etrs89.tif.ovr    43,35 GiB   pyramídy: 2, 4, 8, 16, 32, 64, 128, 256 m

Oba sú BigTIFF, dlaždicované 128×128, Float32, nodata 3,4e38 (hlavný LZW,
pyramídy deflate). Georeferencia je v hlavnom súbore:

    CRS      EPSG:3046 – ETRS89 / TM zone N34 (cm 21° E, k₀ 0,9996, FE 500 000)
    origin   X 191 148,0   Y 5 497 220,0   (ľavý horný ROH, nie stred pixela)
    bunka    1,0 × 1,0 m

PREČO TO NIE JE `dmr5-raster.py`. Ten číta archív ÚGKK cez
`/vsizip//vsicurl/`, kde je raster zabalený jedným deflate prúdom – v ňom sa
nedá skočiť dopredu, takže celý ten script je postavený na pravidle „čítaj
raz a sekvenčne". Tu to pravidlo neplatí: súbory sú holé, každá dlaždica má
vlastnú kompresiu a Range funguje na ľubovoľnom offsete (overené na 20 GB aj
145 GB). Náhodný prístup je zadarmo, a tým sa mení celý návrh – výrez sa
neplatí vzdialenosťou od začiatku súboru, ale počtom dlaždíc, ktoré ho
pretínajú.

ČÍTA SA PRIHLÁSENÝ AKO VLASTNÍK, a inak sa nečíta vôbec. Kým tu stáli pevné
file id, dal sa model ťahať aj verejným odkazom – s denným limitom sťahovania,
ktorý zdieľajú všetci, kto naň siahnu (beh 31315890474). Odkedy je zdrojom
PRIEČINOK, tá cesta neexistuje: čo v priečinku je, povie len Drive API a to
anonymné požiadavky neobsluhuje. Token vlastníka zo secretu
`GDRIVE_CREDENTIALS` (alebo trojice `DRIVE_*`) drží `workers/drive/auth.py`
a `--auth-check` povie, ktorým účtom beh číta a či na súbory vidí. Bez neho
beh spadne hneď a s návodom – nie po hodine na tom, že Drive prestal púšťať.

TRI VECI, KTORÉ TENTO SÚBOR RIEŠI:

  1. DRIVE KLAME O VEĽKOSTI. Na HEAD vracia `content-length: 0`, takže GDAL
     súbor odmietne. Obchádza to `workers/drive/serve.py` – malý HTTP server
     na localhoste, ktorý tú jednu hlavičku opraví. Podáva OBA súbory pod
     jedným menom (`dmr5_etrs89.tif` a `dmr5_etrs89.tif.ovr`), takže si GDAL
     nájde pyramídy ako sidecar sám a pri hrubšom cieli číta z nich. Overené:
     `gdalinfo` vypíše všetkých 8 úrovní, otvorenie 145 GiB trvá 8 s a stojí
     9 požiadaviek / 0,3 MB.

  2. LATENCIA, NIE ŠÍRKA PÁSMA. Jeden Range request na Drive trvá rádovo
     0,1–1 s bez ohľadu na to, koľko bajtov nesie. Výrez 6×6 km pri 1 m
     (243 požiadaviek, 103 MB) trval jedným procesom 90 s, čo je 1,1 MB/s –
     pritom to isté pásmo utiahne 75 MB/s. Zmerané na 48 náhodných výrezoch:
     1 vlákno 1 143 ms/req, 8 vlákien 147 ms/req, 24 vlákien 68 ms/req.
     Preto sa okno KRÁJA NA BLOKY a tie sa čítajú súbežne (`--jobs`).
     GDAL je vnútri jedného `gdal_translate` v čítaní jednovláknový, takže
     súbežnosť musí prísť zvonku, z viacerých procesov.

  3. VÝŠKY SÚ ELIPSOIDICKÉ, NIE Bpv. Toto je ETRS89 verzia a nesie výšky nad
     elipsoidom GRS80. Vidno to na štatistike v súbore: maximum 2 697,03 m,
     kým Gerlachovský štít má 2 654,4 m n. m. Bpv – rozdiel +42,6 m je
     geoidová undulácia. Na SKALY to nevadí (geoid sa mení plynulo, sklon
     ostáva ten istý), ale vrstevnice by z toho vyšli popísané o 42 m vyššie
     než z ostatných zdrojov v pipeline. Preto sa predvolene odčíta geoid
     EGM2008 (`--geoid=egm2008`). Kontrola na Gerlachu: 2 697,03 m
     elipsoidicky → 2 654,37 m po prevode, oficiálne 2 654,4 m. Sedí na 3 cm.

VÝSTUP JE TEN ISTÝ AKO DOTERAZ, aby `workers/dem/fetch.sh` nemusel vedieť,
odkiaľ dáta prišli:

    pohorie          out/ugkk-<area>.tif      jeden COG vo WGS84 → dem-ugkk
    celé Slovensko   out/N49E019.tif …        dlaždice 1°×1°     → dem-dmr5
    výrez + --tiles  out/N49E019.tif …        len dotknuté stupne → dem-dmr5

Tretí riadok je to, čo si Build map dopĺňa sám: tieňovanie chce dlaždicovú
podobu (na celý región 1 m neexistuje), ale nepotrebuje kvôli nej celú
krajinu – stačia stupne, ktoré jeho bbox pretína. Okno sa pri `--tiles`
rozširuje na celé stupne, lebo meno `N49E020.tif` je sľub o celej dlaždici
a polovičná by v ďalšom behu prešla kontrolou ako hotová.

`--area` BERIE AJ BBOX, a to je pri výreze to podstatné. Build map ho tak aj
volá: čo sa má prečítať, je územie, ktoré si beh naozaj vypýtal (výrez pretnutý
s regiónom, pri rýchlom teste štvorec na pár km²) – nie celý obdĺžnik pohoria
z `areas.json`. Meno výsledku sa vtedy podá zvlášť cez `--asset`, lebo
`ugkk-20,49,21,50.tif` si build vypýtať nevie. Kým sa podával len kľúč
pohoria, prečítal sa vždy celý obdĺžnik z `areas.json` a rýchly test na pár km²
čítal z Drive 541 km² Vysokých Tatier.

ROZDELENÉ NA FÁZY (`--stage`), aby dlhé čakanie nebolo jeden nemý krok:

    plan     otvor zdroj, spočítaj okno a bloky, povedz, čo to bude stáť
    read     prečítaj bloky z Drive (jediná fáza, ktorá siaha na sieť)
    finish   mozaika → COG alebo 1° dlaždice (už len nad diskom)
    all      všetko za sebou, ako predtým (predvolené pri ručnom spustení)

Fázy si podávajú stav cez `<work>/dmr5-drive-stav.json` a rozčítané bloky
cez `<work>/blok-*.tif`, takže sa dajú spustiť ako samostatné kroky workflowu
– každý s vlastným nadpisom v logu a vlastným postupom.

Použitie:
    python3 workers/drive/dmr5.py --area=vysoke_tatry --grid-m=1 \\
        --out=out --asset=ugkk-vysoke_tatry.tif
    python3 workers/drive/dmr5.py --area=20.0,49.1,20.1,49.2 --grid-m=1 \\
        --out=out --asset=ugkk-vysoke_tatry_test4.tif
    python3 workers/drive/dmr5.py --area=cele_slovensko --grid-m=5 --out=out
    python3 workers/drive/dmr5.py --area=20,49,21,50 --grid-m=5 --tiles --out=out
    python3 workers/drive/dmr5.py --stage=plan --area=vysoke_tatry --grid-m=1
    python3 workers/drive/dmr5.py --probe-only
    python3 workers/drive/dmr5.py --auth-check
"""
import argparse
import importlib.util
import json
import math
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
# Priečinok = job, súbor = krok; spoločné veci ležia o úroveň vyššie.
_WORKERS = os.path.dirname(_HERE)          # workers/
_DATA = os.path.join(_WORKERS, "data")     # číselníky (areas, regions, zdroje)

# KDE DMR 5.0 LEŽÍ – jedno číslo, a je ním PRIEČINOK, nie dva súbory.
#
# Kým tu stáli pevné file id, presun modelu na iný účet alebo do iného
# priečinka znamenal prepísať dve id na štyroch miestach v hláškach a dúfať,
# že sa na žiadne nezabudlo. Priečinok je pritom to, čo človek naozaj presúva
# a zdieľa; súbory v ňom sa hľadajú podľa mena (a keď sa aj to zmení, podľa
# veľkosti – najväčší `.tif` v priečinku JE model).
#
# Tajomstvo to nie je: id priečinka chodí v zdieľanom odkaze. Tajomstvom je
# token vlastníka v secrete GDRIVE_CREDENTIALS (viď `drive-auth.py`).
FOLDER_ID = "1H62op_LMUYDqKeFf-_sXS-46PLEmxDyd"
TIF_NAME = "dmr5_etrs89.tif"
OVR_NAME = TIF_NAME + ".ovr"


# Stav medzi fázami. Leží v `--work`, ktorý medzi krokmi jobu prežije.
STATE = "dmr5-drive-stav.json"

# Odhad ceny čítania, NA PIXEL ZDROJA. Namerané, nie odhadnuté od stola:
# výrez 5,2 × 5,6 km pri 1 m (29 km² = 29 mil. px na plnom rozlíšení) trval
# 1,2 min a stiahol 0,11 GB v 697 požiadavkách, pri `--jobs=12`.
#
# Na pixel ZDROJA, a nie cieľa, preto, že hrubšiu mriežku číta GDAL z pyramíd
# a tie majú vlastné rozlíšenie: cieľ 5 m sa berie z úrovne 4 m, čiže sa
# prečíta (5/4)² = 1,6× viac pixelov, než má výstup. Kontrola na inom konci
# rozsahu: jeden 1° stupeň na 5 m = 8 065 km² z pyramídy 4 m = 504 mil. px,
# čiže ~21 min a ~1,9 GB. Sedí s tým, čo o cene stupňa hovorí check-dem.sh.
#
# Je to rádový odhad – má povedať „minúty alebo hodiny", nie predpovedať minútu.
PX_PER_MIN = 24e6         # pri --jobs=12
BYTES_PER_PX = 3.8


def load(name, path):
    """workers/*.py sa kvôli pomlčke v mene nedajú `import`-núť normálne.

    Cez `sys.modules` preto, aby ten istý modul nevznikol dvakrát:
    `drive-auth.py` si `drive-serve.py` vypýta tiež (berie si odtiaľ spojenia)
    a dve kópie shimu by boli dva nezávislé bazény spojení.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


drive = load("drive_serve", "serve.py")
auth = load("drive_auth", "auth.py")       # kto sme na Drive
folder = load("drive_folder", "folder.py")  # čo je v priečinku
raster = load("dmr5_raster", "dmr5-raster.py")   # Heartbeat, run_live, pomocníci

# VÝREZ JE VEDĽA. `dmr5-cut.py` vie z bboxu spočítať okno v projekcii zdroja,
# rozkrájať ho na bloky, prečítať ich súbežne z Drive a zapísať buď jeden COG,
# alebo 1° dlaždice. Tento súbor sa pýta na niečo iné: ako sa k tým dátam
# dostať, čo to bude stáť a v akých fázach to spraviť. (Rozdelené preto, že
# spolu to malo 888 riadkov – pravidlo 5 v CLAUDE.md.)
#
# `LOG`, `log()` a `run()` prišli s výrezom a berú sa ODTIAĽ, nie sa píšu
# druhýkrát: `LOG` je jeden denník a dve kópie by znamenali súhrn s polovicou
# riadkov (pravidlo 1).
cut = load("dmr5_cut", "dmr5-cut.py")
LOG, log, run = cut.LOG, cut.log, cut.run
src_window, blocks, read_blocks = cut.src_window, cut.blocks, cut.read_blocks
pyramid_level = cut.pyramid_level
to_wgs84, country_tiles = cut.to_wgs84, cut.country_tiles
SRC_EPSG = cut.SRC_EPSG





# ---------- stav medzi fázami ----------

def state_path(work):
    return os.path.join(work, STATE)


# Koľko riadkov LOGu už v stave je. Fázy sú samostatné procesy, takže bez
# tohto by v súhrne na konci ostal len log poslednej z nich.
_LOG_SAVED = 0


def save_state(work, state):
    global _LOG_SAVED
    state["log"] = state.get("log", []) + LOG[_LOG_SAVED:]
    _LOG_SAVED = len(LOG)
    os.makedirs(work, exist_ok=True)
    with open(state_path(work), "w") as f:
        json.dump(state, f, indent=1)


def load_state(work):
    """Stav z fázy `plan`. Keď chýba, volajúci spustil fázy v zlom poradí –
    a to je chyba zadania, nie niečo, čo sa dá dopočítať: `read` bez plánu by
    prečítal iné okno, než na aké sa pýtal `plan`."""
    p = state_path(work)
    if not os.path.exists(p):
        raise SystemExit(
            f"::error::Chýba {p} – fáza sa spúšťa až po `--stage=plan`.")
    with open(p) as f:
        return json.load(f)


def credentials():
    """Prihlásenie na Drive z prostredia, alebo None (a to už ďaleko nedôjde:
    `resolve_ids` bez neho priečinok nevypíše).

    Chybu prekladá na `::error::` a pád: kto secret nastavil, čaká prihlásený
    beh, a ticho spadnúť na verejný denný limit sa pozná až vtedy, keď Drive
    po pol dni prestane púšťať. Rozpis vo `workers/drive/auth.py`.
    """
    try:
        creds = auth.from_env()
        if creds is None:
            return None
        # Token si vypýtaj HNEĎ: keď ho Drive nedá, povie sa to tu a nie po
        # hodine čítania. Toto je tvrdá chyba – s pokazeným prihlásením sa
        # nedá čítať a ticho prejsť na verejný limit je zakázané.
        creds.token()
    except auth.AuthError as exc:
        raise SystemExit(f"::error::{exc}")
    try:
        auth.whoami(creds)
    except auth.AuthError as exc:
        # Toto je len na výpis „ktorým účtom čítame". Keď zlyhá práve táto
        # jedna metadátová požiadavka, čítanie tým nekončí – token platí
        # a bloky si prípadnú chybu ohlásia samy a presnejšie.
        log(f"::warning::Účet sa nepodarilo zistiť ({exc}). Čítanie beží "
            f"prihlásené, len sa v logu nebude vedieť ktorým účtom.")
    return creds


# Raz vyriešené id sa v procese nehľadajú druhýkrát: fázy `plan` a `read`
# otvárajú zdroj každá raz, ale `slope-chunks.py` si shim pýta v tom istom
# procese pri každom pokuse o časť.
_IDS = None


def resolve_ids(creds):
    """Priečinok na Drive → (id modelu, id pyramíd). Vypíše, čo našiel.

    HĽADÁ SA PODĽA MENA, NIE PODĽA PORADIA. `dmr5_etrs89.tif` je meno, ktoré
    má model dnes; keby sa premenoval, berie sa najväčší `.tif` v priečinku –
    145 GiB raster sa s ničím iným pomýliť nedá. Pyramídy sú `<meno>.ovr`
    vedľa neho.

    BEZ PRIHLÁSENIA TO NEJDE a nemá zmysel to zakrývať: obsah priečinka
    povie len Drive API a to anonymné požiadavky neobsluhuje. Kým tu stáli
    pevné file id, dal sa model čítať aj verejným odkazom (s denným limitom);
    priečinok, v ktorom teraz leží, verejný nie je.
    """
    global _IDS
    if _IDS is not None:
        return _IDS
    if creds is None:
        raise SystemExit(
            "::error::DMR 5.0 leží v priečinku na Drive "
            f"({FOLDER_ID}) a jeho obsah vie vypísať len prihlásený beh – "
            "Drive API anonymné požiadavky neobsluhuje. Doplň secret "
            "GDRIVE_CREDENTIALS (alebo premennú DRIVE_CLIENT a secrety "
            "DRIVE_SECRET / DRIVE_REFRESH): vyrobí ich workflow „Prihlásenie "
            "na Drive (jednorazové)“, z počítača `python3 workers/"
            "drive-auth.py --login`.")
    files, _skipped = folder.listing(creds, FOLDER_ID)
    tifs = [f for f in files if f["name"].lower().endswith(".tif")]
    ovrs = [f for f in files if f["name"].lower().endswith(".ovr")]
    if not tifs:
        raise SystemExit(
            f"::error::V priečinku {FOLDER_ID} na Drive nie je ani jeden "
            f".tif (videl som: "
            + (", ".join(f["name"] for f in files[:8]) or "nič")
            + "). Vidí naň prihlásený účet a je v ňom DMR 5.0?")
    tif = next((f for f in tifs if f["name"] == TIF_NAME),
               max(tifs, key=lambda f: f["size"]))
    ovr = next((f for f in ovrs if f["name"] == tif["name"] + ".ovr"),
               max(ovrs, key=lambda f: f["size"]) if ovrs else None)
    log(f"  priečinok {FOLDER_ID}: {len(files)} súborov")
    for f in (tif, ovr):
        if f is not None:
            log(f"    {f['name']}  {f['size'] / 2**30:.2f} GiB"
                + ("" if f["owned"] else "  (tento účet ho NEVLASTNÍ – platí "
                                         "naň denný limit sťahovania)"))
    if ovr is None:
        # Nie je to chyba, ale je to drahé: bez pyramíd sa hrubšie mriežky
        # počítajú z plného 1 m rastra.
        log(f"::warning::V priečinku nie je `{OVR_NAME}` (pyramídy). Hrubšie "
            f"mriežky sa budú čítať z plného 1 m rastra a potrvá to násobne "
            f"dlhšie.")
    _IDS = (tif["id"], ovr["id"] if ovr else None)
    return _IDS


def serve_drive(port=0):
    """Shim nad OBOMA súbormi DMR 5.0. Vracia (base, sizes, stats, creds).

    Jediné miesto, kde je napísané, odkiaľ sa berú dáta a s akým prihlásením
    – `open_source` aj `slope-chunks.py` si to volajú odtiaľto, nech sa zdroj
    dá vymeniť na jednom mieste.

    Shim ich podáva pod KANONICKÝMI menami (`dmr5_etrs89.tif` a `.tif.ovr`) aj
    vtedy, keď sa na Drive volajú inak: GDAL si pyramídy hľadá ako sidecar
    podľa mena vedľa hlavného súboru a to meno musí sedieť.
    """
    creds = credentials()
    tif_id, ovr_id = resolve_ids(creds)
    ids = {TIF_NAME: tif_id}
    if ovr_id:
        ids[OVR_NAME] = ovr_id
    base, sizes, stats = drive.serve(ids, port, creds=creds)
    return base, sizes, stats, creds


def auth_check():
    """Povedz, ktorým účtom sa bude čítať, a či na oba súbory vidí.

    Vlastný krok workflowu preto, že je LACNÝ (token + výpis priečinka)
    a odpovedá na to, čo sa inak zistí až vtedy, keď Drive prestane dávať
    dáta: vidí ten účet na priečinok s modelom, a vlastní ho?
    """
    print("Prístup k DMR 5.0 na Google Drive:")
    try:
        creds = auth.from_env()
        ids = [i for i in resolve_ids(creds) if i]
        return auth.do_check(argparse.Namespace(file=ids))
    except auth.AuthError as exc:
        print(f"::error::{exc}")
        return 2


def open_source(args):
    """Shim nad Drive + otvorený raster.

    Vracia (src, env, info, native_m, stats, creds). Robia to fázy `plan`
    a `read`; `finish` už na sieť nesiaha vôbec, počíta nad blokmi na disku –
    a práve preto je oddelená.
    """
    log("Otváram DMR 5.0 (ETRS89) na Drive cez lokálny shim…")
    base, sizes, stats, creds = serve_drive(args.port)
    log(f"  prístup: {auth.describe(creds)}")
    for name, size in sizes.items():
        log(f"  {name}: {size / 2**30:.2f} GiB")
    src = f"/vsicurl/{base}/{TIF_NAME}"
    env = drive.gdal_env()
    if args.geoid == "egm2008":
        # Mriežku geoidu si PROJ stiahne z CDN, keď ju nemá lokálne.
        env["PROJ_NETWORK"] = "ON"

    t0 = time.time()
    info = json.loads(run(["gdalinfo", "-json", "-nomd", src], env).stdout)
    ov = [o["size"] for o in info["bands"][0].get("overviews", [])]
    log(f"  otvorené za {time.time() - t0:.1f} s: "
        f"{info['size'][0]:,} × {info['size'][1]:,} px, "
        f"mriežka {abs(info['geoTransform'][1]):g} m, {len(ov)} úrovní pyramíd")
    if not ov:
        log("::warning::Pyramídy sa nenašli – hrubšie mriežky sa budú počítať "
            "z plného 1 m rastra a potrvá to násobne dlhšie.")
    return src, env, info, abs(info["geoTransform"][1]), stats, creds


def drive_totals(state, stats):
    """Prirátaj, čo z Drive prišlo v tejto fáze, k tomu, čo prišlo v predošlých.

    Fázy sú samostatné procesy, takže počítadlo shimu začína v každej od nuly –
    bez tohto by súhrn na konci tvrdil, že sa stiahlo len to z poslednej.
    """
    if stats is None:
        return state.get("drive_bytes", 0), state.get("drive_requests", 0)
    with stats["lock"]:
        req, got = stats["requests"], stats["bytes"]
    state["drive_bytes"] = state.get("drive_bytes", 0) + got
    state["drive_requests"] = state.get("drive_requests", 0) + req
    return state["drive_bytes"], state["drive_requests"]


# ---------- fáza 1: plán ----------

def stage_plan(args):
    """Otvor zdroj, spočítaj okno a bloky a povedz, čo to bude stáť.

    Vlastná fáza preto, že je LACNÁ (otvorenie stojí 9 požiadaviek a 0,3 MB)
    a odpovedá na jedinú otázku, ktorá pred hodinovým čítaním zaujíma: koľko
    toho bude. Trojhodinový krok, ktorý spadne na timeout, je najhorší možný
    výsledok – minie celý rozpočet a nevyrobí nič.
    """
    src, env, info, native_m, stats, creds = open_source(args)
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    wkt_file = os.path.join(args.work, "src.wkt")
    with open(wkt_file, "w") as f:
        f.write((info.get("coordinateSystem") or {}).get("wkt", ""))

    area_name, bbox = raster.resolve_area(args.area, os.path.join(_DATA, "areas.json"))

    # DLAŽDICOVÝ REŽIM S VÝREZOM. Build mapy si dlaždice hľadá podľa mena
    # (`N49E020.tif`) a to meno je sľub: „tento celý stupeň je tu". Keby sa
    # pod ním v release ocitol len prienik s bboxom, ďalší beh by kontrolou
    # prešiel („dlaždica tam je“) a tieňovanie by ticho končilo v polovici
    # mapy. Okno sa preto rozširuje na celé stupne – čítať sa musí celá
    # dlaždica, nie len to, čo dnes treba.
    tiles_out = bbox is None or args.tiles
    if bbox is not None and args.tiles:
        w, s, e, n = bbox
        bbox = (float(math.floor(w)), float(math.floor(s)),
                float(math.ceil(e)), float(math.ceil(n)))
        deg = int((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        area_name += (f" → celé stupne {bbox[0]:g},{bbox[1]:g}…{bbox[2]:g},"
                      f"{bbox[3]:g} ({deg} dlaždíc)")

    log(f"Územie: {area_name}, cieľová mriežka {args.grid_m:g} m")

    if bbox is None:
        box = (info["geoTransform"][0], info["geoTransform"][3]
               + info["geoTransform"][5] * info["size"][1],
               info["geoTransform"][0] + info["geoTransform"][1] * info["size"][0],
               info["geoTransform"][3])
    else:
        box = src_window(bbox, wkt_file, info, env)
    parts, (nx, ny) = blocks(box, args.grid_m, args.jobs)

    km_x, km_y = (box[2] - box[0]) / 1000, (box[3] - box[1]) / 1000
    area_m2 = (box[2] - box[0]) * (box[3] - box[1])
    cells = area_m2 / args.grid_m ** 2

    # ČO SA BUDE ČÍTAŤ, NIE ČO VYPADNE. Cena je počet pixelov, ktoré prídu
    # z Drive, a tie nie sú bunky cieľa: číta sa z najhrubšej pyramídy, ktorá
    # je ešte jemnejšia než cieľ. Pri cieli 5 m to je úroveň 4 m, čiže
    # (5/4)² = 1,6× viac pixelov, než má výstup – a to je presne ten rozdiel
    # medzi „13 minút" a „21 minút na stupeň".
    #
    # ODPOVEDÁ NA TO `cut.pyramid_level`, NIE TENTO RIADOK. Z tej istej úrovne
    # totiž vyplýva aj POMER pixel/bunka, a ten rozhoduje, ktorým resamplingom
    # sa smie čítať; kým to boli dve miesta, plán hovoril „4 m" a čítanie
    # priemerovalo, akoby bol pomer poctivý (rozpis pri `cut.pyramid_level`).
    ovr_level, read_m = pyramid_level(info, native_m, args.grid_m)
    src_px = area_m2 / read_m ** 2

    # Rýchlosť je meraná pri `--jobs=12`; pri inom počte vlákien sa škáluje,
    # ale nie donekonečna – nad ~16 vláknami Drive začne odpovedať 403.
    rate = PX_PER_MIN * min(args.jobs, 16) / 12.0
    est_min = src_px / max(rate, 1.0)
    est_gb = src_px * BYTES_PER_PX / 1e9
    asset = args.asset or f"ugkk-{args.area}.tif"

    print("── Plán čítania z Drive ─────────────────────────────")
    print(f"  územie          {area_name}")
    print(f"  okno            {km_x:.1f} × {km_y:.1f} km "
          f"({km_x * km_y:.0f} km²) v EPSG:{SRC_EPSG}")
    print(f"  cieľová mriežka {args.grid_m:g} m → {cells / 1e6:.1f} mil. buniek")
    print(f"  číta sa z       {read_m:g} m "
          + ("(plné rozlíšenie)" if read_m == native_m else
             f"(pyramída, úroveň {ovr_level})")
          + f" → {src_px / 1e6:.1f} mil. px")
    # ČÍM sa to prevzorkuje, patrí do plánu rovnako ako to, z čoho: práve
    # tento riadok je rozdiel medzi mriežkou v tieni a hladkým reliéfom.
    print(f"  prevzorkovanie  {cut.read_args(args.grid_m, read_m, ovr_level)[1]}")
    print(f"  blokov          {len(parts)} ({nx}×{ny}), {args.jobs} naraz")
    print(f"  odhad           ~{est_min:.0f} min, ~{est_gb:.2f} GB z Drive")
    print("  výstup          " + (f"1° dlaždice do {args.out}/" if tiles_out
                                  else f"{args.out}/{asset}"))
    print("  výšky           " + ("EGM2008 (≈ Bpv)" if args.geoid == "egm2008"
                                  else "elipsoidické ETRS89"))
    print("─────────────────────────────────────────────────────", flush=True)
    if est_min > 120:
        print(f"::warning::Odhad čítania je ~{est_min / 60:.1f} h. Kratšie to "
              f"ide s menším územím alebo hrubšou mriežkou (--grid-m).")

    state = {
        "area": args.area,
        "area_name": area_name,
        # Ako sa k dátam pristupovalo, patrí do súhrnu – prepnutie na verejný
        # odkaz (zmazaný secret) sa inak nemá kde ukázať a zistilo by sa až
        # tým, že Drive prestane púšťať. Rovnaký dôvod ako `dem-source.txt`:
        # nesie sa, čo sa NAOZAJ použilo.
        "drive_auth": auth.describe(creds),
        "bbox": list(bbox) if bbox is not None else None,
        "box": list(box),
        "blocks": [list(p) for p in parts],
        "grid_m": args.grid_m,
        "native_m": native_m,
        # Z ČOHO SA ČÍTA – vyrátané v pláne, použité vo fáze `read`. Fázy sú
        # samostatné procesy, takže sa to musí preniesť; prepočítať to druhýkrát
        # by znamenalo dve odpovede na jednu otázku.
        "read_m": read_m,
        "ovr_level": ovr_level,
        "tiles": tiles_out,
        "geoid": args.geoid,
        "asset": asset,
        "src_px": list(info["size"]),
        "cells": cells,
        "est_min": est_min,
    }

    # ROZČÍTANÉ BLOKY SÚ DOBRÉ, LEN KEĎ SEDIA NA TENTO PLÁN. Fáza `read`
    # pozná blok podľa poradového čísla v mene (`blok-0007.tif`), takže po
    # zmene územia či mriežky by pod tým istým menom ležal úplne iný kus zeme
    # a mozaika by bola poskladaná z dvoch rôznych zadaní. Rovnaký plán =
    # opakovaný krok dopočíta zvyšok; iný plán = začína sa odznova.
    old = None
    if os.path.exists(state_path(args.work)):
        with open(state_path(args.work)) as f:
            old = json.load(f)
    same = old is not None and all(old.get(k) == state[k]
                                   for k in ("box", "blocks", "grid_m", "geoid"))
    stale = [f for f in os.listdir(args.work)
             if f.startswith("blok-") and f.endswith((".tif", ".part"))]
    if stale and not same:
        for f in stale:
            os.remove(os.path.join(args.work, f))
        log(f"  plán sa zmenil – zahodených {len(stale)} blokov z predošlého")
    elif stale:
        log(f"  {len(stale)} blokov z predošlého pokusu sedí na tento plán "
            f"a znova sa čítať nebudú")

    if same and old.get("t_start"):
        state["t_start"] = old["t_start"]
        state["drive_bytes"] = old.get("drive_bytes", 0)
        state["drive_requests"] = old.get("drive_requests", 0)
        state["log"] = old.get("log", [])
    else:
        state["t_start"] = time.time()
    drive_totals(state, stats)
    save_state(args.work, state)
    return state


# ---------- fáza 2: čítanie ----------

def stage_read(args, state):
    """Bloky z Drive na disk. Jediná fáza, ktorá siaha na sieť – a tá dlhá."""
    src, env, _info, _native, stats, creds = open_source(args)
    state["drive_auth"] = auth.describe(creds)
    parts = [tuple(p) for p in state["blocks"]]
    log(f"  {len(parts)} blokov, {args.jobs} naraz, cieľová mriežka "
        f"{state['grid_m']:g} m")
    read_blocks(src, parts, state["grid_m"], args.work, args.jobs, env,
                state["native_m"], state.get("read_m"), state.get("ovr_level"))
    got, req = drive_totals(state, stats)
    log(f"Z Drive doteraz {got / 1e9:.2f} GB v {req:,} požiadavkách")
    save_state(args.work, state)
    return state


# ---------- fáza 3: zloženie výstupu ----------

def stage_finish(args, state):
    """Bloky na disku → COG alebo 1° dlaždice. Na sieť sa už nesiaha.

    Výnimka je mriežka geoidu, ktorú si PROJ stiahne z CDN – pár MB, nie Drive.
    """
    env = drive.gdal_env()
    if state["geoid"] == "egm2008":
        env["PROJ_NETWORK"] = "ON"
    parts = sorted(os.path.join(args.work, f)
                   for f in os.listdir(args.work)
                   if f.startswith("blok-") and f.endswith(".tif"))
    if not parts:
        raise SystemExit(f"::error::V {args.work} nie je ani jeden blok – "
                         f"fáza `read` nebežala alebo spadla.")
    if len(parts) != len(state["blocks"]):
        raise SystemExit(
            f"::error::Na disku je {len(parts)} blokov, plán ich má "
            f"{len(state['blocks'])}. Mozaika s dierou by sa doplnila nulami "
            f"a z nuly je v mape more – spusti fázu `read` znova.")
    log(f"Skladám {len(parts)} blokov, "
        f"{sum(os.path.getsize(p) for p in parts) / 1048576:.0f} MB na disku")

    if state["tiles"]:
        # Okno je to, čo si fáza `plan` rozšírila na celé stupne – a podáva sa
        # ďalej, aby sa pod menom dlaždice neuložil presah prevodu do WGS84
        # (rozpis pri `country_tiles`). `None` = celé Slovensko.
        country_tiles(parts, args.out, args.work, env, state["geoid"],
                      window=state["bbox"], grid_m=state["grid_m"])
        made = sorted(f for f in os.listdir(args.out) if f.endswith(".tif"))
        log(f"Hotovo: {len(made)} dlaždíc v {args.out}")
    else:
        dest = to_wgs84(parts, os.path.join(args.out, state["asset"]),
                        state["bbox"], state["grid_m"], args.work, env,
                        state["geoid"])
        made = [os.path.basename(dest)]

    # Bloky až teraz: kým výstup nie je hotový, sú to jediné prečítané dáta
    # a opakovaný `read` by ich musel stiahnuť znova.
    for p in parts:
        os.remove(p)
    state["made"] = made
    save_state(args.work, state)
    return state


def write_summary(path, state):
    got = state.get("drive_bytes", 0)
    req = state.get("drive_requests", 0)
    made = state.get("made", [])
    with open(path, "w") as f:
        f.write("## DMR 5.0 (ETRS89) z Drive\n\n")
        f.write("| vec | hodnota |\n|---|---|\n")
        f.write(f"| územie | {state['area_name']} |\n")
        f.write(f"| mriežka | {state['grid_m']:g} m |\n")
        f.write(f"| okno | {(state['box'][2] - state['box'][0]) / 1000:.1f} × "
                f"{(state['box'][3] - state['box'][1]) / 1000:.1f} km, "
                f"{len(state['blocks'])} blokov |\n")
        f.write(f"| zdroj | {state['src_px'][0]:,}×{state['src_px'][1]:,} px "
                f"@ {state['native_m']:g} m, EPSG:{SRC_EPSG} |\n")
        f.write(f"| výšky | {'EGM2008 (≈ Bpv)' if state['geoid'] == 'egm2008' else 'elipsoidické ETRS89'} |\n")
        f.write(f"| z Drive | {got / 1e9:.2f} GB / {req:,} požiadaviek |\n")
        f.write(f"| prístup | {state.get('drive_auth', '?')} |\n")
        f.write(f"| trvanie | {(time.time() - state['t_start']) / 60:.1f} min "
                f"(odhad bol {state['est_min']:.0f} min) |\n")
        f.write(f"| výstup | {', '.join(f'`{m}`' for m in made[:12]) or '–'} |\n")
        f.write("\n<details><summary>Log</summary>\n\n```\n"
                + "\n".join(state.get("log", []) + LOG[_LOG_SAVED:])
                + "\n```\n\n</details>\n")


# ---------- beh ----------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--area", default="cele_slovensko",
                    help="kľúč z workers/data/areas.json, `cele_slovensko`, alebo bbox W,S,E,N")
    ap.add_argument("--grid-m", type=float, default=1.0)
    ap.add_argument("--out", default="out")
    ap.add_argument("--work", default="drive-work")
    ap.add_argument("--asset", default=None,
                    help="meno výsledku pri výreze; predvolene ugkk-<area>.tif. "
                         "Pri bboxe v --area je povinné – `ugkk-20,49,21,50.tif` "
                         "si build vypýtať nevie.")
    ap.add_argument("--jobs", type=int, default=12,
                    help="koľko blokov sa číta naraz; nad ~16 začne Drive "
                         "odpovedať 403 a čakanie zožerie viac, než sa získa")
    ap.add_argument("--geoid", choices=("egm2008", "elipsoid"), default="egm2008")
    ap.add_argument("--tiles", action="store_true",
                    help="výstup sú 1° dlaždice (dem-dmr5) aj pri zadanom "
                         "výreze – okno sa rozšíri na celé stupne. Bez toho "
                         "je z výrezu jeden COG (dem-ugkk).")
    ap.add_argument("--stage", choices=("all", "plan", "read", "finish"),
                    default="all",
                    help="ktorú fázu spustiť; stav si podávajú cez --work")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--probe-only", action="store_true",
                    help="len otvor zdroj a vypíš, čo v ňom je")
    ap.add_argument("--auth-check", action="store_true",
                    help="povedz, ktorým účtom sa bude z Drive čítať a či "
                         "na oba súbory vidí; nič sa nečíta")
    ap.add_argument("--summary", default=None)
    args = ap.parse_args()

    if args.auth_check:
        return auth_check()

    if args.probe_only:
        _src, _env, info, native_m, _stats, _creds = open_source(args)
        log(f"  CRS: {(info.get('coordinateSystem') or {}).get('wkt', '')[:80]}…")
        log(f"  origin: {info['geoTransform'][0]}, {info['geoTransform'][3]}")
        for i, o in enumerate(info["bands"][0].get("overviews", [])):
            w, h = o["size"]
            log(f"    úroveň {i}: {w:,} × {h:,} px = "
                f"{native_m * info['size'][0] / w:.0f} m")
        return 0

    # Fázy sa reťazia zhora nadol; `all` je všetky tri v jednom procese.
    state = None
    if args.stage in ("all", "plan"):
        state = stage_plan(args)
    if args.stage in ("all", "read"):
        state = stage_read(args, state or load_state(args.work))
    if args.stage in ("all", "finish"):
        state = stage_finish(args, state or load_state(args.work))
        got, req = state.get("drive_bytes", 0), state.get("drive_requests", 0)
        log(f"Z Drive prišlo {got / 1e9:.2f} GB v {req:,} požiadavkách, "
            f"celý beh {(time.time() - state['t_start']) / 60:.1f} min")

    if args.summary:
        write_summary(args.summary, state or load_state(args.work))
    return 0


if __name__ == "__main__":
    sys.exit(main())

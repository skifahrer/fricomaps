#!/usr/bin/env python3
"""
Raster sklonu po častiach – s trvalým skladom, aby sa nič nepočítalo dvakrát.

ČO RIEŠI. Skaly pre pohorie sa dovtedy počítali takto: `mirror-dmr5-area`
prečítal z Drive CELÝ výrez naraz a uložil ho ako jeden COG (`ugkk-<pohorie>
.tif`, do 2 GB), a až potom sa z neho rátal sklon. Jednotka práce aj jednotka
uloženia bola „celé územie", takže to bolo všetko alebo nič – zrušený beh
31310604408 čítal Vysoké Tatry hodinu a nechal po sebe NULU.

Tu je jednotkou ČASŤ. Každá časť sa z Drive prečíta, prevedie na sklon a
uloží zvlášť; ďalší beh si ju už len stiahne. Beh, ktorý spadne alebo ho
niekto zruší v polovici, teda o hotové časti nepríde a nasledujúci dopočíta
len zvyšok.

PREČO JE JEDNOTKOU SKLON, A NIE HOTOVÉ SKALY. Vektorizovať po častiach sa
skúšalo a nefunguje to: diera prerezaná hranicou časti sa zmenila na zárez
v okraji a späť sa už nezlepila – z dvoch plôch s dierami vyšli štyri bez
dier (zápis v `rock-areas.py`). Sklon je pritom presne tá drahá časť
(čítanie z Drive + warp + gdaldem), kým vektorizácia je jeden lacný priechod
nad hotovou mozaikou. Vedľajší zisk: zmena prahu `rock_slope` už NEznamená
nové čítanie z Drive – prahy sa uplatňujú až pri vektorizácii, takže sa
prepočítajú len vektory.

ABSOLÚTNA MRIEŽKA ČASTÍ. Hranice častí sú prichytené na mriežku ukotvenú
v počiatku EPSG:3035, nie na bbox územia. To je ten rozdiel, vďaka ktorému
má sklad zmysel: tá istá zem padne vždy do tej istej časti s tým istým
menom, takže časti spočítané pre `vysoke_tatry` si vezme aj neskorší beh na
`tatry`. Keby boli indexy relatívne k bboxu (ako v `chunk_plan`), každé
územie by malo vlastnú mriežku a sklad by netrafil nikdy nič.

GEOID SA TU ZÁMERNE NEPREVÁDZA. Výšky z Drive sú elipsoidické (o ~42,6 m nad
Bpv), ale geoid sa mení plynulo – na ploche jednej časti je to prakticky
konštantný posun a SKLON sa ním nemení. Vrstevnice by prevod potrebovali,
sklon nie, takže sa ušetrí sťahovanie mriežky EGM2008 aj čas.

Použitie:
    python3 workers/slope-chunks.py --bbox=19.9,49.09,20.32,49.25 \\
        --res=2 --drive --out=slope-chunks --jobs=6
    python3 workers/slope-chunks.py --bbox=… --res=auto --print-res
"""
import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, path):
    """workers/*.py sa kvôli pomlčke v mene nedajú `import`-núť normálne."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Plánovanie, metrická sústava aj mierka sklonu sú v `rock-areas.py` a berú sa
# odtiaľ – dve implementácie toho istého sa vždy raz rozídu (presne to zhodilo
# beh 31307163093, keď si kontrola a sťahovanie odpovedali každý inak).
rock = load("rock_areas", "rock-areas.py")
METRIC, SCALE = rock.METRIC, rock.SCALE

# Strana časti v pixeloch. 4096² = 16,8 mil. buniek: pri 2 m je to 8,2 km na
# stranu, čo je ~35 častí na Vysoké Tatry. Menšie časti = jemnejšie
# obnovenie po páde a menšie assety, väčšie = menej réžie okolo každej.
CHUNK_PX = 4096
MARGIN_PX = 8    # presah, aby sklon na okraji časti nebol zrezaný
MAX_CHUNKS = 600  # nad tým prestáva byť sklad výhodou (viď poistku v main)


def chunk_grid(bbox, res, chunk_px=CHUNK_PX):
    """Časti absolútnej mriežky, ktoré zasahujú do bboxu.

    Vracia zoznam `(ix, iy, x0, y0, x1, y1)` v metroch EPSG:3035, kde `ix`,
    `iy` sú indexy v mriežke ukotvenej v počiatku sústavy – teda nezávislé od
    toho, pre aké územie sa práve počíta.

    Prevod do stupňov ide JEDNÝM volaním `gdaltransform` nad všetkými rohmi
    naraz. Volať ho na každú časť zvlášť vyzerá nevinne, ale je to jeden
    proces na časť – pri jemnej mriežke ich sú tisíce a samotné plánovanie
    potom trvá dlhšie než výpočet.
    """
    side = chunk_px * res
    mx0, my0, mx1, my1 = rock.to_metric(bbox)
    cand = []
    for iy in range(math.floor(my0 / side), math.ceil(my1 / side)):
        for ix in range(math.floor(mx0 / side), math.ceil(mx1 / side)):
            x0, y0 = ix * side, iy * side
            cand.append((ix, iy, x0, y0, x0 + side, y0 + side))
    if not cand:
        return []

    # Osem bodov na časť: rohy a stredy strán. Samotné rohy nestačia – hranica
    # je po prevode krivka a pri veľkej časti by sa mohla do bboxu vydúvať
    # stredom strany, kým rohy ostanú vonku.
    pts = []
    for _, _, x0, y0, x1, y1 in cand:
        pts += [(x0, y0), (x1, y0), (x0, y1), (x1, y1),
                ((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1),
                (x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2)]
    try:
        out = subprocess.run(
            ["gdaltransform", "-s_srs", METRIC, "-t_srs", "EPSG:4326"],
            input="\n".join(f"{x} {y}" for x, y in pts),
            capture_output=True, text=True, check=True).stdout.split()
    except subprocess.CalledProcessError:
        return cand   # keď sa to nedá zistiť, radšej počítať než vynechať

    xs = [float(v) for v in out[0::3]]
    ys = [float(v) for v in out[1::3]]
    keep = []
    for i, c in enumerate(cand):
        cx, cy = xs[i * 8:(i + 1) * 8], ys[i * 8:(i + 1) * 8]
        if not (max(cx) < bbox[0] or min(cx) > bbox[2]
                or max(cy) < bbox[1] or min(cy) > bbox[3]):
            keep.append(c)
    return keep


def chunk_name(ix, iy, res):
    """Meno časti v sklade. Nesie všetko, čo mení jej obsah: mriežku aj
    polohu. Prah sklonu v ňom NIE JE – ten sa uplatňuje až pri vektorizácii,
    takže jeho zmena smie sklad použiť.

    Znamienko ide do písmena (E/W, N/S) a nie do mínusu: mená assetov sa
    porovnávajú aj očami a `slope-r2--615--358` sa číta zle.
    """
    sx = f"E{ix:04d}" if ix >= 0 else f"W{-ix:04d}"
    sy = f"N{iy:04d}" if iy >= 0 else f"S{-iy:04d}"
    return f"slope-r{res:g}-{sx}{sy}.tif"


# ---------- sklad ----------

class Store:
    """Časti v adresári (cache behu) a v release (trvalé).

    Dve vrstvy zámerne: cache je rýchla, ale GitHub ju po siedmich dňoch bez
    použitia zmaže a repo má strop 10 GB. Release nevyprší, takže hodina
    čítania z Drive sa nemá ako stratiť. Adresár je zároveň to, čo sa medzi
    behmi ukladá do cache, takže stiahnuté časti sa doň vracajú.
    """

    def __init__(self, path, release, repo, use_release=True):
        self.path = path
        self.release = release
        self.repo = repo
        self.use_release = use_release and bool(repo)
        self.lock = threading.Lock()
        self.hits_local = 0
        self.hits_release = 0
        self.made = 0
        os.makedirs(path, exist_ok=True)
        self.assets = self._list_assets() if self.use_release else set()

    def _list_assets(self):
        try:
            out = subprocess.run(
                ["gh", "release", "view", self.release, "--repo", self.repo,
                 "--json", "assets", "-q", ".assets[].name"],
                capture_output=True, text=True, check=True).stdout
            return set(out.split())
        except subprocess.CalledProcessError:
            return set()   # release ešte nie je – vyrobí sa pri prvom uploade

    def local(self, name):
        p = os.path.join(self.path, name)
        return p if os.path.exists(p) and os.path.getsize(p) > 0 else None

    def take(self, name):
        """Časť z cache alebo z releasu; None = treba ju spočítať."""
        p = self.local(name)
        if p:
            with self.lock:
                self.hits_local += 1
            return p
        if not self.use_release or name not in self.assets:
            return None
        ok = subprocess.run(
            ["gh", "release", "download", self.release, "--repo", self.repo,
             "--pattern", name, "--dir", self.path, "--clobber"],
            capture_output=True, text=True).returncode == 0
        if ok and self.local(name):
            with self.lock:
                self.hits_release += 1
            return os.path.join(self.path, name)
        return None

    def put(self, name):
        """Hotovú časť do releasu. Zlyhanie uploadu NESMIE zhodiť beh – časť
        je spočítaná a v adresári, takže build môže pokračovať; stratí sa len
        to, že ju nabudúce netreba počítať."""
        with self.lock:
            self.made += 1
        if not self.use_release:
            return
        with self.lock:
            if not self.assets:
                subprocess.run(
                    ["gh", "release", "create", self.release, "--repo", self.repo,
                     "--title", "Raster sklonu po častiach",
                     "--notes", "Medzivýsledok skál: sklon terénu v stotinách "
                                "stupňa (Int16, EPSG:3035) po častiach absolútnej "
                                "mriežky. Vyrába workers/slope-chunks.py."],
                    capture_output=True, text=True)
        r = subprocess.run(
            ["gh", "release", "upload", self.release, os.path.join(self.path, name),
             "--repo", self.repo, "--clobber"], capture_output=True, text=True)
        if r.returncode:
            print(f"::warning::Časť {name} sa nepodarila uložiť do releasu "
                  f"{self.release} – nabudúce sa bude počítať znova. "
                  f"{r.stderr.strip()[:200]}", flush=True)
        else:
            with self.lock:
                self.assets.add(name)


# ---------- výpočet jednej časti ----------

def slope_chunk(dem, chunk, res, out_path, work, env=None):
    """DEM → sklon pre jednu časť, orezaný presne na jej hranicu.

    Presah `MARGIN_PX` je preto, že `gdaldem slope` počíta z okolia bunky –
    bez neho by mal každý okraj časti pás nezmyselného sklonu a v mozaike by
    z toho boli šachovnicové švy. Zapisuje sa až orezané, takže dlaždice na
    seba sadnú presne.
    """
    ix, iy, x0, y0, x1, y1 = chunk
    m = MARGIN_PX * res
    dem_tif = os.path.join(work, f"dem-{ix}-{iy}.tif")
    slope_tif = os.path.join(work, f"slope-{ix}-{iy}.tif")

    def run(cmd):
        # Stderr MUSÍ byť v chybe. `capture_output` ho inak prehltne a
        # z padnutého behu ostane len „returned non-zero exit status 1"
        # a zoznam argumentov – teda presne to, čo sa nedá odladiť z logu.
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if r.returncode:
            raise RuntimeError(
                f"{cmd[0]} skončil s kódom {r.returncode} pre časť {ix},{iy}:\n"
                f"  {' '.join(cmd)}\n  {r.stderr.strip()[:500]}")
        return r
    try:
        run(["gdalwarp", "-q", "-overwrite", "-t_srs", METRIC,
             "-te", repr(x0 - m), repr(y0 - m), repr(x1 + m), repr(y1 + m),
             "-tr", repr(res), repr(res), "-r", "cubicspline",
             "-ot", "Float32", "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES",
             "-multi", "-ovr", "AUTO", dem, dem_tif])
        run(["gdaldem", "slope", "-q", "-compute_edges",
             "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES", dem_tif, slope_tif])
        # Float32 → Int16 v stotinách stupňa: mozaika celého pohoria sa vo
        # Float32 na disk runnera nezmestí a 0,01° je od presného Float32
        # nerozoznateľné (merané v rock-areas.py).
        tmp_out = out_path + ".part"
        # `-of GTiff` výslovne: ovládač sa háda z prípony a `.part` GDAL
        # nepozná („Cannot guess driver"). Prípona pritom musí ostať iná než
        # `.tif`, inak by rozrobený súbor vyzeral ako hotová časť.
        run(["gdal_translate", "-q", "-of", "GTiff", "-ot", "Int16",
             "-scale", "0", repr(90.0), "0", repr(90.0 * SCALE),
             "-projwin", repr(x0), repr(y1), repr(x1), repr(y0),
             "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=2", "-co", "TILED=YES",
             slope_tif, tmp_out])
        # Až premenovanie spraví časť „hotovou". Bez toho by prerušený beh
        # nechal v cache useknutý súbor, ktorý ďalší beh vezme ako platný.
        os.replace(tmp_out, out_path)
    finally:
        for f in (dem_tif, slope_tif):
            if os.path.exists(f):
                os.remove(f)
    return out_path


# ---------- beh ----------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", required=True, help="west,south,east,north v stupňoch")
    ap.add_argument("--res", default="auto",
                    help="mriežka sklonu v metroch, alebo `auto`")
    ap.add_argument("--out", default="slope-chunks",
                    help="adresár so skladom častí (ukladá sa do cache)")
    ap.add_argument("--work", default="", help="pracovný adresár (default: --out/tmp)")
    ap.add_argument("--dem", default="", help="lokálny DEM (.vrt/.tif)")
    ap.add_argument("--drive", action="store_true",
                    help="čítať priamo z DMR 5.0 na Drive cez HTTP Range")
    ap.add_argument("--jobs", type=int, default=4,
                    help="koľko častí naraz; pri --drive rozhoduje latencia, "
                         "nie pásmo, takže sa oplatí ísť vyššie")
    ap.add_argument("--release", default=os.environ.get("SLOPE_RELEASE", "dem-slope"))
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--no-store", action="store_true",
                    help="nepoužiť ani needkladať do releasu (testovací beh)")
    ap.add_argument("--rebuild", action="store_true",
                    help="prepočítať časti aj keď v sklade sú")
    ap.add_argument("--chunk-px", type=int, default=CHUNK_PX)
    ap.add_argument("--dem-cell-m", type=float, default=0.0,
                    help="bunka zdroja v metroch (na `--res=auto`); "
                         "pri --drive je to 1")
    ap.add_argument("--budget-min", type=float, default=100.0)
    ap.add_argument("--chunk-cells", type=float, default=150e6)
    ap.add_argument("--print-res", action="store_true",
                    help="len vypíš zvolenú mriežku a skonči")
    ap.add_argument("--stats", default="", help="kam zapísať štatistiku (key=value)")
    args = ap.parse_args()

    bbox = tuple(float(v) for v in args.bbox.split(","))
    x0, y0, x1, y1 = rock.to_metric(bbox)

    dem_cell = args.dem_cell_m or (1.0 if args.drive else 0.0)
    if not dem_cell and args.dem:
        dem_cell = rock.dem_cell_metres(args.dem, (bbox[1] + bbox[3]) / 2)[0]
    if str(args.res).strip().lower() in ("auto", "", "0"):
        # Tabuľka výberu ide na STDERR. Volajúci si berie mriežku cez
        # `RES=$(… --print-res)`, takže na stdout smie byť len to číslo –
        # inak sa mu do premennej dostane aj tých pätnásť riadkov rozvahy
        # a `--res` potom dostane zmes textu.
        import contextlib
        with contextlib.redirect_stdout(sys.stderr):
            res = rock.pick_res(x0, y0, x1, y1, args.chunk_cells, bbox,
                                args.budget_min, dem_cell)
    else:
        res = float(args.res)

    if args.print_res:
        print(f"{res:g}")
        return 0

    chunks = chunk_grid(bbox, res, args.chunk_px)
    if not chunks:
        print(f"::error::Ani jedna časť nezasahuje do územia {args.bbox}.")
        return 2
    # Poistka na počet častí. Každá časť je jeden asset v release a jedno kolo
    # réžie navyše, takže pri niekoľkých tisícoch prestane byť sklad výhodou a
    # stane sa z neho problém sám o sebe. Stane sa to ľahko: strana časti je
    # `chunk_px × res`, takže jemná mriežka ju zmenší a počet rastie s
    # druhou mocninou. Radšej to povedať hneď než po hodine.
    if len(chunks) > MAX_CHUNKS:
        side_km = args.chunk_px * res / 1000
        print(f"::error::Vyšlo {len(chunks)} častí po {side_km:g} km "
              f"(mriežka {res:g} m, {args.chunk_px}² px) a strop je "
              f"{MAX_CHUNKS}. Zdvihni --chunk-px (napr. "
              f"{args.chunk_px * 4}), zvoľ hrubšiu mriežku (rock_res) "
              f"alebo menší výrez (rock_area).")
        return 2

    work = args.work or os.path.join(args.out, "tmp")
    os.makedirs(work, exist_ok=True)
    store = Store(args.out, args.release, args.repo, use_release=not args.no_store)

    # ČO TO BUDE STÁŤ – pred prvým gdalwarpom. Trojhodinový beh, ktorý spadne
    # na timeout, je najhorší možný výsledok: minie celý rozpočet a nevyrobí
    # nič. Odkedy je sklad po častiach, patrí do odhadu aj to, čo v ňom už je –
    # druhý beh na tom istom pohorí nestojí nič a to má byť z plánu vidieť.
    side_km = args.chunk_px * res / 1000
    names = [chunk_name(ix, iy, res) for ix, iy, *_ in chunks]
    have = 0 if args.rebuild else sum(
        1 for n in names if store.local(n) or n in store.assets)
    todo = len(chunks) - have
    cells = len(chunks) * args.chunk_px ** 2
    est_s = todo * args.chunk_px ** 2 / rock.SLOPE_CELLS_PER_S

    print("── Plán sklonu ──────────────────────────────────────")
    print(f"  mriežka         {res:g} m")
    print(f"  častí           {len(chunks)} po {side_km:g}×{side_km:g} km "
          f"({args.chunk_px}² px)")
    print(f"  buniek          {cells / 1e9:.2f} mld.")
    print(f"  sklad           {args.out}"
          + (f" + release {args.release}" if store.use_release else " (release vypnutý)"))
    print(f"  z toho hotových {have} → počítať treba {todo}")
    print(f"  odhad           {rock.hms(est_s)}"
          + ("  (všetko je v sklade)" if not todo else ""))
    print(f"  mozaika na disk ~{cells / 1e9 * rock.MOSAIC_MB_PER_GCELL:.0f} MB")
    print("─────────────────────────────────────────────────────", flush=True)

    t0 = time.time()
    env = None
    stats_drive = None
    if args.drive:
        # Shim nad Drive, ID súborov aj prihlásenie vlastníka sú v
        # `dmr5-drive.py` – sem sa neopisujú, nech je jedno miesto, kde sa dá
        # vymeniť zdroj (a jedno miesto, ktoré vie, čím sa prihlasuje).
        dd = load("dmr5_drive", "dmr5-drive.py")
        base, sizes, stats_drive, creds = dd.serve_drive(0)
        dem = f"/vsicurl/{base}/{dd.TIF_NAME}"
        env = dd.drive.gdal_env()
        print(f"  zdroj: DMR 5.0 na Drive, "
              + ", ".join(f"{n} {s / 2**30:.1f} GiB" for n, s in sizes.items()))
        print(f"  prístup: {dd.auth.describe(creds)}")
    elif args.dem:
        dem = args.dem
        print(f"  zdroj: {dem}")
    else:
        print("::error::Chýba zdroj – zadaj --dem alebo --drive.")
        return 2

    done = [0]
    lock = threading.Lock()

    def one(chunk):
        ix, iy = chunk[0], chunk[1]
        name = chunk_name(ix, iy, res)
        path = os.path.join(args.out, name)
        got = None if args.rebuild else store.take(name)
        if got is None:
            slope_chunk(dem, chunk, res, path, work, env)
            store.put(name)
        with lock:
            done[0] += 1
            el = time.time() - t0
            eta = el / done[0] * (len(chunks) - done[0])
            print(f"  [{done[0]}/{len(chunks)}] {name} "
                  f"{'zo skladu' if got else 'spočítaná'} – "
                  f"{rock.hms(el)} za sebou, zostáva ~{rock.hms(eta)}", flush=True)
        return path

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        tiles = list(ex.map(one, chunks))

    vrt = os.path.join(args.out, f"slope-r{res:g}.vrt")
    subprocess.run(["gdalbuildvrt", "-q", vrt] + tiles, check=True)
    mb = sum(os.path.getsize(t) for t in tiles) / 1048576
    print(f"Mozaika sklonu: {len(tiles)} častí, {mb:.0f} MB → {vrt}")
    print(f"  zo skladu {store.hits_local} lokálne + {store.hits_release} "
          f"z releasu, novo spočítaných {store.made}, "
          f"celkom {rock.hms(time.time() - t0)}")
    if stats_drive:
        with stats_drive["lock"]:
            print(f"  z Drive {stats_drive['bytes'] / 1e9:.2f} GB "
                  f"v {stats_drive['requests']:,} požiadavkách")

    if args.stats:
        with open(args.stats, "w") as f:
            f.write(f"res={res:g}\nvrt={vrt}\nchunks={len(tiles)}\n"
                    f"from_cache={store.hits_local}\n"
                    f"from_release={store.hits_release}\n"
                    f"computed={store.made}\nmosaic_mb={mb:.0f}\n")
    print(f"slope_vrt={vrt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

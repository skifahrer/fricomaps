#!/usr/bin/env python3
"""
DMR 5.0 ako JEDEN GeoTIFF vo vzdialenom ZIPe – čítaný cez /vsizip//vsicurl/.

ČO JE V ARCHÍVE (zmerané behom 31184095104, `mode: len plán`):

    dmr5_0/dmr5_jtsk03.tif        151,43 GB   celé Slovensko, 1 m, jeden raster
    dmr5_0/dmr5_jtsk03.tif.ovr     46,28 GB   prehľadové úrovne (pyramídy)
    dmr5_0/dmr5_jtsk03.tfw                    world file
    dmr5_0/dmr5_jtsk03.tif.aux.xml / .xml     metadáta
    INFO_*.txt, 4× PDF, prehlad_lokalit_*.shp licencie a prehľad lokalít

Čakali sme textové výškové body po blokoch. Nie sú. Je to jeden súvislý
raster, takže sa nedá deliť po položkách archívu – a celé rozdeľovanie na
časti (workers/dmr5-chunk.py) tu nemá čo deliť.

ZATO SA DÁ ČÍTAŤ PRIAMO. GDAL vie `/vsizip//vsicurl/URL/cesta.tif`: ZIP číta
cez HTTP Range a GeoTIFF je dlaždicovaný, takže si vypýta len tie dlaždice,
ktoré potrebuje. Žiadne sťahovanie 151 GB na disk, žiadne medzivýsledky.

JEDNA VEC O TOM ALE PLATÍ A URČUJE CELÝ NÁVRH: položka v ZIPe je uložená
deflate-om (ÚGKK ju tak zabalil), a v deflate prúde sa nedá skočiť dopredu –
dá sa doň len rozbaliť od začiatku. Cena čítania je preto úmerná tomu, AKO
ĎALEKO V SÚBORE dáta ležia. Zmerané na napodobenine (44 MB ZIP, dlaždicovaný
DEFLATE GeoTIFF, HTTP server s Range):

    výrez na začiatku rastra      0,5 MB    1 % archívu
    výrez na konci rastra        44,1 MB  100 % archívu
    celý raster 1 m → 5 m        37,8 MB   (s .ovr, 1,1 s)
    to isté bez .ovr             46,1 MB   (2,7 s)

Z toho plynú pravidlá, podľa ktorých je tento script napísaný:

  1. JEDEN PRECHOD, NIE VIAC. Prevzorkovanie celej krajiny sa robí jedným
     `gdal_translate -tr`, nie po dlaždiciach – N výrezov by stálo N× cestu
     od začiatku súboru. Dlaždice 1°×1° sa krájajú až z hotového malého
     rastra.
  2. ČÍTAŤ SA MUSÍ DOPREDU. Výrez ide v dvoch krokoch: najprv
     `gdal_translate -projwin` (číta raster po riadkoch zhora nadol, teda
     sekvenčne) na disk, až potom `gdalwarp` z disku do WGS84. Warp priamo
     nad vzdialeným zdrojom si dlaždice pýta v poradí CIEĽOVEJ mriežky,
     a každý skok späť v deflate prúde znamená rozbaľovanie od začiatku –
     jeden krok späť môže stáť desiatky GB.
  3. PYRAMÍDY MIESTO RASTRA, KEĎ TO IDE. Pri cieli aspoň 2× hrubšom než
     zdroj sa číta z `.ovr` (46 GB) a nie z hlavného rastra (151 GB).
     Nespoliehame sa na to, že si `.ovr` nájde GDAL sám – vyberáme ho
     výslovne a je to vidieť v logu.
  4. SIDECARY SA NESMÚ SCHOVAŤ. `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`
     síce šetrí požiadavky, ale skryje `.ovr` aj `.tfw`.
  5. KAŽDÝCH 30 SEKÚND POVEDZ, ŽE ŽIJEŠ. Hodinový prechod cez 151 GB je
     inak v logu úplne ticho (GDAL kreslí percentá cez `\\r`, čo sa v logu
     GitHub Actions neobjaví). Heartbeat vypisuje prenesené bajty zo
     sieťovky, rýchlosť a odhad zvyšku.
  6. NAJPRV 16 BAJTOV, POTOM GDAL. Hlavička TIFFu nesie offset adresára
     dlaždíc (IFD) a ten rozhoduje o všetkom: keď je na začiatku, súbor sa
     otvorí za sekundu; keď je na konci, GDAL sa k nemu prehryzie len
     rozbalením celého člena – teda 151 GB ešte pred prvým pixelom. Beh
     31191478190 sa zasekol presne tu a v logu nebolo nič, z čoho by sa to
     dalo zistiť. Teraz sa tých 16 bajtov prečíta ako prvé a `gdalinfo` má
     strop (`--probe-timeout`), aby beh skončil s vysvetlením a nie po
     šiestich hodinách bez slova.

Použitie:
    python3 workers/dmr5-raster.py --url=URL --area=cele --grid-m=5 --out=tiles
    python3 workers/dmr5-raster.py --url=URL --area=vysoke_tatry --grid-m=1 \\
        --out=out --asset=ugkk-vysoke_tatry.tif
    python3 workers/dmr5-raster.py --url=URL --probe-only
"""
import argparse
import importlib.util
import json
import math
import os
import struct
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

# Prípony, ktoré vieme otvoriť ako raster. `.ovr` a `.aux.xml` sú sidecary –
# tie sa NEotvárajú samostatne, GDAL si ich nájde sám vedľa hlavného súboru.
RASTER_EXT = (".tif", ".tiff", ".img", ".vrt", ".dem")
SIDECAR = (".ovr", ".aux.xml", ".xml", ".tfw", ".prj", ".rrd")

# Keď sa georeferencia skladá z `.tfw`, projekcia v ňom nie je – `.tfw` nesie
# len čísla. Archív sa volá `sjtsk03`, čo je Krovák East North.
FALLBACK_EPSG = 8353

GDAL_ENV = {
    **os.environ,
    # Bez PAM by si gdalinfo -stats odkladal .aux.xml vedľa výstupov a tie by
    # sa viezli do releasu ako smetie.
    "GDAL_PAM_ENABLED": "NO",
    # Vyrovnávacia pamäť na dlaždice: čím väčšia, tým menej sa to isté číta
    # dvakrát. Runner má 16 GB, 2 GB je bezpečné.
    "GDAL_CACHEMAX": os.environ.get("GDAL_CACHEMAX", "2048"),
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": os.environ.get("VSI_CACHE_SIZE", str(256 * 1024 * 1024)),
    "GDAL_NUM_THREADS": "ALL_CPUS",
    # ZÁMERNE NEnastavujeme GDAL_DISABLE_READDIR_ON_OPEN – viď hlavička.
}


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True,
                          env=GDAL_ENV, **kw)


def rx_bytes():
    """Koľko bajtov prišlo zo siete od štartu stroja (bez loopbacku)."""
    total = 0
    try:
        for iface in os.listdir("/sys/class/net"):
            if iface == "lo":
                continue
            with open(f"/sys/class/net/{iface}/statistics/rx_bytes") as f:
                total += int(f.read().strip())
    except OSError:
        return None
    return total


class Heartbeat:
    """Každých pár sekúnd povie, že to žije – a hlavne AKO RÝCHLO.

    Bez toho je hodinový prechod cez 151 GB v logu úplne ticho a nedá sa
    odlíšiť od zaseknutého behu. GDAL síce kreslí percentá, ale s `\\r` bez
    nového riadku, takže sa v logu GitHub Actions neobjavia, kým krok
    neskončí. Prenesené bajty zo sieťovky sú navyše presne to číslo, ktoré
    pri čítaní cez /vsicurl/ zaujíma: hovoria, či sa vôbec sťahuje a koľko
    ešte ostáva.
    """

    def __init__(self, label, every=30, expect_bytes=None, watch=None):
        self.label = label
        self.every = every
        self.expect = expect_bytes
        self.watch = watch          # súbor, ktorého veľkosť sa sleduje
        self.stop = threading.Event()
        self.t0 = time.time()
        self.rx0 = rx_bytes()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        last_rx, last_t = self.rx0, self.t0
        while not self.stop.wait(self.every):
            now = time.time()
            el = now - self.t0
            parts = [f"[{el / 60:5.1f} min] {self.label}"]
            rx = rx_bytes()
            if rx is not None and self.rx0 is not None:
                got = rx - self.rx0
                rate = (rx - last_rx) / max(now - last_t, 1e-6)
                parts.append(f"stiahnuté {got / 1e9:6.2f} GB")
                parts.append(f"({rate / 1e6:5.1f} MB/s)")
                if self.expect:
                    parts.append(f"z ~{self.expect / 1e9:.0f} GB")
                # Odhad zvyšku má zmysel, len keď sa naozaj sťahuje. Pri
                # rýchlosti okolo nuly by vyšli tisíce minút a to je horšie
                # než nič nepovedať.
                if self.expect and rate > 1e6 and got < self.expect:
                    eta = (self.expect - got) / rate
                    parts.append(f"ostáva ~{eta / 60:.0f} min")
                last_rx, last_t = rx, now
            if self.watch and os.path.exists(self.watch):
                parts.append(f"výstup {os.path.getsize(self.watch) / 1e6:.1f} MB")
            print("  " + "  ".join(parts), flush=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        rx = rx_bytes()
        if rx is not None and self.rx0 is not None:
            print(f"  … {self.label}: spolu {(rx - self.rx0) / 1e9:.2f} GB "
                  f"za {(time.time() - self.t0) / 60:.1f} min", flush=True)
        return False


def run_live(cmd, label=None, expect_bytes=None, watch=None):
    """Dlhé kroky idú do logu naživo – hodinu tichého behu sa nedá odlíšiť
    od zaseknutého behu."""
    print("  $ " + " ".join(cmd), flush=True)
    if label is None:
        return subprocess.run(cmd, check=True, env=GDAL_ENV)
    with Heartbeat(label, expect_bytes=expect_bytes, watch=watch):
        return subprocess.run(cmd, check=True, env=GDAL_ENV)


def vsi_path(url, member):
    """`/vsizip//vsicurl/<url>/<cesta v archíve>`."""
    return f"/vsizip//vsicurl/{url}/{member}"


def pick_member(plan_path, explicit):
    """Ktorý súbor v archíve je ten raster. Sidecary sa preskakujú."""
    if explicit:
        return explicit
    plan = json.load(open(plan_path))
    best = None
    for e in plan["entries"]:
        low = e["name"].lower()
        if any(low.endswith(s) for s in SIDECAR):
            continue
        if not low.endswith(RASTER_EXT):
            continue
        if best is None or e["usize"] > best["usize"]:
            best = e
    if not best:
        raise SystemExit("::error::V pláne nie je ani jeden raster – pozri "
                         "inventár v súhrne behu a zadaj --member ručne.")
    return best["name"]


def load_remote_zip():
    spec = importlib.util.spec_from_file_location(
        "zip_remote", os.path.join(_HERE, "zip-remote.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tiff_layout(url, entry, log, timeout=60):
    """Kde v TIFFe leží adresár dlaždíc (IFD). Číta 16 BAJTOV.

    Toto je tá otázka, ktorá rozhoduje, či sa súbor otvorí za sekundu alebo
    za hodiny. Hlavička TIFFu nesie offset prvého IFD, a v ňom sú offsety
    všetkých dlaždíc. Keď je IFD na začiatku, GDAL ho prečíta hneď. Keď je na
    konci – a zapisovatelia ho tam bežne dávajú, lebo počas zápisu ešte
    nevedia, kde dlaždice skončia – musí sa k nemu GDAL prehrýzť.

    Nad obyčajným súborom je to jedno: `fseek` na koniec je zadarmo. Ale
    člen ZIPu zabalený deflate-om sa preskakovať NEDÁ, dá sa doň len rozbaliť
    od začiatku. IFD na konci 151 GB člena teda znamená, že samotné OTVORENIE
    súboru rozbalí celých 151 GB – ešte pred prvým pixelom.

    Vracia dict alebo None (keď sa hlavička nedá prečítať).
    """
    try:
        rz = load_remote_zip().RemoteZip(url, timeout=timeout, verbose=False)
        head = rz.head(entry, 64)
    except Exception as exc:
        log(f"::warning::Hlavičku `{entry['name']}` sa nepodarilo prečítať: {exc}")
        return None
    if len(head) < 16 or head[:2] not in (b"II", b"MM"):
        log(f"  `{entry['name']}`: nezačína ako TIFF ({head[:4]!r})")
        return None
    end = "<" if head[:2] == b"II" else ">"
    magic = struct.unpack(end + "H", head[2:4])[0]
    if magic == 42:
        kind, ifd = "TIFF", struct.unpack(end + "I", head[4:8])[0]
    elif magic == 43:
        kind, ifd = "BigTIFF", struct.unpack(end + "Q", head[8:16])[0]
    else:
        log(f"  `{entry['name']}`: neznáme magické číslo {magic}")
        return None
    size = entry["usize"] or 1
    share = 100.0 * ifd / size
    log(f"  {kind}, {'little' if end == '<' else 'big'}-endian, "
        f"adresár dlaždíc (IFD) na offsete {ifd:,} z {size:,} "
        f"= {share:.1f} % súboru")
    return {"kind": kind, "ifd": ifd, "size": size, "share": share}


def read_tfw(url, entry, log, timeout=60):
    """World file: 6 čísel, ktoré georeferencujú raster aj bez jeho hlavičky.

        pixel_x, rotácia, rotácia, pixel_y (záporný), stred ľavého horného
        pixela X, ten istý Y

    Kvôli tomuto sa dá `.ovr` použiť aj vtedy, keď sa hlavný raster vôbec
    neotvorí: veľkosť pixela z `.tfw` × pomer zmenšenia dá mriežku pyramídy
    a roh je ten istý. Bez `.tfw` by sme parametre museli vziať z rodiča –
    a práve k nemu sa nedostaneme.
    """
    try:
        rz = load_remote_zip().RemoteZip(url, timeout=timeout, verbose=False)
        txt = rz.head(entry, 4096).decode("ascii", "replace")
    except Exception as exc:
        log(f"::warning::`{entry['name']}` sa nedá prečítať: {exc}")
        return None
    vals = []
    for line in txt.splitlines():
        line = line.strip().replace(",", ".")
        if not line:
            continue
        try:
            vals.append(float(line))
        except ValueError:
            break
    if len(vals) < 6:
        log(f"::warning::`{entry['name']}` nemá 6 čísel ({len(vals)}).")
        return None
    log(f"  world file: pixel {vals[0]}×{abs(vals[3])} m, "
        f"ľavý horný pixel v {vals[4]:.1f}, {vals[5]:.1f}")
    return vals[:6]


def probe(vsi, log, timeout=900, no_sidecars=False, expect_bytes=None):
    """Hlavička rastra. Nad rozumne uloženým súborom je to pár stoviek kB.

    `timeout` tu nie je z opatrnosti: keď je IFD na konci deflate člena,
    `gdalinfo` sa nezasekne – on poctivo rozbaľuje 151 GB a vráti sa o pár
    hodín. To je horšie než chyba, lebo to vyzerá rovnako ako zamrznutie.
    Radšej to zastaviť a povedať prečo.
    """
    t0 = time.time()
    env = dict(GDAL_ENV)
    if no_sidecars:
        # Bez tohto GDAL pri otváraní hľadá .ovr, .aux.xml, .tfw… a keby bol
        # drahý niektorý z NICH, vyzeralo by to ako problém hlavného súboru.
        env["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"
    try:
        # Heartbeat AJ tu. Beh 31197330753 strávil 87 minút práve v tomto
        # jednom `gdalinfo` a v logu nebolo nič – heartbeat vtedy strážil len
        # kroky po sonde. Otvorenie súboru je pritom presne to miesto, kde sa
        # to zaseklo.
        with Heartbeat("otváranie rastra", expect_bytes=expect_bytes):
            r = subprocess.run(["gdalinfo", "-json", vsi], check=True, env=env,
                               capture_output=True, text=True, timeout=timeout)
        info = json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        log(f"::error::`gdalinfo` sa neozval ani za {timeout / 60:.0f} min. "
            f"Súbor sa neotvára – nie je to pomalá sieť, ale to, že sa GDAL "
            f"k adresáru dlaždíc dostane len rozbalením celého člena archívu. "
            f"Pozri offset IFD vyššie.")
        return None
    except subprocess.CalledProcessError as exc:
        log("::error::Raster sa nedá otvoriť cez /vsizip//vsicurl/: "
            f"{(exc.stderr or '').strip()[:400]}")
        return None
    band = info["bands"][0]
    gt = info["geoTransform"]
    wkt = (info.get("coordinateSystem") or {}).get("wkt", "")
    ovr = [o.get("size") for o in band.get("overviews", [])]
    out = {
        "size": info["size"],
        "geoTransform": gt,
        "wkt": wkt,
        "pixel": [abs(gt[1]), abs(gt[5])],
        "type": band["type"],
        "block": band.get("block"),
        "nodata": band.get("noDataValue"),
        "compression": (info.get("metadata", {}).get("IMAGE_STRUCTURE", {})
                        .get("COMPRESSION")),
        "crs": wkt.split('"')[1] if '"' in wkt else "?",
        "overviews": ovr,
        "seconds": round(time.time() - t0, 1),
    }
    log(f"Raster: {out['size'][0]}×{out['size'][1]} px, "
        f"mriežka {out['pixel'][0]}×{out['pixel'][1]}, {out['type']}")
    log(f"  CRS {out['crs']}, kompresia {out['compression']}, "
        f"dlaždica {out['block']}, nodata {out['nodata']}")
    # Pri vypnutom readdir sú tu vždy nuly – pyramídy si otvárame sami
    # (viď ovr_source), takže to nie je zlá správa.
    note = ovr if ovr else "(GDAL ich tu nevidí; .ovr otvárame sami)"
    log(f"  prehľadových úrovní: {len(ovr)} {note}")
    log(f"  hlavička prečítaná za {out['seconds']} s")
    return out


def find_sidecar(plan_path, member, suffix):
    """Nájde v pláne sidecar k `member` a vráti jeho položku, alebo None.

    Skúša OBE konvencie, lebo sa v jednom archíve miešajú:
      `dmr5_jtsk03.tif` + `.ovr` → `dmr5_jtsk03.tif.ovr`   (prípona sa PRIDÁ)
      `dmr5_jtsk03.tif` + `.tfw` → `dmr5_jtsk03.tfw`       (prípona sa NAHRADÍ)
    World file je vždy ten druhý prípad – a práve o neho sa opiera záchranná
    cesta cez pyramídy.
    """
    try:
        plan = json.load(open(plan_path))
    except (OSError, ValueError):
        return None
    wants = [(member + suffix).lower()]
    if suffix:
        wants.append((os.path.splitext(member)[0] + suffix).lower())
    for want in wants:
        for e in plan.get("entries") or []:
            if e["name"].lower() == want:
                return e
    return None


def ovr_source(url, member, info, grid_m, work, log, plan_path, timeout=900):
    """Keď je cieľ hrubší než zdroj, čítaj z pyramíd (.ovr), nie zo samotného
    rastra.

    Pri DMR 5.0 je to rozdiel medzi 46 GB a 151 GB. `.ovr` je obyčajný TIFF,
    len bez georeferencie – tá sa mu dolepí z rodiča (ten istý roh, pixel
    zväčšený v pomere veľkostí). Zmerané na napodobenine: výsledok je bit za
    bit ten istý ako z hlavného rastra.

    Nespoliehame sa na to, že si `.ovr` nájde GDAL sám: keď ho z akéhokoľvek
    dôvodu neuvidí, prečítal by celý raster a nikto by sa to nedozvedel.
    Takto je v logu čierne na bielom, z čoho sa číta.
    """
    src_cell = info["pixel"][0]
    if grid_m < 2 * src_cell:
        log(f"Cieľová mriežka {grid_m} m je blízko zdroju ({src_cell} m) – "
            f"čítam plné rozlíšenie.")
        return None, None
    side = find_sidecar(plan_path, member, ".ovr")
    if not side:
        log(f"::warning::V archíve nie je `{member}.ovr` – prevzorkovanie "
            f"prečíta plný raster. Bude to trvať.")
        return None, None

    vsi = vsi_path(url, side["name"])
    env = dict(GDAL_ENV, GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR")
    try:
        r = subprocess.run(["gdalinfo", "-json", vsi], check=True, env=env,
                           capture_output=True, text=True, timeout=timeout)
        oi = json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        log(f"::warning::`{side['name']}` sa neotvoril ani za "
            f"{timeout / 60:.0f} min – čítam plný raster.")
        return None, None
    except subprocess.CalledProcessError as exc:
        log(f"::warning::`{side['name']}` sa nedá otvoriť ({(exc.stderr or '')[:160]}) "
            f"– čítam plný raster.")
        return None, None

    ow, oh = oi["size"]
    pw, ph = info["size"]
    factor = pw / ow
    # Rozsah rodiča – ten sa nemení, mení sa len počet pixelov v ňom.
    gt = info["geoTransform"]
    ulx, uly = gt[0], gt[3]
    lrx, lry = ulx + gt[1] * pw, uly + gt[5] * ph

    wkt_file = os.path.join(work, "parent.wkt")
    os.makedirs(work, exist_ok=True)
    with open(wkt_file, "w") as f:
        f.write(info["wkt"])
    vrt = os.path.join(work, "ovr.vrt")
    run(["gdal_translate", "-q", "-of", "VRT", "-a_srs", wkt_file,
         "-a_ullr", repr(ulx), repr(uly), repr(lrx), repr(lry), vsi, vrt])

    log(f"Čítam z pyramíd: {side['name']}")
    log(f"  {ow}×{oh} px = mriežka {src_cell * factor:g} m "
        f"(zdroj má {pw}×{ph} px pri {src_cell} m)")
    log(f"  v archíve má {side['csize'] / 1e9:.2f} GB namiesto "
        f"{find_sidecar(plan_path, member, '')['csize'] / 1e9:.2f} GB "
        f"hlavného rastra")
    if oi["bands"][0].get("overviews"):
        log(f"  a sám má ďalšie úrovne: "
            f"{[o['size'] for o in oi['bands'][0]['overviews']]}")
    return vrt, side["csize"]


def wgs_bbox_to_src(bbox_wgs, wkt_file, log):
    """(W, S, E, N) vo WGS84 → obálka v projekcii zdroja, cez `gdaltransform`.

    Nie cez pyproj: job má nainštalovaný len GDAL. A nie len rohy – Krovák je
    kužeľové zobrazenie, takže obdĺžnik vo WGS84 nie je obdĺžnik v S-JTSK
    a rohy by výrez odrezali.
    """
    w, s, e, n = bbox_wgs
    pts, steps = [], 16
    for i in range(steps + 1):
        f = i / steps
        pts += [(w + (e - w) * f, s), (w + (e - w) * f, n),
                (w, s + (n - s) * f), (e, s + (n - s) * f)]
    inp = "\n".join(f"{x} {y}" for x, y in pts) + "\n"
    r = subprocess.run(["gdaltransform", "-s_srs", "EPSG:4326",
                        "-t_srs", wkt_file],
                       input=inp, capture_output=True, text=True, env=GDAL_ENV)
    if r.returncode:
        raise SystemExit(f"::error::gdaltransform zlyhal: {r.stderr[:300]}")
    xs, ys = [], []
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) >= 2:
            xs.append(float(f[0]))
            ys.append(float(f[1]))
    if not xs:
        raise SystemExit(f"::error::bbox {bbox_wgs} sa nedá prepočítať")
    log(f"  v projekcii zdroja: {min(xs):.0f},{min(ys):.0f} … "
        f"{max(xs):.0f},{max(ys):.0f}")
    return min(xs), min(ys), max(xs), max(ys)


def clamp_to_raster(box, info, pad_px, log):
    """Prienik okna s rozsahom rastra.

    Bez toho by `gdal_translate -projwin` presahujúcu časť DOPLNIL nulami –
    a nula je platná výška, takže by sa to potom prejavilo ako pás mora
    v mape, nie ako diera. (Presne to sa stalo pri prvom pokuse: minimum
    výšok spadlo zo 400 na 0.)
    """
    gt = info["geoTransform"]
    px, py = info["size"]
    rw, rn = gt[0], gt[3]
    re_, rs = rw + gt[1] * px, rn + gt[5] * py
    pad_x, pad_y = pad_px * abs(gt[1]), pad_px * abs(gt[5])
    w = max(box[0] - pad_x, min(rw, re_))
    s = max(box[1] - pad_y, min(rs, rn))
    e = min(box[2] + pad_x, max(rw, re_))
    n = min(box[3] + pad_y, max(rs, rn))
    if w >= e or s >= n:
        raise SystemExit("::error::Výrez nemá s rastrom spoločný ani jeden "
                         "pixel – skontroluj `area`.")
    if (w, s, e, n) != tuple(box):
        log(f"  orezané na rozsah rastra: {w:.0f},{s:.0f} … {e:.0f},{n:.0f}")
    return w, s, e, n


def ovr_fallback(url, member, work, log, plan_path, timeout):
    """Keď sa hlavný raster NEOTVORÍ, skús pyramídy samotné.

    Toto nie je optimalizácia, ale záchrana. Beh 31197330753 strávil 87 minút
    v jedinom `gdalinfo` nad hlavným rastrom a neotvoril ho – zato `.ovr` má
    46 GB namiesto 151 GB, takže má trikrát väčšiu šancu prejsť.

    Georeferencia nemôže prísť z rodiča (ten sa neotvára), tak sa poskladá
    z `.tfw`: veľkosť pixela × pomer zmenšenia a ten istý ľavý horný roh.
    Výsledok je model s hrubšou mriežkou – ale hotový model je viac než
    dokonalý model, ktorý sa nikdy nedopočíta.
    """
    side = find_sidecar(plan_path, member, ".ovr")
    tfw = find_sidecar(plan_path, member, ".tfw")
    if not side:
        log("::error::Hlavný raster sa neotvoril a `.ovr` v archíve nie je – "
            "iná cesta odtiaľto nevedie.")
        return None, None, None
    if not tfw:
        log("::error::Hlavný raster sa neotvoril a bez `.tfw` sa `.ovr` nedá "
            "georeferencovať.")
        return None, None, None

    log("Skúšam to obísť pyramídami – hlavný raster sa neotvoril.")
    w = read_tfw(url, tfw, log, timeout=60)
    if not w:
        return None, None, None

    vsi = vsi_path(url, side["name"])
    env = dict(GDAL_ENV, GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR")
    try:
        with Heartbeat("otváranie pyramíd", expect_bytes=side["csize"]):
            r = subprocess.run(["gdalinfo", "-json", vsi], check=True, env=env,
                               capture_output=True, text=True, timeout=timeout)
        oi = json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        log(f"::error::Ani `.ovr` sa neotvorilo za {timeout / 60:.0f} min. "
            "Tento archív sa cez /vsizip//vsicurl/ čítať nedá.")
        return None, None, None
    except subprocess.CalledProcessError as exc:
        log(f"::error::`.ovr` sa nedá otvoriť: {(exc.stderr or '')[:300]}")
        return None, None, None

    ow, oh = oi["size"]
    # `.tfw` popisuje rodiča; prvá úroveň pyramídy je zmenšenina, a pomer
    # zistíme z rozmerov – tie z rodiča nepoznáme, tak berieme štandardné 2×
    # a overíme to na rozsahu (Slovensko má ~450 × 250 km).
    px, py = abs(w[0]), abs(w[3])
    factor = 2.0
    span_km = ow * px * factor / 1000.0
    log(f"  pyramída {ow}×{oh} px; pri zmenšení {factor:g}× to je mriežka "
        f"{px * factor:g} m a šírka {span_km:.0f} km")
    if not (200 <= span_km <= 900):
        log(f"::warning::Šírka {span_km:.0f} km nesedí na Slovensko – "
            f"georeferencia pyramídy môže byť posunutá.")

    ulx = w[4] - px / 2.0          # .tfw dáva STRED pixela, GDAL chce roh
    uly = w[5] + py / 2.0
    lrx = ulx + ow * px * factor
    lry = uly - oh * py * factor
    os.makedirs(work, exist_ok=True)
    vrt = os.path.join(work, "ovr-fallback.vrt")
    run(["gdal_translate", "-q", "-of", "VRT",
         "-a_srs", f"EPSG:{FALLBACK_EPSG}",
         "-a_ullr", repr(ulx), repr(uly), repr(lrx), repr(lry), vsi, vrt])
    info = json.loads(run(["gdalinfo", "-json", vrt]).stdout)
    out = {
        "size": info["size"],
        "geoTransform": info["geoTransform"],
        "wkt": (info.get("coordinateSystem") or {}).get("wkt", ""),
        "pixel": [abs(info["geoTransform"][1]), abs(info["geoTransform"][5])],
        "type": info["bands"][0]["type"],
        "block": info["bands"][0].get("block"),
        "nodata": info["bands"][0].get("noDataValue"),
        "compression": None,
        "crs": f"EPSG:{FALLBACK_EPSG} (dolepené z .tfw)",
        "overviews": [o.get("size") for o in info["bands"][0].get("overviews", [])],
        "seconds": 0,
    }
    log(f"::warning::Ide sa z pyramíd – najjemnejšia dostupná mriežka je "
        f"{px * factor:g} m, nie {px:g} m.")
    return vrt, side["csize"], out


def degrees_per_metre(lat):
    """Krok mriežky v stupňoch pre daný krok v metroch na tejto šírke."""
    return (1.0 / (111320 * math.cos(math.radians(lat))), 1.0 / 110540)


def whole_country(vsi, grid_m, work, out_dir, log, expect=None):
    """Celá krajina: JEDEN prechod na hrubšiu mriežku, potom dlaždice.

    Krájať 1° dlaždice priamo zo zdroja by znamenalo prejsť ten deflate prúd
    toľkokrát, koľko je dlaždíc. Preto sa najprv prevzorkuje (sekvenčne, raz)
    a až malý výsledok sa krája.
    """
    os.makedirs(work, exist_ok=True)
    small = os.path.join(work, "dmr5-national.tif")
    t0 = time.time()
    log(f"Prevzorkovanie celej krajiny na {grid_m} m – jeden prechod, "
        f"toto je tá dlhá časť.")
    if expect:
        log(f"  čakám ~{expect / 1e9:.0f} GB zo siete")
    run_live(["gdal_translate", "-tr", str(grid_m), str(grid_m),
              "-r", "average", "-of", "GTiff",
              "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3",
              "-co", "TILED=YES", "-co", "BIGTIFF=YES",
              "-co", "NUM_THREADS=ALL_CPUS", vsi, small],
             label="prevzorkovanie", expect_bytes=expect, watch=small)
    mb = os.path.getsize(small) / 1048576
    log(f"  hotovo za {(time.time() - t0) / 60:.1f} min, {mb:.0f} MB")

    log("Krájanie na dlaždice 1°×1° vo WGS84…")
    run_live(["python3", os.path.join(_HERE, "dem-tiles.py"), "--out", out_dir, small])
    return small


def area_cut(vsi, bbox_wgs, grid_m, dest, work, log, info, expect=None):
    """Výrez v DVOCH krokoch – a to poradie je celá pointa.

    1. `gdal_translate -projwin` vyreže okno v pôvodnej projekcii a uloží ho
       na disk. Číta pritom raster po riadkoch zhora nadol, teda dopredu.
    2. `gdalwarp` prevedie ten malý miestny súbor do WGS84.

    Prečo nie rovno gdalwarp na vzdialený zdroj: warp si dlaždice pýta v takom
    poradí, v akom ich potrebuje pre CIEĽOVÚ mriežku, a to nie je poradie,
    v akom ležia v súbore. Každý skok späť v deflate prúde ale znamená
    rozbaľovanie od začiatku člena – jeden krok späť môže stáť desiatky GB.
    Sekvenčné čítanie tú pascu obchádza a warp potom pracuje nad diskom, kde
    je skákanie zadarmo.

    Cena prvého kroku je daná tým, ako ďaleko v súbore výrez leží: raster sa
    číta od severu, takže Tatry sú lacnejšie než Slovenský kras. S tým sa
    nedá spraviť nič, deflate sa preskakovať nedá.
    """
    os.makedirs(work, exist_ok=True)
    native = os.path.join(work, "vyrez-nativ.tif")
    wkt_file = os.path.join(work, "src.wkt")
    with open(wkt_file, "w") as f:
        f.write(info["wkt"])
    t0 = time.time()

    log(f"Výrez {bbox_wgs} pri {grid_m} m, krok 1/2: okno v pôvodnej "
        f"projekcii, čítané sekvenčne…")
    box = wgs_bbox_to_src(bbox_wgs, wkt_file, log)
    bw, bs, be, bn = clamp_to_raster(box, info, 4, log)
    run_live(["gdal_translate",
              "-projwin", repr(bw), repr(bn), repr(be), repr(bs),
              "-of", "GTiff", "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3",
              "-co", "TILED=YES", "-co", "BIGTIFF=YES",
              "-co", "NUM_THREADS=ALL_CPUS", vsi, native],
             label="čítanie okna", expect_bytes=expect, watch=native)
    log(f"  okno: {os.path.getsize(native) / 1048576:.0f} MB, "
        f"{(time.time() - t0) / 60:.1f} min")

    dx, dy = degrees_per_metre((bbox_wgs[1] + bbox_wgs[3]) / 2)
    log(f"Krok 2/2: prevod do WGS84 ({grid_m * dx:.7f}° × {grid_m * dy:.7f}°) "
        f"– už len z disku, rýchle.")
    run_live(["gdalwarp", "-overwrite",
              "-t_srs", "EPSG:4326",
              "-te", *[repr(v) for v in bbox_wgs],
              "-tr", repr(grid_m * dx), repr(grid_m * dy),
              "-r", "bilinear", "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
              "-of", "COG", "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3",
              "-co", "RESAMPLING=BILINEAR", "-co", "NUM_THREADS=ALL_CPUS",
              native, dest])
    os.remove(native)
    mb = os.path.getsize(dest) / 1048576
    log(f"  hotovo za {(time.time() - t0) / 60:.1f} min, {mb:.0f} MB")
    return dest


def resolve_area(area, areas_path):
    key = (area or "cele").strip()
    if key.lower() in ("", "cele", "cele_slovensko", "all", "vsetko"):
        return "celé Slovensko", None
    if "," in key:
        vals = [float(v) for v in key.split(",")]
        if len(vals) != 4:
            raise SystemExit(f"::error::bbox musí mať 4 čísla: {key}")
        return f"bbox {key}", tuple(vals)
    areas = json.load(open(areas_path))
    if key not in areas:
        known = ", ".join(k for k in areas if not k.startswith("_"))
        raise SystemExit(f"::error::neznámy výrez „{key}“. Známe: {known}")
    return areas[key]["name"], tuple(areas[key]["bbox"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--plan", default="plan.json",
                    help="z neho sa vyberie najväčší raster v archíve")
    ap.add_argument("--member", default="", help="cesta v archíve natvrdo")
    ap.add_argument("--area", default="cele")
    ap.add_argument("--areas", default=os.path.join(_HERE, "areas.json"))
    ap.add_argument("--grid-m", type=float, default=5.0)
    ap.add_argument("--out", default="out")
    ap.add_argument("--work", default="raster-work")
    ap.add_argument("--asset", default="", help="meno súboru pri výreze")
    ap.add_argument("--probe-only", action="store_true",
                    help="len prečítať hlavičku a skončiť")
    ap.add_argument("--no-ovr", action="store_true",
                    help="nečítať z pyramíd, ani keď to ide")
    ap.add_argument("--debug", action="store_true",
                    help="CPL_DEBUG=ON – každá požiadavka do logu. Len na "
                         "krátke behy, pri 151 GB je toho milión riadkov.")
    ap.add_argument("--probe-timeout", type=float, default=900,
                    help="sekundy, koľko sa čaká na otvorenie rastra")
    ap.add_argument("--summary", default="")
    ap.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = ap.parse_args()

    if args.debug:
        GDAL_ENV["CPL_DEBUG"] = "ON"
        GDAL_ENV["CPL_CURL_VERBOSE"] = "YES"

    lines = []

    def log(m):
        print(m, flush=True)
        lines.append(m)

    member = pick_member(args.plan, args.member)
    vsi = vsi_path(args.url, member)
    log(f"Položka v archíve: {member}")
    log(f"Cesta pre GDAL:    {vsi}")

    # ---- lacná diagnostika PRED tým, než sa pustí GDAL ----
    # 16 bajtov z každého súboru povie, či sa vôbec dá otvoriť rozumne rýchlo.
    # Beh 31191478190 sa zasekol presne tu a v logu nebolo nič, z čoho by sa
    # to dalo zistiť.
    log("Rozloženie rastrov v archíve (16 bajtov z každého):")
    main_entry = find_sidecar(args.plan, member, "")
    ovr_entry = find_sidecar(args.plan, member, ".ovr")
    lay = tiff_layout(args.url, main_entry, log) if main_entry else None
    lay_ovr = tiff_layout(args.url, ovr_entry, log) if ovr_entry else None
    for name, l in (("hlavný raster", lay), ("pyramídy", lay_ovr)):
        if l and l["share"] > 1.0:
            log(f"::warning::{name}: adresár dlaždíc je až na {l['share']:.0f} % "
                f"súboru. Člen ZIPu je deflate, takže sa k nemu GDAL dostane "
                f"len rozbalením všetkého pred ním – otvorenie samo o sebe "
                f"prečíta ~{l['ifd'] / 1e9:.1f} GB.")

    # Sondu púšťame BEZ hľadania sidecarov. Keby bol drahý niektorý z nich
    # (napr. `.ovr` so 46 GB), vyzeralo by to ako problém hlavného súboru –
    # a my ich aj tak otvárame sami, výslovne.
    t0 = time.time()
    forced = None
    info = probe(vsi, log, timeout=args.probe_timeout, no_sidecars=True,
                 expect_bytes=main_entry["csize"] if main_entry else None)
    if info is not None:
        log(f"  otvorené bez sidecarov za {time.time() - t0:.0f} s")
        gt = info["geoTransform"]
        if gt[:6] == [0.0, 1.0, 0.0, 0.0, 0.0, 1.0] or not info["wkt"]:
            # Georeferencia nie je v samotnom TIFFe – musí prísť z .tfw
            # alebo .aux.xml, a tie GDAL nájde len s povoleným readdir.
            log("Raster nemá georeferenciu v sebe – skúšam znova aj so "
                "sidecarmi (.tfw / .aux.xml).")
            info = probe(vsi, log, timeout=args.probe_timeout)
    if info is None:
        # Hlavný raster sa neotvoril. Namiesto toho, aby beh skončil naprázdno,
        # skúsime pyramídy – majú 46 GB namiesto 151 GB. Model s hrubšou
        # mriežkou je viac než žiadny model.
        forced, expect_fb, info = ovr_fallback(
            args.url, member, args.work, log, args.plan, args.probe_timeout)
        if info is None:
            log("::error::Raster sa nepodarilo otvoriť ani cez pyramídy. "
                "Ak je vyššie vidieť, že adresár dlaždíc leží hlboko v súbore, "
                "je to tá príčina: člen ZIPu je deflate, takže sa GDAL k nemu "
                "dostane len rozbalením všetkého pred ním.")
            return 3
    if args.github_output:
        with open(args.github_output, "a") as f:
            f.write(f"member={member}\n")
            f.write(f"px={info['size'][0]}x{info['size'][1]}\n")
            f.write(f"cell_m={info['pixel'][0]}\n")
            f.write(f"overviews={len(info['overviews'])}\n")

    if args.probe_only:
        log("Len sonda – nič sa nesťahovalo okrem hlavičky.")
        area_name = None
    else:
        area_name, bbox = resolve_area(args.area, args.areas)
        os.makedirs(args.out, exist_ok=True)

        # Z čoho sa bude čítať – hlavný raster, alebo pyramídy. Pri 151 GB vs
        # 46 GB je to tá jediná vec, ktorá rozhoduje o dĺžke behu, tak nech
        # je v logu vidieť, čo sa vybralo a prečo.
        if forced:
            # Hlavný raster sa neotvoril, ideme z pyramíd – nie je z čoho
            # vyberať a jemnejšie než ich mriežka to nepôjde.
            src, expect = forced, expect_fb
            if args.grid_m < info["pixel"][0]:
                log(f"::warning::Vypýtaná mriežka {args.grid_m} m je jemnejšia "
                    f"než pyramída ({info['pixel'][0]:g} m) – výsledok bude "
                    f"interpolovaný, nová informácia v ňom nepribudne.")
        else:
            src, expect = (None, None) if args.no_ovr else ovr_source(
                args.url, member, info, args.grid_m, args.work, log, args.plan,
                args.probe_timeout)
        if src is None:
            src = vsi
            main_entry = find_sidecar(args.plan, member, "")
            expect = main_entry["csize"] if main_entry else None
            log(f"Čítam hlavný raster ({(expect or 0) / 1e9:.2f} GB v archíve).")

        if bbox is None:
            whole_country(src, args.grid_m, args.work, args.out, log, expect)
        else:
            asset = args.asset or "ugkk-vyrez.tif"
            area_cut(src, bbox, args.grid_m, os.path.join(args.out, asset),
                     args.work, log, info, expect)
        made = sorted(f for f in os.listdir(args.out) if f.endswith(".tif"))
        total = sum(os.path.getsize(os.path.join(args.out, f)) for f in made)
        log(f"Hotovo: {len(made)} súborov, {total / 1048576:.0f} MB")
        if args.github_output:
            with open(args.github_output, "a") as f:
                f.write(f"files={len(made)}\n")

    if args.summary:
        with open(args.summary, "w") as f:
            f.write("## Raster priamo z archívu\n\n")
            f.write("| vec | hodnota |\n|---|---|\n")
            f.write(f"| položka | `{member}` |\n")
            f.write(f"| veľkosť | {info['size'][0]}×{info['size'][1]} px |\n")
            f.write(f"| mriežka zdroja | {info['pixel'][0]} m |\n")
            f.write(f"| CRS | {info['crs']} |\n")
            f.write(f"| kompresia | {info['compression']} |\n")
            f.write(f"| dlaždica | {info['block']} |\n")
            f.write(f"| prehľadové úrovne | {len(info['overviews'])} |\n")
            if area_name:
                f.write(f"| územie | {area_name} |\n")
                f.write(f"| cieľová mriežka | {args.grid_m} m |\n")
            f.write("\n<details><summary>Log</summary>\n\n```\n"
                    + "\n".join(lines) + "\n```\n\n</details>\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

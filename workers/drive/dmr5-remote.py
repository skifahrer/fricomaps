#!/usr/bin/env python3
"""
Vzdialený raster DMR 5.0: čítanie cez /vsizip//vsicurl/, sonda a pyramídy.

ČO JE TU. Všetko, čo sa týka OTVORENIA 151 GB rastra v cudzom ZIPe: prehľad
archívu, sonda, ktorá povie, čo v tom TIFFe naozaj je, `.tfw` a `.ovr`
sidecary – a dve cesty cez pyramídy. Výrez, mriežku a dlaždice rieši
`workers/drive/dmr5-raster.py`, ktorý si tento modul berie.

PREČO ZVLÁŠŤ. `dmr5-raster.py` mal 853 riadkov a v jednom takom súbore sa
nedá rýchlo nájsť, čo sa zmenilo (pravidlo 5 v CLAUDE.md, strop 800 stráži
`Kontrola · workflowy a workery`). Rez je v tom mieste, kde sa mení otázka: hore „čo sa dá
z toho archívu prečítať a ako rýchlo", dole „ktorý kus zeme z toho vyrezať".

DVE CESTY CEZ PYRAMÍDY, a nie sú to to isté:

  `ovr_source`    cieľ je HRUBŠÍ než zdroj → čítaj z `.ovr`, je to lacnejšie
                  (46 GB namiesto 151 GB) a výsledok je bit za bit ten istý.
  `ovr_fallback`  hlavný raster sa NEOTVORIL → skús `.ovr` samotné. Nie
                  optimalizácia, ale záchrana: beh 31197330753 strávil 87 min
                  v jedinom `gdalinfo` nad hlavným rastrom a neotvoril ho.

Georeferencia sa v prvom prípade dolepí z rodiča, v druhom z `.tfw` – rodič sa
totiž neotvára. Preto je druhá cesta hrubšia a hlási to varovaním.

Spúšťa sa ako modul, nie z príkazovej riadky:
    remote = load("dmr5_remote", "dmr5-remote.py")
"""
import importlib.util
import json
import os
import struct
import subprocess
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

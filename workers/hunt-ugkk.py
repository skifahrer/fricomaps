#!/usr/bin/env python3
"""
Lovec odkazov na ÚGKK DMR 5.0 (1 m LiDAR) – nájdi ich a stiahni ako artefakty.

PREČO ĎALŠÍ SKRIPT VEDĽA probe-dem-source.py: sonda testuje HYPOTÉZU –
„existuje ArcGIS ImageServer s DMR 5.0 na zbgis.skgeodesy.sk?". V behu
30997189220 na ňu odpovedala jasne: nie, lebo ten hostiteľ z runnera vôbec
neodpovedá (`URLError: timed out` na všetkých šiestich kandidátoch aj troch
WCS, dokopy 6 min 50 s čakania).

Tento skript sa nepýta na jednu hypotézu, ale HĽADÁ: prejde širokú sadu
vstupných bodov – vrátane takých, ktoré s `skgeodesy.sk` nemajú nič spoločné
(národný katalóg otvorených dát, ArcGIS Online, INSPIRE geoportál) – vytiahne
z nich všetky odkazy, ktoré vyzerajú na výškové dáta, a **čo sa dá, to aj
stiahne**, nech je výsledok súbor v artefakte, nie veta v logu.

Beží v štyroch fázach a každá má svoj výstup:

  1. DOSTUPNOSŤ hostiteľov. Bez tohto sa nedá odlíšiť „hádame zlé názvy
     služieb" od „na ten stroj sa z runnera nedá dostať". V zozname sú aj
     kontrolné hostitelia (pypi.org, arcgis.com): keď neodpovie ani jeden
     `.sk` a kontrolné áno, je to smerovanie/geoblok, nie naše URL.
  2. ZBER odkazov. Katalógy sa čítajú ako JSON (majú štruktúru), stránky sa
     grepnú regexom. Z nájdených katalógových záznamov sa ide o úroveň
     hlbšie – tam bývajú tie skutočné súbory.
  3. TRIEDENIE na priame súbory (.tif/.laz/.zip…), ArcGIS služby a OGC služby.
  4. SŤAHOVANIE. Priamy súbor sa najprv ťahá po kúskoch (Range) a overí sa
     podľa magických bajtov, či je to naozaj TIFF/LAZ/ZIP – až potom celý.
     Z ArcGIS služby sa vypýta malý výrez cez `exportImage`. Všetko padá do
     `--out-dir`, ktorý workflow vyhodí ako artefakt.

Skript zámerne **nekončí chybou**, keď nič nenájde: nález aj nenález je
výsledok merania a report sa musí dostať do artefaktu tak či tak. Nenulový
kód vráti len s `--strict`.

Použitie:
    python3 workers/hunt-ugkk.py --out-dir=ugkk-hunt --bbox=20.12,49.15,20.16,49.18
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_probe():
    """Načíta probe-dem-source.py ako modul.

    Má v mene pomlčku, takže sa nedá `import`nuť normálne – a kopírovať sem
    profily prehliadačov a `smart_get` by znamenalo, že sa raz rozídu.
    """
    path = os.path.join(HERE, "probe-dem-source.py")
    spec = importlib.util.spec_from_file_location("probe_dem_source", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


probe = _load_probe()
smart_get = probe.smart_get
host_reachable = probe.host_reachable
BROWSERS = probe.BROWSERS

# ─────────────────────────────────────────────────────────────────────────
# Vstupné body. Kľúčové je, že NIE SÚ len zo `skgeodesy.sk`: práve to je to,
# čo sa doteraz netestovalo. Ak sú nedostupné všetky `.sk` vládne hostitele,
# ale arcgis.com odpovie, je to iná diagnóza – a iné riešenie – než keď
# nefunguje len jeden server.
#
# `kind`:
#   catalog – vracia JSON so štruktúrou, číta sa cielene
#   page    – HTML/JS, grepne sa regexom
#   service – ArcGIS adresár služieb
#   control – nemá s ÚGKK nič spoločné, je tam len na porovnanie
SEEDS = [
    # Národný katalóg otvorených dát SR (CKAN API). Iný rezort, iný hostiteľ
    # než ZBGIS – ÚGKK sem svoje datasety registruje.
    {"kind": "catalog", "flavor": "ckan", "name": "data.slovensko.sk",
     "url": "https://data.slovensko.sk/api/3/action/package_search"
            "?q=DMR+OR+lidar+OR+%C3%BAgkk+OR+v%C3%BD%C5%A1kov%C3%BD&rows=50"},
    {"kind": "catalog", "flavor": "ckan", "name": "data.gov.sk (staršia inštancia)",
     "url": "https://data.gov.sk/api/3/action/package_search?q=DMR+lidar&rows=50"},
    # ArcGIS Online – verejné vyhľadávanie služieb. Keď má ÚGKK ImageServer
    # zaregistrovaný, je tu aj s presnou URL, a to bez hádania názvov.
    {"kind": "catalog", "flavor": "agol", "name": "ArcGIS Online (DMR 5.0)",
     "url": "https://www.arcgis.com/sharing/rest/search"
            "?f=json&num=50&q=DMR%205.0%20OR%20%22DMR%205%22%20Slovakia%20OR%20Slovensko"},
    {"kind": "catalog", "flavor": "agol", "name": "ArcGIS Online (skgeodesy)",
     "url": "https://www.arcgis.com/sharing/rest/search"
            "?f=json&num=50&q=owner%3Askgeodesy%20OR%20skgeodesy%20OR%20zbgis"},
    # INSPIRE / Register priestorových informácií – metadáta majú
    # `distributionInfo` s URL na sťahovacie služby.
    {"kind": "page", "name": "RPI CSW (GetRecords, DMR)",
     "url": "https://rpi.gov.sk/rpi_csw/service.svc/get?service=CSW&version=2.0.2"
            "&request=GetRecords&typeNames=csw:Record&resultType=results"
            "&elementSetName=full&maxRecords=50&constraintLanguage=CQL_TEXT"
            "&constraint_language_version=1.1.0&constraint=AnyText%20like%20%27%25DMR%25%27"},
    {"kind": "page", "name": "INSPIRE geoportál SR",
     "url": "https://inspire.gov.sk/"},
    # Stránky ÚGKK/GKÚ – tam, kde je popísané, ako sa DMR 5.0 vydáva.
    # Bez `www`: s ním vracia `www.geoportal.sk` chybu certifikátu
    # („no alternative certificate subject name matches target host name“),
    # čo nie je nedostupnosť, ale zle nastavené SNI na ich strane.
    {"kind": "page", "name": "geoportal.sk – LLS/DMR",
     "url": "https://geoportal.sk/sk/udaje/lls-dmr/"},
    {"kind": "page", "name": "geoportal.sk – na stiahnutie",
     "url": "https://geoportal.sk/sk/zbgis/na-stiahnutie/"},
    {"kind": "page", "name": "skgeodesy.sk",
     "url": "https://www.skgeodesy.sk/sk/ugkk/geodezia-kartografia/"},
    {"kind": "page", "name": "ZBGIS Mapový klient (konfigurácia)",
     "url": "https://zbgis.skgeodesy.sk/mkzbgis/"},
    # Statické úložisko otvorených dát – iný stroj než `zbgis.`, bez ArcGIS
    # a bez WAF-u. Práve preto má šancu tam, kde ostatné ÚGKK hostitele
    # timeoutujú. Odtiaľ berie DMR 3.5 aj `workers/fetch-dem-open.py`.
    {"kind": "page", "name": "opendata.skgeodesy.sk",
     "url": "https://opendata.skgeodesy.sk/"},
    {"kind": "page", "name": "opendata – priečinok DMR3_5",
     "url": "https://opendata.skgeodesy.sk/static/DMR3_5/"},
    # ArcGIS adresáre služieb – to, čo sonda skúšala doteraz.
    {"kind": "service", "name": "ZBGIS adresár služieb",
     "url": "https://zbgis.skgeodesy.sk/zbgis/rest/services"},
    # Iný hostiteľ než `zbgis.` – ten jediný z celého ÚGKK timeoutuje, takže
    # sa oplatí skúsiť aj jeho súrodenca. Odkaz naň prišiel z ArcGIS Online
    # (`SK_UGKK_ZBGIS_WMS_DMR`), nie z hádania.
    {"kind": "service", "name": "zbgisws adresár služieb",
     "url": "https://zbgisws.skgeodesy.sk/arcgis/rest/services"},
    # Kontrola: keď tieto odpovedia a `.sk` nie, nie je to o našich URL.
    {"kind": "control", "name": "pypi.org", "url": "https://pypi.org/"},
    {"kind": "control", "name": "arcgis.com", "url": "https://www.arcgis.com/"},
    {"kind": "control", "name": "ČÚZK (rovnaká služba v Česku)",
     "url": "https://ags.cuzk.cz/arcgis2/rest/services/dmr5g/ImageServer?f=json"},
]

# Čo v texte hľadať. Zámerne aj `.laz`/`.las`: keď sa dá stiahnuť mračno
# bodov, DMR sa z neho dá vyrobiť – to je stále lepšie než nič.
LINK_RE = re.compile(
    r'https?://[^\s"\'<>\\)\]]+?'
    r'(?:ImageServer|MapServer|FeatureServer|WCSServer|WMSServer|'
    r'/wcs|/wms|/wfs|/atom|\.tiff?|\.laz|\.las|\.zip|\.xyz|\.asc|\.vrt)'
    r'[^\s"\'<>\\)\]]*', re.I)

# Ktoré z nájdených odkazov majú vôbec šancu byť výškové dáta.
RELEVANT_RE = re.compile(
    r'dmr|dtm|dem|lidar|lls|vysk|výšk|elev|terrain|teren|height', re.I)

FILE_RE = re.compile(r'\.(tiff?|laz|las|zip|xyz|asc|vrt)(\?|$)', re.I)

# Magické bajty – aby sa HTML chybová stránka nevydávala za GeoTIFF.
MAGIC = [
    (b"II*\x00", "GeoTIFF (little-endian)"),
    (b"MM\x00*", "GeoTIFF (big-endian)"),
    (b"II+\x00", "BigTIFF"),
    (b"PK\x03\x04", "ZIP"),
    (b"LASF", "LAS/LAZ"),
]


def sniff(head):
    for magic, name in MAGIC:
        if head.startswith(magic):
            return name
    text = head[:200].lstrip().lower()
    if text.startswith(b"<!doctype") or text.startswith(b"<html"):
        return "HTML (chybová stránka, nie dáta)"
    if text.startswith(b"{") or text.startswith(b"["):
        return "JSON"
    if text.startswith(b"<?xml") or text.startswith(b"<"):
        return "XML"
    return f"neznáme ({head[:8]!r})"


def hosts_of(seeds):
    seen = []
    for s in seeds:
        h = urllib.parse.urlparse(s["url"]).hostname
        if h and h not in seen:
            seen.append(h)
    return seen


# ─────────────────────────────────────────────────────────────────────────
# 1. dostupnosť
def phase_hosts(seeds, out):
    print("\n══ 1. Dostupnosť hostiteľov ══════════════════════════════════")
    rows = []
    for host in hosts_of(seeds):
        kinds = {s["kind"] for s in seeds
                 if urllib.parse.urlparse(s["url"]).hostname == host}
        t0 = time.time()
        ok, why = host_reachable(f"https://{host}/")
        dt = time.time() - t0
        mark = "✓" if ok else "✗"
        tag = "kontrola" if kinds == {"control"} else ",".join(sorted(kinds))
        print(f"  {mark} {host:<34} {why:<28} {dt:5.1f}s  [{tag}]")
        rows.append((host, ok, why, dt, tag))
    with open(os.path.join(out, "hosts.tsv"), "w") as f:
        f.write("host\tdostupny\tako\tsekund\ttyp\n")
        for h, ok, why, dt, tag in rows:
            f.write(f"{h}\t{'ano' if ok else 'nie'}\t{why}\t{dt:.1f}\t{tag}\n")
    return {h: ok for h, ok, _, _, _ in rows}, rows


# ─────────────────────────────────────────────────────────────────────────
# 2. zber odkazov
def links_from_ckan(data):
    """CKAN: package_search → resources → url."""
    out = []
    for pkg in (data.get("result") or {}).get("results") or []:
        title = pkg.get("title") or pkg.get("name") or ""
        for res in pkg.get("resources") or []:
            url = res.get("url")
            if url:
                out.append((url, f"{title} / {res.get('name') or res.get('format')}"))
    return out


def links_from_agol(data):
    """ArcGIS Online: search → results → url (adresa služby)."""
    out = []
    for item in data.get("results") or []:
        url = item.get("url")
        if url:
            out.append((url, f"{item.get('title')} ({item.get('type')}) "
                             f"– {item.get('owner')}"))
    return out


def links_from_arcgis_dir(data, base):
    out = []
    for svc in data.get("services") or []:
        name, typ = svc.get("name", ""), svc.get("type", "")
        out.append((f"{base}/{name.split('/')[-1]}/{typ}", f"služba {name} ({typ})"))
    for folder in data.get("folders") or []:
        out.append((f"{base}/{folder}?f=json", f"priečinok {folder}"))
    return out


def phase_collect(seeds, reachable, timeout, out):
    print("\n══ 2. Zber odkazov ═══════════════════════════════════════════")
    found = {}   # url -> popis
    notes = []
    for seed in seeds:
        if seed["kind"] == "control":
            continue
        host = urllib.parse.urlparse(seed["url"]).hostname
        if not reachable.get(host):
            print(f"  – {seed['name']}: hostiteľ nedostupný, preskakujem")
            notes.append((seed["name"], "hostiteľ nedostupný", 0))
            continue
        data, how = smart_get(seed["url"], timeout=timeout)
        if data is None:
            why = how[0] if isinstance(how, list) else str(how)
            print(f"  ✗ {seed['name']}: {why}")
            notes.append((seed["name"], why, 0))
            continue

        hits = []
        flavor = seed.get("flavor")
        if flavor in ("ckan", "agol") or seed["kind"] == "service":
            try:
                parsed = json.loads(data.decode("utf-8", "replace"))
                if flavor == "ckan":
                    hits = links_from_ckan(parsed)
                elif flavor == "agol":
                    hits = links_from_agol(parsed)
                else:
                    hits = links_from_arcgis_dir(parsed, seed["url"])
            except ValueError:
                pass  # neprišiel JSON – spadne to nižšie na regex
        if not hits:
            text = data.decode("utf-8", "replace")
            hits = [(u, "z textu stránky") for u in LINK_RE.findall(text)]

        keep = []
        for url, desc in hits:
            if RELEVANT_RE.search(url) or RELEVANT_RE.search(desc):
                keep.append((url, desc))
        # Adresár služieb má zmysel vypísať celý – práve ten sme nikdy nevideli.
        if seed["kind"] == "service":
            keep = hits
        # Keď zdroj odpovedal, ale nič z neho nevypadlo, ulož surovú odpoveď.
        # Bez nej sa nedá zistiť, či je zlá naša otázka (`data.slovensko.sk`
        # vrátil HTTP 200 a nula záznamov – to nie je „nemá dáta“, to je
        # „pýtame sa zle“) alebo tam naozaj nič nie je.
        if not keep:
            raw = os.path.join(out, "raw",
                               re.sub(r"[^a-zA-Z0-9]+", "-", seed["name"])[:60] + ".txt")
            os.makedirs(os.path.dirname(raw), exist_ok=True)
            with open(raw, "wb") as f:
                f.write(data[:200000])
        for url, desc in keep:
            found.setdefault(url, f"{seed['name']}: {desc}")
        print(f"  ✓ {seed['name']} ({how}) – {len(hits)} odkazov, "
              f"{len(keep)} vyzerá na výškové dáta")
        for url, desc in keep[:15]:
            print(f"      {url}\n        {desc}")
        notes.append((seed["name"], f"OK ({how})", len(keep)))

    with open(os.path.join(out, "found-urls.txt"), "w") as f:
        for url, desc in sorted(found.items()):
            f.write(f"{url}\t{desc}\n")
    print(f"\n  spolu {len(found)} kandidátov → {out}/found-urls.txt")
    return found, notes


def phase_new_hosts(found, reachable, slow_timeout, retry_dead):
    """Dostupnosť hostiteľov, ktorí vypadli až zo zberu.

    V prvom behu sa toto nedialo a bolo to vidieť: `zbgisws.skgeodesy.sk`
    prišiel z ArcGIS Online a nikdy sa neotestoval.

    DRUHÝ POKUS NA MŔTVYCH je odteraz dobrovoľný (`--retry-dead`). Mal
    rozhodnúť otázku „pomalý server, alebo mŕtva cesta?" a rozhodol ju:
    v behoch 31075806874 a 31096745697 obidva ÚGKK hostitele timeoutli aj
    pri 30 s. Ďalšie opakovanie tej istej odpovede stojí ~150 s na hostiteľa,
    čo bolo 6,5 zo 7 minút celého behu. Keď bude treba overiť, či sa na ich
    strane niečo nezmenilo, zapne sa to prepínačom.
    """
    new = []
    for url in found:
        h = urllib.parse.urlparse(url).hostname
        if h and h not in reachable and h not in new:
            new.append(h)
    dead = [h for h, ok in reachable.items() if not ok]
    retry = dead if retry_dead else []
    if not new and not retry:
        if dead:
            print(f"\n══ 2b. Novoobjavení hostitelia ══ (žiadni; druhý pokus na "
                  f"{len(dead)} mŕtvych preskočený, zapni ho cez --retry-dead)")
        return []
    print("\n══ 2b. Novoobjavení hostitelia"
          f"{' (a druhý pokus na tých mŕtvych)' if retry else ''} ══")
    if dead and not retry:
        print(f"  druhý pokus na {len(dead)} mŕtvych preskočený "
              f"(--retry-dead ho zapne)")
    rows = []
    for host in new + retry:
        t0 = time.time()
        ok, why = host_reachable(f"https://{host}/", timeout=slow_timeout)
        dt = time.time() - t0
        tag = "objavený" if host in new else f"2. pokus, {slow_timeout:.0f}s"
        print(f"  {'✓' if ok else '✗'} {host:<34} {why:<30} {dt:5.1f}s  [{tag}]")
        reachable[host] = ok
        rows.append((host, ok, why, dt, tag))
    return rows


# ─────────────────────────────────────────────────────────────────────────
# 3. + 4. sťahovanie
def download_file(url, dest, max_mb, timeout):
    """Najprv kúsok a kontrola, čo to je – až potom celé.

    Šesťsto megabajtov HTML chybovej stránky je stále šesťsto megabajtov,
    takže sa najprv pýta `Range: bytes=0-65535` a pozrie sa na magické bajty.
    """
    probe_cmd = ["curl", "-sSL", "--max-time", str(timeout),
                 "-H", f"User-Agent: {BROWSERS[0][1]['User-Agent']}",
                 "-r", "0-65535", "-D", "-", url]
    try:
        r = subprocess.run(probe_cmd, capture_output=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        return {"ok": False, "why": "timeout pri hlavičkách"}
    if r.returncode != 0:
        return {"ok": False, "why": f"curl rc={r.returncode} "
                                    f"{r.stderr[:80].decode(errors='replace').strip()}"}
    head, _, body = r.stdout.partition(b"\r\n\r\n")
    headers = head.decode("latin-1", "replace")
    m = re.search(r"[Cc]ontent-[Ll]ength:\s*(\d+)", headers)
    part = int(m.group(1)) if m else len(body)
    m = re.search(r"[Cc]ontent-[Rr]ange:\s*bytes\s+\S+/(\d+)", headers)
    total = int(m.group(1)) if m else None
    kind = sniff(body)

    if kind.startswith("HTML") or kind in ("JSON", "XML"):
        # Nie je to dáta – ale ulož to, nech je vidieť, čo server naozaj vrátil.
        with open(dest + ".odpoved", "wb") as f:
            f.write(body)
        return {"ok": False, "why": f"nie sú to dáta: {kind}", "kind": kind,
                "bytes": len(body), "total": total}

    if total and total > max_mb * 1048576:
        with open(dest + ".ukazka", "wb") as f:
            f.write(body)
        return {"ok": True, "partial": True, "kind": kind, "bytes": len(body),
                "total": total,
                "why": f"{total/1048576:.0f} MB > strop {max_mb} MB – "
                       f"uložená len ukážka"}

    dl = ["curl", "-sSL", "--fail", "--max-time", str(timeout * 6),
          "-H", f"User-Agent: {BROWSERS[0][1]['User-Agent']}", "-o", dest, url]
    try:
        r = subprocess.run(dl, capture_output=True, timeout=timeout * 8)
    except subprocess.TimeoutExpired:
        return {"ok": False, "why": "timeout pri sťahovaní", "kind": kind}
    if r.returncode != 0 or not os.path.exists(dest):
        return {"ok": False, "why": f"curl rc={r.returncode}", "kind": kind}
    return {"ok": True, "kind": kind, "bytes": os.path.getsize(dest),
            "total": total, "part_bytes": part}


def export_from_arcgis(url, bbox, dest, timeout):
    """Malý výrez z ImageServeru – to isté, čo by robil build."""
    w, s, e, n = bbox
    px = max(1, min(2048, int((e - w) * 111320 * 0.66)))
    py = max(1, min(2048, int((n - s) * 110540)))
    q = urllib.parse.urlencode({
        "f": "json", "bbox": f"{w},{s},{e},{n}", "bboxSR": "4326",
        "imageSR": "4326", "format": "tiff", "pixelType": "F32",
        "size": f"{px},{py}"})
    data, how = smart_get(f"{url.rstrip('/')}/exportImage?{q}", timeout=timeout)
    if data is None:
        return {"ok": False, "why": f"exportImage: {how[0] if isinstance(how, list) else how}"}
    try:
        d = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return {"ok": False, "why": "exportImage nevrátil JSON"}
    if "href" not in d:
        return {"ok": False, "why": f"bez href: {str(d)[:80]}"}
    return download_file(d["href"], dest, max_mb=512, timeout=timeout)


def probe_service(url, timeout):
    """`?f=json` na ArcGIS službe – čo to vlastne je a akú má mriežku.

    Platí to pre ImageServer aj MapServer. MapServer nevie `exportImage`
    s float32 výškou, ale povie, či pod ním nie je ImageServer so správnym
    menom – a to je presne to, čo sme doteraz hádali.
    """
    data, how = smart_get(f"{url.rstrip('/')}?f=json", timeout=timeout)
    if data is None:
        return {"ok": False, "why": how[0] if isinstance(how, list) else str(how)}
    try:
        d = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return {"ok": False, "why": "nevrátil JSON"}
    if "error" in d:
        return {"ok": False, "why": str(d["error"].get("message", "chyba"))[:70]}
    return {"ok": True, "pixel_m": d.get("pixelSizeX"),
            "type": d.get("serviceDataType") or ",".join(
                l.get("name", "") for l in (d.get("layers") or [])[:4]),
            "desc": (d.get("serviceDescription") or d.get("description")
                     or "")[:90].replace("\n", " ")}


def probe_ogc(url, timeout):
    """GetCapabilities na WMS/WCS – žije to a čo ponúka?"""
    sep = "&" if "?" in url else "?"
    for service in ("WCS", "WMS"):
        q = f"{sep}service={service}&request=GetCapabilities"
        data, how = smart_get(url + q, timeout=timeout)
        if data is None:
            continue
        text = data.decode("utf-8", "replace")
        names = re.findall(r"<(?:wcs:)?(?:CoverageId|Name)>([^<]{1,60})</", text)
        if names:
            return {"ok": True, "service": service,
                    "layers": list(dict.fromkeys(names))[:12]}
    return {"ok": False, "why": "GetCapabilities nevrátilo vrstvy"}


def safe_name(url):
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", urllib.parse.urlparse(url).path.strip("/"))
    return (name or "subor")[-90:]


def phase_download(found, bbox, out, max_mb, total_mb, timeout, reachable):
    print("\n══ 3.+4. Sťahovanie ══════════════════════════════════════════")
    data_dir = os.path.join(out, "data")
    os.makedirs(data_dir, exist_ok=True)
    rows, got = [], 0

    # Na mŕtveho hostiteľa sa neklope druhýkrát. Fáza 1 aj 2b už zistili,
    # kto neodpovedá; `smart_get` pritom skúša štyri profily prehliadača
    # a potom curl, takže jedna URL na mŕtvom stroji stojí ~90 s. Pri desiatich
    # kandidátoch na `zbgis.skgeodesy.sk` to je štvrťhodina čakania na to isté,
    # čo sme vedeli po prvej fáze – v behu 31075806874 to job natiahlo z 90 s
    # na vyše 10 minút.
    def alive(url):
        h = urllib.parse.urlparse(url).hostname
        return reachable.get(h, True)

    skipped = [(u, d) for u, d in found.items() if not alive(u)]
    for url, desc in skipped:
        host = urllib.parse.urlparse(url).hostname
        rows.append((url, "–", f"preskočené: {host} neodpovedá (viď fáza 1)", desc))
    if skipped:
        print(f"  preskočených {len(skipped)} odkazov na nedostupných "
              f"hostiteľoch – klopať druhýkrát nemá zmysel")
    found = {u: d for u, d in found.items() if alive(u)}

    files = [(u, d) for u, d in found.items() if FILE_RE.search(u)]
    # Musí KONČIŤ na ImageServer: `…/ImageServer/WCSServer` je WCS, nie
    # ImageServer, a `exportImage` na ňom nemá čo hľadať.
    services = [(u, d) for u, d in found.items()
                if re.search(r"/ImageServer/?$", u, re.I)]
    # MapServer a WMS/WCS sa v prvej verzii vôbec neskúšali – a práve medzi
    # nimi prišiel z ArcGIS Online jediný skutočný odkaz od ÚGKK
    # (`LLS_DMR5/MapServer`, vlastník UGKK_SR). Zbierať odkazy a potom ich
    # neotvoriť je najhorší možný kompromis.
    maps = [(u, d) for u, d in found.items() if re.search(r"MapServer", u, re.I)]
    ogc = [(u, d) for u, d in found.items()
           if re.search(r"WCSServer|WMSServer|/wcs|/wms|service\.svc", u, re.I)]

    print(f"  priamych súborov: {len(files)}, ImageServerov: {len(services)}, "
          f"MapServerov: {len(maps)}, OGC služieb: {len(ogc)}")
    for i, (url, desc) in enumerate(files, 1):
        if got > total_mb * 1048576:
            print(f"  … strop {total_mb} MB vyčerpaný, zvyšok len v zozname")
            rows.append((url, "–", f"nad celkový strop {total_mb} MB", desc))
            continue
        dest = os.path.join(data_dir, f"{i:03d}-{safe_name(url)}")
        res = download_file(url, dest, max_mb, timeout)
        mark = "✓" if res.get("ok") else "✗"
        note = res.get("why") or f"{res.get('kind')}, {res.get('bytes', 0)/1048576:.1f} MB"
        print(f"  {mark} {url}\n      {note}")
        if res.get("ok") and os.path.exists(dest):
            got += os.path.getsize(dest)
        rows.append((url, mark, note, desc))

    for i, (url, desc) in enumerate(services, 1):
        dest = os.path.join(data_dir, f"svc-{i:03d}-{safe_name(url)}.tif")
        res = export_from_arcgis(url, bbox, dest, timeout)
        mark = "✓" if res.get("ok") else "✗"
        note = res.get("why") or f"{res.get('kind')}, {res.get('bytes', 0)/1024:.0f} kB"
        print(f"  {mark} exportImage {url}\n      {note}")
        rows.append((url, mark, note, desc))

    for url, desc in maps:
        meta = probe_service(url, timeout)
        if meta["ok"]:
            note = (f"MapServer žije – vrstvy „{meta['type']}“, "
                    f"pixel {meta.get('pixel_m')}. {meta['desc']}")
            mark = "~"
            # Ten istý názov ako ImageServer je najlepší tip, aký sa dá mať:
            # nie je hádaný, je odvodený od služby, ktorá naozaj existuje.
            guess = re.sub(r"/MapServer/?$", "/ImageServer", url)
            dest = os.path.join(data_dir, f"guess-{safe_name(guess)}.tif")
            res = export_from_arcgis(guess, bbox, dest, timeout)
            if res.get("ok"):
                mark = "✓"
                note += f" → ImageServer s tým istým menom FUNGUJE: {guess}"
                rows.append((guess, "✓", f"{res.get('kind')}, "
                                         f"{res.get('bytes', 0)/1024:.0f} kB",
                             "odvodené z MapServeru"))
            else:
                note += f" | ImageServer s tým istým menom: {res.get('why')}"
        else:
            mark, note = "✗", meta["why"]
        print(f"  {mark} MapServer {url}\n      {note}")
        rows.append((url, mark, note, desc))

    for url, desc in ogc:
        res = probe_ogc(url, timeout)
        if res.get("ok"):
            note = f"{res['service']} žije – vrstvy: {', '.join(res['layers'])}"
            mark = "~"
        else:
            mark, note = "✗", res["why"]
        print(f"  {mark} OGC {url}\n      {note}")
        rows.append((url, mark, note, desc))

    return rows, got


# ─────────────────────────────────────────────────────────────────────────
def write_report(out, host_rows, notes, dl_rows, got, bbox, took):
    path = os.path.join(out, "report.md")
    ok_files = [r for r in dl_rows if r[1] == "✓"]
    # Delí sa podľa domény, nie podľa toho, načo je hostiteľ v zozname:
    # www.arcgis.com je aj katalóg, aj kontrola, a keby sa počítal medzi
    # „naše“, jeho dostupnosť by zamaskovala, že zo Slovenska neodpovedá nič.
    sk_alive = [h for h, ok, _, _, _ in host_rows if ok and h.endswith(".sk")]
    ctrl_alive = [h for h, ok, _, _, _ in host_rows if ok and not h.endswith(".sk")]
    with open(path, "w") as f:
        f.write("# Lov na ÚGKK DMR 5.0 (1 m LiDAR)\n\n")
        f.write(f"Testovací výrez `{','.join(str(v) for v in bbox)}`, "
                f"beh trval {took:.0f} s.\n\n")

        f.write("## Výsledok\n\n")
        if ok_files:
            f.write(f"**Stiahnutých {len(ok_files)} súborov "
                    f"({got/1048576:.1f} MB)** – sú v artefakte v `data/`.\n\n")
        elif not sk_alive and ctrl_alive:
            f.write("**Z runnera nie je dostupný ani jeden slovenský hostiteľ, "
                    "kým kontrolné odpovedajú.** To nie je otázka zlých URL – "
                    "je to smerovanie alebo geoblok na strane siete. Hľadať "
                    "ďalšie názvy služieb nemá zmysel, kým sa nezmení cesta "
                    "k tým strojom (vlastný runner v SK, proxy, alebo jednorazové "
                    "stiahnutie ručne a zrkadlo do releasu).\n\n")
        else:
            f.write("**Nič sa stiahnuť nepodarilo.** Podrobnosti nižšie.\n\n")

        f.write("## 1. Dostupnosť hostiteľov\n\n")
        f.write("| hostiteľ | stav | ako | čas | typ |\n|---|:-:|---|--:|---|\n")
        for h, ok, why, dt, tag in host_rows:
            f.write(f"| `{h}` | {'✓' if ok else '✗'} | {why} | {dt:.1f} s | {tag} |\n")
        f.write("\n> Kontrolné hostitele nemajú s ÚGKK nič spoločné. Sú tu len "
                "preto, aby sa dalo odlíšiť „naše URL sú zlé“ od „na tie stroje "
                "sa z runnera nedá dostať“.\n\n")

        f.write("## 2. Vstupné body\n\n")
        f.write("| zdroj | stav | kandidátov |\n|---|---|--:|\n")
        for name, why, n in notes:
            f.write(f"| {name} | {why} | {n} |\n")
        f.write("\n")

        f.write("## 3. Sťahovanie\n\n")
        if dl_rows:
            f.write("| odkaz | stav | čo prišlo | odkiaľ |\n|---|:-:|---|---|\n")
            for url, mark, note, desc in dl_rows:
                f.write(f"| `{url}` | {mark} | {note} | {desc} |\n")
        else:
            f.write("Nebolo čo skúšať – zber nenašiel ani jeden kandidátsky odkaz.\n")
        f.write("\n")

        f.write("## Ak nič nevyšlo\n\n"
                "ÚGKK oficiálne vydáva DMR 5.0 cez ZBGIS Mapový klient "
                "(interaktívny export, najviac 400 km² na požiadavku) a cez "
                "vládny cloud. Obe cesty predpokladajú človeka s prehliadačom. "
                "Spoľahlivá cesta preto je: stiahnuť výrez raz ručne a nahrať "
                "ho do releasu `dem-ugkk` ako `ugkk-<vyrez>.tif` – build si ho "
                "potom už len stiahne, presne ako Sonnyho. Odkazy z toho "
                "exportu sa dajú dať aj priamo do inputu `direct_urls`.\n")
    print(f"\nReport: {path}")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="ugkk-hunt")
    ap.add_argument("--bbox", default="20.12,49.15,20.16,49.18",
                    help="malý testovací výrez (Vysoké Tatry)")
    ap.add_argument("--extra-urls", default="",
                    help="ďalšie vstupné body oddelené čiarkou alebo medzerou")
    ap.add_argument("--max-mb", type=float, default=200.0,
                    help="strop na jeden súbor; väčší sa uloží len ako ukážka")
    ap.add_argument("--total-mb", type=float, default=800.0,
                    help="strop na celý artefakt")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--slow-timeout", type=float, default=30.0,
                    help="timeout druhého pokusu (platí len s --retry-dead)")
    ap.add_argument("--retry-dead", action="store_true",
                    help="skúsiť nedostupných hostiteľov ešte raz s dlhším "
                         "timeoutom. Odpoveď už poznáme (ÚGKK timeoutuje aj "
                         "pri 30 s), takže je to vypnuté – zapni, keď chceš "
                         "overiť, či sa na ich strane niečo nezmenilo. "
                         "Stojí to ~150 s na hostiteľa.")
    ap.add_argument("--summary", default="", help="kam pripojiť report (GITHUB_STEP_SUMMARY)")
    ap.add_argument("--strict", action="store_true",
                    help="skončiť chybou, keď sa nič nestiahne")
    args = ap.parse_args()

    t0 = time.time()
    bbox = tuple(float(v) for v in args.bbox.split(","))
    os.makedirs(args.out_dir, exist_ok=True)

    seeds = list(SEEDS)
    for extra in re.split(r"[,\s]+", args.extra_urls.strip()):
        if extra:
            seeds.append({"kind": "page", "name": "zadané ručne", "url": extra})

    print(f"Lovím odkazy na ÚGKK DMR 5.0 – {len(seeds)} vstupných bodov, "
          f"výrez {args.bbox}")
    reachable, host_rows = phase_hosts(seeds, args.out_dir)
    found, notes = phase_collect(seeds, reachable, args.timeout, args.out_dir)

    # Známi kandidáti z dem-sources.json idú do sťahovania vždy, aj keď ich
    # zber tentoraz nenašiel. Inak by beh, v ktorom vypadne katalóg, ticho
    # preskočil práve tie URL, kvôli ktorým to celé vzniklo.
    try:
        src = json.load(open(os.path.join(HERE, "dem-sources.json")))["ugkk"]
        for u in list(src.get("candidates") or []) + list(src.get("wcs") or []):
            found.setdefault(u, "kandidát z workers/dem-sources.json")
    except (OSError, ValueError, KeyError) as exc:
        print(f"::warning::dem-sources.json sa nedá prečítať ({exc}) – "
              f"idú len odkazy zo zberu.")

    host_rows += phase_new_hosts(found, reachable, args.slow_timeout,
                                 args.retry_dead)
    dl_rows, got = phase_download(found, bbox, args.out_dir, args.max_mb,
                                  args.total_mb, args.timeout, reachable)
    path = write_report(args.out_dir, host_rows, notes, dl_rows, got, bbox,
                        time.time() - t0)

    if args.summary:
        with open(args.summary, "a") as f, open(path) as r:
            f.write(r.read())

    ok = any(r[1] == "✓" for r in dl_rows)
    if not ok:
        print("::warning::Nepodarilo sa stiahnuť ani jeden súbor s výškovými "
              "dátami – čo presne sa dialo, je v artefakte (report.md).")
    return 1 if (args.strict and not ok) else 0


if __name__ == "__main__":
    sys.exit(main())

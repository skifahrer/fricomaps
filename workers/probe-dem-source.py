#!/usr/bin/env python3
"""
Zistí, či a odkiaľ sa dá stiahnuť 1 m LiDAR od ÚGKK (DMR 5.0).

PREČO SAMOSTATNÝ SKRIPT: hostiteľov `*.skgeodesy.sk` nevidno z každej siete
a názvy služieb v ArcGIS adresári nie sú nikde zdokumentované. Namiesto
hádania sa teda spustí sonda z GitHub runnera, ktorá:

  1. stiahne adresár služieb (`/rest/services?f=json`) a vypíše VŠETKY,
     nech je vidieť, čo tam vlastne je,
  2. pre každého kandidáta z workers/dem-sources.json zistí metadáta
     (rozlíšenie pixela, rozsah, typ dát),
  3. z toho, čo odpovedalo, si vypýta malý výrez cez `exportImage` a overí,
     že prišiel naozaj GeoTIFF s očakávanou mriežkou.

Výsledok je tabuľka „funguje / nefunguje a prečo". Až podľa nej sa dá
`dem_source: ugkk` zapnúť s istotou, nie s nádejou.

Použitie:
    python3 workers/probe-dem-source.py [--bbox=W,S,E,N] [--summary=SÚBOR]
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "fricomaps-dem-probe/1 (+https://github.com/skifahrer/fricomaps)"}
# Malý výrez vo Vysokých Tatrách – Gerlachovský štít a okolie. Musí byť
# niekde, kde LiDAR určite je, inak by prázdna odpoveď vyzerala ako chyba.
TEST_BBOX = (20.12, 49.15, 20.16, 49.18)


# Krátke timeouty zámerne: keď server neodpovie, chceme to vedieť za sekundy,
# nie za sedem minút. V behu 30997189220 zabralo šesť kandidátov + tri WCS
# 6 min 50 s čistého čakania a výsledok bol rovnaký ako po 20 sekundách.
DEFAULT_TIMEOUT = 12


def host_reachable(url, timeout=8):
    """Odpovie vôbec ten stroj? Rozlišuje „server nie je" od „cesta nie je".

    Bez toho log povie len „URLError" pri každom kandidátovi a nie je z neho
    poznať, či sme hádali zlé názvy služieb, alebo je celý hostiteľ z runnera
    nedostupný. To sú dve úplne rôzne veci s úplne rôznym riešením.

    Testuje sa skutočným HTTPS požiadavkom na koreň, nie len TCP spojením:
    za HTTP proxy sa TCP otvorí vždy (na proxy) a až CONNECT sa odmietne,
    takže samotný socket by tvrdil „dostupné" aj tam, kde nie je.
    """
    p = urllib.parse.urlparse(url)
    root = f"{p.scheme}://{p.netloc}/"
    try:
        req = urllib.request.Request(root, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as exc:
        # 403/404 na koreni je v poriadku – server existuje a odpovedá.
        return True, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def fetch(url, params=None, timeout=DEFAULT_TIMEOUT, binary=False):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else json.loads(data.decode("utf-8", "replace"))


def probe_directory(url):
    print(f"\n── Adresár služieb: {url}")
    ok, why = host_reachable(url)
    if not ok:
        print(f"   ✗ hostiteľ neodpovedá ({why})")
        print(f"      → z tohto stroja sa na {urllib.parse.urlparse(url).hostname} "
              f"nedá dostať vôbec; nie je to otázka názvu služby.")
        return []
    try:
        d = fetch(url, {"f": "json"})
    except Exception as exc:
        print(f"   ✗ nedostupný: {type(exc).__name__}: {exc}")
        return []
    folders = d.get("folders", [])
    services = d.get("services", [])
    print(f"   ✓ odpovedal – {len(services)} služieb, {len(folders)} priečinkov")
    for f in folders:
        print(f"     priečinok: {f}")
    found = []
    for s in services:
        line = f"     {s.get('name')} ({s.get('type')})"
        print(line)
        if s.get("type") == "ImageServer":
            found.append(f"{url}/{s['name'].split('/')[-1]}/ImageServer")
    return found


def probe_image_server(url):
    """Metadáta služby: rozlíšenie, rozsah, typ. None = neodpovedala."""
    try:
        d = fetch(url, {"f": "json"})
    except urllib.error.HTTPError as exc:
        return {"ok": False, "why": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"ok": False, "why": f"{type(exc).__name__}"}
    if "error" in d:
        return {"ok": False, "why": str(d["error"].get("message", "chyba"))[:60]}
    px = d.get("pixelSizeX")
    ext = d.get("extent", {})
    return {
        "ok": True,
        "pixel_m": px,
        "type": d.get("serviceDataType", "?"),
        "wkid": (ext.get("spatialReference") or {}).get("latestWkid")
                or (ext.get("spatialReference") or {}).get("wkid"),
        "name": d.get("name", ""),
        "desc": (d.get("description") or "")[:80].replace("\n", " "),
    }


def probe_export(url, bbox):
    """Vypýta si malý výrez a overí, že prišiel GeoTIFF."""
    w, s, e, n = bbox
    # ~1 m mriežka: koľko pixelov je taký výrez v metroch
    px = max(1, min(2048, int((e - w) * 111320 * 0.66)))
    py = max(1, min(2048, int((n - s) * 110540)))
    try:
        d = fetch(url + "/exportImage", {
            "f": "json", "bbox": f"{w},{s},{e},{n}",
            "bboxSR": "4326", "imageSR": "4326",
            "format": "tiff", "pixelType": "F32",
            "size": f"{px},{py}",
        })
    except Exception as exc:
        return {"ok": False, "why": f"exportImage: {type(exc).__name__}"}
    if "href" not in d:
        return {"ok": False, "why": f"bez href: {str(d)[:70]}"}
    try:
        raw = fetch(d["href"], binary=True, timeout=90)
    except Exception as exc:
        return {"ok": False, "why": f"sťahovanie: {type(exc).__name__}"}
    # GeoTIFF začína "II*\0" (little endian) alebo "MM\0*" (big endian)
    if raw[:2] not in (b"II", b"MM"):
        return {"ok": False, "why": f"nie je TIFF ({raw[:12]!r})"}
    return {"ok": True, "bytes": len(raw), "px": f"{px}×{py}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="workers/dem-sources.json")
    ap.add_argument("--bbox", default=",".join(str(v) for v in TEST_BBOX))
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    args = ap.parse_args()
    bbox = tuple(float(v) for v in args.bbox.split(","))

    src = json.load(open(args.sources))["ugkk"]
    print(f"Hľadám: {src['label']}")
    print(f"Testovací výrez: {args.bbox} (Vysoké Tatry)")

    # 1. adresár – nech je vidieť, čo tam naozaj je
    discovered = probe_directory(src["directory"])

    # 2. kandidáti zo súboru + čo sa našlo v adresári
    todo, seen = [], set()
    for u in list(src["candidates"]) + discovered:
        if u not in seen:
            seen.add(u)
            todo.append(u)

    print(f"\n── Skúšam {len(todo)} služieb")
    rows, winner = [], None
    for u in todo:
        meta = probe_image_server(u)
        if not meta["ok"]:
            print(f"   ✗ {u}\n       {meta['why']}")
            rows.append((u, "✗", meta["why"], ""))
            continue
        px = meta.get("pixel_m")
        print(f"   ✓ {u}\n       pixel {px} m, {meta['type']}, EPSG:{meta['wkid']}, {meta['desc']}")
        exp = probe_export(u, bbox)
        if exp["ok"]:
            note = f"{exp['px']} px, {exp['bytes']//1024} kB"
            print(f"       exportImage OK – {note}")
            rows.append((u, "✓", f"pixel {px} m, {meta['type']}", note))
            if winner is None and px and px <= 2:
                winner = u
        else:
            print(f"       exportImage zlyhal: {exp['why']}")
            rows.append((u, "~", f"pixel {px} m, metadáta OK", exp["why"]))

    print("\n── Výsledok")
    if winner:
        print(f"✓ POUŽITEĽNÉ: {winner}")
        print("  Zapíš ho ako prvého kandidáta do workers/dem-sources.json")
        print("  a `dem_source: ugkk` bude fungovať.")
    else:
        usable = [r for r in rows if r[1] == "✓"]
        if usable:
            print("~ Niečo odpovedalo, ale nič s mriežkou ≤ 2 m – to nie je DMR 5.0.")
        else:
            print("✗ Ani jedna služba neodpovedala tak, aby sa dala použiť.")
        print("  ÚGKK oficiálne dáva DMR 5.0 cez ZBGIS Mapový klient (interaktívny")
        print("  export do 400 km²) a cez vládny cloud. Ak ImageServer neexistuje,")
        print("  jediná cesta je stiahnuť to raz ručne a nazrkadliť do releasu –")
        print("  presne tak, ako to robí workflow Update DEM pre Sonnyho.")

    if args.summary:
        with open(args.summary, "a") as f:
            f.write("# Sonda: ÚGKK DMR 5.0 (1 m LiDAR)\n\n")
            f.write(f"Testovací výrez `{args.bbox}` (Vysoké Tatry)\n\n")
            f.write("| služba | stav | metadáta | exportImage |\n|---|:-:|---|---|\n")
            for u, st, meta, note in rows:
                f.write(f"| `{u}` | {st} | {meta} | {note} |\n")
            f.write("\n")
            if winner:
                f.write(f"**✓ Použiteľné:** `{winner}`\n\n"
                        "Zapíš ho ako prvého kandidáta do `workers/dem-sources.json`"
                        " a `dem_source: ugkk` bude fungovať.\n")
            else:
                f.write("**✗ Nič použiteľné.** ÚGKK oficiálne dáva DMR 5.0 cez ZBGIS "
                        "Mapový klient (interaktívny export do 400 km²) a cez vládny "
                        "cloud. Ak ImageServer neexistuje, jediná cesta je stiahnuť "
                        "to raz ručne a nazrkadliť do releasu – tak, ako to robí "
                        "*Update DEM* pre Sonnyho.\n")
    return 0 if winner else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Čo je v archíve ÚGKK DMR 5.0 – prečítané zo 198 GB bez toho, aby sa čokoľvek
stiahlo.

Beží ako prvý job workflowu „Pripraviť DMR 5.0“ a číta iba centrálny adresár
ZIPu (pár MB na konci súboru). Z toho vyrobí:

  plan.json    zoznam položiek s offsetmi a veľkosťami
  inventár     do logu aj do súhrnu behu: priečinky, prípony, všetky mená
  layout       čo ten archív vlastne je

LAYOUT JE TU TO PODSTATNÉ. Prvá verzia tejto pipeline stála na predpoklade,
že archív sú textové výškové body po blokoch – teda že sa dá deliť po
položkách. Beh 31184095104 ukázal, že nie: je to JEDEN GeoTIFF
`dmr5_0/dmr5_jtsk03.tif` so 151 GB, k nemu 46 GB pyramíd a pár PDF.
Jeden súvislý raster sa po položkách archívu deliť nedá – zato ho GDAL
prečíta priamo cez `/vsizip//vsicurl/`, čo robí workers/dmr5-raster.py.

INVENTÁR NIE JE OZDOBA. Predchádzajúca verzia filtrovala položky podľa
prípon a ticho zahodila 16 z 19 súborov; v logu nebolo ani jedno meno, takže
sa nedalo zistiť, čo v archíve je. Preto sa teraz nefiltruje nič a zoznam ide
do súhrnu skôr, než sa čokoľvek ďalšie môže pokaziť.

Použitie:
    python3 workers/dmr5-plan.py --url=URL --out=plan.json --summary=sum.md
"""
import argparse
import fnmatch
import importlib.util
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

MB = 1024 * 1024

# Sidecary sa neotvárajú samostatne – GDAL si ich nájde vedľa hlavného súboru.
RASTER_EXT = (".tif", ".tiff", ".img", ".vrt", ".dem")
SIDECAR = (".ovr", ".aux.xml", ".xml", ".tfw", ".prj", ".rrd")


def load_remote_zip():
    """workers/zip-remote.py sa kvôli pomlčke nedá `import`-núť normálne."""
    spec = importlib.util.spec_from_file_location(
        "zip_remote", os.path.join(_HERE, "zip-remote.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def human(b):
    return f"{b / 1e9:.2f} GB" if b >= 1e9 else f"{b / 1e6:.1f} MB"


# ---------- inventár ----------

def describe_inventory(entries, log):
    """Vypíše, čo v archíve naozaj je – mená, veľkosti, kompresia, priečinky.

    Vracia riadky s priečinkami pre súhrn behu.
    """
    METHOD = {0: "uložené", 8: "deflate"}
    ext = {}
    for e in entries:
        x = os.path.splitext(e["name"])[1].lower() or "(bez prípony)"
        k = ext.setdefault(x, [0, 0])
        k[0] += 1
        k[1] += e["csize"]

    # Ktorá úroveň cesty archív naozaj delí. Keď je všetko pod jedným
    # `dmr5_0/`, je zoznam „jeden priečinok" nanič – zaujíma nás prvá úroveň,
    # na ktorej sa mená rozchádzajú.
    def group(depth):
        out = {}
        for e in entries:
            parts = e["name"].split("/")
            d = min(depth, len(parts) - 1)
            head = "/".join(parts[:d]) + "/" if d > 0 else "(koreň)"
            t = out.setdefault(head, [0, 0])
            t[0] += 1
            t[1] += e["csize"]
        return out

    top = group(1)
    for d in (2, 3):
        if len(top) > 1 or len(entries) <= 1:
            break
        deeper = group(d)
        if len(deeper) > len(top):
            top = deeper

    lines = []
    log("Priečinky v archíve:")
    for name, (n, sz) in sorted(top.items(), key=lambda kv: -kv[1][1]):
        log(f"  {name:40s} {n:6d} položiek  {human(sz)}")
        lines.append(f"| `{name}` | {n} | {human(sz)} |")
    log("Prípony:")
    for name, (n, sz) in sorted(ext.items(), key=lambda kv: -kv[1][1]):
        log(f"  {name:40s} {n:6d} položiek  {human(sz)}")

    show = entries if len(entries) <= 200 else entries[:100]
    log(f"Súbory ({len(show)} z {len(entries)}):")
    for e in show:
        ratio = (1 - e["csize"] / e["usize"]) * 100 if e["usize"] else 0
        log(f"  [{e['i']:5d}] {human(e['csize']):>10s} "
            f"({METHOD.get(e['method'], e['method'])}, -{ratio:.0f} %)  "
            f"{e['name']}")
    return lines


def filter_names(entries, include, exclude, log):
    """Výber podľa priečinka alebo vzoru – bez toho, aby sa čítali dáta.

    Vzor bez `*?[` sa berie ako cesta priečinka (stačí začiatok mena), inak je
    to `fnmatch`. Veľké/malé písmená sa neriešia. Pri DMR 5.0 nemá čo zúžiť
    (celý model je jeden súbor), ale iné archívy tak vyzerajú.
    """
    def match(pattern, name):
        p, n = pattern.strip().lower(), name.lower()
        if not p:
            return False
        if any(c in p for c in "*?["):
            return fnmatch.fnmatch(n, p)
        return n.startswith(p.rstrip("/") + "/") or n == p

    out = entries
    if include.strip():
        pats = [p for p in include.split(",") if p.strip()]
        out = [e for e in out if any(match(p, e["name"]) for p in pats)]
        log(f"Filter „{include}“: {len(out)} z {len(entries)} položiek")
    if exclude.strip():
        pats = [p for p in exclude.split(",") if p.strip()]
        before = len(out)
        out = [e for e in out if not any(match(p, e["name"]) for p in pats)]
        log(f"Bez „{exclude}“: {len(out)} z {before} položiek")
    return out


def dominant_raster(entries, share=0.5):
    """Je archív v podstate jeden veľký raster? Vráti tú položku, alebo None.

    „V podstate jeden" znamená, že jeden raster má aspoň polovicu bajtov –
    zvyšok sú pyramídy, metadáta a licencie.
    """
    total = sum(e["csize"] for e in entries) or 1
    best = None
    for e in entries:
        low = e["name"].lower()
        if any(low.endswith(s) for s in SIDECAR) or not low.endswith(RASTER_EXT):
            continue
        if best is None or e["csize"] > best["csize"]:
            best = e
    if best and best["csize"] / total >= share:
        return best
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", default="plan.json")
    ap.add_argument("--summary", default="", help="markdown do súhrnu behu")
    ap.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    ap.add_argument("--include", default="",
                    help="len tieto priečinky/vzory v archíve, oddelené čiarkou")
    ap.add_argument("--exclude", default="", help="tieto naopak vynechať")
    ap.add_argument("--timeout", type=float, default=60)
    args = ap.parse_args(argv)

    notes = []

    def log(msg):
        print(msg, flush=True)
        if msg.startswith("::warning::"):
            notes.append("⚠️ " + msg[len("::warning::"):])

    t0 = time.time()
    rz = load_remote_zip().RemoteZip(args.url, timeout=args.timeout)
    log(f"Archív: {args.url}")
    log(f"Veľkosť: {human(rz.size)} ({rz.size} B)")

    raw = rz.entries()
    entries = [e for e in raw
               if not e["name"].endswith("/") and e["usize"] > 0]
    log(f"Položiek: {len(raw)} (adresáre a prázdne: {len(raw) - len(entries)}, "
        f"súborov: {len(entries)})")

    folder_rows = describe_inventory(entries, log)

    entries = filter_names(entries, args.include, args.exclude, log)
    if not entries:
        log(f"::error::Filter „{args.include or '*'}“ nevybral ani jednu "
            f"položku. Mená vidíš v zozname vyššie.")
        return 4

    main_raster = dominant_raster(entries)
    layout = "raster" if main_raster else "ine"
    if main_raster:
        log(f"Archív je JEDEN raster: {main_raster['name']} "
            f"({human(main_raster['csize'])} z "
            f"{human(sum(e['csize'] for e in entries))})")
        log("  → číta sa priamo cez /vsizip//vsicurl/, nič sa nesťahuje")
    else:
        log("::warning::V archíve nie je jeden dominantný raster. Táto "
            "pipeline vie spracovať práve taký – pozri inventár vyššie.")

    entries.sort(key=lambda e: e["offset"])
    plan = {
        "url": args.url,
        "size": rz.size,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "layout": layout,
        "member": main_raster["name"] if main_raster else None,
        "entries": [{k: e[k] for k in
                     ("i", "name", "offset", "csize", "usize", "method", "nlen")}
                    for e in entries],
    }
    with open(args.out, "w") as f:
        json.dump(plan, f, ensure_ascii=False)
    log(f"Plán: {args.out} ({os.path.getsize(args.out) / MB:.1f} MB), "
        f"{time.time() - t0:.0f} s")

    if args.github_output:
        with open(args.github_output, "a") as f:
            f.write(f"layout={layout}\n")
            f.write(f"member={main_raster['name'] if main_raster else ''}\n")
            f.write(f"member_gb={(main_raster['csize'] / 1e9) if main_raster else 0:.1f}\n")
            f.write(f"entries={len(entries)}\n")

    if args.summary:
        with open(args.summary, "w") as f:
            f.write(f"## Čo je v archíve\n\n`{args.url}`\n\n")
            f.write(f"{human(rz.size)}, {len(raw)} položiek "
                    f"({len(entries)} súborov)\n\n")
            if main_raster:
                f.write(f"Hlavný raster: **`{main_raster['name']}`** "
                        f"({human(main_raster['csize'])})\n\n")
            if folder_rows:
                f.write("| priečinok | položiek | veľkosť |\n|---|--:|--:|\n")
                f.write("\n".join(folder_rows) + "\n\n")
            f.write("```\n")
            for e in (entries if len(entries) <= 200 else entries[:100]):
                f.write(f"{human(e['csize']):>10s}  {e['name']}\n")
            if len(entries) > 200:
                f.write(f"… a ďalších {len(entries) - 100}\n")
            f.write("```\n\n")
            if notes:
                f.write("\n".join(notes) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

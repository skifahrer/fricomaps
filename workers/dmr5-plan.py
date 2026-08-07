#!/usr/bin/env python3
"""
Plán rozobratia ÚGKK DMR 5.0: čo je v 198 GB archíve a ako sa rozdelí.

Beží ako prvý job workflowu „Rozobrať DMR 5.0“ a NESTIAHNE ani jeden výškový
bod – číta iba centrálny adresár ZIPu (pár MB) a z niekoľkých položiek prvé
kilobajty. Z toho vyrobí:

  plan.json    zoznam všetkých položiek s offsetmi + rozdelenie na časti
  matrix.json  `strategy.matrix` pre nasledujúci job (jedna položka = jedna časť)

ROZDELENIE NIE JE ĽUBOVOĽNÉ. Časť sa sťahuje jedným HTTP prenosom ako súvislý
úsek bajtov (viď workers/zip-remote.py), takže položky idú do častí V PORADÍ
OFFSETOV a nová časť sa začne vtedy, keď by:

  * komprimované dáta časti prekročili `--chunk-mb` (koľko sa reálne stiahne), alebo
  * úsek od prvej po poslednú položku prekročil `--span-mb` (koľko sa musí
    pretiecť cez sieť aj s dierami – to je to, čo bolí pri filtrovaní na výrez).

KDE KTORÁ POLOŽKA LEŽÍ sa zisťuje, nie háda. Meno súboru v archíve obsahuje
súradnice bloku, ale ÚGKK nikde nepíše v akom tvare a v akom poradí. Preto sa
z pár položiek prečítajú prvé riadky, tie dajú skutočné (east, north) a z nich
sa dopočíta, ktoré číslo v mene je východ a ktoré sever (`--calibrate`).

KEĎ KALIBRÁCIA NEPREJDE, `--area` KONČÍ CHYBOU. V behu 31182614668 to takto
nebolo a bola to chyba: vypýtal si Vysoké Tatry, poloha blokov sa nedala
zistiť a plán ticho navrhol stiahnuť 151 GB. Kto si vypýta pohorie, nechce
celé Slovensko. Zúžiť sa vtedy dá cez `--include` (priečinok v archíve), čo
je len meno, a teda to funguje vždy.

INVENTÁR JE HLAVNÝ VÝSTUP, keď ide o prvý beh. Ten istý beh nevypísal do logu
ani jedno meno súboru, takže sa nedalo zistiť, čo v archíve je – a filter na
prípony pritom ticho zahodil 16 z 19 súborov. Teraz sa žiadny súbor nezahadzuje
a zoznam (mená, veľkosti, uložené vs deflate) ide do logu aj do súhrnu behu
skôr, než sa čokoľvek ďalšie môže pokaziť.

Použitie:
    python3 workers/dmr5-plan.py --url=URL --out=plan.json --matrix=matrix.json \
        --area=vysoke_tatry --chunk-mb=2048 --summary=summary.md
"""
import argparse
import fnmatch
import importlib.util
import json
import os
import re
import sys
import time
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import sjtsk  # noqa: E402

MB = 1024 * 1024
NUM_RE = re.compile(r"-?\d+")


def load_remote_zip():
    """workers/zip-remote.py sa kvôli pomlčke nedá `import`-núť normálne."""
    spec = importlib.util.spec_from_file_location(
        "zip_remote", os.path.join(_HERE, "zip-remote.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resolve_area(area, areas_path):
    """`cele` | kľúč z areas.json | „W,S,E,N“ → (meno, bbox alebo None)."""
    key = (area or "cele").strip()
    if key.lower() in ("", "cele", "cele_slovensko", "all", "vsetko"):
        return "celé Slovensko", None
    if "," in key:
        vals = [float(v) for v in key.split(",")]
        if len(vals) != 4:
            raise ValueError(f"bbox musí mať 4 čísla, má {len(vals)}: {key}")
        return f"bbox {key}", tuple(vals)
    areas = json.load(open(areas_path))
    if key not in areas:
        known = ", ".join(k for k in areas if not k.startswith("_"))
        raise ValueError(f"neznámy výrez „{key}“. Známe: {known}")
    return areas[key]["name"], tuple(areas[key]["bbox"])


# ---------- kalibrácia mena súboru na súradnice ----------

def sample_indexes(n, k):
    """Rovnomerne rozložené indexy – nie prvých k, lebo na začiatku archívu
    býva `citajma.txt` a podobné neúdajové súbory."""
    if n <= k:
        return list(range(n))
    return [round(i * (n - 1) / (k - 1)) for i in range(k)]


def first_point(rz, entry, orient=None):
    """Prvý čitateľný bod položky (alebo None) + prvé riadky na ukážku."""
    try:
        head = rz.head(entry, 64 * 1024)
    except Exception as exc:
        return None, [f"(nedá sa prečítať: {exc})"]
    lines = head.split(b"\n")[:200]
    shown = [ln.decode("utf-8", "replace").strip() for ln in lines[:3]]
    for ln in lines:
        v = sjtsk.parse_numbers(ln)
        if len(v) >= 3:
            if orient is None:
                return (v[0], v[1], v[2]), shown
            e, n = orient(v[0], v[1])
            return (e, n, v[2]), shown
    return None, shown


def calibrate(rz, entries, count, log):
    """Zistí orientáciu stĺpcov a mapovanie „číslo v mene → súradnica“.

    Vráti (orient_name, orient_fn, mapping, sample_lines), kde mapping je
    {"east": (index, mierka, znamienko), "north": (...)} alebo None.
    """
    picks = [entries[i] for i in sample_indexes(len(entries), count)]
    raw, shown, shown_ok = [], [], False
    for e in picks:
        p, s = first_point(rz, e)
        raw.append((e, p))
        # Ukážka má byť z položky s bodmi, nie z `citajma.txt`, ktoré býva
        # v archíve prvé.
        if s and not shown_ok:
            shown = ([f"# {e['name']}"] + s) if p else s
            shown_ok = bool(p)
    pairs = [(p[0], p[1]) for _, p in raw if p]
    if not pairs:
        log("::warning::V ukážkových položkách nie je ani jeden čitateľný "
            "výškový bod – archív asi nie je textový (LAZ? vnorené ZIPy?).")
        return None, None, None, shown

    det = sjtsk.detect_orientation(pairs)
    if not det:
        log(f"::warning::Súradnice z ukážky nepadajú do Slovenska v žiadnom "
            f"z výkladov stĺpcov. Prvá dvojica: {pairs[0]}")
        return None, None, None, shown
    oname, ofn = det
    log(f"Orientácia stĺpcov: {oname} "
        f"(prvý bod → {tuple(round(v) for v in ofn(*pairs[0]))})")

    good = [(e, ofn(p[0], p[1])) for e, p in raw if p]
    nums = [NUM_RE.findall(e["name"]) for e, _ in good]
    width = Counter(len(x) for x in nums).most_common(1)[0][0]
    good = [g for g, x in zip(good, nums) if len(x) == width]
    nums = [[int(v) for v in x] for x in nums if len(x) == width]
    if len(good) < 3:
        log("::warning::Mená súborov nemajú rovnaký počet čísel – výrez podľa "
            "územia nebude fungovať.")
        return oname, ofn, None, shown

    def fit(axis):
        # Hľadá sa (index čísla, mierka, znamienko), pri ktorom je bod vždy
        # kúsok „vpravo hore“ od odvodeného rohu bloku a nikdy ďalej než 20 km.
        best = None
        for i in range(width):
            for scale in (1, 10, 100, 1000):
                for sign in (1, -1):
                    d = [c[axis] - sign * scale * n[i] for (_, c), n in zip(good, nums)]
                    if all(-1.0 <= x < 20000.0 for x in d):
                        score = max(d)
                        if best is None or score < best[0]:
                            best = (score, (i, scale, sign))
        return best[1] if best else None

    m_e, m_n = fit(0), fit(1)
    if not m_e or not m_n:
        log("::warning::Z mena súboru sa nedá odvodiť poloha bloku – výrez "
            f"podľa územia nebude fungovať. Ukážka mena: {good[0][0]['name']}")
        return oname, ofn, None, shown
    log(f"Poloha z mena: východ = číslo #{m_e[0]} × {m_e[1]} × {m_e[2]}, "
        f"sever = číslo #{m_n[0]} × {m_n[1]} × {m_n[2]}")
    return oname, ofn, {"east": m_e, "north": m_n, "width": width}, shown


def origin_of(name, mapping):
    nums = [int(v) for v in NUM_RE.findall(name)]
    if len(nums) != mapping["width"]:
        return None
    ie, se, ge = mapping["east"]
    inn, sn, gn = mapping["north"]
    return ge * se * nums[ie], gn * sn * nums[inn]


def block_size(origins):
    """Rozmer bloku = najčastejší rozostup medzi susednými rohmi."""
    def step(vals):
        u = sorted(set(vals))
        diffs = [b - a for a, b in zip(u, u[1:]) if b > a]
        if not diffs:
            return 0
        return Counter(diffs).most_common(1)[0][0]
    return step([o[0] for o in origins]), step([o[1] for o in origins])


# ---------- rozdelenie na časti ----------

def make_chunks(entries, chunk_bytes, span_bytes, max_chunks):
    """Súvislé skupiny v poradí offsetov (viď zip-remote.extract_span)."""
    chunks, cur = [], []
    csum = 0

    def flush():
        nonlocal cur, csum
        if not cur:
            return
        first, last = cur[0], cur[-1]
        span = last["offset"] + last["csize"] - first["offset"]
        boxes = [e["en"] for e in cur if e.get("en")]
        bbox = None
        if boxes:
            bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes)]
        chunks.append({
            "id": f"{len(chunks):03d}",
            "n": len(cur),
            "csize": csum,
            "usize": sum(e["usize"] for e in cur),
            "span": span,
            "bbox_en": bbox,
            "idx": [e["i"] for e in cur],
        })
        cur, csum = [], 0

    for e in entries:
        if cur:
            span = e["offset"] + e["csize"] - cur[0]["offset"]
            if csum + e["csize"] > chunk_bytes or span > span_bytes:
                flush()
        cur.append(e)
        csum += e["csize"]
    flush()
    if max_chunks > 0:
        chunks = chunks[:max_chunks]
    return chunks


def human(b):
    return f"{b / 1e9:.2f} GB" if b >= 1e9 else f"{b / 1e6:.1f} MB"


# ---------- inventár a výber priečinka ----------

def describe_inventory(entries, log):
    """Vypíše, čo v archíve naozaj je – mená, veľkosti, kompresia, priečinky.

    Pri `mode: len plán` je toto celý zmysel behu: 21 mien povie o archíve
    viac než akýkoľvek odhad. Vracia riadky pre súhrn behu.
    """
    METHOD = {0: "uložené", 8: "deflate"}
    lines = []
    ext = {}
    for e in entries:
        x = os.path.splitext(e["name"])[1].lower() or "(bez prípony)"
        k = ext.setdefault(x, [0, 0])
        k[0] += 1
        k[1] += e["csize"]

    # Ktorá úroveň cesty archív naozaj delí. Keď je všetko pod jedným
    # `DMR5_0/`, je zoznam „jeden priečinok" nanič – zaujíma nás prvá
    # úroveň, na ktorej sa mená rozchádzajú.
    def group(depth):
        out = {}
        for e in entries:
            parts = e["name"].split("/")
            # Súbor plytší než `depth` sa zaradí do svojho vlastného
            # priečinka, nie do zberného koša – inak by `citajma.txt` vyzeral
            # ako samostatná skupina.
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

    log("Priečinky v archíve:")
    for name, (n, sz) in sorted(top.items(), key=lambda kv: -kv[1][1]):
        log(f"  {name:40s} {n:6d} položiek  {human(sz)}")
        lines.append(f"| `{name}` | {n} | {human(sz)} |")
    log("Prípony:")
    for name, (n, sz) in sorted(ext.items(), key=lambda kv: -kv[1][1]):
        log(f"  {name:40s} {n:6d} položiek  {human(sz)}")

    # Celý zoznam, kým sa dá prečítať očami. Pri desiatkach tisíc položiek
    # by z toho bol nečitateľný val, tak sa vtedy vypíše len začiatok.
    show = entries if len(entries) <= 200 else entries[:100]
    log(f"Súbory ({len(show)} z {len(entries)}):")
    for e in show:
        ratio = (1 - e["csize"] / e["usize"]) * 100 if e["usize"] else 0
        log(f"  [{e['i']:5d}] {human(e['csize']):>10s} "
            f"({METHOD.get(e['method'], e['method'])}, -{ratio:.0f} %)  "
            f"{e['name']}")
    return lines, top, ext


# Sidecary sa neotvárajú samostatne – GDAL si ich nájde vedľa hlavného súboru.
RASTER_EXT = (".tif", ".tiff", ".img", ".vrt", ".dem")
SIDECAR = (".ovr", ".aux.xml", ".xml", ".tfw", ".prj", ".rrd")


def dominant_raster(entries, share=0.5):
    """Je archív v podstate jeden veľký raster? Vráti tú položku, alebo None.

    „V podstate jeden" znamená, že jeden raster má aspoň polovicu bajtov –
    zvyšok sú pyramídy, metadáta a licencie. Vtedy nemá zmysel deliť archív
    po položkách; delí sa až samotný raster, a to vie GDAL sám.
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


def filter_names(entries, include, exclude, log):
    """Výber podľa priečinka alebo vzoru – bez toho, aby sa čítali dáta.

    Toto je jediný filter, ktorý funguje VŽDY. Filter podľa územia potrebuje
    vedieť, kde ktorý blok leží, a to sa dá len vtedy, keď sa poloha dá
    prečítať z mena súboru. Priečinok v archíve je proti tomu istota.

    Vzor bez `*?[` sa berie ako cesta priečinka (stačí začiatok mena),
    inak je to `fnmatch`. Veľké/malé písmená sa neriešia.
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", default="plan.json")
    ap.add_argument("--matrix", default="matrix.json")
    ap.add_argument("--summary", default="", help="markdown do súhrnu behu")
    ap.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    ap.add_argument("--area", default="cele")
    ap.add_argument("--areas", default=os.path.join(_HERE, "areas.json"))
    ap.add_argument("--include", default="",
                    help="len tieto priečinky/vzory v archíve, oddelené čiarkou")
    ap.add_argument("--exclude", default="",
                    help="tieto naopak vynechať")
    ap.add_argument("--chunk-mb", type=float, default=2048)
    ap.add_argument("--span-mb", type=float, default=0,
                    help="strop na súvislý úsek vrátane dier (0 = 3× chunk-mb)")
    ap.add_argument("--max-chunks", type=int, default=0, help="0 = bez stropu")
    ap.add_argument("--calibrate", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=60)
    args = ap.parse_args(argv)

    notes = []

    def log(msg):
        print(msg, flush=True)
        if msg.startswith("::warning::"):
            notes.append("⚠️ " + msg[len("::warning::"):])

    area_name, area_bbox = resolve_area(args.area, args.areas)
    zr = load_remote_zip()
    t0 = time.time()
    rz = zr.RemoteZip(args.url, timeout=args.timeout)
    log(f"Archív: {args.url}")
    log(f"Veľkosť: {human(rz.size)} ({rz.size} B)")

    raw = rz.entries()
    entries = [e for e in raw
               if not e["name"].endswith("/") and e["usize"] > 0]
    skipped = len(raw) - len(entries)
    log(f"Položiek: {len(raw)} (adresáre a prázdne: {skipped}, "
        f"súborov: {len(entries)})")

    # Inventár PRED akýmkoľvek filtrom. Beh 31182614668 spadol práve na tom,
    # že filter na prípony ticho zahodil 16 z 19 súborov a v logu nebolo ani
    # jedno meno – nedalo sa zistiť, čo v archíve vlastne je. Toto je pri
    # `mode: len plán` to hlavné, čo z behu chceš.
    folder_rows, _tops, _exts = describe_inventory(entries, log)
    # Inventár ide do súhrnu HNEĎ, nie až na konci. Plán môže ešte skončiť
    # chybou (nepoužiteľný výrez, prázdny filter) a práve vtedy je zoznam
    # toho, čo v archíve je, to jediné, čo z behu potrebuješ.
    if args.summary:
        with open(args.summary, "w") as f:
            f.write(f"## Čo je v archíve\n\n`{args.url}`\n\n")
            f.write(f"{human(rz.size)}, {len(raw)} položiek "
                    f"({len(entries)} súborov)\n\n")
            if folder_rows:
                f.write("| priečinok | položiek | veľkosť |\n|---|--:|--:|\n")
                f.write("\n".join(folder_rows) + "\n\n")
                f.write("Ktorýkoľvek z nich sa dá spracovať sám – vlož ho do "
                        "`folder` pri spustení workflowu.\n\n")
            f.write("```\n")
            for e in (entries if len(entries) <= 200 else entries[:100]):
                f.write(f"{human(e['csize']):>10s}  {e['name']}\n")
            if len(entries) > 200:
                f.write(f"… a ďalších {len(entries) - 100}\n")
            f.write("```\n\n")

    kept = filter_names(entries, args.include, args.exclude, log)
    if not kept:
        log(f"::error::Filter „{args.include or '*'}“ nevybral ani jednu "
            f"položku. Mená vidíš v zozname vyššie.")
        return 4
    entries = kept
    nested = sum(1 for e in entries if e["name"].lower().endswith(".zip"))
    if nested:
        log(f"::warning::{nested} položiek sú vnorené ZIPy – rozbaľujú sa až "
            f"v spracovaní časti.")

    # Rozdeľovanie na časti dáva zmysel len vtedy, keď je archív z mnohých
    # samostatných súborov. DMR 5.0 taký NIE JE: beh 31184095104 ukázal, že
    # je to jeden 151 GB GeoTIFF (`dmr5_0/dmr5_jtsk03.tif`) plus 46 GB
    # pyramíd a pár PDF. Jeden súvislý raster sa po položkách archívu deliť
    # nedá – zato sa dá čítať priamo cez /vsizip//vsicurl/, čo robí
    # workers/dmr5-raster.py.
    main = dominant_raster(entries)
    layout = "raster" if main else "points"
    if main:
        log(f"Archív je JEDEN raster: {main['name']} "
            f"({human(main['csize'])} z {human(sum(e['csize'] for e in entries))})")
        log("  → nekrája sa na časti, číta sa priamo cez /vsizip//vsicurl/")

    # Kalibrácia hľadá výškové body v texte. Pri rastri by len minula čas
    # a napísala varovanie o tom, že text nenašla – lebo tam žiadny nie je.
    if layout == "raster":
        oname, ofn, mapping, shown = None, None, None, []
    else:
        oname, ofn, mapping, shown = calibrate(rz, entries, args.calibrate, log)

    origins = {}
    if mapping:
        for e in entries:
            o = origin_of(e["name"], mapping)
            if o:
                origins[e["i"]] = o
        bw, bh = block_size(list(origins.values()))
        log(f"Blokov s polohou: {len(origins)}/{len(entries)}, "
            f"blok {bw or '?'}×{bh or '?'} m")
        for e in entries:
            o = origins.get(e["i"])
            e["en"] = [o[0], o[1], o[0] + (bw or 0), o[1] + (bh or 0)] if o else None
    else:
        bw = bh = 0
        for e in entries:
            e["en"] = None

    # ---- výrez ----
    en_box = None
    # Pri rastri výrez rieši gdalwarp v dmr5-raster.py – tu sa nefiltruje nič.
    if area_bbox and layout == "points":
        if not mapping:
            # NIKDY nepokračovať „len tak" na celý archív. V behu 31182614668
            # sa presne to stalo: vypýtal si `vysoke_tatry`, poloha blokov sa
            # nedala zistiť, a plán ticho naplánoval 151 GB. Kto si vypýtal
            # pohorie, nechce stiahnuť celé Slovensko.
            log("::error::Výrez „" + args.area + "“ sa nedá použiť – poloha "
                "blokov sa z mien súborov nedá zistiť (pozri kalibráciu "
                "vyššie). Zúž to priečinkom v archíve (`folder`), alebo "
                "vedome daj `area: cele`.")
            return 4
        else:
            en_box = sjtsk.wgs_bbox_to_en(area_bbox)
            w, s, e_, n = en_box
            keep = [e for e in entries if e["en"] and
                    e["en"][0] <= e_ and e["en"][2] >= w and
                    e["en"][1] <= n and e["en"][3] >= s]
            log(f"Výrez {area_name}: {area_bbox} → S-JTSK "
                f"{tuple(round(v) for v in en_box)}")
            log(f"  položiek vo výreze: {len(keep)} z {len(entries)}")
            if not keep:
                log("::warning::Vo výreze nie je ani jedna položka. Skontroluj "
                    "`area` – a v logu vyššie kalibráciu polohy blokov.")
                return 4
            entries = keep

    entries.sort(key=lambda e: e["offset"])
    if layout == "raster":
        chunks = []
        total_c = main["csize"]
        total_u = main["usize"]
        total_span = 0
    else:
        chunk_bytes = int(args.chunk_mb * MB)
        span_bytes = int((args.span_mb or args.chunk_mb * 3) * MB)
        chunks = make_chunks(entries, chunk_bytes, span_bytes, args.max_chunks)

        picked = {i for c in chunks for i in c["idx"]}
        entries = [e for e in entries if e["i"] in picked]
        total_c = sum(c["csize"] for c in chunks)
        total_u = sum(c["usize"] for c in chunks)
        total_span = sum(c["span"] for c in chunks)
        log(f"Častí: {len(chunks)} — stiahne sa {human(total_span)} "
            f"(dáta {human(total_c)} → rozbalené {human(total_u)})")

        if len(chunks) > 256:
            log(f"::warning::{len(chunks)} častí, ale `strategy.matrix` má strop "
                f"256 jobov. Zväčši chunk_mb alebo zúž výrez.")

    plan = {
        "url": args.url,
        "size": rz.size,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "area": {"key": args.area, "name": area_name,
                 "bbox_wgs": list(area_bbox) if area_bbox else None,
                 "bbox_en": list(en_box) if en_box else None},
        "filter": {"include": args.include, "exclude": args.exclude},
        "layout": layout,
        "member": main["name"] if main else None,
        "orientation": oname,
        "name_mapping": mapping,
        "block_m": [bw, bh],
        "sample_lines": shown,
        "entries": [{k: e[k] for k in
                     ("i", "name", "offset", "csize", "usize", "method",
                      "nlen", "en")} for e in entries],
        "chunks": chunks,
    }
    with open(args.out, "w") as f:
        json.dump(plan, f, ensure_ascii=False)
    with open(args.matrix, "w") as f:
        json.dump([{"id": c["id"]} for c in chunks], f)
    log(f"Plán: {args.out} ({os.path.getsize(args.out) / MB:.1f} MB), "
        f"matrix: {args.matrix}")

    if args.github_output:
        # Kľúč výrezu ide do mena assetu v release, tak musí byť bez medzier
        # a diakritiky. „cele“ znamená celé Slovensko a ide inou cestou.
        akey = re.sub(r"[^a-z0-9_]+", "-", args.area.strip().lower()) or "cele"
        with open(args.github_output, "a") as f:
            f.write(f"layout={layout}\n")
            f.write(f"member={main['name'] if main else ''}\n")
            f.write(f"area_key={akey}\n")
            f.write(f"whole_country={'true' if area_bbox is None else 'false'}\n")
            f.write(f"chunks={len(chunks)}\n")
            f.write(f"entries={len(entries)}\n")
            f.write(f"download_bytes={total_span}\n")
            f.write(f"unpacked_bytes={total_u}\n")
            f.write(f"block_m={bw}\n")
            f.write(f"has_position={'true' if mapping else 'false'}\n")
            f.write("matrix=" + json.dumps([{"id": c["id"]} for c in chunks]) + "\n")

    if args.summary:
        with open(args.summary, "a") as f:
            f.write(f"## Plán rozobratia – {area_name}\n\n")
            f.write("| vec | hodnota |\n|---|---|\n")
            f.write(f"| archív | {human(rz.size)} |\n")
            f.write(f"| položiek v archíve | {len(raw)} |\n")
            f.write(f"| filter | `{args.include or '(všetko)'}` |\n")
            f.write(f"| položiek na spracovanie | {len(entries)} |\n")
            f.write(f"| podoba archívu | {'jeden veľký raster' if main else 'súbory po blokoch'} |\n")
            if main:
                f.write(f"| hlavný raster | `{main['name']}` ({human(main['csize'])}) |\n")
                f.write("| ako sa číta | priamo cez `/vsizip//vsicurl/`, "
                        "bez sťahovania na disk |\n")
            else:
                f.write(f"| **stiahne sa** | **{human(total_span)}** "
                        f"({100 * total_span / max(rz.size, 1):.1f} % archívu) |\n")
                f.write(f"| rozbalené dáta | {human(total_u)} |\n")
                f.write(f"| častí (jobov) | {len(chunks)} |\n")
                f.write(f"| orientácia stĺpcov | {oname or '—'} |\n")
                f.write(f"| blok | {bw or '?'}×{bh or '?'} m |\n")
            f.write(f"| čas plánu | {time.time() - t0:.0f} s |\n\n")
            if shown:
                f.write("Prvé riadky ukážkovej položky:\n\n```\n"
                        + "\n".join(shown) + "\n```\n\n")
            if notes:
                f.write("\n".join(notes) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

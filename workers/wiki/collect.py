#!/usr/bin/env python3
"""
Články z Wikipédie ku všetkému, čo v regióne odkazuje na wiki.

ČO TO ROBÍ. Z regionálneho PBF vyberie objekty (body, čiary aj plochy), ktoré
majú odkaz na Wikipédiu alebo Wikidata, poskládá z nich zoznam článkov
a stiahne ich – KAŽDÝ ČLÁNOK DO SAMOSTATNÉHO SÚBORU. Balík z toho robí
`workers/deploy/publish-map.py` (balík `wikipedia`), ktorý ho nahrá na Drive
vedľa mapy a zapíše do `maps.json`.

    data/region.osm.pbf
      → osmium tags-filter    len objekty s wiki odkazom (z 30 MB PBF ostane
                              rádovo 1 MB, takže ďalšie kroky sú sekundy)
      → osmium cat -f opl     typ, id a tagy KAŽDÉHO takého objektu
      → wikidata sitelinks    `Q…` → názov článku v požadovanom jazyku
      → api.php prop=extracts text článku (`--format=html` ho vezme z REST,
                              `--format=intro` len úvod, ale po dvadsiatich)
      → wiki-out/<jazyk>/<Názov>.txt + wiki-out/index.json

ODKAZ MÁ VIAC PODÔB a všetky sú v dátach:

    wikipedia=sk:Devín (hrad)        jazyk je v hodnote (najčastejšie u nás)
    wikipedia:sk=Devín (hrad)        jazyk je v kľúči
    wikipedia=https://sk.wikipedia.org/wiki/Devín   celé URL (býva to tak)
    wikidata=Q123456                 článok sa dohľadá cez sitelinks

`brand:wikipedia` a `operator:wikipedia` sa zámerne NEBERÚ: to nie je článok
o tom mieste, ale o firme, a v kraji by z toho boli stovky kópií článku o
Lidli. Kto ich chce, podá si ich cez `--keys`.

INDEX JE SÚČASŤ VÝSLEDKU. `index.json` hovorí, ktorý článok patrí ktorému OSM
objektu (typ, id, meno, súradnice) – bez neho je to hromada textov, ktorú sa
v mape nemá ako na čo napojiť. To isté platí pre články, ktoré sa nestiahli:
sú v `index.json` ako `chybne`, nie zamlčané (pravidlo 8 – tichý omyl je horší
než pád; „stiahlo sa 900 z 1000" musí byť napísané).

ZDVORILOSŤ K WIKIMEDII. Požiadavky idú sériovo, s krátkou pauzou, s
`User-Agent`, ktorý hovorí, kto sme, a pri 429/503 sa čaká `Retry-After`.
Wikidata id sa vypytujú po päťdesiatich naraz; PLNÝ TEXT ČLÁNKU sa ale
dávkovať NEDÁ (viď `INTRO_BATCH`), takže celý kraj je rádovo tisíc požiadaviek
a pár minút – preto to job hovorí v pláne dopredu.

Použitie:
    python3 workers/wiki/collect.py --pbf=data/region.osm.pbf --out=wiki-out
    python3 workers/wiki/collect.py --pbf=… --langs=sk,en --format=html
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Kto sme – Wikimedia to vyžaduje a bez toho vracia 403. Odkaz na repozitár je
# tam zámerne: keď niečo robíme zle, je z logu vidieť, komu to napísať.
UA = ("FricoMaps/1.0 (https://github.com/skifahrer/maptiles; "
      "mapy z OSM a DMR 5.0) python-urllib")

# Kľúče, v ktorých hľadáme odkaz. Sú to tie, čo hovoria o TOM objekte –
# `brand:`/`operator:`/`subject:` sa dajú pridať cez `--keys`.
KEYS = ("wikipedia", "wikidata")

# CELÝ TEXT SA DÁVKOVAŤ NEDÁ, a je to vlastnosť API, nie naše rozhodnutie:
# `prop=extracts` vráti viac článkov na jednu požiadavku LEN s `exintro`
# (teda len úvod). Bez neho dostaneš text prvého článku a na ostatné
# `continue` – čiže dávka po dvadsiatich ticho vráti jeden článok
# z dvadsiatich. Overené na `sk.wikipedia.org`: dávka troch názvov vrátila
# jeden text a dva „chýbajúce" články, ktoré pritom existujú.
#
# Preto: `text` a `html` idú po jednom článku, `intro` po dávkach.
INTRO_BATCH = 20
WIKIDATA_BATCH = 50

# Medzi požiadavkami sa krátko počká. Nie je to strop od Wikimedie, je to
# slušnosť: celý kraj je aj tak dvesto požiadaviek, takže nás to nezdrží.
PAUSE_S = 0.2
TRIES = 4


def log(msg):
    print(msg, flush=True)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


# ---------- 1. čo v regióne odkazuje na wiki ----------

def filter_pbf(pbf, dst, keys):
    """`osmium tags-filter` – z celého regiónu len objekty s wiki odkazom.

    Predfilter je tu na cenu: OPL celého regiónu je stovky megabajtov textu,
    kým odfiltrovaný PBF má rádovo megabajt a ďalšie kroky sú potom sekundy.
    """
    vyrazy = [f"nwr/{k}" for k in keys]
    run(["osmium", "tags-filter", "--overwrite", "-o", dst, pbf, *vyrazy])
    return dst


def objekty(pbf):
    """Objekty s tagmi z OPL – typ, id, tagy a súradnice (pri bodoch).

    OPL, nie `osmium export`: export skladá geometriu a objekt, ktorému ju
    nezloží (relácia bez úplných členov), ZAHODÍ – prišli by sme o článok,
    ktorý v dátach je. OPL je textový výpis KAŽDÉHO objektu; súradnice v ňom
    majú len body, čo je pri článku vedľajšie (poloha je bonus, nie dôvod).
    """
    out = run(["osmium", "cat", "-f", "opl", pbf]).stdout
    for line in out.splitlines():
        if not line:
            continue
        typ, telo = line[0], line[1:]
        if typ not in "nwr":
            continue
        oid = telo.split(" ", 1)[0]
        tags, lat, lon = {}, None, None
        for pole in telo.split(" "):
            if pole.startswith("T") and len(pole) > 1:
                for kv in pole[1:].split(","):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        # OPL escapuje `%XX`; bez odkódovania by v názve
                        # článku ostalo `%20` a stiahlo by sa nič.
                        tags[opl_unescape(k)] = opl_unescape(v)
            elif pole.startswith("x") and len(pole) > 1:
                lon = pole[1:]
            elif pole.startswith("y") and len(pole) > 1:
                lat = pole[1:]
        if tags:
            yield {"typ": {"n": "node", "w": "way", "r": "relation"}[typ],
                   "id": oid, "tags": tags,
                   "lat": float(lat) if lat else None,
                   "lon": float(lon) if lon else None}


def opl_unescape(text):
    """OPL escapuje `%<kód znaku v hexa>%` – teda `%20%` je medzera.

    Uzatvárajúce `%` je POVINNÉ a je to podstatné: bez neho by tento prepis
    zjedol aj percentá z URL (`…/wiki/Dev%C3%ADn` je percentové kódovanie
    UTF-8 bajtov, nie OPL) a z názvu článku by ostala kaša. URL rozkóduje
    `urllib.parse.unquote` na svojom mieste.
    """
    return re.sub(r"%([0-9A-Fa-f]{1,6})%",
                  lambda m: chr(int(m.group(1), 16)), text)


def odkaz(tags, keys, langs):
    """Z tagov objektu vytiahne `(jazyk, názov)` alebo `("wikidata", Q…)`.

    Poradie je dané: najprv `wikipedia` v požadovaných jazykoch, potom hociktorý
    jazyk, až nakoniec `wikidata` – článok v jazyku, ktorý si beh vypýtal, je
    lepší než ten, na ktorý ukazuje sitelink.
    """
    hodnoty = {}
    for k, v in tags.items():
        if not v.strip():
            continue
        if k == "wikipedia" or k.startswith("wikipedia:"):
            hodnoty.setdefault(*wiki_hodnota(k, v))
    for lang in langs:
        if lang in hodnoty:
            return lang, hodnoty[lang]
    if hodnoty:
        lang = sorted(hodnoty)[0]
        return lang, hodnoty[lang]
    for k in keys:
        if k.endswith("wikidata") and re.fullmatch(r"Q\d+", tags.get(k, "")):
            return "wikidata", tags[k]
    return None, None


def wiki_hodnota(key, value):
    """`(jazyk, názov)` z jednej podoby odkazu."""
    value = value.strip()
    if value.startswith("http"):
        # `https://sk.wikipedia.org/wiki/Devín` – jazyk je v hostname.
        u = urllib.parse.urlsplit(value)
        lang = u.netloc.split(".")[0]
        nazov = urllib.parse.unquote(u.path.rsplit("/", 1)[-1]).replace("_", " ")
        return lang, nazov
    if key.startswith("wikipedia:"):
        return key.split(":", 1)[1], value
    if re.match(r"^[a-z]{2,3}(-[a-z0-9-]+)?:", value):
        lang, nazov = value.split(":", 1)
        return lang, nazov.strip()
    # `wikipedia=Devín` bez jazyka: taký odkaz je nejednoznačný, tak sa berie
    # ako prvý požadovaný jazyk – to je jediné, čo o ňom vieme.
    return "", value


# ---------- 2. sieť ----------

class Api:
    """Volania na api.php a REST – sériovo, so slušnosťou a s meraním."""

    def __init__(self, pause=PAUSE_S):
        self.pause = pause
        self.pocet = 0
        self.bajtov = 0
        self.cakanie = 0.0

    def get(self, url):
        for pokus in range(1, TRIES + 1):
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Encoding": "identity"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    telo = r.read()
                self.pocet += 1
                self.bajtov += len(telo)
                time.sleep(self.pause)
                return telo
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and pokus < TRIES:
                    # Wikimedia povie, koľko čakať – tak sa to počká, a nie
                    # háda. Bez toho by opakovanie útočilo do toho istého.
                    cakaj = float(exc.headers.get("Retry-After") or 5 * pokus)
                    log(f"  Wikipedia povedala HTTP {exc.code}, čakám "
                        f"{cakaj:.0f} s ({pokus}. z {TRIES})")
                    self.cakanie += cakaj
                    time.sleep(cakaj)
                    continue
                if exc.code == 404:
                    return None
                if pokus >= TRIES:
                    raise
            except (urllib.error.URLError, TimeoutError) as exc:
                if pokus >= TRIES:
                    raise
                log(f"  sieť zlyhala ({exc}), skúšam znova "
                    f"({pokus}. z {TRIES})")
                time.sleep(2 * pokus)
        return None

    def json(self, url):
        telo = self.get(url)
        return json.loads(telo) if telo else None


def wikidata_na_nazvy(api, qids, langs):
    """`Q…` → `(jazyk, názov)` podľa sitelinks, v poradí jazykov."""
    out = {}
    qids = sorted(set(qids))
    for i in range(0, len(qids), WIKIDATA_BATCH):
        davka = qids[i:i + WIKIDATA_BATCH]
        url = ("https://www.wikidata.org/w/api.php?action=wbgetentities"
               "&props=sitelinks&format=json&formatversion=2&ids="
               + "|".join(davka))
        data = api.json(url) or {}
        for qid, ent in (data.get("entities") or {}).items():
            links = ent.get("sitelinks") or {}
            for lang in langs:
                sl = links.get(f"{lang}wiki")
                if sl and sl.get("title"):
                    out[qid] = (lang, sl["title"])
                    break
        log(f"  wikidata {min(i + WIKIDATA_BATCH, len(qids))}/{len(qids)} → "
            f"{len(out)} článkov")
    return out


def stiahni_texty(api, lang, nazvy, out_dir, fmt):
    """Články jedného jazyka do súborov. Vracia `{názov: (súbor, bajty)}`.

    Tri podoby, tri ceny:
      `text`  celý článok ako čistý text – JEDNA POŽIADAVKA NA ČLÁNOK, lebo
              `prop=extracts` viac plných textov naraz nevydá (viď INTRO_BATCH)
      `intro` len úvod, ale po dvadsiatich na požiadavku – na rýchly prehľad
      `html`  celý článok v HTML z REST API, tiež po jednom
    """
    hotove, chybne = {}, []
    jazyk_dir = os.path.join(out_dir, lang)
    os.makedirs(jazyk_dir, exist_ok=True)
    nazvy = sorted(set(nazvy))

    if fmt in ("html", "text"):
        for n, nazov in enumerate(nazvy, 1):
            if fmt == "html":
                url = (f"https://{lang}.wikipedia.org/api/rest_v1/page/html/"
                       + urllib.parse.quote(nazov.replace(" ", "_"), safe=""))
                telo = api.get(url)
                nazov_final, subor = nazov, slug(nazov) + ".html"
            else:
                url = (f"https://{lang}.wikipedia.org/w/api.php?action=query"
                       f"&prop=extracts|info&explaintext=1"
                       f"&exsectionformat=plain&exlimit=1&redirects=1"
                       f"&inprop=url&format=json&formatversion=2&titles="
                       + urllib.parse.quote(nazov))
                data = api.json(url) or {}
                page = ((data.get("query") or {}).get("pages") or [{}])[0]
                text = page.get("extract") or ""
                if page.get("missing") or not text.strip():
                    telo = None
                else:
                    nazov_final = page.get("title") or nazov
                    hlavicka = (f"{nazov_final}\n{page.get('fullurl', '')}\n"
                                f"{'=' * len(nazov_final)}\n\n")
                    telo = (hlavicka + text.strip() + "\n").encode()
                    subor = slug(nazov_final) + ".txt"
            # Postup sa vypíše VŽDY, aj keď článok nevyšel – inak posledný
            # riadok chýba práve vtedy, keď zlyhal posledný článok, a z logu
            # to vyzerá, že sa sťahovanie zaseklo.
            if n % 25 == 0 or n == len(nazvy):
                log(f"  {lang}: {n}/{len(nazvy)} článkov")
            if not telo:
                chybne.append(nazov)
                continue
            cesta = os.path.join(jazyk_dir, subor)
            with open(cesta, "wb") as f:
                f.write(telo)
            hotove[nazov] = (os.path.relpath(cesta, out_dir), len(telo))
        return hotove, chybne

    for i in range(0, len(nazvy), INTRO_BATCH):
        davka = nazvy[i:i + INTRO_BATCH]
        url = (f"https://{lang}.wikipedia.org/w/api.php?action=query"
               f"&prop=extracts|info&explaintext=1&exintro=1"
               f"&exsectionformat=plain&exlimit={INTRO_BATCH}"
               f"&redirects=1&inprop=url&format=json&formatversion=2&titles="
               + "|".join(urllib.parse.quote(t) for t in davka))
        data = api.json(url) or {}
        query = data.get("query") or {}
        # `redirects` a `normalized`: názov z OSM nemusí byť ten, pod ktorým
        # článok naozaj leží. Bez tejto mapy by sa článok stiahol, ale index
        # by ho k objektu nepriradil.
        prezvane = {r["from"]: r["to"] for r in query.get("redirects") or []}
        prezvane.update({r["from"]: r["to"]
                         for r in query.get("normalized") or []})
        podla_nazvu = {p.get("title"): p for p in query.get("pages") or []}
        for nazov in davka:
            page = podla_nazvu.get(prezvane.get(nazov, nazov))
            text = (page or {}).get("extract") or ""
            if not page or page.get("missing") or not text.strip():
                chybne.append(nazov)
                continue
            cesta = os.path.join(jazyk_dir, slug(page["title"]) + ".txt")
            hlavicka = (f"{page['title']}\n{page.get('fullurl', '')}\n"
                        f"{'=' * len(page['title'])}\n\n")
            telo = (hlavicka + text.strip() + "\n").encode()
            with open(cesta, "wb") as f:
                f.write(telo)
            hotove[nazov] = (os.path.relpath(cesta, out_dir), len(telo))
        log(f"  {lang}: {min(i + INTRO_BATCH, len(nazvy))}/{len(nazvy)} "
            f"úvodov")
    return hotove, chybne


def slug(nazov):
    """Názov článku → meno súboru: bez lomítok a bez medzier, inak ako je.

    Diakritika ostáva – meno súboru je to, čo človek v ZIPe hľadá očami, a
    `Devín (hrad).txt` sa číta lepšie než `devin_hrad.txt`.
    """
    return re.sub(r'[/\\:*?"<>|]', "_", nazov).replace(" ", "_")[:180]


# ---------- 3. beh ----------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbf", default="data/region.osm.pbf")
    ap.add_argument("--out", default="wiki-out")
    ap.add_argument("--langs", default="sk,en",
                    help="poradie jazykov (prvý, ktorý je, sa berie)")
    ap.add_argument("--keys", default=",".join(KEYS),
                    help="tagy, v ktorých sa hľadá odkaz")
    ap.add_argument("--format", default="text",
                    choices=("text", "intro", "html"),
                    help="`text` celý článok, `intro` len úvod (rýchle, "
                         "dávkové), `html` celý článok v HTML")
    ap.add_argument("--max", type=int, default=5000,
                    help="strop počtu článkov (0 = bez stropu)")
    ap.add_argument("--stats", default="", help="kam dopísať meranie (TSV)")
    args = ap.parse_args()

    langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    keys = [x.strip() for x in args.keys.split(",") if x.strip()]
    if not os.path.exists(args.pbf):
        print(f"::error::PBF {args.pbf} neexistuje – job `wiki` ho dostáva "
              f"z prípravy ako artefakt `pbf`.")
        return 1
    os.makedirs(args.out, exist_ok=True)
    t0 = time.time()

    maly = filter_pbf(args.pbf, os.path.join(args.out, "wiki.osm.pbf"), keys)
    log(f"Predfilter: {os.path.getsize(args.pbf) / 1e6:.0f} MB → "
        f"{os.path.getsize(maly) / 1e6:.1f} MB")

    # Odkaz → objekty, ktoré naň ukazujú. Jeden článok má často viac objektov
    # (hrad ako bod aj ako plocha), a sťahovať ho dvakrát netreba.
    kde = {}
    bez_odkazu = 0
    for o in objekty(maly):
        lang, nazov = odkaz(o["tags"], keys, langs)
        if not nazov:
            bez_odkazu += 1
            continue
        if lang == "":
            lang = langs[0]
        kde.setdefault((lang, nazov), []).append({
            "typ": o["typ"], "id": o["id"],
            "name": o["tags"].get("name"),
            "lat": o["lat"], "lon": o["lon"]})

    qids = [n for (lang, n) in kde if lang == "wikidata"]
    clankov = len({k for k in kde if k[0] != "wikidata"})
    log("── Plán ────────────────────────────────────────────")
    log(f"  objektov s odkazom   {sum(len(v) for v in kde.values())}"
        + (f" (+{bez_odkazu} s tagom, ktorému nerozumiem)" if bez_odkazu else ""))
    log(f"  článkov priamo       {clankov}")
    log(f"  cez wikidata         {len(qids)}")
    log(f"  jazyky               {', '.join(langs)}, formát {args.format}")
    # Odhad z merania: jedna požiadavka s pauzou trvá ~0,5 s. `text` a `html`
    # sú požiadavka na článok, `intro` na dvadsať článkov.
    spolu = clankov + len(qids)
    odhad = (spolu / INTRO_BATCH * 0.6 if args.format == "intro"
             else spolu * 0.5) + len(qids) / WIKIDATA_BATCH * 0.6
    log(f"  odhad                ~{odhad / 60:.1f} min")
    log("─────────────────────────────────────────────────────")

    api = Api()
    if qids:
        log(f"Dohľadávam články pre {len(qids)} wikidata id…")
        for qid, (lang, nazov) in wikidata_na_nazvy(api, qids, langs).items():
            kde.setdefault((lang, nazov), []).extend(kde[("wikidata", qid)])
    for q in list(kde):
        if q[0] == "wikidata":
            del kde[q]

    if args.max and len(kde) > args.max:
        log(f"::warning::Článkov je {len(kde)}, strop je {args.max} – beriem "
            f"prvých {args.max} (podľa počtu objektov, ktoré na ne ukazujú). "
            f"Zdvihni `wiki_max`, ak ich má byť viac.")
        poradie = sorted(kde, key=lambda k: (-len(kde[k]), k))[:args.max]
        kde = {k: kde[k] for k in poradie}

    podla_jazyka = {}
    for lang, nazov in kde:
        podla_jazyka.setdefault(lang, []).append(nazov)

    index, chybne_vsetky = [], []
    for lang in sorted(podla_jazyka):
        log(f"Sťahujem {len(podla_jazyka[lang])} článkov ({lang})…")
        hotove, chybne = stiahni_texty(api, lang, podla_jazyka[lang],
                                       args.out, args.format)
        for nazov in podla_jazyka[lang]:
            objekt = kde[(lang, nazov)]
            if nazov in hotove:
                subor, velkost = hotove[nazov]
                index.append({"title": nazov, "lang": lang, "file": subor,
                              "size": velkost,
                              "url": f"https://{lang}.wikipedia.org/wiki/"
                                     + urllib.parse.quote(nazov.replace(" ", "_")),
                              "osm": objekt})
            else:
                chybne_vsetky.append({"title": nazov, "lang": lang,
                                      "osm": objekt})
        chybne_vsetky += [{"title": n, "lang": lang, "osm": kde.get((lang, n), [])}
                          for n in chybne if n not in hotove
                          and not any(c["title"] == n for c in chybne_vsetky)]

    os.remove(maly)
    bajtov = sum(c["size"] for c in index)
    with open(os.path.join(args.out, "index.json"), "w") as f:
        json.dump({"_comment": "Ktorý článok patrí ktorému OSM objektu. "
                               "Vyrába workers/wiki/collect.py.",
                   "langs": langs, "format": args.format,
                   "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime()),
                   "articles": sorted(index, key=lambda c: (c["lang"],
                                                            c["title"])),
                   "chybne": sorted(chybne_vsetky,
                                    key=lambda c: (c["lang"], c["title"]))},
                  f, ensure_ascii=False, indent=1)

    took = time.time() - t0
    log(f"Hotovo: {len(index)} článkov ({bajtov / 1e6:.1f} MB) za "
        f"{took / 60:.1f} min, {api.pocet} požiadaviek na Wikipédiu "
        f"({api.bajtov / 1e6:.1f} MB"
        + (f", čakanie na limit {api.cakanie:.0f} s" if api.cakanie else "")
        + f"); odhad bol ~{odhad / 60:.1f} min")
    if chybne_vsetky:
        # NIE JE TO CHYBA BEHU, ale musí to byť napísané: odkaz v OSM môže
        # mieriť na článok, ktorý neexistuje (preklep, premenovaný článok,
        # jazyk bez článku). Zamlčať to by znamenalo „stiahlo sa všetko".
        log(f"::warning::{len(chybne_vsetky)} odkazov nemá článok "
            f"(napr. {', '.join(c['title'] for c in chybne_vsetky[:5])}) – "
            f"sú v index.json v `chybne`, aj s objektmi, ktoré na ne ukazujú.")
    if args.stats:
        with open(args.stats, "a") as f:
            f.write(f"60\tČlánky z Wikipédie\t{int(took)}\t"
                    f"{len(index)} článkov, {bajtov / 1e6:.1f} MB, "
                    f"{len(chybne_vsetky)} bez článku\n")
    if not index:
        log("::warning::Ani jeden článok – v regióne nie je objekt s odkazom "
            "na wiki, alebo sa nič nestiahlo. Balík sa nepublikuje.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

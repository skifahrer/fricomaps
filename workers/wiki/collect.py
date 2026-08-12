#!/usr/bin/env python3
"""
Články z Wikipédie ku všetkému, čo v regióne odkazuje na wiki.

ČO TO ROBÍ. Z regionálneho PBF vyberie objekty (body, čiary aj plochy), ktoré
majú odkaz na Wikipédiu alebo Wikidata, poskládá z nich zoznam článkov
a stiahne ich PO PÄŤDESIATICH NA POŽIADAVKU do JEDNÉHO súboru. Balík z toho
robí `workers/deploy/publish-map.py` (balík `wikipedia`), ktorý ho nahrá na
Drive vedľa mapy a zapíše do `maps.json`.

    data/region.osm.pbf
      → osmium tags-filter    len objekty s wiki odkazom (z 30 MB PBF ostane
                              rádovo 1 MB, takže ďalšie kroky sú sekundy)
      → osmium cat -f opl     typ, id a tagy KAŽDÉHO takého objektu
      → wikidata sitelinks    `Q…` → názov článku v požadovanom jazyku (50/req)
      → api.php prop=revisions celý článok, PÄŤDESIAT NA POŽIADAVKU
      → wiki-out/articles.ndjson + wiki-out/index.json

JEDEN SÚBOR, NIE SÚBOR NA ČLÁNOK. Formát je NDJSON – riadok = jeden článok
ako JSON. Tak to robí aj Wikimedia Enterprise so svojimi dumpmi a má to dva
namerané dôvody (vzorka 153 článkov sk wiki, 267 kB textu):

    súbor na článok   149,1 kB v ZIPe, 153 záznamov
    jeden NDJSON      101,3 kB v ZIPe, 1 záznam      → o 32 % menej

Za tým rozdielom je jedna vec dvakrát: ZIP má na každý záznam hlavičku
(~320 B nameraných vrátane centrálneho adresára – pri 5000 článkoch je to
1,6 MB samej režie) a deflate si na každom súbore ZAČÍNA SLOVNÍK ODZNOVA,
takže tisíc krátkych článkov o tej istej doline sa komprimuje horšie než jeden
prúd. K tomu praktické: rozbaliť 5000 súborov je na väčšine systémov citeľne
pomalšie než jeden, a čítať sa to dá po riadkoch bez rozbaľovania všetkého.

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

ZDVORILOSŤ K WIKIMEDII. Požiadavky idú SÉRIOVO (tak to žiada API:Etiquette –
„waiting for one request to finish before sending a new request"), s krátkou
pauzou, s `User-Agent`, ktorý hovorí, kto sme, s `maxlag`, a pri 429/503 sa
čaká `Retry-After`.

Použitie:
    python3 workers/wiki/collect.py --pbf=data/region.osm.pbf --out=wiki-out
    python3 workers/wiki/collect.py --pbf=… --langs=sk,en --format=wikitext
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

# Všetky články v jednom súbore, riadok = článok. Meno drží `index.json`
# v poli `file`, takže kto to číta, nemusí ho poznať dopredu.
NDJSON = "articles.ndjson"

# PLNÝ TEXT SA DÁVKOVAŤ DÁ, ale NIE cez `prop=extracts`. To je celý dôvod,
# prečo sa články berú z `prop=revisions` a prevádzajú sa tu, a nie hotové
# z TextExtracts. Namerané na `sk.wikipedia.org`, 10 názvov v jednej
# požiadavke:
#
#   prop=extracts&explaintext=1&exlimit=20     1 z 10 článkov, a k tomu
#       warning „exlimit was too large for a whole article extracts request,
#       lowered to 1" – ostatných deväť vyzerá ako neexistujúce
#   prop=revisions&rvprop=content&rvslots=main   10 z 10, jedna požiadavka
#
# Strop je 50 názvov na požiadavku (`lowlimit`; s botským právom 500) a nad
# ním API vráti CHYBU `toomanyvalues`, nie ticho zrezanú dávku – takže sa
# nemá ako stať, že by dávka po 60 vrátila 50 a o desiatich mlčala.
CONTENT_BATCH = 50
# `exintro` je jediná podoba extracts, ktorú API dávkuje, a strop je 20.
INTRO_BATCH = 20
WIKIDATA_BATCH = 50

# Namerané (`--format=text`, sk wiki): 153 článkov v 4 požiadavkách za 2,7 s,
# teda 18 ms na článok. Po jednom to bolo 484 ms na článok – 27× viac.
MS_PER_ARTICLE_BATCHED = 20
MS_PER_ARTICLE_SINGLE = 500

# Medzi požiadavkami sa krátko počká. Nie je to strop od Wikimedie, je to
# slušnosť: celý kraj je pri dávkach po 50 rádovo desiatky požiadaviek.
PAUSE_S = 0.2
TRIES = 4
# Nerob to na servery, ktoré práve nestíhajú replikáciu (API:Etiquette).
# Wikimedia na to odpovie 503 s `Retry-After`, čo `Api.get` počká.
MAXLAG = 5


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
    # Odkaz na ODDIEL je odkaz na ten istý článok: `sk:Devín (hrad)#Historia`.
    # Bez odrezania kotvy by sa článok hľadal pod menom s `#` a API by ho
    # vyhlásilo za neexistujúci – tichá strata článku, ktorý v dátach je.
    if "#" in value and not value.startswith("http"):
        value = value.split("#", 1)[0].strip()
    if value.startswith("http"):
        # `https://sk.wikipedia.org/wiki/Devín` – jazyk je v hostname.
        u = urllib.parse.urlsplit(value)
        lang = u.netloc.split(".")[0]
        nazov = urllib.parse.unquote(u.path.rsplit("/", 1)[-1]).replace("_", " ")
        # `#Historia` je v URL vo fragmente, ten `urlsplit` oddelí sám; kotva
        # napísaná do cesty ostane, tak ju odrežeme aj tu.
        return lang, nazov.split("#", 1)[0].strip()
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


TABULKA = re.compile(r"\{\|.*?\|\}", re.S)


def na_text(wikitext):
    """Wikitext → čistý text. Tabuľky sa odstrihnú PRED parsovaním.

    `mwparserfromhell.strip_code()` tabuľky nerozoberá – nechá ich ako text,
    takže v článku ostanú riadky `| align=center` a `|-`. Namerané na ôsmich
    článkoch sk wiki: bez tohto krokov 102 zvyškov `| param=`, s ním jeden
    jediný (neuzavretá šablóna v jednom článku).

    Čo tým NESTRATÍME: proti hotovému textu z `prop=extracts` má takto
    prevedený článok 92–144 % dĺžky (medián ~106 %) – teda o nič, čo by
    v článku bolo, neprichádzame.
    """
    # Import je tu a nie na začiatku súboru zámerne: `wikitext`, `intro` ani
    # `html` túto knižnicu nepotrebujú, tak nech sa beh o ňu opiera len keď
    # si vypýtal `text`. A keď chýba, nech je to hláška s návodom, nie
    # `ModuleNotFoundError` v tretej minúte sťahovania.
    try:
        import mwparserfromhell
    except ImportError:
        raise SystemExit(
            "::error::Chýba `mwparserfromhell` – prevádza wikitext na čistý "
            "text pri `wiki_format=text`. Doinštaluj ho (`pip install "
            "mwparserfromhell`, robí to `workers/wiki/build.sh`), alebo zvoľ "
            "`wiki_format=wikitext` (bez prevodu) či `wiki_format=intro`.")
    prev = None
    while prev != wikitext:            # vnorené tabuľky, zvnútra von
        prev, wikitext = wikitext, TABULKA.sub("", wikitext)
    txt = mwparserfromhell.parse(wikitext).strip_code()
    txt = re.sub(r"(?m)^[|!].*$", "", txt)     # zvyšky riadkov tabuliek
    txt = re.sub(r"\n{3,}", "\n\n", txt)       # tri a viac prázdnych riadkov
    return txt.strip()


def rozuzli(query):
    """`{názov, ktorý sme si vypýtali: názov, pod ktorým článok leží}`.

    API vracia dve mapy a MÔŽU SA ZARETIAZIŤ: `normalized` opraví prvé veľké
    písmo a podčiarkovníky (`devín_hrad` → `Devín hrad`), `redirects` potom
    presmeruje na cieľ (`Devín` → `Devín (hrad)`). Kto by prešiel len jednu,
    stratí článok, ktorý sa pritom stiahol – text by v balíku bol a index by
    ho k objektu nepriradil.
    """
    krok = {r["from"]: r["to"] for r in query.get("normalized") or []}
    krok.update({r["from"]: r["to"] for r in query.get("redirects") or []})
    out = {}
    for zdroj in krok:
        ciel, videne = zdroj, {zdroj}
        while ciel in krok and krok[ciel] not in videne:
            ciel = krok[ciel]
            videne.add(ciel)
        out[zdroj] = ciel
    return out


def stiahni_texty(api, lang, nazvy, fmt):
    """Články jedného jazyka. Vracia `({názov z OSM: záznam}, chybné)`.

    Štyri podoby, dve ceny. Dávkové (desiatky požiadaviek na kraj):
      `text`      celý článok ako čistý text – wikitext po 50 a prevod tu
      `wikitext`  celý článok ako wikitext, po 50 a bez prevodu
      `intro`     len úvod, po 20 (jediná dávková podoba `prop=extracts`)
    Po jednom článku (tisíce požiadaviek na kraj):
      `html`      celý článok v HTML z REST API – batch tam neexistuje
    """
    nazvy = sorted(set(nazvy))
    if fmt == "html":
        return _po_jednom_html(api, lang, nazvy)
    return _po_davkach(api, lang, nazvy, fmt)


def _po_davkach(api, lang, nazvy, fmt):
    """`text`, `wikitext` a `intro` – po 50, resp. po 20 na požiadavku."""
    davka_max = INTRO_BATCH if fmt == "intro" else CONTENT_BATCH
    hotove, chybne = {}, []
    for i in range(0, len(nazvy), davka_max):
        davka = nazvy[i:i + davka_max]
        if fmt == "intro":
            dotaz = (f"&prop=extracts|info&explaintext=1&exintro=1"
                     f"&exsectionformat=plain&exlimit={INTRO_BATCH}")
        else:
            dotaz = "&prop=revisions|info&rvprop=content|ids&rvslots=main"
        url = (f"https://{lang}.wikipedia.org/w/api.php?action=query{dotaz}"
               f"&redirects=1&inprop=url&maxlag={MAXLAG}"
               f"&format=json&formatversion=2&titles="
               + "|".join(urllib.parse.quote(t) for t in davka))
        data = api.json(url) or {}
        if data.get("error"):
            # Dávka po 50 nemá ako naraziť na `toomanyvalues`, ale keby áno
            # (alebo na iný `error`), nesmie z toho byť 50 „neexistujúcich"
            # článkov – to je presne tichý omyl, ktorý sa potom hľadá v mape.
            raise SystemExit(f"::error::Wikipedia ({lang}) odmietla dávku "
                             f"{len(davka)} názvov: "
                             f"{data['error'].get('code')} – "
                             f"{data['error'].get('info')}")
        query = data.get("query") or {}
        prezvane = rozuzli(query)
        podla_nazvu = {p.get("title"): p for p in query.get("pages") or []}
        for nazov in davka:
            page = podla_nazvu.get(prezvane.get(nazov, nazov))
            zaznam = _zaznam(lang, nazov, page, fmt)
            if zaznam:
                hotove[nazov] = zaznam
            else:
                chybne.append(nazov)
        log(f"  {lang}: {min(i + davka_max, len(nazvy))}/{len(nazvy)} článkov, "
            f"{api.pocet} požiadaviek")
    return hotove, chybne


def _zaznam(lang, nazov, page, fmt):
    """Z jednej stránky odpovede spraví záznam, alebo `None` keď z nej nič nie je."""
    if not page or page.get("missing") is True or page.get("invalid"):
        return None
    if fmt == "intro":
        text = (page.get("extract") or "").strip()
    else:
        try:
            wt = page["revisions"][0]["slots"]["main"]["content"]
        except (KeyError, IndexError):
            return None
        text = wt.strip() if fmt == "wikitext" else na_text(wt)
    if not text:
        return None
    titul = page.get("title") or nazov
    return {"key": f"{lang}:{titul}", "lang": lang, "title": titul,
            "pageid": page.get("pageid"),
            "revid": (page.get("revisions") or [{}])[0].get("revid"),
            "url": page.get("fullurl") or
                   f"https://{lang}.wikipedia.org/wiki/"
                   + urllib.parse.quote(titul.replace(" ", "_")),
            "text": text}


def _po_jednom_html(api, lang, nazvy):
    """`html` z REST API. Dávka tu NIE JE – REST vydá jednu stránku na volanie."""
    hotove, chybne = {}, []
    for n, nazov in enumerate(nazvy, 1):
        url = (f"https://{lang}.wikipedia.org/api/rest_v1/page/html/"
               + urllib.parse.quote(nazov.replace(" ", "_"), safe=""))
        telo = api.get(url)
        # Postup sa vypíše VŽDY, aj keď článok nevyšel – inak posledný riadok
        # chýba práve vtedy, keď zlyhal posledný článok, a z logu to vyzerá,
        # že sa sťahovanie zaseklo.
        if n % 25 == 0 or n == len(nazvy):
            log(f"  {lang}: {n}/{len(nazvy)} článkov (HTML, po jednom)")
        if not telo:
            chybne.append(nazov)
            continue
        hotove[nazov] = {
            "key": f"{lang}:{nazov}", "lang": lang, "title": nazov,
            "pageid": None, "revid": None,
            "url": f"https://{lang}.wikipedia.org/wiki/"
                   + urllib.parse.quote(nazov.replace(" ", "_")),
            "text": telo.decode("utf-8", "replace")}
    return hotove, chybne


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
                    choices=("text", "wikitext", "intro", "html"),
                    help="`text` celý článok ako čistý text, `wikitext` bez "
                         "prevodu, `intro` len úvod, `html` z REST (po jednom)")
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
    # Odhad z NAMERANÉHO: dávkové podoby ~20 ms na článok, `html` ~500 ms
    # (pauza medzi požiadavkami je v oboch číslach). Nech je z plánu dopredu
    # vidieť, či to budú sekundy alebo hodina – job, ktorý spadne na strop
    # času, minie rozpočet a nevyrobí nič.
    spolu = clankov + len(qids)
    na_clanok = (MS_PER_ARTICLE_SINGLE if args.format == "html"
                 else MS_PER_ARTICLE_BATCHED)
    odhad = (spolu * na_clanok / 1000.0
             + len(qids) / WIKIDATA_BATCH * (PAUSE_S + 0.4))
    davka = 1 if args.format == "html" else (
        INTRO_BATCH if args.format == "intro" else CONTENT_BATCH)
    log(f"  dávka                {davka} článkov na požiadavku, "
        f"teda ~{-(-spolu // davka)} požiadaviek")
    log(f"  odhad                ~{odhad / 60:.1f} min")
    if args.format == "html":
        log("  ::warning::`html` sa dávkovať nedá (REST vydá jednu stránku "
            "na volanie) – pri stovkách článkov je to desiatky minút. "
            "`text` je z tých istých článkov a ide po päťdesiatich.")
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

    # Jeden článok má často viac OSM objektov (hrad ako bod aj ako plocha)
    # a viac názvov, ktoré na ten istý článok vedú cez presmerovanie. Preto sa
    # zbiera podľa `key` (`sk:Devín (hrad)`), nie podľa toho, čo bolo v tagu.
    clanky, kde_je, chybne_vsetky = {}, {}, []
    for lang in sorted(podla_jazyka):
        log(f"Sťahujem {len(podla_jazyka[lang])} článkov ({lang})…")
        hotove, chybne = stiahni_texty(api, lang, podla_jazyka[lang],
                                       args.format)
        for nazov in podla_jazyka[lang]:
            objekty_odkazu = kde[(lang, nazov)]
            z = hotove.get(nazov)
            if not z:
                chybne_vsetky.append({"title": nazov, "lang": lang,
                                      "osm": objekty_odkazu})
                continue
            clanky.setdefault(z["key"], z).setdefault("asked", [])
            if nazov != z["title"]:
                clanky[z["key"]]["asked"].append(nazov)
            for o in objekty_odkazu:
                kde_je[f"{o['typ']}/{o['id']}"] = {
                    "key": z["key"], "name": o["name"],
                    "lat": o["lat"], "lon": o["lon"]}

    os.remove(maly)
    znakov, index = 0, []
    # NDJSON: riadok = článok. Píše sa priebežne, nie z jedného veľkého
    # reťazca v pamäti – 5000 článkov je rádovo 100 MB textu.
    nd = os.path.join(args.out, NDJSON)
    with open(nd, "w", encoding="utf-8") as f:
        for kluc in sorted(clanky):
            z = dict(clanky[kluc])
            z["asked"] = sorted(set(z.get("asked") or []))
            z["chars"] = len(z["text"])
            znakov += z["chars"]
            # Odsadenie (`offset`) a dĺžka riadka: kto si NDJSON rozbalí, vie
            # skočiť na článok cez `seek` a nemusí prejsť celý súbor.
            offset = f.tell()
            riadok = json.dumps(z, ensure_ascii=False) + "\n"
            f.write(riadok)
            index.append({"key": kluc, "lang": z["lang"], "title": z["title"],
                          "url": z["url"], "chars": z["chars"],
                          "offset": offset, "len": len(riadok.encode())})

    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"_comment": f"Čo je v {NDJSON} a ktorý článok patrí ktorému "
                               f"OSM objektu. Vyrába workers/wiki/collect.py.",
                   "file": NDJSON, "langs": langs, "format": args.format,
                   "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime()),
                   "counts": {"articles": len(index), "osm": len(kde_je),
                              "chybne": len(chybne_vsetky)},
                   # Text článku je v NDJSON a NIE TU (pravidlo 1: jedna
                   # odpoveď na jednom miesto). Tu je len to, čo treba na
                   # nájdenie – kľúč, kde v súbore leží a koľko má.
                   "articles": index,
                   # `<typ>/<id>` → článok. Toto je to, čo mapa potrebuje:
                   # klikneš na objekt, dostaneš kľúč článku.
                   "osm": dict(sorted(kde_je.items())),
                   "chybne": sorted(chybne_vsetky,
                                    key=lambda c: (c["lang"], c["title"]))},
                  f, ensure_ascii=False, indent=1)

    took = time.time() - t0
    nd_mb = os.path.getsize(nd) / 1e6
    log(f"Hotovo: {len(index)} článkov ({znakov / 1e6:.1f} M znakov, "
        f"{NDJSON} má {nd_mb:.1f} MB) za {took / 60:.1f} min, "
        f"{api.pocet} požiadaviek na Wikipédiu ({api.bajtov / 1e6:.1f} MB"
        + (f", čakanie na limit {api.cakanie:.0f} s" if api.cakanie else "")
        + f"); odhad bol ~{odhad / 60:.1f} min")
    log(f"  {len(kde_je)} OSM objektov má článok, "
        f"{api.pocet and len(index) / api.pocet:.0f} článkov na požiadavku")
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
                    f"{len(index)} článkov, {nd_mb:.1f} MB, "
                    f"{api.pocet} požiadaviek, "
                    f"{len(chybne_vsetky)} bez článku\n")
    if not index:
        log("::warning::Ani jeden článok – v regióne nie je objekt s odkazom "
            "na wiki, alebo sa nič nestiahlo. Balík sa nepublikuje.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

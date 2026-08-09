#!/usr/bin/env python3
"""
Stiahnutie celého priečinka z Google Drive – prihlásený, cez Drive API.

PREČO TO EXISTUJE. Sonnyho dlaždice sa sťahovali `gdown --folder --no-cookies`,
čiže NEPRIHLÁSENE. Verejný odkaz má denný strop sťahovania na súbor a ten
zdieľajú všetci, kto naň siahnu – nielen naše behy. Keď sa vyčerpá, Drive
nevráti chybu, ale HTTP 200 a HTML stránku „Too many users have viewed or
downloaded this file recently" (presne to zhodilo doplnenie modelu; ten istý
tvar odmietnutia rozoberá `quota_hint` v `drive-serve.py`). Prihlásená cesta je
v tomto slušná: pošle 403 a JSON s dôvodom, takže beh spadne v sekundách
a s vysvetlením, nie po hodinách ticha.

ČO PRIHLÁSENIE RIEŠI A ČO NIE – a toto sa NESMIE zamlčať. Strop je viazaný na
VLASTNÍKA súboru, nie na toho, kto sťahuje. Na súbory, ktoré prihlásený účet
naozaj vlastní (DMR 5.0), je strop rádovo vyšší a nedelí sa oň s cudzími
klientmi. Na cudzí priečinok zdieľaný odkazom (Sonny) platí ten istý denný
strop ako predtým – prihlásenie z neho spraví požiadavku s menom účtu namiesto
anonymnej, čo pomáha, ale zázrak to nie je. Preto sa pri každom súbore vypíše,
či ho tento účet vlastní, a v súhrne to stojí čiernym po bielom. Skutočná
poistka proti Sonnyho stropu je to, čo pipeline robí aj tak: stiahnuť raz sem
a uložiť do releasu, z ktorého si už build berie dlaždice bez Drive.

PREČO NIE `gdown`. Nevie sa prihlásiť tokenom, obsah priečinka zisťuje
parsovaním HTML stránky (mení sa) a stiahnuté súbory nevie dopočítať – zrušený
beh po sebe nenechá nič. Tu je jednotkou práce SÚBOR: hotový sa preskočí,
rozrobený sa dopočíta z `.part` cez `Range`, takže zrušený beh nezahodí prácu
(to isté pravidlo ako sklad sklonu v `slope-chunks.py`).

Odpoveď na „prihlásený, alebo nie" je JEDNA a je tu: `--mode`. Workflow sa
pýta jej, neprepočítava si to z toho, či je nastavený secret – dve miesta,
ktoré si tú istú vec počítajú samy, sa raz rozídu.

Použitie:
    python3 workers/drive-folder.py --mode
    python3 workers/drive-folder.py --folder=<URL alebo id> --list
    python3 workers/drive-folder.py --folder=<URL alebo id> --out=dl
"""
import argparse
import importlib.util
import os
import re
import sys
import time
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, path):
    """workers/*.py sa kvôli pomlčke v mene nedajú `import`-núť normálne."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Prihlásenie aj spojenia sú hotové inde a berú sa odtiaľ: `drive-auth.py` vie
# „kto som a aký mám token", `drive-serve.py` má `Pool` – čítanie cez Range,
# obnovu vypršaného tokenu, presmerovania a preklad odmietnutí do hlášky, ktorá
# povie, čo s tým. Napísať to tu druhýkrát by znamenalo druhú pravdu o tom
# istom.
drive = load("drive_serve", "drive-serve.py")
auth = load("drive_auth", "drive-auth.py")

FOLDER_MIME = "application/vnd.google-apps.folder"

# Bloky po 16 MiB: dosť veľké na to, aby réžia okolo požiadavky nebola vidieť,
# a dosť malé na to, aby sa zrušený beh vrátil najviac o 16 MiB dozadu.
CHUNK = 16 * 1024 * 1024

# Východiskový odhad rýchlosti, kým nie je meranie práve z tejto cesty.
# Skript na konci vypíše NAMERANÉ MB/s oproti tomuto číslu – po prvom behu ho
# sem prepíš tým nameraným, nech odhad na začiatku niečo znamená.
EST_MB_S = 20.0

# Vnorenie priečinkov. Sonnyho priečinok je plochý, ale cudzí priečinok sa
# môže zmeniť a nekonečná rekurzia v cudzích dátach je zlý nápad.
MAX_DEPTH = 5


def folder_id(text):
    """Id priečinka z URL aj z holého id.

    Berie `…/drive/folders/<id>`, `…/open?id=<id>` aj samotné `<id>` – vo
    formulári býva raz jedno, raz druhé a hádať sa o tvar nemá zmysel.
    """
    text = (text or "").strip()
    if not text:
        raise SystemExit("::error::Chýba --folder (URL priečinka alebo jeho id).")
    m = re.search(r"/folders/([-\w]{10,})", text)
    if m:
        return m.group(1)
    parts = urllib.parse.urlsplit(text)
    if parts.query:
        got = urllib.parse.parse_qs(parts.query).get("id")
        if got:
            return got[0]
    if re.fullmatch(r"[-\w]{10,}", text):
        return text
    raise SystemExit(f"::error::Z „{text}“ sa nedá vyčítať id priečinka na "
                     f"Drive. Čakám odkaz tvaru "
                     f"https://drive.google.com/drive/folders/<id> alebo id.")


def listing(creds, fid, depth=0, prefix=""):
    """Súbory v priečinku (aj v podpriečinkoch), zoradené podľa mena.

    Vracia zoznam dictov `{id, name, path, size, owned}`. Google-natívne
    dokumenty (tabuľky, prezentácie) sa preskakujú: `alt=media` ich nestiahne
    a v priečinku s dlaždicami nemajú čo hľadať.
    """
    out, skipped, token = [], [], ""
    while True:
        q = urllib.parse.quote(f"'{fid}' in parents and trashed = false")
        path = (f"/drive/v3/files?q={q}&pageSize=1000&orderBy=folder,name"
                f"&supportsAllDrives=true&includeItemsFromAllDrives=true"
                f"&fields=nextPageToken,files(id,name,size,mimeType,ownedByMe)")
        if token:
            path += "&pageToken=" + urllib.parse.quote(token)
        data = auth.api_get(creds, path)
        for f in data.get("files") or []:
            name = f.get("name") or f["id"]
            rel = f"{prefix}{name}"
            if f.get("mimeType") == FOLDER_MIME:
                if depth >= MAX_DEPTH:
                    skipped.append(f"{rel}/ (vnorené hlbšie než {MAX_DEPTH})")
                    continue
                sub, sub_skipped = listing(creds, f["id"], depth + 1, rel + "/")
                out += sub
                skipped += sub_skipped
                continue
            if f.get("size") is None:
                skipped.append(f"{rel} ({f.get('mimeType', '?')})")
                continue
            out.append({"id": f["id"], "name": name, "path": rel,
                        "size": int(f["size"]), "owned": bool(f.get("ownedByMe"))})
        token = data.get("nextPageToken") or ""
        if not token:
            return out, skipped


def human(n):
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


class Progress:
    """Tep sťahovania: čo sa práve deje a koľko ešte.

    Hodina ticha v logu sa nedá odlíšiť od zaseknutého behu, a `\\r`-ové
    percentá sa v logu GitHub Actions neobjavia, kým krok neskončí – preto
    celé riadky a preto raz za `every` sekúnd.
    """

    def __init__(self, total, every=30):
        self.total = total
        self.every = every
        self.done = 0
        self.t0 = time.time()
        self.last = self.t0

    def add(self, n, label):
        self.done += n
        now = time.time()
        if now - self.last < self.every:
            return
        self.last = now
        rate = self.done / max(now - self.t0, 1e-6)
        line = (f"    [{(now - self.t0) / 60:5.1f} min] {label}: "
                f"{human(self.done)} z {human(self.total)} "
                f"({rate / 1e6:.1f} MB/s)")
        # Odhad zvyšku má zmysel len vtedy, keď sa naozaj sťahuje – pri
        # rýchlosti okolo nuly by vyšli tisíce minút a to je horšie než nič.
        if rate > 1e5 and self.done < self.total:
            line += f", zostáva ~{(self.total - self.done) / rate / 60:.0f} min"
        print(line, flush=True)


def fetch(pool, item, dest, progress):
    """Jeden súbor do `dest`; hotový preskočí, rozrobený dopočíta.

    Cez `.part` a premenovanie, nie priamo do cieľa: inak by po zrušenom behu
    ostal skrátený súbor s finálnym menom a ďalší beh by ho pokladal za hotový
    (to isté pravidlo, na ktorom stojí sklad častí sklonu).
    """
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) == item["size"]:
        progress.done += item["size"]
        print(f"    hotové z predošlého behu ({human(item['size'])})", flush=True)
        return 0
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have > item["size"]:                 # cudzí zvyšok alebo zmenený súbor
        have = 0
    if have:
        print(f"    pokračujem od {human(have)}", flush=True)
        progress.done += have
    got = 0
    with open(part, "r+b" if have else "wb") as f:
        f.seek(have)
        f.truncate(have)
        while have < item["size"]:
            end = min(have + CHUNK, item["size"]) - 1
            want = end - have + 1
            _, _, body = pool.get(item["id"], f"bytes={have}-{end}", want=want)
            f.write(body)
            have += len(body)
            got += len(body)
            progress.add(len(body), item["name"])
    os.replace(part, dest)
    return got


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", default="",
                    help="URL priečinka na Drive alebo jeho id")
    ap.add_argument("--out", default="dl", help="kam sťahovať")
    ap.add_argument("--mode", action="store_true",
                    help="vypíš `auth` alebo `public` a skonči – jediná "
                         "odpoveď na „vieme sa prihlásiť?“")
    ap.add_argument("--list", action="store_true",
                    help="vypíš, čo v priečinku je, a nesťahuj nič")
    args = ap.parse_args()

    # Neúplná trojica secretov je CHYBA a `from_env` na nej padne – zámerne:
    # kto nastavil polovicu, čaká prihlásený beh, a ticho prepadnúť na verejný
    # denný limit je presne ten omyl, čo sa nájde až o pol dňa.
    creds = auth.from_env()

    if args.mode:
        print("auth" if creds is not None else "public")
        return 0

    if creds is None:
        print("::error::Priečinok z Drive sa dá vypísať len prihlásene "
              "(Drive API neobsluhuje anonymné požiadavky), ale v prostredí "
              "nie je token vlastníka. Doplň secrety DRIVE_CLIENT / "
              "DRIVE_SECRET / DRIVE_REFRESH – vyrobí ich workflow "
              "„Prihlásenie na Drive (jednorazové)“ (.github/workflows/"
              "drive-login.yml), z počítača `python3 workers/drive-auth.py "
              "--login`.")
        return 3

    fid = folder_id(args.folder)
    who = auth.whoami(creds)
    print(f"Režim čítania z Drive: {auth.describe(creds)}")
    print(f"  účet     {who.get('emailAddress', '?')} "
          f"({who.get('displayName', '?')})")
    print(f"  údaje    z {creds.source}")
    print(f"  priečinok {fid}")

    files, skipped = listing(creds, fid)
    if not files:
        print(f"::error::V priečinku {fid} nie je ani jeden stiahnuteľný súbor. "
              f"Vidí naň účet {who.get('emailAddress', '?')}? Priečinok musí "
              f"byť zdieľaný aspoň „ktokoľvek s odkazom – čitateľ“.")
        return 1
    total = sum(f["size"] for f in files)
    foreign = [f for f in files if not f["owned"]]

    # PLÁN PRED DRAHOU ČASŤOU. Krok, ktorý len mlčí a po hodine spadne na
    # timeout, minie celý rozpočet a nevyrobí nič.
    print(f"\nPlán: {len(files)} súborov, {human(total)}, "
          f"odhad ~{total / (EST_MB_S * 1e6) / 60:.0f} min "
          f"pri {EST_MB_S:.0f} MB/s")
    for f in skipped:
        print(f"  preskakujem {f}")
    if foreign:
        # Nesmie to vyzerať ako vyriešený problém, keď vyriešený nie je.
        print(f"  POZOR: {len(foreign)} z {len(files)} súborov tento účet "
              f"NEVLASTNÍ. Na cudzí zdieľaný súbor platí ten istý denný strop "
              f"sťahovania ako na verejný odkaz – prihlásenie ho nedvíha. "
              f"Preto sa sťahujú raz sem a ukladajú do releasu; build mapy už "
              f"na Drive nesiaha.")
    if args.list:
        for f in files:
            print(f"  {'vlastné' if f['owned'] else 'cudzie '}  "
                  f"{human(f['size']):>9}  {f['path']}")
        return 0

    pool = drive.Pool(creds=creds)
    progress = Progress(total)
    t0 = time.time()
    fresh = 0
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f['path']} – {human(f['size'])}", flush=True)
        fresh += fetch(pool, f, os.path.join(args.out, f["path"]), progress)

    # NAMERANÉ ČÍSLA OPROTI ODHADU. Bez nich sa odhad hore nikdy neopraví.
    el = time.time() - t0
    rate = fresh / max(el, 1e-6) / 1e6
    print(f"\nHotovo: {len(files)} súborov, {human(total)} "
          f"(z toho {human(fresh)} stiahnutých v tomto behu) "
          f"za {el / 60:.1f} min, {rate:.1f} MB/s "
          f"(odhad počítal s {EST_MB_S:.0f} MB/s)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except auth.AuthError as exc:
        # Text tých hlášok už nesie, čo s nimi – netreba k nemu nič pridávať.
        print(f"::error::{exc}")
        sys.exit(1)
    except RuntimeError as exc:
        print(f"::error::{exc}")
        sys.exit(1)

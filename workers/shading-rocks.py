#!/usr/bin/env python3
"""
Skaly z TIEŇOVANÝCH DLAŽDÍC (JPG) → vektorové plochy (GeoPackage).

Pokusná druhá cesta k skalám. Tá prvá (`workers/rock-areas.py`) počíta sklon
z DEM a vektorizuje izolíniu sklonu. Táto berie hotový hillshade – dlaždice
`https://sk-hires-shading.tiles.freemap.sk/{z}/{x}/{y}.jpg` – a hľadá v ňom
TMAVÉ PLOCHY. Nič sa nepočíta z výšok, čítajú sa obrázky:

    XYZ dlaždice (JPG) → mozaika odtieňov šedej v EPSG:3857 →
    raster „tmavosti" → gdal_contour -p (izolínia tmavosti ako PLOCHY) →
    filter plochy a dier → zjemnenie + zaoblenie obrysu → rock.gpkg

PREČO TO MÔŽE FUNGOVAŤ: tieňovanie je obraz sklonu. Kde je stena, tam je
tieň – a hires vrstva freemap.sk je robená z 1 m LiDARu, takže pri z18 vyjde
jeden pixel na ~0,4 m terénu. To je jemnejšie, než na čo vieme rozumne
spočítať sklon sami.

PREČO TO MÔŽE KLAMAŤ: tmavý nie je len sklon, ale sklon NA ODVRÁTENEJ STRANE.
Rovnako strmá stena otočená k slnku je na hillshade najsvetlejšia zo všetkého.
Táto cesta preto systematicky nájde severozápadné steny a systematicky
prehliadne juhovýchodné. Je to POKUS, nie náhrada `rock-areas.py`; v mape sa
zapína zvlášť (`rock_source: tienovanie`).

── čo ukázala skutočná dlaždica ────────────────────────────────────────────

Namerané na výreze z tej vrstvy (1260×1933 px, Vysoké Tatry):

  * Je to FAREBNÝ hillshade, nie šedý – žltozelený nádych, tiene ťahajú do
    modra (sýtosť ~34, B−R od −95 do +50). Čítame ho ako jas (`convert("L")`,
    luma 601), kde modrý kanál váži najmenej, takže sa modré tiene ešte
    prehĺbia. To nám vyhovuje. Farba ako druhý, nezávislý signál zatiaľ
    použitá NIE JE.
  * Rozloženie jasu: medián 176, 20. percentil 135, 10. percentil 107.
    Prah `--dark 125` z toho odkrojí ~16 % plochy a sedí na skalnatý terén.
  * TMAVÉ NIE JE PLOCHA, ALE SIEŤ. Tmavé miesta nie sú súvislé steny, ale
    hustá sieť žliabkov, ryhiek a mikrotieňov v rozčlenenom teréne. Práve
    táto jemná štruktúra je to, čo chceme – nie vyplnená klaksa. Preto je
    `--fill` (spriemerovanie tmavosti v okolí, ktoré zo siete spraví súvislú
    plochu) štandardne VYPNUTÉ.
  * Sieť je pospájaná: 16 útvarov pokrylo 15 % výrezu, takže počet útvarov
    neexploduje. Explodujú BODY – pri z18 to vyšlo na ~2 MB GeoPackage na km²
    skalnatého terénu.
  * Z toho vyšli aj predvolené hodnoty filtrov. Merané na tom istom výreze:

        min_area 200 m², min_hole 50 m², simplify ½ px, Chaikin 2× →
            16 plôch,  89 dier,  3,95 MB/km²
        min_area  50 m², min_hole 10 m², simplify 1 px,  Chaikin 1× →
            78 plôch, 392 dier,  1,97 MB/km²   ← toto

    Jemnejšie filtre a hrubšie zjednodušenie dali SÚČASNE viac štruktúry aj
    polovičné dáta: pol pixela a druhý prechod Chaikinom leštili obrys, ktorý
    aj tak nikto nerozozná, zatiaľ čo `min_area 200` zmazal práve tie drobné
    útvary, o ktoré ide.

── ako sa rozhoduje, čo je tmavé ────────────────────────────────────────────

Jeden prah na celú mozaiku nestačí: celý zatienený svah je tmavý bez toho,
aby bol skala, a naopak stena v presvetlenej doline býva svetlejšia než
priemerná tráva vedľa. Prah sa preto skladá z troch čísel:

    ref   = clip(miestne_pozadie − --rel, --dark-always, --dark)
    score = max(0, ref − šedá)

    šedá nad `--dark`         → nikdy nie je skala
    šedá pod `--dark-always`  → vždy je skala, nech je okolo čokoľvek
    medzi tým                 → skala len vtedy, keď je aspoň `--rel` pod
                                miestnym pozadím

Dolný strop `--dark-always` tam nie je pre ozdobu. Bez neho sa VEĽKÁ súvislá
stena nenájde: okno pozadia sa celé zmestí dovnútra nej, pozadie klesne na
tmavosť samotnej steny a nájde sa len jej okraj (namerané na skúšobných
dátach – z plochy ostal prstenec). Pod `--dark-always` už o okolí nikto
nehlasuje.

Miestne pozadie NIE JE obyčajný priemer, ale priemer tých SVETLEJŠÍCH
pixelov v okne (dva prechody: najprv priemer, potom priemer len z pixelov
nad ním). Odpoveď na otázku „ako svetlý je tu osvetlený terén" sa nesmie dať
stiahnuť dole tým, čo práve hľadáme. Okno `--local` je v METROCH na zemi,
nie v pixeloch – nastavenie tak platí na každom zoome rovnako a má byť
podstatne väčšie než najväčšia hľadaná skala.

Pozadie sa počíta na 8× zmenšenom obraze a späť sa roztiahne. Je to pole
osvetlenia, nie detail – na osemnásobne menšej mriežke vyzerá rovnako a je
64× lacnejšie na pamäť aj čas.

── prečo gdal_contour, a nie gdal_polygonize ───────────────────────────────

`gdal_polygonize` by obrys viedol po hranách pixelov (schodíky) a potreboval
by python bindings GDALu. `gdal_contour -p` nad poľom tmavosti interpoluje
medzi celými stupňami šedej, takže obrys je hladký a sub-pixelový – a je to
presne ten istý nástroj a tá istá sémantika dier, akú používa `rock-areas.py`:
pásmo [prah, ∞) je polygón s vnútornými prstencami tam, kde hodnota pod prah
klesla. Svetlé miesto vnútri tmavej plochy (polica, sneh, kosodrevina) tak
ostane DIEROU a nezafarbí sa – presne ako pri skalách z DEM.

Dve triedy naraz: úrovne sú `0,5` (trieda `steep`) a `0,5 + --cliff`
(trieda `cliff`). Trieda `cliff` leží v diere triedy `steep`, kreslí sa nad
ňou a dieru vyplní – rovnaká situácia ako u vrstevnicových pásiem.

── prečo sa vektorizuje naraz, a nie po dlaždiciach ────────────────────────

Rovnaký dôvod ako v `rock-areas.py`: diera prerezaná hranicou časti sa zmení
na zárez v okraji a späť sa už nezlepí. Po častiach sa preto počíta LEN
raster tmavosti (pás dlaždicových riadkov naraz, s presahom kvôli oknu
pozadia), zapíše sa na disk ako komprimovaný GTiff a `gdal_contour` ide
jedným priechodom nad celou mozaikou.

── čo to stojí ─────────────────────────────────────────────────────────────

Vysoké Tatry, z17: ~12 000 dlaždíc (~300 MB), mozaika 0,8 mld. pixelov,
dokopy jednotky minút. z18 je štvornásobok všetkého. Dlaždice sa sťahujú do
`--cache-dir` a pri opakovanom behu sa berú odtiaľ, takže ladenie prahov
nestojí ani jeden request navyše.

Sťahuje sa z dobrovoľníckej služby freemap.sk – `--jobs` je zámerne nízke.

Použitie:
    python3 workers/shading-rocks.py --bbox=19.9,49.09,20.32,49.25 \\
        --zoom=auto --dark=110 --local=512 --rel=18 --cliff=25 \\
        --out=data/rock.gpkg --stats=out/rock-img-stats.txt \\
        --preview=out/preview.png
"""
import argparse
import gzip
import http.client
import json
import math
import os
import random
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import zlib

import numpy as np
from PIL import Image

# Dlaždice sú vo Web Mercatore a mozaika sa v ňom aj počíta – žiadne
# prevzorkovanie, jeden pixel dlaždice = jeden pixel rastra.
WEBMERC = "EPSG:3857"
R = 20037508.342789244  # polovica strany sveta v metroch EPSG:3857
TILE = 256

# Na akom zmenšení sa počíta pole osvetlenia (pozadie). Pozadie je hladká
# funkcia – na 8× menšej mriežke vyzerá rovnako a je 64× lacnejšie.
BG_DOWN = 8

# Namerané na GitHub runneri, len na odhad dopredu. gdal_contour nad hotovou
# mozaikou ide ~3,5 mil. buniek/s (číslo prevzaté z rock-areas.py, rovnaký
# nástroj aj rovnaký typ vstupu).
CONTOUR_CELLS_PER_S = 3.5e6
TILES_PER_S = 25.0  # pri --jobs=12 a ~25 kB na dlaždicu

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from watch import hms, dir_mb, run_watched  # noqa: E402


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


# ---------------------------------------------------------------- dlaždice --

def lonlat_to_tile(lon, lat, z):
    """Súradnice → dlaždicové súradnice (desatinné)."""
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    la = math.radians(max(-85.05112, min(85.05112, lat)))
    y = (1.0 - math.log(math.tan(la) + 1.0 / math.cos(la)) / math.pi) / 2.0 * n
    return x, y


def tile_range(bbox, z):
    """Bbox v stupňoch → rozsah dlaždíc [x0, x1) × [y0, y1) na zoome z."""
    w, s, e, n = bbox
    x0f, y0f = lonlat_to_tile(w, n, z)   # sever = menšie y
    x1f, y1f = lonlat_to_tile(e, s, z)
    lim = 2 ** z
    x0, y0 = max(0, int(math.floor(x0f))), max(0, int(math.floor(y0f)))
    x1, y1 = min(lim, int(math.ceil(x1f))), min(lim, int(math.ceil(y1f)))
    return x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)


def tile_res(z):
    """Veľkosť pixela v metroch EPSG:3857 (nie na zemi – viď ground_res)."""
    return 2.0 * R / (TILE * 2.0 ** z)


def ground_res(z, lat):
    """Skutočná veľkosť pixela na zemi. Mercator naťahuje mierku 1/cos(šírka),
    takže meter v EPSG:3857 je pri 49° len ~0,65 m terénu."""
    return tile_res(z) * math.cos(math.radians(lat))


# Hlavičky, ktorými sa pipeline predstavuje. Každý profil je JEDEN skutočný
# prehliadač – UA, `Sec-CH-UA` aj platforma musia sedieť dokopy. Chrome, ktorý
# o sebe v `Sec-CH-UA` tvrdí, že je Firefox, nie je maskovanie, to je len
# rozbitá hlavička. Firefox a Safari `Sec-CH-UA` neposielajú vôbec, preto majú
# `None`.
#
# POZOR, ČO TO ROBÍ: predvolené je toto, lebo si to vypýtal input `ua`. Berie
# to ale službe freemap.sk možnosť rozoznať, že ide o dávku a nie o človeka –
# a to je dobrovoľnícky server. Preto ostáva `jobs` nízke (12) a dlaždice sa
# cachujú: slušnosť má zabezpečiť objem, keď ju už nezabezpečuje meno.
# `ua=project` vráti pôvodnú hlavičku, ktorá sa priznáva.
BROWSERS = (
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
     '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"', '"Windows"', "sk-SK,sk;q=0.9,en-US;q=0.8,en;q=0.7"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
     '"Chromium";v="133", "Not(A:Brand";v="24", "Google Chrome";v="133"', '"macOS"', "sk,cs;q=0.9,en;q=0.8"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
     '"Chromium";v="132", "Not_A Brand";v="8", "Google Chrome";v="132"', '"Linux"', "en-US,en;q=0.9"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0",
     '"Microsoft Edge";v="134", "Chromium";v="134", "Not:A-Brand";v="24"', '"Windows"', "sk-SK,sk;q=0.9,en;q=0.8"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
     None, None, "sk-SK,sk;q=0.8,en-US;q=0.5,en;q=0.3"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:134.0) Gecko/20100101 Firefox/134.0",
     None, None, "en-US,en;q=0.5"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
     None, None, "sk-SK,sk;q=0.9"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1",
     None, None, "sk-SK,sk;q=0.9,en-US;q=0.8"),
    ("Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Mobile Safari/537.36",
     '"Chromium";v="133", "Not(A:Brand";v="24", "Google Chrome";v="133"', '"Android"', "sk-SK,sk;q=0.9,en;q=0.8"),
)

PROJECT_UA = "fricomaps/shading-rocks (github.com/skifahrer/fricomaps)"

# Prvé bajty formátov, ktoré vie PIL prečítať a ktoré dlaždicová služba môže
# vrátiť. Slúži to na rozoznanie obrázka od chybovej stránky, nie na výber
# dekodéra – ten si nájde PIL sám.
IMAGE_MAGIC = (b"\xff\xd8\xff",          # JPEG
               b"\x89PNG\r\n\x1a\n",     # PNG
               b"GIF87a", b"GIF89a",     # GIF
               b"RIFF")                  # WebP (RIFF....WEBP)


def looks_like_image(body):
    return bool(body) and body.startswith(IMAGE_MAGIC)


def decode_body(body, encoding):
    """Rozbalí telo, keď ho server zabalil. Prehliadačovité hlavičky pýtajú
    `gzip, deflate`, takže to treba vedieť aj prijať."""
    enc = (encoding or "").strip().lower()
    if not enc or enc == "identity":
        return body
    try:
        if enc == "gzip":
            return gzip.decompress(body)
        if enc == "deflate":
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)  # bez hlavičky
    except (OSError, zlib.error):
        return b""
    return body


class Fetcher:
    """Sťahovanie dlaždíc s trvalým spojením a diskovou cache.

    Trvalé spojenie nie je kozmetika: pri 12 000 dlaždiciach je nové TLS
    handshake na každú z nich väčšina celého času. Spojenie je thread-local,
    takže si vlákna neprekážajú.

    Chýbajúca dlaždica (404) nie je chyba – tam jednoducho nie sú dáta.
    Zapíše sa ako prázdny súbor, aby sa pri ďalšom behu neskúšala znova.
    """

    def __init__(self, url_tmpl, cache_dir, jobs=12, retries=3, timeout=30,
                 ua="rotate"):
        self.tmpl = url_tmpl
        self.cache = cache_dir
        self.jobs, self.retries, self.timeout = jobs, retries, timeout
        self.ua = ua
        self.ua_seen = set()
        u = urllib.parse.urlsplit(url_tmpl)
        self.scheme, self.host = u.scheme, u.netloc
        self.local = threading.local()
        self.lock = threading.Lock()
        self.n_ok = self.n_miss = self.n_cached = self.n_fail = 0
        self.bytes = 0
        # Proxy sa rieši tunelom (CONNECT), nie prepísaním URL – inak by sa
        # trvalé spojenie zahodilo.
        self.proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""

    def path(self, z, x, y):
        return os.path.join(self.cache, str(z), str(x), f"{y}.jpg")

    def headers(self):
        """Hlavičky na jeden request.

        `ua=rotate` (predvolené): vyberie sa náhodný profil zo `BROWSERS`,
        takže každý request vyzerá ako iný prehliadač. Ostatné hlavičky idú
        z toho istého profilu, nech si neodporujú.

        `ua=project` sa priznáva menom projektu, hocičo iné sa pošle
        doslova ako `User-Agent`.

        Trvalé spojenie sa tým NEZAHADZUJE – server teda uvidí jedno TCP
        spojenie, cez ktoré chodí viacero prehliadačov. To nie je dokonalé
        maskovanie a ani sa oň nesnažíme; ide o to, aby dávka nevyzerala ako
        jeden skript s jednou hlavičkou.
        """
        # `Accept-Encoding` bez `br`/`zstd` zámerne: `http.client` telo
        # nerozbaľuje, takže rozbaliť to musíme sami a v stdlib je len gzip
        # a deflate. Sľúbiť brotli a potom ho nevedieť prečítať by znamenalo
        # uložiť na disk nečitateľné bajty. (JPEG sa aj tak prakticky nikdy
        # nekomprimuje druhýkrát.)
        h = {"Accept": "image/avif,image/webp,image/jpeg,image/*,*/*;q=0.8",
             "Accept-Encoding": "gzip, deflate",
             "Connection": "keep-alive"}
        if self.ua == "rotate":
            agent, ch_ua, platform, lang = random.choice(BROWSERS)
            h["Accept-Language"] = lang
            if ch_ua:
                h["Sec-CH-UA"] = ch_ua
                h["Sec-CH-UA-Mobile"] = "?1" if "Mobile" in agent else "?0"
                h["Sec-CH-UA-Platform"] = platform
            h["Sec-Fetch-Dest"] = "image"
            h["Sec-Fetch-Mode"] = "no-cors"
            h["Sec-Fetch-Site"] = "cross-site"
        elif self.ua == "project":
            agent = PROJECT_UA
            h["Accept-Language"] = "sk-SK,sk;q=0.9,en;q=0.8"
        else:
            agent = self.ua
        h["User-Agent"] = agent
        with self.lock:
            self.ua_seen.add(agent)
        return h

    def _conn(self):
        c = getattr(self.local, "conn", None)
        if c is None:
            if self.proxy:
                p = urllib.parse.urlsplit(self.proxy)
                c = http.client.HTTPSConnection(p.hostname, p.port or 8080,
                                                timeout=self.timeout)
                c.set_tunnel(self.host)
            elif self.scheme == "https":
                c = http.client.HTTPSConnection(self.host, timeout=self.timeout)
            else:
                c = http.client.HTTPConnection(self.host, timeout=self.timeout)
            self.local.conn = c
        return c

    def _drop(self):
        c = getattr(self.local, "conn", None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
            self.local.conn = None

    def get(self, z, x, y):
        """Stiahne jednu dlaždicu do cache. True = máme dáta, False = nie."""
        dst = self.path(z, x, y)
        if os.path.exists(dst):
            with self.lock:
                self.n_cached += 1
            return os.path.getsize(dst) > 0
        url = self.tmpl.format(z=z, x=x, y=y)
        rel = urllib.parse.urlsplit(url).path
        body, status = None, 0
        for attempt in range(self.retries):
            try:
                c = self._conn()
                c.request("GET", rel, headers=self.headers())
                resp = c.getresponse()
                status = resp.status
                body = decode_body(resp.read(), resp.getheader("Content-Encoding"))
                if status == 200 and looks_like_image(body):
                    break
                if status == 200:
                    # Chybová stránka s kódom 200 je pri dlaždicových
                    # službách bežná – uložiť ju ako .jpg by znamenalo tichú
                    # dieru v mozaike a v cache navždy. Skúsi sa znova.
                    status, body = 0, None
                    self._drop()
                elif status == 404:
                    body = b""
                    break
                else:
                    self._drop()
            except Exception:
                self._drop()
                body, status = None, 0
            time.sleep(0.5 * (attempt + 1))

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if status == 200 and body:
            tmp = dst + ".part"
            with open(tmp, "wb") as f:
                f.write(body)
            os.replace(tmp, dst)
            with self.lock:
                self.n_ok += 1
                self.bytes += len(body)
            return True
        if status == 404:
            open(dst, "wb").close()   # značka „tu nič nie je"
            with self.lock:
                self.n_miss += 1
            return False
        with self.lock:
            self.n_fail += 1
        return False

    def fetch_all(self, z, x0, y0, x1, y1):
        """Stiahne celý obdĺžnik dlaždíc. Vlákna si berú prácu zo spoločného
        zoznamu, takže pomalá dlaždica nebrzdí celý pás."""
        jobs = [(x, y) for y in range(y0, y1) for x in range(x0, x1)]
        total = len(jobs)
        idx = [0]
        lock = threading.Lock()
        t0 = time.time()
        last = [t0]

        def worker():
            while True:
                with lock:
                    i = idx[0]
                    idx[0] += 1
                if i >= total:
                    return
                x, y = jobs[i]
                self.get(z, x, y)
                now = time.time()
                if now - last[0] >= 15:
                    with lock:
                        if now - last[0] >= 15:
                            last[0] = now
                            done = i + 1
                            rate = done / max(1e-6, now - t0)
                            eta = (total - done) / max(1e-6, rate)
                            print(f"  … dlaždice: {done}/{total} "
                                  f"({100.0 * done / total:.0f} %), "
                                  f"{rate:.0f}/s, ostáva {hms(eta)}, "
                                  f"{self.bytes / 1048576:.0f} MB", flush=True)

        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(max(1, self.jobs))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        dt = time.time() - t0
        print(f"  dlaždice: {self.n_ok} stiahnutých, {self.n_cached} z cache, "
              f"{self.n_miss} chýba (404), {self.n_fail} zlyhalo, "
              f"{self.bytes / 1048576:.0f} MB za {hms(dt)}", flush=True)
        # Len keď sa naozaj niečo sťahovalo – pri behu celom z cache by
        # „0 rôznych prehliadačov" vyzeralo ako porucha, a pritom nešiel
        # von ani jeden request.
        if self.ua == "rotate" and self.ua_seen:
            print(f"  hlavičky: {len(self.ua_seen)} rôznych prehliadačov "
                  f"z {len(BROWSERS)} profilov", flush=True)
        if self.n_fail and self.n_fail > total * 0.02:
            print(f"::warning::Nepodarilo sa stiahnuť {self.n_fail} dlaždíc "
                  f"z {total} – v mozaike budú prázdne miesta.")
        return dt


def probe_zoom(fetcher, bbox, zmax, zmin, max_tiles):
    """Najvyšší zoom, na ktorom server naozaj vracia dlaždicu a ktorý sa ešte
    zmestí do stropu `--max-tiles`.

    Nedá sa to prečítať z metadát – XYZ šablóna žiadne nemá. Skúša sa preto
    jedna dlaždica v strede územia, zhora nadol.
    """
    w, s, e, n = bbox
    lon, lat = (w + e) / 2.0, (s + n) / 2.0
    print("── Hľadám najvyšší zoom ─────────────────────────────")
    for z in range(zmax, zmin - 1, -1):
        x0, y0, x1, y1 = tile_range(bbox, z)
        count = (x1 - x0) * (y1 - y0)
        if count > max_tiles:
            print(f"  z{z:<3} {count:>8} dlaždíc  × nad strop {max_tiles}")
            continue
        tx, ty = lonlat_to_tile(lon, lat, z)
        ok = fetcher.get(z, int(tx), int(ty))
        print(f"  z{z:<3} {count:>8} dlaždíc  {'✓ dlaždica je' if ok else '× server nedal dlaždicu'}")
        if ok:
            print(f"  vybrané         z{z}")
            print("─────────────────────────────────────────────────────", flush=True)
            return z
    print("─────────────────────────────────────────────────────", flush=True)
    print("::error::Ani na jednom zoome sa nepodarilo stiahnuť skúšobnú "
          "dlaždicu. Skontroluj --url alebo dostupnosť servera.")
    return 0


# ------------------------------------------------------------------ raster --

def block_mean(gray, k, chunk_rows=4096):
    """Priemer v blokoch k×k → k-krát menší obraz vo float32.

    Po pásoch riadkov, aby float medzivýsledok nikdy nemal veľkosť celého
    pásu – pri 78 000 px na šírku je to rozdiel medzi 80 MB a 2,5 GB.
    """
    h, w = gray.shape
    h2, w2 = h // k, w // k
    out = np.empty((h2, w2), np.float32)
    step = max(1, (chunk_rows // k)) * k
    for r in range(0, h2 * k, step):
        r1 = min(r + step, h2 * k)
        blk = gray[r:r1, :w2 * k].reshape((r1 - r) // k, k, w2, k)
        out[r // k:r1 // k] = blk.mean(axis=(1, 3), dtype=np.float32)
    return out


def box_mean(a, r):
    """Priemer v okne (2r+1)² cez integrálny obraz; okraje sa doplnia hranou.

    Beží na zmenšenom obraze (BG_DOWN), takže float64 kumulatívny súčet je
    lacný a presný – na plnom rozlíšení by to boli gigabajty.
    """
    if r <= 0:
        return a.astype(np.float32)
    h, w = a.shape
    r = min(r, max(h, w))
    pad = np.pad(a.astype(np.float64), ((r, r), (r, r)), mode="edge")
    ii = np.zeros((pad.shape[0] + 1, pad.shape[1] + 1), np.float64)
    np.cumsum(np.cumsum(pad, axis=0), axis=1, out=ii[1:, 1:])
    win = 2 * r + 1
    s = (ii[win:win + h, win:win + w] - ii[0:h, win:win + w]
         - ii[win:win + h, 0:w] + ii[0:h, 0:w])
    return (s / (win * win)).astype(np.float32)


def box_blur_u8(a, r):
    """Priemer v malom okne (2r+1)² priamo na šedej – zmaže zrno JPEGu.

    JPEG kóduje po blokoch 8×8 a na hladkom tieni z toho ostáva šum ±2
    stupne šedej. Bez neho by izolínia okolo prahu vyrábala tisíce
    odrobiniek, ktoré by aj tak vypadli na `--min-area` – len by ich najprv
    musel niekto vektorizovať.
    """
    if r <= 0:
        return a
    h, w = a.shape
    ap = np.pad(a, r, mode="edge")
    acc = np.zeros((h, w), np.uint16)
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            acc += ap[dy:dy + h, dx:dx + w]
    acc //= (2 * r + 1) ** 2
    return acc.astype(np.uint8)


def load_band(fetcher, z, x0, x1, ty0, ty1):
    """Dlaždicové riadky [ty0, ty1) ako jeden obraz odtieňov šedej.

    Chýbajúca dlaždica ostane 255 (= úplne svetlá, teda určite nie skala),
    nie 0 – nula by sa vyhodnotila ako najtmavšie miesto v mozaike.
    """
    w = (x1 - x0) * TILE
    h = (ty1 - ty0) * TILE
    band = np.full((h, w), 255, np.uint8)
    for ty in range(ty0, ty1):
        for tx in range(x0, x1):
            p = fetcher.path(z, tx, ty)
            try:
                if not os.path.exists(p) or os.path.getsize(p) == 0:
                    continue
                with Image.open(p) as im:
                    a = np.asarray(im.convert("L"), np.uint8)
            except Exception:
                continue
            if a.shape != (TILE, TILE):
                continue
            ry, rx = (ty - ty0) * TILE, (tx - x0) * TILE
            band[ry:ry + TILE, rx:rx + TILE] = a
    return band


def upsample(small, h, w, k=BG_DOWN):
    """Zmenšené pole späť na plné rozlíšenie (opakovaním pixelov).

    `block_mean` zaokrúhľuje rozmer nadol, takže keď šírka alebo výška nie je
    násobkom `k`, chýba na okraji pár riadkov a stĺpcov – tie sa doplnia
    hranou. V mozaike z celých dlaždíc (256 px) to nikdy nenastane, ale
    funkcia nemá padať na tom, kde ju kto zavolá.
    """
    full = np.repeat(np.repeat(small, k, axis=0), k, axis=1)
    if full.shape[0] < h or full.shape[1] < w:
        full = np.pad(full, ((0, max(0, h - full.shape[0])),
                             (0, max(0, w - full.shape[1]))), mode="edge")
    return full[:h, :w]


def bright_background(small, r):
    """Ako svetlý je tu OSVETLENÝ terén – priemer svetlejšej polovice okna.

    Obyčajný priemer sa nedá použiť: veľká tmavá plocha si stiahne vlastné
    pozadie k sebe a potom sa nájde len jej okraj. Dva prechody to opravia –
    najprv hrubý priemer, potom priemer len z tých pixelov, ktoré sú nad ním.
    Tmavá plocha do druhého priemeru už nehlasuje.

    Keď je v okne svetlých pixelov úplne minimum (okno celé vnútri steny),
    padá sa späť na hrubý priemer – tam rozhoduje `--dark-always`.
    """
    m1 = box_mean(small, r)
    lit = (small >= m1).astype(np.float32)
    s = box_mean(small * lit, r)
    c = box_mean(lit, r)
    return np.where(c > 0.05, s / np.maximum(c, 1e-6), m1).astype(np.float32)


def score_band(gray, dark, always, local_px, rel, blur, fill_px=0):
    """Šedá → „tmavosť" (Byte): o koľko je pixel pod referenciou.

    ref   = clip(pozadie − rel, always, dark)   (bez pozadia rovno `dark`)
    score = clip(ref − šedá, 0, 255)

    `fill_px` (input `fill`, default vypnuté) navyše spriemeruje tmavosť
    v okolí, takže sa z jemnej siete žliabkov stane súvislá plocha. Viď
    hlavičku súboru – zámerne je to vypnuté, lebo tá jemná štruktúra JE
    to, čo chceme.
    """
    gray = box_blur_u8(gray, blur)
    h, w = gray.shape
    if local_px > 0:
        small = block_mean(gray, BG_DOWN)
        bg = bright_background(small, max(1, int(round(local_px / BG_DOWN / 2))))
        np.subtract(bg, float(rel), out=bg)
        np.clip(bg, float(always), float(dark), out=bg)
    else:
        bg = None

    out = np.empty((h, w), np.uint8)
    step = 2048
    for r in range(0, h, step):
        r1 = min(r + step, h)
        g = gray[r:r1].astype(np.int16)
        if bg is None:
            np.subtract(np.int16(dark), g, out=g)
        else:
            rows = bg[r // BG_DOWN:(r1 + BG_DOWN - 1) // BG_DOWN]
            full = upsample(rows, r1 - r, w)
            np.subtract(full, g.astype(np.float32), out=full)
            g = full.astype(np.int16)
        np.clip(g, 0, 255, out=g)
        out[r:r1] = g.astype(np.uint8)

    if fill_px > 0:
        # Priemerná tmavosť v okolí namiesto tmavosti pixela. Počíta sa na
        # tom istom 8× zmenšení ako pozadie – pole hustoty je hladké, na
        # jemnejšej mriežke vyzerá rovnako. Obrys je potom odkrokovaný po
        # 8 px, čo Chaikin zahladí; komu ide o súvislé plochy, tomu to
        # nevadí, a komu ide o detail, ten `fill` nezapína.
        out = upsample(box_mean(block_mean(out, BG_DOWN),
                                max(1, int(round(fill_px / BG_DOWN / 2)))),
                       h, w).astype(np.uint8)
    return out, gray


VRT_RAW = """<VRTDataset rasterXSize="{w}" rasterYSize="{h}">
  <SRS>EPSG:3857</SRS>
  <GeoTransform>{ox}, {res}, 0.0, {oy}, 0.0, -{res}</GeoTransform>
  <VRTRasterBand dataType="Byte" band="1" subClass="VRTRawRasterBand">
    <SourceFilename relativeToVRT="1">{raw}</SourceFilename>
    <ImageOffset>0</ImageOffset>
    <PixelOffset>1</PixelOffset>
    <LineOffset>{w}</LineOffset>
  </VRTRasterBand>
</VRTDataset>
"""


def write_chunk(arr, ox, oy, res, out_tif):
    """numpy → georeferencovaný komprimovaný GTiff, bez python bindings GDALu.

    Cesta je raw súbor + VRTRawRasterBand (to je len XML hlavička nad ním)
    + `gdal_translate`. Raw sa hneď maže, na disku ostáva len komprimovaný
    dlaždicovaný TIFF – pole tmavosti je väčšinou nula, takže z 800 MB
    ostane pár desiatok.
    """
    h, w = arr.shape
    raw = out_tif + ".raw"
    arr.tofile(raw)
    vrt = out_tif + ".vrt"
    with open(vrt, "w") as f:
        f.write(VRT_RAW.format(w=w, h=h, ox=repr(ox), oy=repr(oy),
                               res=repr(res), raw=os.path.basename(raw)))
    try:
        run(["gdal_translate", "-q", "-of", "GTiff",
             "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=2",
             "-co", "TILED=YES", "-co", "BIGTIFF=IF_SAFER",
             vrt, out_tif])
    finally:
        for f in (raw, vrt):
            if os.path.exists(f):
                os.remove(f)


def build_score_raster(fetcher, z, x0, y0, x1, y1, args, tmp, preview_rows):
    """Mozaika tmavosti po pásoch dlaždicových riadkov → zoznam GTiffov.

    Pás sa načíta s PRESAHOM niekoľkých dlaždicových riadkov hore aj dole,
    aby okno pozadia na jeho okraji nebolo zrezané, a zapíše sa až orezaný
    presne na svoje riadky. Presahové dlaždice sú už v cache, takže sa
    nesťahujú druhýkrát.
    """
    res = tile_res(z)
    w_px = (x1 - x0) * TILE
    local_px = args.local_px
    pad_tiles = (int(math.ceil(max(local_px, args.fill_px) / 2.0 / TILE))
                 + (1 if args.blur else 0))
    rows_per_band = max(1, int(args.band_cells // max(1, w_px * TILE)))
    tifs = []
    t0 = time.time()
    n_bands = int(math.ceil((y1 - y0) / rows_per_band))
    print(f"  pás = {rows_per_band} dlaždicových riadkov "
          f"({rows_per_band * TILE} px), presah {pad_tiles}, "
          f"{n_bands} pásov", flush=True)

    for bi, ty in enumerate(range(y0, y1, rows_per_band)):
        ty1 = min(ty + rows_per_band, y1)
        py0, py1 = max(y0, ty - pad_tiles), min(y1, ty1 + pad_tiles)
        gray = load_band(fetcher, z, x0, x1, py0, py1)
        score, blurred = score_band(gray, args.dark, args.dark_always,
                                    local_px, args.rel, args.blur, args.fill_px)
        del gray
        top = (ty - py0) * TILE
        bot = top + (ty1 - ty) * TILE
        cut = score[top:bot]
        ox = -R + x0 * TILE * res
        oy = R - ty * TILE * res
        tif = os.path.join(tmp, f"score{bi:04d}.tif")
        write_chunk(np.ascontiguousarray(cut), ox, oy, res, tif)
        tifs.append(tif)
        # Náhľad: zmenšený obraz sa skladá priebežne, nikdy sa nedrží celá
        # mozaika v pamäti.
        if preview_rows is not None:
            k = max(1, args.preview_down)
            vis = blurred[top:bot]
            vh = (vis.shape[0] // k) * k
            if vh:
                preview_rows.append((
                    block_mean(vis[:vh], k).astype(np.uint8),
                    block_mean(cut[:vh], k).astype(np.uint8)))
        del score, blurred, cut
        done = ty1 - y0
        el = time.time() - t0
        eta = el / max(1, done) * (y1 - y0 - done)
        print(f"  … tmavosť: pás {bi + 1}/{n_bands}, "
              f"{done}/{y1 - y0} riadkov, beží {hms(el)}, ostáva {hms(eta)}, "
              f"na disku {dir_mb(tmp):.0f} MB", flush=True)
    return tifs, time.time() - t0


# ------------------------------------------------------------------ vektor --

def ring_area(ring):
    """Plocha prstenca (shoelace) v jednotkách súradníc, so znamienkom."""
    s = 0.0
    for i in range(len(ring) - 1):
        x0, y0 = ring[i][0], ring[i][1]
        x1, y1 = ring[i + 1][0], ring[i + 1][1]
        s += x0 * y1 - x1 * y0
    return s / 2.0


def filter_stream(src, dst, min_area, min_hole, cliff_level, merc_factor):
    """Prúdový filter nad GeoJSONSeq: odrobinky preč, dierky preč, triedy von.

    Vstup je už rozbitý na samostatné plochy (`-explodecollections`), takže
    jeden riadok = jedna skala. Bez toho by z celého pohoria vyšli DVA
    útvary – pásmo `steep` a pásmo `cliff` – a atribút `area` by hovoril,
    koľko je skál dokopy, nie aká veľká je tá jedna pod hrebeňom.

    Plocha sa počíta zo súradníc v EPSG:3857 a násobí `merc_factor`
    (= cos²(šírka)). Mercator naťahuje mierku 1/cos(šírka), takže bez toho
    by pri 49° vyšla plocha 2,3× väčšia, než v skutočnosti je.

    Diery sa zahadzujú podľa VLASTNEJ plochy, nie podľa plochy celku:
    svetlá polica vnútri steny je platná diera a má ostať, jednopixelová
    dierka po zrne JPEGu nie.
    """
    n_in = n_out = 0
    holes_kept = holes_drop = 0
    total = 0.0
    n_cliff = 0
    biggest = 0.0
    with open(src) as fi, open(dst, "w") as fo:
        for line in fi:
            line = line.strip()
            if not line:
                continue
            feat = json.loads(line)
            g = feat.get("geometry") or {}
            t = g.get("type")
            if t == "Polygon":
                parts = [g["coordinates"]]
            elif t == "MultiPolygon":
                parts = g["coordinates"]
            else:
                continue
            dmin = feat.get("properties", {}).get("dmin")
            try:
                dmin = float(dmin)
            except (TypeError, ValueError):
                dmin = 0.0
            cls = "cliff" if dmin >= cliff_level else "steep"

            for poly in parts:
                if not poly:
                    continue
                n_in += 1
                area = abs(ring_area(poly[0])) * merc_factor
                rings = [poly[0]]
                for hole in poly[1:]:
                    a = abs(ring_area(hole)) * merc_factor
                    if a >= min_hole:
                        rings.append(hole)
                        area -= a
                        holes_kept += 1
                    else:
                        holes_drop += 1
                if area < min_area:
                    continue
                fo.write(json.dumps({
                    "type": "Feature",
                    # `ceil`, nie `round`: dolná hranica pásma je 0,5 (resp.
                    # 0,5 + cliff) a `dark` má povedať „aspoň o toľko stupňov
                    # šedej pod referenciou". Zaokrúhlenie by z 0,5 spravilo
                    # nulu, čo sa číta ako „vôbec nie tmavé".
                    "properties": {"class": cls, "dark": int(math.ceil(dmin)),
                                   "area": int(round(area))},
                    "geometry": {"type": "Polygon", "coordinates": rings},
                }, separators=(",", ":")) + "\n")
                n_out += 1
                total += area
                biggest = max(biggest, area)
                n_cliff += (cls == "cliff")
    return {"n_in": n_in, "n": n_out, "cliff": n_cliff, "total_m2": total,
            "max_m2": biggest, "holes": holes_kept, "holes_dropped": holes_drop}


def vectorize(tifs, args, tmp, out, lat_mid):
    """Mozaika tmavosti → hotový rock.gpkg v EPSG:4326."""
    vrt = os.path.join(tmp, "score.vrt")
    run(["gdalbuildvrt", "-q", vrt] + tifs)

    raw_gpkg = os.path.join(tmp, "bands.gpkg")
    if os.path.exists(raw_gpkg):
        os.remove(raw_gpkg)
    # Prah je 0,5, nie 1: dáta sú celé čísla, takže izolínia v polovici kroku
    # ide presne stredom medzi „nie je tmavé" a „je tmavé" a obrys vyjde
    # sub-pixelový. Horná úroveň 256 je nad maximom Byte – bez nej niektoré
    # verzie GDALu najvrchnejšie pásmo nevytvoria.
    cliff_level = 0.5 + args.cliff
    levels = ["-fl", "0.5", "-fl", repr(cliff_level), "-fl", "256"]
    run_watched(["gdal_contour", "-p", "-amin", "dmin", "-amax", "dmax",
                 *levels, "-f", "GPKG", "-nln", "band", vrt, raw_gpkg],
                "obrysy tmavých plôch", tmp=raw_gpkg,
                every=args.heartbeat)

    seq = os.path.join(tmp, "bands.geojsonl")
    # Ovládač GeoJSON prepočítava vždy do WGS84 – `-a_srs EPSG:4326` mu
    # metrické súradnice len preznačí, neprepočíta (rovnaký trik má
    # smooth-polygons.py a je tam vysvetlený).
    # `-explodecollections`: pásmo je jeden útvar cez celé územie. Skala má
    # byť samostatný útvar s vlastnou plochou – rovnako to robí rock-areas.py.
    run(["ogr2ogr", "-f", "GeoJSONSeq", seq, raw_gpkg, "band",
         "-a_srs", "EPSG:4326", "-lco", "COORDINATE_PRECISION=2",
         "-explodecollections", "-where", "dmin >= 0.5"])

    filt = os.path.join(tmp, "rock.geojsonl")
    merc = math.cos(math.radians(lat_mid)) ** 2
    st = filter_stream(seq, filt, args.min_area, args.min_hole,
                       cliff_level, merc)
    print(f"  filter: {st['n_in']} → {st['n']} plôch "
          f"(pod {args.min_area:g} m² preč), diery {st['holes']} ostali, "
          f"{st['holes_dropped']} pod {args.min_hole:g} m² preč", flush=True)
    if not st["n"]:
        return st

    metric = os.path.join(tmp, "rock-metric.gpkg")
    cmd = ["ogr2ogr", "-f", "GPKG", metric, filt, "-nln", "rock",
           "-nlt", "MULTIPOLYGON", "-a_srs", WEBMERC,
           "-lco", "GEOMETRY_NAME=geom"]
    if args.simplify > 0:
        cmd += ["-simplify", repr(args.simplify)]
    run(cmd)

    smooth = os.path.join(tmp, "rock-smooth.gpkg")
    src = metric
    if args.smooth > 0:
        subprocess.run([sys.executable,
                        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "smooth-polygons.py"),
                        f"--in={metric}", f"--out={smooth}", "--layer=rock",
                        f"--passes={args.smooth}"], check=True)
        src = smooth

    if os.path.exists(out):
        os.remove(out)
    run(["ogr2ogr", "-f", "GPKG", out, src, "rock", "-nln", "rock",
         "-t_srs", "EPSG:4326", "-nlt", "MULTIPOLYGON",
         "-lco", "GEOMETRY_NAME=geom"])
    return st


def empty_rock(out):
    """Prázdna vrstva – schéma na skaly odkazuje vždy, súbor musí existovať."""
    tmp = out + ".empty.geojson"
    with open(tmp, "w") as f:
        f.write('{"type":"FeatureCollection","features":[]}')
    if os.path.exists(out):
        os.remove(out)
    run(["ogr2ogr", "-f", "GPKG", out, tmp, "-nln", "rock", "-nlt", "POLYGON",
         "-a_srs", "EPSG:4326", "-lco", "GEOMETRY_NAME=geom"])
    os.remove(tmp)


# ----------------------------------------------------------------- náhľad ---

def save_preview(rows, path):
    """Zmenšená mozaika vedľa nájdených plôch – aby sa prahy dali doladiť
    pohľadom, nie hádaním. Vľavo šedá, vpravo to isté s červenou maskou."""
    if not rows:
        return
    gray = np.concatenate([g for g, _ in rows], axis=0)
    mask = np.concatenate([m for _, m in rows], axis=0)
    rgb = np.dstack([gray, gray, gray])
    hit = mask > 0
    rgb[..., 0] = np.where(hit, 255, rgb[..., 0])
    rgb[..., 1] = np.where(hit, (gray * 0.35).astype(np.uint8), rgb[..., 1])
    rgb[..., 2] = np.where(hit, (gray * 0.35).astype(np.uint8), rgb[..., 2])
    both = np.concatenate([np.dstack([gray, gray, gray]), rgb], axis=1)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    Image.fromarray(both).save(path)
    print(f"  náhľad: {path} ({both.shape[1]}×{both.shape[0]} px)", flush=True)


def histogram(rows):
    """Rozloženie odtieňov šedej – prvé, čo treba vidieť pri ladení prahu."""
    if not rows:
        return ""
    gray = np.concatenate([g for g, _ in rows], axis=0)
    hist, _ = np.histogram(gray, bins=16, range=(0, 256))
    tot = max(1, hist.sum())
    out = ["  šedá     podiel"]
    for i, v in enumerate(hist):
        pct = 100.0 * v / tot
        out.append(f"  {i * 16:>3}–{i * 16 + 15:<3} {pct:5.1f} % "
                   f"{'█' * int(round(pct / 2))}")
    return "\n".join(out)


# ------------------------------------------------------------------- plán ---

def print_plan(z, x0, y0, x1, y1, args):
    n_tiles = (x1 - x0) * (y1 - y0)
    cells = n_tiles * TILE * TILE
    dl_s = n_tiles / TILES_PER_S
    ct_s = cells / CONTOUR_CELLS_PER_S
    print("── Plán: skaly z tieňovaných dlaždíc ────────────────")
    print(f"  zoom            z{z}  ({tile_res(z):.2f} m/px v Mercatore, "
          f"~{ground_res(z, (args.bbox[1] + args.bbox[3]) / 2):.2f} m na zemi)")
    print(f"  dlaždice        {x1 - x0} × {y1 - y0} = {n_tiles}")
    print(f"  mozaika         {(x1 - x0) * TILE} × {(y1 - y0) * TILE} px "
          f"= {cells / 1e9:.2f} mld.")
    print(f"  prah tmavosti   nikdy nad {args.dark}, vždy pod {args.dark_always}"
          + (f", medzi tým {args.rel} pod pozadím "
             f"(okno {args.local:g} m = {args.local_px} px)"
             if args.local_px else ", bez miestneho pozadia"))
    print(f"  triedy          steep, cliff od {args.cliff} stupňov navyše")
    print("  štruktúra       " + (f"vyplnená, okno {args.fill:g} m "
                                  f"({args.fill_px} px)" if args.fill_px
                                  else "jemná sieť žliabkov (fill vypnuté)"))
    print("  hlavičky        " + ("každý request ako iný prehliadač "
                                  f"({len(BROWSERS)} profilov)"
                                  if args.ua == "rotate" else
                                  "meno projektu" if args.ua == "project"
                                  else f"vlastné: {args.ua}"))
    print(f"  odhad sťahovanie ~{hms(dl_s)}")
    print(f"  odhad obrysy     ~{hms(ct_s)}")
    print("─────────────────────────────────────────────────────", flush=True)
    return n_tiles, cells


def apply_options(ap, args):
    """`kľúč=hodnota` z jedného textového poľa → tie isté prepínače.

    `workflow_dispatch` dovolí najviac 10 inputov (viď parse-options.py),
    takže zriedka menené veci idú do jedného poľa. Rozkladá sa to tu a nie
    v YAMLe zámerne: prepínače pozná táto trieda argparse, nie shell, a
    preklep tak vypadne ako hláška, nie ako ticho iné nastavenie.
    """
    raw = (args.options or "").strip()
    if not raw:
        return args
    known = {a.dest for a in ap._actions if a.dest not in ("help", "options")}
    extra = []
    for tok in shlex.split(raw):
        if "=" not in tok:
            print(f"::error::Voľba „{tok}“ nemá tvar kľúč=hodnota.",
                  file=sys.stderr)
            sys.exit(1)
        k, v = tok.split("=", 1)
        k = k.strip().replace("-", "_")
        if k not in known:
            print(f"::error::Neznáma voľba „{k}“. Známe voľby: "
                  f"{', '.join(sorted(known))}", file=sys.stderr)
            sys.exit(1)
        extra.append(f"--{k.replace('_', '-')}={v}")
    print(f"Z options: {' '.join(extra)}", flush=True)
    return ap.parse_args(sys.argv[1:] + extra)


def main():
    ap = argparse.ArgumentParser(
        description="Skalné plochy z tmavých miest v tieňovaných dlaždiciach.")
    ap.add_argument("--bbox", required=True, help="W,S,E,N v stupňoch")
    ap.add_argument("--url", default="https://sk-hires-shading.tiles.freemap.sk/{z}/{x}/{y}.jpg",
                    help="XYZ šablóna s {z}/{x}/{y}")
    ap.add_argument("--zoom", default="auto", help="číslo alebo `auto`")
    ap.add_argument("--zoom-max", type=int, default=19, help="odkiaľ `auto` skúša")
    ap.add_argument("--zoom-min", type=int, default=12)
    ap.add_argument("--max-tiles", type=int, default=60000,
                    help="strop na počet dlaždíc – `auto` pod neho zíde sám")
    ap.add_argument("--dark", type=int, default=125,
                    help="absolútny strop: nad touto šedou nie je skala nikdy")
    ap.add_argument("--dark-always", type=int, default=70,
                    help="pod touto šedou je skala vždy, nech je okolo čokoľvek")
    ap.add_argument("--local", type=float, default=1500.0,
                    help="okno miestneho pozadia v METROCH na zemi (0 = vypnuté)")
    ap.add_argument("--rel", type=int, default=18,
                    help="o koľko musí byť pixel pod miestnym pozadím")
    ap.add_argument("--cliff", type=int, default=25,
                    help="o koľko stupňov tmavšie začína trieda `cliff`")
    ap.add_argument("--blur", type=int, default=1,
                    help="polomer vyhladenia šedej v px (0 = vypnuté, max 2)")
    ap.add_argument("--fill", type=float, default=0.0,
                    help="spriemerovať tmavosť v okne toľkých METROV – zo "
                         "siete žliabkov spraví súvislú plochu (0 = vypnuté)")
    ap.add_argument("--min-area", type=float, default=50.0,
                    help="najmenšia skalná plocha v m²")
    ap.add_argument("--min-hole", type=float, default=10.0,
                    help="najmenšia diera, ktorá sa zachová, v m²")
    ap.add_argument("--simplify", type=float, default=-1,
                    help="zjednodušenie obrysu v metroch (-1 = jeden pixel)")
    ap.add_argument("--smooth", type=int, default=1,
                    help="koľkokrát zaobliť rohy (Chaikin, 0 = vypnuté)")
    ap.add_argument("--jobs", type=int, default=12, help="paralelné sťahovanie")
    ap.add_argument("--ua", default="rotate",
                    help="`rotate` = každý request ako iný prehliadač, "
                         "`project` = priznať sa menom projektu, alebo "
                         "vlastný User-Agent doslova")
    ap.add_argument("--cache-dir", default="tiles-cache")
    ap.add_argument("--band-cells", type=float, default=150e6,
                    help="koľko pixelov naraz drží jeden pás v pamäti")
    ap.add_argument("--heartbeat", type=int, default=30)
    ap.add_argument("--preview", default="", help="kam uložiť náhľad PNG")
    ap.add_argument("--preview-down", type=int, default=16,
                    help="koľkokrát zmenšiť náhľad")
    ap.add_argument("--stats", default="", help="kam zapísať kľúč=hodnota")
    ap.add_argument("--options", default="",
                    help="zriedka menené prepínače ako `kľúč=hodnota`, "
                         "napr. `local=800 min_area=300`")
    ap.add_argument("--out", required=True)
    args = apply_options(ap, ap.parse_args())

    args.bbox = [float(v) for v in args.bbox.split(",")]
    if len(args.bbox) != 4:
        print("::error::--bbox musí byť W,S,E,N.", file=sys.stderr)
        return 1
    args.blur = max(0, min(2, args.blur))
    lat_mid = (args.bbox[1] + args.bbox[3]) / 2.0

    os.makedirs(args.cache_dir, exist_ok=True)
    fetcher = Fetcher(args.url, args.cache_dir, jobs=args.jobs, ua=args.ua)

    if str(args.zoom).strip().lower() == "auto":
        z = probe_zoom(fetcher, args.bbox, args.zoom_max, args.zoom_min,
                       args.max_tiles)
        if not z:
            return 1
    else:
        z = int(args.zoom)

    # Okno pozadia je zadané v metroch na zemi – v pixeloch je až tu, keď je
    # známy zoom. Vďaka tomu má to isté nastavenie na z17 aj z18 rovnaký zmysel.
    args.local_px = (int(round(args.local / ground_res(z, lat_mid)))
                     if args.local > 0 else 0)
    args.fill_px = (int(round(args.fill / ground_res(z, lat_mid)))
                    if args.fill > 0 else 0)
    x0, y0, x1, y1 = tile_range(args.bbox, z)
    n_tiles, cells = print_plan(z, x0, y0, x1, y1, args)
    if n_tiles > args.max_tiles:
        print(f"::error::z{z} má {n_tiles} dlaždíc, strop je {args.max_tiles}. "
              f"Zvoľ menší výrez alebo nižší zoom (alebo zdvihni --max-tiles).",
              file=sys.stderr)
        return 2

    if args.simplify < 0:
        # Jeden pixel, nie štvrtina ako pri skalách z DEM: zdroj je 8-bitový
        # JPEG, takže pod pixel je už len zrno kompresie. Namerané na
        # skutočnej dlaždici (viď hlavičku súboru) – pol pixela a 2× Chaikin
        # stáli dvojnásobok dát za obrys, ktorý vyzerá rovnako.
        args.simplify = tile_res(z)

    tmp = os.path.abspath(args.out) + ".work"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)

    t_all = time.time()
    print("── Sťahovanie dlaždíc ───────────────────────────────", flush=True)
    dl_s = fetcher.fetch_all(z, x0, y0, x1, y1)
    if fetcher.n_ok + fetcher.n_cached == 0:
        print("::error::Nestiahla sa ani jedna dlaždica – bez dát sa nedá "
              "nič vektorizovať.", file=sys.stderr)
        return 1

    print("── Raster tmavosti ──────────────────────────────────", flush=True)
    preview_rows = [] if args.preview else None
    tifs, sc_s = build_score_raster(fetcher, z, x0, y0, x1, y1, args, tmp,
                                    preview_rows)

    print("── Vektorizácia ─────────────────────────────────────", flush=True)
    t_vec = time.time()
    st = vectorize(tifs, args, tmp, args.out, lat_mid)
    vec_s = time.time() - t_vec
    if not st.get("n"):
        print("::warning::Nenašla sa ani jedna skalná plocha – prahy sú "
              "pravdepodobne prísne. Pozri náhľad a histogram nižšie.")
        empty_rock(args.out)

    if args.preview:
        save_preview(preview_rows, args.preview)
    hist = histogram(preview_rows) if preview_rows else ""
    if hist:
        print("── Rozloženie odtieňov šedej ────────────────────────")
        print(hist)
        print("─────────────────────────────────────────────────────", flush=True)

    total_km2 = st.get("total_m2", 0.0) / 1e6
    print("── Hotovo ───────────────────────────────────────────")
    print(f"  plôch           {st.get('n', 0)} "
          f"(z toho {st.get('cliff', 0)} `cliff`), "
          f"{st.get('n_in', 0) - st.get('n', 0)} pod {args.min_area:g} m² preč")
    print(f"  spolu           {total_km2:.2f} km², "
          f"najväčšia {st.get('max_m2', 0) / 1e4:.1f} ha")
    print(f"  diery           {st.get('holes', 0)}")
    # Koľko dát na km² skál. To je to číslo, ktoré rozhoduje, či sa vrstva
    # zmestí do rozpočtu mapy – nie počet plôch. Jemná sieť žliabkov má málo
    # útvarov a veľa bodov.
    out_mb = dir_mb(args.out)
    per_km2 = out_mb / total_km2 if total_km2 > 0.001 else 0.0
    print(f"  výstup          {args.out} ({out_mb:.1f} MB"
          + (f", {per_km2:.1f} MB na km² skál)" if per_km2 else ")"))
    print(f"  čas             sťahovanie {hms(dl_s)}, tmavosť {hms(sc_s)}, "
          f"vektor {hms(vec_s)}, spolu {hms(time.time() - t_all)}")
    print("─────────────────────────────────────────────────────", flush=True)

    if args.stats:
        os.makedirs(os.path.dirname(os.path.abspath(args.stats)) or ".",
                    exist_ok=True)
        with open(args.stats, "w") as f:
            for k, v in [
                ("count", st.get("n", 0)), ("cliff", st.get("cliff", 0)),
                ("total_km2", f"{total_km2:.3f}"),
                ("max_m2", int(st.get("max_m2", 0))),
                ("holes", st.get("holes", 0)),
                ("holes_dropped", st.get("holes_dropped", 0)),
                ("dropped", st.get("n_in", 0) - st.get("n", 0)),
                ("zoom", z), ("tiles", n_tiles),
                ("tiles_missing", fetcher.n_miss),
                ("tiles_failed", fetcher.n_fail),
                ("mb_downloaded", f"{fetcher.bytes / 1048576:.0f}"),
                ("ua", args.ua), ("ua_profiles", len(fetcher.ua_seen)),
                ("cells", cells), ("px_m", f"{ground_res(z, lat_mid):.2f}"),
                ("dark", args.dark), ("dark_always", args.dark_always),
                ("local_m", f"{args.local:g}"), ("local_px", args.local_px),
                ("rel", args.rel),
                ("cliff_delta", args.cliff), ("blur", args.blur),
                ("fill_m", f"{args.fill:g}"),
                ("out_mb", f"{out_mb:.1f}"), ("mb_per_km2", f"{per_km2:.1f}"),
                ("min_area_m2", f"{args.min_area:g}"),
                ("min_hole_m2", f"{args.min_hole:g}"),
                ("simplify_m", f"{args.simplify:.2f}"), ("smooth", args.smooth),
                ("seconds", int(time.time() - t_all)),
            ]:
                f.write(f"{k}={v}\n")

    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

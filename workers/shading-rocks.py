#!/usr/bin/env python3
"""
TMAVÉ PLOCHY z TIEŇOVANÝCH DLAŽDÍC (JPG) → vektorové plochy (GeoPackage).

TOTO NIE SÚ SKALY, TOTO JE MASKA. Výstup ide do `rock_source: tienovanie`,
kde ním `workers/rock-areas.py` OREŽE svoje pásmo sklonu (`--clip`) – skala
je až to, čo je zároveň tmavé a zároveň strmé. Dôvod je nižšie („prečo to
môže klamať"): hillshade je osvetlený z jednej strany, takže tmavý je každý
odvrátený svah, aj úplne mierny. Kým sa tieto polygóny brali ako hotové
skaly, pokryli na testovacom výreze v Tatrách 0,68 km² z 2 km² (34 %)
a v mape z toho bola jedna sivá deka bez detailu.

Čo teda maska pridáva: TVAR. Hires vrstva freemap.sk je z 1 m LiDARu, kým
sklon sa počíta zo Sonnyho (20 m) – obrys skaly tak drží jemný detail, ktorý
by samotný DEM nikdy nedal, a sklon rozhoduje, kde skala vôbec je.

Berie hotový hillshade – dlaždice
`https://sk-hires-shading.tiles.freemap.sk/{z}/{x}/{y}.jpg` – a hľadá v ňom
TMAVÉ PLOCHY. Nič sa nepočíta z výšok, čítajú sa obrázky:

    XYZ dlaždice (JPG) → mozaika odtieňov šedej v EPSG:3857 →
    raster „tmavosti" → gdal_contour -p (izolínia tmavosti ako PLOCHY) →
    filter plôch → zjemnenie + zaoblenie obrysu → rock.gpkg

JEDNA TRIEDA (predvolene, `--plne`): jedno pásmo, teda žiadna plocha vnútri
inej plochy. Kým sa `steep` a `cliff` kreslili rôzne tmavo, malo zmysel mať
pásma dve; odkedy je všetko jedna sivá bez priehľadnosti, je z druhého len
dvojnásobok prstencov na obtiahnutie – a `gdal_contour` je tá najdrahšia fáza
celého behu. `--plne=0` vráti pásma `steep`/`cliff`.

DIERY OSTÁVAJÚ. Sú to medzery medzi vláknami siete žliabkov a práve ony sú tá
štruktúra – bez nich je z pol pohoria jedna súvislá plocha, v ktorej nie je
vidieť nič. `--zapln-diery=1` ich zaplní, ak by niekto chcel súvislé klaksy.

POZADIE SA ZAHADZUJE. `gdal_contour -p` nevyrobí len pásmo skál, ale VŠETKY
pásma – vrátane toho POD prahom, čiže „všetko, čo skala nie je". Je to jeden
obrovský polygón na blok a keď prejde ďalej, prekryje mapu súvislou plochou,
v ktorej nie je vidieť ani skaly, ani obrysy. Filter ho preto vyhadzuje podľa
`dmin` a beh navyše kričí, keď skaly vyjdú na viac než 60 % územia.

PREČO TO MÔŽE FUNGOVAŤ: tieňovanie je obraz sklonu. Kde je stena, tam je
tieň – a hires vrstva freemap.sk je robená z 1 m LiDARu, takže pri z18 vyjde
jeden pixel na ~0,4 m terénu. Ťaháme z17 (~0,8 m/px) – na z18 sú to 4×
dlaždice a obrysy rastú ešte rýchlejšie, pričom mapa z toho nemá nič.
Aj tak je to jemnejšie, než na čo vieme rozumne spočítať sklon sami.

PREČO TO MÔŽE KLAMAŤ: tmavý nie je len sklon, ale sklon NA ODVRÁTENEJ STRANE.
Rovnako strmá stena otočená k slnku je na hillshade najsvetlejšia zo všetkého,
a naopak mierny severozápadný svah je tmavý bez toho, aby bol skala. Táto
cesta preto systematicky nájde severozápadné steny, systematicky prehliadne
juhovýchodné a bez sklonu navrch berie aj to, čo skala nie je. Preto je to
maska pre `rock-areas.py`, a nie jeho náhrada; v mape sa zapína výberom
`rock_source: tienovanie`.

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
    útvary, o ktoré ide. Predvolené `--min-area` sme podľa toho posunuli ešte
    nižšie, na 5 m² (~8 pixelov na z17) – tabuľka ostáva pri tom, čo bolo
    naozaj namerané.

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

── prečo sa vektorizuje po blokoch ─────────────────────────────────────────

Pôvodne to bol jeden `gdal_contour` nad celou mozaikou – rovnako ako
v `rock-areas.py` a z toho istého dôvodu: diera prerezaná hranicou časti sa
zmení na zárez v okraji. Nad 3,62 mld. pixelov to ale bežalo 2 h 41 min,
nedopočítalo sa a zabil to timeout jobu, pričom pamäť ostala na 0,7 GB.
Nebola to teda pamäť, ale skladanie prstencov: `-p` z izolínií zostavuje
uzavreté obrysy a v zrnitom JPEGu ich je obrovské množstvo – spájanie
segmentov rastie rýchlejšie než lineárne, takže dvojnásobný raster nestojí
dvojnásobok, ale oveľa viac.

Preto sa contour púšťa po blokoch (`--block-tiles`, default 8 = 2048 px).
Blok je malý raster: prstence sa v ňom poskladajú rýchlo, pamäť je zhora
ohraničená a čo je hotové, leží na disku – po páde sa pokračuje od prvého
nespočítaného bloku.

Cena za to je presne tá pôvodná námietka: plocha cez hranicu bloku vypadne
ako dva kusy. Rieši to `zlep_svy()` – útvary, ktoré sa hranice naozaj
dotýkajú, sa označia už pri bloku (`sev=1`) a na konci sa zlepia cez
`ST_Union` (spatialite). Nie je to únia všetkého so všetkým: dotýka sa jej
zlomok plôch. Keď spatialite chýba, beh pokračuje s rozseknutými plochami
a povie to – rozseknutá skala je horšia mapa, nie zlá mapa.

Raster tmavosti sa aj naďalej počíta po pásoch dlaždicových riadkov
s presahom kvôli oknu pozadia; to sa nemenilo.

── čo to stojí ─────────────────────────────────────────────────────────────

Nie je to štvornásobok na zoom, ako tu stálo predtým. Sťahovanie áno, ale
obrysy nie – a tie sú to drahé. Namerané na Vysokých Tatrách (beh 31222472790):

  z17   13 815 dlaždíc, 0,91 mld. buniek   obrysy ~50 min (odhad)
  z18   55 260 dlaždíc, 3,62 mld. buniek   obrysy 2 h 41 min a NEDOPOČÍTALO SA
                                           (sťahovanie pritom len 12 minút)

Preto má `auto` okrem stropu na dlaždice aj rozpočet času (`--budget-min`)
a obrysy sa nad ním zastavia s hláškou. Dlaždice sa sťahujú do `--cache-dir`
a pri opakovanom behu sa berú odtiaľ, takže ani ladenie prahov, ani krok
o zoom nižšie nestojí jeden request navyše.

── keď to spadne, netreba začínať odznova ──────────────────────────────────

Rozrobené leží v `<cache-dir>/_rozrobene/<podpis prahov>/` – teda v cache
dlaždíc, ktorá sa ukladá aj po páde a po timeoute. Ďalší beh preskočí, čo už
je hotové:

    score0000.tif …   pásy rastra tmavosti  (pás po páse)
    bloky/b00000…     obrysy po blokoch     (blok po bloku)
    bands.geojsonl    bloky zlepené do jedného prúdu
    rock.geojsonl     vyfiltrované polygóny

Každá fáza sa píše do `.part` a premenuje sa až celá, takže nedopísaný kus
sa za hotový nikdy nevydá. Po úspešnom behu sa `_rozrobene` maže – nemá
zmysel, aby cache rástla o medzivýsledky. `--fresh=1` ho zahodí dopredu.

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
import re
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

# Koľko buniek za sekundu zvládne `gdal_contour` nad hotovou mozaikou.
#
# Bolo tu 3,5 mil./s, prevzaté z rock-areas.py – „rovnaký nástroj, rovnaký typ
# vstupu". Nebola to pravda a stálo to celý beh 31222472790: Vysoké Tatry na
# z18 (3,62 mld. buniek) mali podľa toho odhadu trvať 17 minút, v skutočnosti
# contour bežal 2 h 41 min, nevypísal ani jeden megabajt výstupu a zabil ho
# timeout jobu. Skutočná rýchlosť je teda POD 375 tis. buniek/s, čiže aspoň
# 9× menej. Rozdiel oproti skalám z DEM je v dátach: tam je izolínia sklonu
# nad hladkým rastrom, tu je izolínia tmavosti nad zrnitým JPEGom – tá má
# rádovo viac segmentov a práve tie contour stoja.
#
# 3e5 je bezpečná strana toho merania. Radšej nech `auto` zvolí o zoom nižšie
# a beh dobehne, než aby sľuboval detail, ktorý sa nikdy nedopočíta.
CONTOUR_CELLS_PER_S = 3.0e5
TILES_PER_S = 25.0  # pri --jobs=12 a ~25 kB na dlaždicu

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from watch import hms, dir_mb, run_watched, Heartbeat  # noqa: E402


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
                 ua="rotate", log_every=25):
        self.tmpl = url_tmpl
        self.cache = cache_dir
        self.jobs, self.retries, self.timeout = jobs, retries, timeout
        self.ua = ua
        self.log_every = log_every
        self.ua_seen = set()
        u = urllib.parse.urlsplit(url_tmpl)
        self.scheme, self.host = u.scheme, u.netloc
        self.local = threading.local()
        self.lock = threading.Lock()
        self.n_ok = self.n_miss = self.n_cached = self.n_fail = 0
        self.n_done = 0
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
        """True = dlaždicu máme (z cache alebo stiahnutú)."""
        return self.fetch(z, x, y) in ("cache", "stiahnuté")

    def fetch(self, z, x, y):
        """Stiahne jednu dlaždicu do cache a povie, ako to dopadlo:
        `cache` / `stiahnuté` / `chýba` (404) / `zlyhalo`.

        Stav ide von preto, aby sa pri každej dlaždici dalo vypísať, čo sa
        s ňou stalo – z holého „2 %" sa nedá poznať, či server dáva dáta,
        alebo len rýchlo odpovedá 404."""
        dst = self.path(z, x, y)
        if os.path.exists(dst):
            with self.lock:
                self.n_cached += 1
            return "cache" if os.path.getsize(dst) > 0 else "chýba"
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
            return "stiahnuté"
        if status == 404:
            open(dst, "wb").close()   # značka „tu nič nie je"
            with self.lock:
                self.n_miss += 1
            return "chýba"
        with self.lock:
            self.n_fail += 1
        return "zlyhalo"

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
                stav = self.fetch(z, x, y)
                now = time.time()
                # Riadok na dlaždicu (podľa `--log-every`): koľkátu práve
                # máme, čo s ňou bolo, ktorá to je a koľko ešte zostáva.
                # Časový strop je poistka: keď server spomalí na pár dlaždíc
                # za minútu, log nesmie stíchnuť.
                with self.lock:
                    self.n_done += 1
                    done = self.n_done
                if (self.log_every and done % self.log_every == 0) or \
                        now - last[0] >= 15:
                    with lock:
                        last[0] = now
                        rate = done / max(1e-6, now - t0)
                        eta = (total - done) / max(1e-6, rate)
                        print(f"  [{done}/{total}] {self.tmpl.format(z=z, x=x, y=y)}"
                              f"  {stav}, zostáva {total - done}, "
                              f"{rate:.0f}/s, ešte {hms(eta)}, "
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


def probe_zoom(fetcher, bbox, zmax, zmin, max_tiles, budget_s):
    """Najvyšší zoom, ktorý server naozaj dá a ktorý sa STIHNE spočítať.

    Dva stropy, lebo dve rôzne veci: `--max-tiles` chráni dobrovoľnícky server
    (koľko requestov mu pošleme) a `--budget-min` chráni beh (koľko z toho
    stihne `gdal_contour`). Ten druhý pribudol až po behu, ktorý sa na z18
    nedopočítal ani za tri hodiny – dlaždíc bolo pritom pod stropom.

    Zoom sa nedá prečítať z metadát – XYZ šablóna žiadne nemá. Skúša sa preto
    jedna dlaždica v strede územia, zhora nadol.
    """
    w, s, e, n = bbox
    lon, lat = (w + e) / 2.0, (s + n) / 2.0
    print("── Hľadám najvyšší zoom ─────────────────────────────")
    for z in range(zmax, zmin - 1, -1):
        x0, y0, x1, y1 = tile_range(bbox, z)
        count = (x1 - x0) * (y1 - y0)
        odhad = count * TILE * TILE / CONTOUR_CELLS_PER_S
        if count > max_tiles:
            print(f"  z{z:<3} {count:>8} dlaždíc  × nad strop {max_tiles}")
            continue
        if budget_s and odhad > budget_s:
            print(f"  z{z:<3} {count:>8} dlaždíc  × obrysy ~{hms(odhad)}, "
                  f"rozpočet {hms(budget_s)}")
            continue
        tx, ty = lonlat_to_tile(lon, lat, z)
        ok = fetcher.get(z, int(tx), int(ty))
        print(f"  z{z:<3} {count:>8} dlaždíc  "
              f"{'✓ dlaždica je' if ok else '× server nedal dlaždicu'}"
              + (f", obrysy ~{hms(odhad)}" if ok else ""))
        if ok:
            print(f"  vybrané         z{z}")
            print("─────────────────────────────────────────────────────", flush=True)
            return z
    print("─────────────────────────────────────────────────────", flush=True)
    print("::error::Žiadny zoom neprešiel: buď je výrez privelký na rozpočet "
          "(zdvihni --budget-min alebo zmenši area), alebo server nedal ani "
          "jednu skúšobnú dlaždicu (skontroluj --url).")
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


def load_band(fetcher, z, x0, x1, ty0, ty1, every=30):
    """Dlaždicové riadky [ty0, ty1) ako jeden obraz odtieňov šedej.

    Chýbajúca dlaždica ostane 255 (= úplne svetlá, teda určite nie skala),
    nie 0 – nula by sa vyhodnotila ako najtmavšie miesto v mozaike.

    Dekódovanie tisícok JPEGov je najdlhšia tichá časť celého behu, preto
    sa priebežne hlási, ktorý dlaždicový riadok je na rade.
    """
    w = (x1 - x0) * TILE
    h = (ty1 - ty0) * TILE
    band = np.full((h, w), 255, np.uint8)
    t0 = last = time.time()
    n = 0
    for ty in range(ty0, ty1):
        now = time.time()
        if every and now - last >= every:
            last = now
            hotovo = ty - ty0
            eta = (now - t0) / max(1, hotovo) * (ty1 - ty - 0) if hotovo else 0
            print(f"  … dekódovanie: riadok {hotovo + 1}/{ty1 - ty0}, "
                  f"{n} dlaždíc, beží {hms(now - t0)}"
                  + (f", ostáva {hms(eta)}" if hotovo else ""), flush=True)
        for tx in range(x0, x1):
            n += 1
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


def score_band(gray, dark, always, local_px, rel, blur, fill_px=0, every=0):
    """Šedá → „tmavosť" (Byte): o koľko je pixel pod referenciou.

    ref   = clip(pozadie − rel, always, dark)   (bez pozadia rovno `dark`)
    score = clip(ref − šedá, 0, 255)

    `fill_px` (input `fill`, default vypnuté) navyše spriemeruje tmavosť
    v okolí, takže sa z jemnej siete žliabkov stane súvislá plocha. Viď
    hlavičku súboru – zámerne je to vypnuté, lebo tá jemná štruktúra JE
    to, čo chceme.
    """
    def faza(text, t0):
        if every:
            print(f"  … tmavosť: {text} ({hms(time.time() - t0)})", flush=True)

    t_f = time.time()
    gray = box_blur_u8(gray, blur)
    h, w = gray.shape
    if local_px > 0:
        faza("miestne pozadie", t_f)
        small = block_mean(gray, BG_DOWN)
        bg = bright_background(small, max(1, int(round(local_px / BG_DOWN / 2))))
        np.subtract(bg, float(rel), out=bg)
        np.clip(bg, float(always), float(dark), out=bg)
    else:
        bg = None

    faza("prah tmavosti", t_f)
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
        faza("vyplnenie", t_f)
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
    # Cieľ sa píše cez `.part` a premenuje sa až hotový. Pri pokračovaní po
    # páde sa totiž existencia súboru berie ako „tento pás je spočítaný" –
    # polovičný TIFF by tichú dieru v mozaike zamkol navždy.
    final_tif, out_tif = out_tif, out_tif + ".part"
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
    os.replace(out_tif, final_tif)


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
        tif = os.path.join(tmp, f"score{bi:04d}.tif")
        # Hotový pás z predošlého (spadnutého) behu sa nepočíta znova. Súbor
        # tam je len vtedy, keď sa dopísal celý – `write_chunk` ide cez
        # `.part` a premenovanie, takže polovičný TIFF sa za hotový nikdy
        # nevydá. Náhľad sa tým pádom skladá len z toho, čo sa naozaj
        # počítalo; pri úplnom pokračovaní bude prázdny a to je v poriadku.
        if os.path.exists(tif) and os.path.getsize(tif) > 0:
            tifs.append(tif)
            print(f"  … tmavosť: pás {bi + 1}/{n_bands} už je "
                  f"({dir_mb(tif):.0f} MB) – preskakujem", flush=True)
            continue
        # Tep okolo celého pásu: dekódovanie aj numpy fázy sú dlhé a tiché,
        # takže bez neho z logu nepoznáš „počíta" od „zaseklo sa". Toto je
        # záruka, že ticho nikdy nepresiahne `--heartbeat` sekúnd; riadky
        # nižšie k tomu hovoria, čo sa práve deje.
        hb = Heartbeat(f"pás {bi + 1}/{n_bands}", every=args.heartbeat)
        hb.start()
        try:
            gray = load_band(fetcher, z, x0, x1, py0, py1,
                             every=args.heartbeat)
            score, blurred = score_band(gray, args.dark, args.dark_always,
                                        local_px, args.rel, args.blur,
                                        args.fill_px, every=args.heartbeat)
        finally:
            hb.stop()
        del gray
        top = (ty - py0) * TILE
        bot = top + (ty1 - ty) * TILE
        cut = score[top:bot]
        ox = -R + x0 * TILE * res
        oy = R - ty * TILE * res
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

def bbox_km2(bbox):
    """Plocha bboxu v km² – len na kontrolu, či výsledok nepokrýva všetko."""
    w, s_, e, n = bbox
    return ((e - w) * 111.32 * math.cos(math.radians((s_ + n) / 2))
            * (n - s_) * 110.54)


def ring_area(ring):
    """Plocha prstenca (shoelace) v jednotkách súradníc, so znamienkom."""
    s = 0.0
    for i in range(len(ring) - 1):
        x0, y0 = ring[i][0], ring[i][1]
        x1, y1 = ring[i + 1][0], ring[i + 1][1]
        s += x0 * y1 - x1 * y0
    return s / 2.0


def filter_stream(src, dst, min_area, min_hole, cliff_level, merc_factor,
                  every=30, zapln_diery=False, min_level=0.5):
    """Prúdový filter nad GeoJSONSeq: odrobinky preč, dierky preč, triedy von.

    Vstup je už rozbitý na samostatné plochy (`-explodecollections`), takže
    jeden riadok = jedna skala. Bez toho by z celého pohoria vyšli DVA
    útvary – pásmo `steep` a pásmo `cliff` – a atribút `area` by hovoril,
    koľko je skál dokopy, nie aká veľká je tá jedna pod hrebeňom.

    Plocha sa počíta zo súradníc v EPSG:3857 a násobí `merc_factor`
    (= cos²(šírka)). Mercator naťahuje mierku 1/cos(šírka), takže bez toho
    by pri 49° vyšla plocha 2,3× väčšia, než v skutočnosti je.

    DIERY OSTÁVAJÚ. Sú to medzery medzi vláknami tej siete žliabkov a práve
    ony sú tá štruktúra – bez nich je z pol pohoria jedna súvislá plocha,
    v ktorej nie je vidieť nič (namerané: zapnuté zapĺňanie zožralo detail
    úplne). Zahadzujú sa podľa VLASTNEJ plochy, nie podľa plochy celku:
    svetlá polica vnútri steny je platná diera a má ostať, jednopixelová
    dierka po zrne JPEGu nie.

    `zapln_diery` ich zaplní – je to voľba pre prípad, že by niekto chcel
    súvislé klaksy namiesto siete, nie predvolené správanie.
    """
    n_in = n_out = 0
    n_pozadie = 0
    holes_kept = holes_drop = 0
    total = 0.0
    n_cliff = 0
    biggest = 0.0
    # Píše sa priebežne do `.part` a premenuje sa až hotové. Riadok = jedna
    # skala, takže výstup rastie plynulo a je pri ňom počuť; nedopísaný
    # súbor sa pritom nikdy nevydá za hotovú fázu (viď `hotove`).
    part = dst + ".part"
    t0 = time.time()
    last = t0
    with open(src) as fi, open(part, "w") as fo:
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
            # POZADIE PREČ. `gdal_contour -p -fl 0,5 -fl 256` nevyrobí len
            # pásmo skál, ale VŠETKY pásma – vrátane toho pod prahom, teda
            # „všetko, čo skala nie je". To je jeden obrovský polygón na blok
            # a keď prejde ďalej, prekryje mapu súvislou plochou, v ktorej
            # nie je vidieť ani skaly, ani obrysy. (Presne to sa dialo:
            # skaly z tieňovania boli sivá plocha cez celý výrez.)
            #
            # Pri skalách z DEM to nikdy nenastalo – `rock-areas.py` má
            # `WHERE smin >= prah` priamo v SQL. Tu ten filter chýbal.
            if dmin < min_level:
                n_pozadie += 1
                continue
            cls = "cliff" if dmin >= cliff_level else "steep"

            for poly in parts:
                if not poly:
                    continue
                n_in += 1
                area = abs(ring_area(poly[0])) * merc_factor
                rings = [poly[0]]
                if zapln_diery:
                    holes_drop += len(poly) - 1
                else:
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
                now = time.time()
                if every and now - last >= every:
                    last = now
                    fo.flush()
                    print(f"  … filter plôch: {n_in} prečítaných, "
                          f"{n_out} ponechaných, beží {hms(now - t0)}",
                          flush=True)
                total += area
                biggest = max(biggest, area)
                n_cliff += (cls == "cliff")
    os.replace(part, dst)
    return {"n_in": n_in, "n": n_out, "cliff": n_cliff, "total_m2": total,
            "pozadie": n_pozadie,
            "max_m2": biggest, "holes": holes_kept, "holes_dropped": holes_drop}


# Čísla o sťahovaní vedľa cache, nie v `_rozrobene`: sú o dlaždiciach, nie
# o prahoch, takže sa nesmú stratiť pri zmene prahu ani pri `fresh=1`.
STIAHNUTE = "_stiahnute.txt"


def zapis_stiahnute(cache_dir, fetcher, n_tiles):
    """Odloží, čo stálo sieť – fáza `spojit` už nesťahuje a nemá to odkiaľ vedieť."""
    dl = {"tiles": n_tiles, "tiles_missing": fetcher.n_miss,
          "tiles_failed": fetcher.n_fail,
          "mb_downloaded": f"{fetcher.bytes / 1048576:.0f}",
          "ua_profiles": len(fetcher.ua_seen)}
    try:
        with open(os.path.join(cache_dir, STIAHNUTE), "w") as f:
            for k, v in dl.items():
                f.write(f"{k}={v}\n")
    except OSError as exc:
        print(f"  čísla o sťahovaní sa neuložili ({exc}) – v štatistike "
              f"ďalšej fázy budú nuly.", flush=True)
    return dl


def nacitaj_stiahnute(cache_dir, n_tiles):
    """Čísla z fázy sťahovania. Keď chýbajú, radšej nuly než vymyslené hodnoty."""
    dl = {"tiles": n_tiles, "tiles_missing": 0, "tiles_failed": 0,
          "mb_downloaded": "0", "ua_profiles": 0}
    cesta = os.path.join(cache_dir, STIAHNUTE)
    try:
        with open(cesta) as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k in dl:
                    dl[k] = int(v) if k not in ("mb_downloaded",) else v
        print(f"  čísla o sťahovaní z {cesta}: {dl['tiles']} dlaždíc, "
              f"{dl['mb_downloaded']} MB", flush=True)
    except OSError:
        print(f"  {cesta} nie je – čísla o dlaždiciach v štatistike budú nuly.",
              flush=True)
    return dl


def hotove(path, label):
    """True = túto fázu spravil predošlý beh a netreba ju robiť znova.

    Každá fáza sa píše do `.part` a premenuje sa až celá, takže existencia
    súboru znamená „dokončené"; nedopísaný kus sa za hotový nikdy nevydá.
    Zmysel to má vďaka tomu, že pracovný priečinok leží v cache dlaždíc –
    prežije teda aj beh, ktorý spadol alebo ho zabil timeout.
    """
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print(f"  {label}: hotové z predošlého behu "
              f"({dir_mb(path):.0f} MB) – preskakujem", flush=True)
        return True
    return False


def raster_size(vrt):
    """Rozmer rastra v pixeloch, prečítaný z hlavičky VRT (bez bindings)."""
    with open(vrt) as f:
        head = f.read(4096)
    m = re.search(r'rasterXSize="(\d+)"\s+rasterYSize="(\d+)"', head)
    if not m:
        m = re.search(r'rasterYSize="(\d+)"\s+rasterXSize="(\d+)"', head)
        return (int(m.group(2)), int(m.group(1))) if m else (0, 0)
    return int(m.group(1)), int(m.group(2))


def skontroluj_jednotky(src, x0, y0, x1, y1):
    """Sú súradnice prvého bloku v metroch, ako ich čaká výpočet plochy?

    PREČO: `filter_stream` počíta plochu zo súradníc, akoby boli metrické
    (EPSG:3857). Keby ovládač GeoJSON medzitým prepočítal na stupne, každá
    skala vyjde rádovo 1e-9 m², spadne pod `min_area` a výsledok je NULA
    plôch – po hodine počítania a bez jedinej chybovej hlášky. Presne to sa
    stalo behu 31245134321.

    Overiť sa to dá lacno: prvý vrchol prvého bloku musí ležať v jeho
    vlastnom rozsahu. Stupne (rádovo desiatky) sa do metrov (rádovo milióny)
    nezmestia, takže sa to pozná hneď.
    """
    with open(src) as f:
        for line in f:
            if not line.strip():
                continue
            g = (json.loads(line).get("geometry") or {})
            polys = ([g["coordinates"]] if g.get("type") == "Polygon"
                     else g.get("coordinates") or [])
            if not polys or not polys[0]:
                continue
            x, y = polys[0][0][0][:2]
            rez = max(abs(x1 - x0), abs(y1 - y0))
            if (min(x0, x1) - rez <= x <= max(x0, x1) + rez
                    and min(y0, y1) - rez <= y <= max(y0, y1) + rez):
                return
            raise RuntimeError(
                f"obrysy prišli v iných súradniciach, než v akých sa počíta "
                f"plocha: prvý vrchol [{x:.2f}, {y:.2f}], blok má byť "
                f"[{x0:.0f}…{x1:.0f}, {y1:.0f}…{y0:.0f}] v metroch "
                f"(EPSG:3857). Vyzerá to na prepočet do stupňov – pozri "
                f"vyhodenie <SRS> z okna bloku.")


def oznac_svy(src, dst, x0, y0, x1, y1, res):
    """Označí útvary, ktoré sa dotýkajú hranice bloku (`sev=1`).

    Plocha cez hranicu vypadne z dvoch blokov ako dva kusy. Zlepiť sa musia,
    inak by v mape boli vidieť rovné rezy – skalné plochy sa kreslia
    s obrysom. Zlepenie je drahé, tak sa robí len nad tými, ktorých sa to
    naozaj týka; ostatné (drvivá väčšina) idú rovno ďalej.

    Tolerancia je pol pixela: obrys `gdal_contour` končí presne na hrane
    okna, ale v desatinnom čísle.
    """
    tol = abs(res) / 2.0
    xmin, xmax = min(x0, x1), max(x0, x1)
    ymin, ymax = min(y0, y1), max(y0, y1)
    # Vlastné dočasné meno: `src` je už `dst + ".part"`, takže rovnaká
    # prípona by znamenala, že si súbor prepisuje sám seba.
    part = dst + ".sev"
    with open(src) as fi, open(part, "w") as fo:
        for line in fi:
            line = line.strip()
            if not line:
                continue
            feat = json.loads(line)
            g = feat.get("geometry") or {}
            polys = ([g["coordinates"]] if g.get("type") == "Polygon"
                     else g.get("coordinates") or [])
            sev = 0
            for poly in polys:
                for x, y, *_ in poly[0] if poly else []:
                    if (abs(x - xmin) <= tol or abs(x - xmax) <= tol
                            or abs(y - ymin) <= tol or abs(y - ymax) <= tol):
                        sev = 1
                        break
                if sev:
                    break
            feat.setdefault("properties", {})["sev"] = sev
            fo.write(json.dumps(feat, separators=(",", ":")) + "\n")
    os.replace(part, dst)
    os.remove(src)


def contour_blocks(vrt, args, tmp, cliff_level, ox, oy, res):
    """Obrysy po blokoch: každý blok zvlášť a hneď na disk.

    PREČO PO BLOKOCH. `gdal_contour -p` skladá z izolínií uzavreté prstence
    a robí to nad celým rastrom naraz. Nad mozaikou 3,62 mld. pixelov to
    bežalo 2 h 41 min, nedopočítalo sa a zabil to timeout jobu – pamäť pritom
    ostala na 0,7 GB, čiže to nebola pamäť, ale skladanie prstencov: tých je
    v zrnitom JPEGu obrovské množstvo a spájanie segmentov rastie rýchlejšie
    než lineárne. Blok je malý raster, takže sa v ňom prstence poskladajú
    rýchlo, pamäť je zhora ohraničená a hlavne: čo je hotové, je na disku.

    ZA ČO SA TO PLATÍ. Plocha cez hranicu bloku vypadne ako dva polygóny.
    Spája ich `zlep_svy()` na konci – ten sa zaoberá len tými, ktoré sa
    hranice naozaj dotýkajú, takže nejde o úniu všetkého so všetkým.
    """
    w_px, h_px = raster_size(vrt)
    if not w_px:
        raise RuntimeError(f"z {vrt} sa nedá prečítať rozmer rastra")
    blok = max(1, args.block_tiles) * TILE
    bloky = [(bx, by)
             for by in range(0, h_px, blok)
             for bx in range(0, w_px, blok)]
    # PLNÉ PLOCHY (`--plne`, predvolene zapnuté): jediné pásmo [0,5; 256).
    # Dve pásma (`steep` a `cliff`) mali zmysel, kým sa kreslili rôzne tmavo –
    # `cliff` ležal v diere `steep`u a spolu dláždili územie bez prekryvu.
    # Odkedy sú všetky plochy jedna sivá bez priehľadnosti, je z toho len
    # dvojnásobok prstencov na obtiahnutie (a `gdal_contour` je tá najdrahšia
    # fáza celého behu). Jedno pásmo = jedna plocha, nič v ničom.
    levels = (["-fl", "0.5", "-fl", "256"] if args.plne else
              ["-fl", "0.5", "-fl", repr(cliff_level), "-fl", "256"])
    d = os.path.join(tmp, "bloky")
    os.makedirs(d, exist_ok=True)

    hotovych = sum(1 for i in range(len(bloky))
                   if os.path.exists(os.path.join(d, f"b{i:05d}.geojsonl")))
    print(f"  blok {args.block_tiles}×{args.block_tiles} dlaždíc "
          f"({blok}×{blok} px), {len(bloky)} blokov"
          + (f", {hotovych} už hotových z predošlého behu" if hotovych else ""),
          flush=True)

    t0 = time.time()
    limit = args.budget_min * 60
    for i, (bx, by) in enumerate(bloky):
        cesta = os.path.join(d, f"b{i:05d}.geojsonl")
        if os.path.exists(cesta):
            continue
        bw, bh = min(blok, w_px - bx), min(blok, h_px - by)
        okno = os.path.join(tmp, "blok.vrt")
        # `-of VRT` je len XML nad tým istým rastrom – neprepisuje ani bajt
        # dát, takže výrez bloku nič nestojí.
        run(["gdal_translate", "-q", "-of", "VRT", "-srcwin",
             str(bx), str(by), str(bw), str(bh), vrt, okno])
        # A TERAZ TO DÔLEŽITÉ: z okna sa vyhodí <SRS>.
        #
        # Ovládač GeoJSON prepočítava do WGS84 vždy, keď zdroj vie, v čom je.
        # Contour nad rastrom s EPSG:3857 by teda vypísal STUPNE – a `filter`
        # počíta plochu zo súradníc ako z metrov, takže by každá skala vyšla
        # rádovo 1e-9 m² a spadla pod `min_area`. Presne to sa aj stalo:
        # 976 725 plôch, z toho 0 ponechaných (beh 31245134321). Predtým to
        # držal `-a_srs EPSG:4326` na `ogr2ogr`, ktorý súradnice len preznačí
        # a neprepočíta; po prechode na bloky ten krok zmizol a s ním aj trik.
        # Bez SRS nemá čo prepočítať a súradnice ostanú metrické.
        with open(okno) as f:
            xml = f.read()
        with open(okno, "w") as f:
            f.write(re.sub(r"\s*<SRS[^>]*>.*?</SRS>", "", xml, flags=re.S))
        part = cesta + ".part"
        run(["gdal_contour", "-p", "-q", "-amin", "dmin", "-amax", "dmax",
             *levels, "-f", "GeoJSONSeq", "-nln", "band",
             "-lco", "COORDINATE_PRECISION=2", okno, part])
        x_od, y_od = ox + bx * res, oy - by * res
        x_do, y_do = ox + (bx + bw) * res, oy - (by + bh) * res
        if i == 0:
            skontroluj_jednotky(part, x_od, y_od, x_do, y_do)
        oznac_svy(part, cesta, x_od, y_od, x_do, y_do, res)

        el = time.time() - t0
        spravene = i + 1 - hotovych
        if spravene > 0 and (i % max(1, len(bloky) // 50) == 0
                             or i == len(bloky) - 1):
            eta = el / spravene * (len(bloky) - i - 1)
            print(f"  … obrysy: blok {i + 1}/{len(bloky)}, beží {hms(el)}, "
                  f"ostáva {hms(eta)}, na disku {dir_mb(d):.0f} MB", flush=True)
        # Rozpočet platí aj tu: keď to nestíha, povie sa to teraz – a čo je
        # hotové, ostáva, takže ďalší beh nadviaže presne tu.
        if limit and el > limit:
            raise TimeoutError(f"obrysy: {i + 1}/{len(bloky)} blokov")
    return d, len(bloky)


def zlep_svy(seq, tmp, cliff_level, args):
    """Spojí plochy rozseknuté hranicou bloku. Vráti cestu k výsledku.

    Robí to `ST_Union` v SQLite dialekte, čo vyžaduje spatialite – a LEN nad
    tými útvarmi, ktoré sa hranice bloku naozaj dotýkajú (`sev=1`). Tých je
    zlomok, takže to nie je únia všetkého so všetkým; celé územie naraz by
    bola presne tá fáza, ktorej sme sa blokmi zbavovali.
    Zlučuje sa po triede (`dmin`), inak by sa stena zlepila so svahom.

    Keby spatialite nebolo alebo príkaz zlyhal, beh POKRAČUJE s rozseknutými
    plochami a povie to. Rozseknutá skala je horšia mapa, nie zlá mapa –
    a zhodiť kvôli tomu trojhodinový výpočet by bola hlúposť.
    """
    svy = os.path.join(tmp, "svy.geojsonl")
    zvysok = os.path.join(tmp, "bez-svov.geojsonl")
    n_sev = n_ok = 0
    with open(seq) as fi, open(svy, "w") as fs, open(zvysok, "w") as fz:
        for line in fi:
            if not line.strip():
                continue
            if '"sev":1' in line.replace(" ", ""):
                fs.write(line)
                n_sev += 1
            else:
                fz.write(line)
                n_ok += 1
    if not n_sev:
        print("  švy: žiadna plocha nesiaha na hranicu bloku", flush=True)
        return zvysok

    print(f"  švy: {n_sev} plôch na hranici bloku, {n_ok} mimo – "
          f"zlepujem tie prvé", flush=True)
    zlep = os.path.join(tmp, "zlepene.geojsonl")
    try:
        run_watched(["ogr2ogr", "-f", "GeoJSONSeq", zlep, svy,
                     "-dialect", "SQLITE", "-explodecollections",
                     "-sql", "SELECT dmin, ST_Union(geometry) AS geometry "
                             "FROM svy GROUP BY dmin"],
                    "zlepenie švov", tmp=zlep, every=args.heartbeat,
                    max_s=args.budget_min * 60)
    except Exception as exc:
        print(f"::warning::Švy sa nepodarilo zlepiť ({type(exc).__name__}) – "
              f"plochy cez hranicu bloku ostanú rozseknuté. Chýba "
              f"spatialite? Mapa bude, len s rovnými rezmi v skalách.",
              flush=True)
        return seq

    spolu = os.path.join(tmp, "bands-zlepene.geojsonl")
    with open(spolu, "w") as fo:
        for src in (zvysok, zlep):
            with open(src) as fi:
                for line in fi:
                    if line.strip():
                        fo.write(line)
    return spolu


def vrt_geo(vrt):
    """Ľavý horný roh a veľkosť pixela z hlavičky VRT."""
    with open(vrt) as f:
        head = f.read(8192)
    m = re.search(r"<GeoTransform>(.*?)</GeoTransform>", head, re.S)
    if not m:
        raise RuntimeError(f"{vrt} nemá GeoTransform")
    g = [float(x) for x in m.group(1).split(",")]
    return g[0], g[3], g[1]     # ox, oy, veľkosť pixela (x)


def obrysy(tifs, args, tmp, cliff_level):
    """Mozaika tmavosti → obrysy po blokoch v `tmp/bloky`. Vráti počet blokov.

    Prvá polovica vektorizácie a vo workflowe vlastný job: toto je tá drahá
    časť (hodiny), zlepovanie a filter za ňou sú minúty. Rozdelené preto, aby
    hotové bloky prežili, keď sa beh nezmestí do stropu času.
    """
    vrt = os.path.join(tmp, "score.vrt")
    run(["gdalbuildvrt", "-q", vrt] + tifs)
    ox, oy, res = vrt_geo(vrt)
    _, n_blokov = contour_blocks(vrt, args, tmp, cliff_level, ox, oy, res)
    return n_blokov


def spoj(args, tmp, out, cliff_level, merc, uzemie_km2=0.0):
    """Obrysy blokov → hotový rock.gpkg v EPSG:4326.

    Druhá polovica vektorizácie: zlepenie blokov, plochy rozseknuté hranicou
    bloku, filter, zjednodušenie a vyhladenie. Číta `tmp/bloky` – teda to, čo
    nechal `obrysy()`, či už v tom istom behu alebo v predošlom jobe.
    """
    d_bloky = os.path.join(tmp, "bloky")
    if not os.path.isdir(d_bloky):
        raise RuntimeError(
            f"Chýbajú obrysy blokov ({d_bloky}). Fáza `spojit` nadväzuje na "
            f"fázu `vektor` – buď nebežala, alebo sa medzitým stratila cache "
            f"s rozrobeným. Pusti beh s `--phase=vsetko`.")
    n_blokov = len([f for f in os.listdir(d_bloky) if f.endswith(".geojsonl")])

    # Zlepenie blokov do jedného prúdu. `-explodecollections` netreba –
    # filter nižšie si MultiPolygon rozoberie sám a jeden `ogr2ogr` nad celým
    # územím by bol práve tá fáza, ktorej sa chceme zbaviť.
    seq = os.path.join(tmp, "bands.geojsonl")
    if not hotove(seq, "spojenie blokov"):
        part = seq + ".part"
        n = 0
        with open(part, "w") as fo:
            for f in sorted(os.listdir(d_bloky)):
                if not f.endswith(".geojsonl"):
                    continue
                with open(os.path.join(d_bloky, f)) as fi:
                    for line in fi:
                        if line.strip():
                            fo.write(line)
                            n += 1
        os.replace(part, seq)
        print(f"  spojenie blokov: {n} útvarov z {n_blokov} blokov", flush=True)

    # Zlepovanie plôch rozseknutých hranicou bloku je len kozmetika: odkedy
    # majú všetky plochy tú istú sivú bez priehľadnosti, dva kusy vedľa seba
    # vyzerajú ako jeden. Stojí pritom `ST_Union` nad všetkým, čo sa hranice
    # dotýka, a spatialite navyše – preto predvolene vypnuté (`zlepit=1` ho
    # vráti). Cena za to je, že `area` je plocha kusa, nie celej skaly.
    if args.zlepit:
        seq = zlep_svy(seq, tmp, cliff_level, args)
    else:
        print("  švy: nezlepujem (`options: zlepit=1` to zapne) – rovnaká "
              "sivá bez priehľadnosti spoj aj tak nepotrebuje", flush=True)

    filt = os.path.join(tmp, "rock.geojsonl")
    st = filter_stream(seq, filt, args.min_area, args.min_hole,
                       cliff_level, merc, every=args.heartbeat,
                       zapln_diery=bool(args.zapln_diery), min_level=0.5)
    print(f"  filter: {st['n_in']} → {st['n']} plôch "
          f"({st.get('pozadie', 0)} pásiem pod prahom = pozadie preč, "
          f"pod {args.min_area:g} m² preč), "
          + (f"diery ZAPLNENÉ ({st['holes_dropped']} zahodených) – "
             f"tvar plôch je preč, `zapln_diery=0` ho vráti"
             if args.zapln_diery else
             f"diery {st['holes']} ostali, {st['holes_dropped']} pod "
             f"{args.min_hole:g} m² preč"), flush=True)
    # Poistka proti návratu tej istej chyby: keď jedna plocha pokrýva
    # väčšinu územia, nie je to skala, ale pozadie. Ticho by z toho bola
    # zase sivá deka cez celú mapu.
    # Súčet, nie najväčšia plocha: pozadie je jeden polygón NA BLOK, takže
    # pri mnohých blokoch nie je ani jeden z nich veľký voči celku – ale
    # dokopy pokryjú takmer všetko. Na skutočných dátach je pokrytie skalami
    # jednotky až nižšie desiatky percent; 60 % nedosiahne ani Vysoké Tatry.
    spolu_km2 = st.get("total_m2", 0) / 1e6
    if uzemie_km2 > 0 and spolu_km2 > 0.6 * uzemie_km2:
        print(f"::warning::Skaly pokrývajú {spolu_km2:.2f} km² z "
              f"{uzemie_km2:.2f} km² územia ({100 * spolu_km2 / uzemie_km2:.0f} %). "
              f"Toľko skál nikde nie je – vyzerá to, že do výsledku prešlo "
              f"pásmo POD prahom (pozadie). V mape z toho bude súvislá plocha "
              f"bez detailu a bez obrysov.", flush=True)

    if not st["n"]:
        if st["n_in"] > 1000:
            # Tisíce útvarov a ani jeden dosť veľký nie je „prísny prah" –
            # taký prah by ich neprepustil vôbec. Skôr je zle jednotka plochy.
            print(f"::warning::Filter zahodil VŠETKÝCH {st['n_in']} útvarov "
                  f"ako menšie než {args.min_area:g} m². Pri takom počte to "
                  f"nevyzerá na prísny prah, ale na to, že plocha sa počíta "
                  f"v iných jednotkách než v metroch – skontroluj súradnice "
                  f"v {seq}.", flush=True)
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
    # z17 je strop, nie z19. Vyššie zoomy síce server dá, ale na z18 sú to 4×
    # dlaždice a obrysy rastú ešte rýchlejšie – 3,62 mld. pixelov na z18 nad
    # Vysokými Tatrami bežalo 2 h 41 min a nedopočítalo sa. Mapa z toho
    # nemá nič: z17 je ~0,8 m na pixel a plocha sa zobrazuje do maximálneho
    # zoomu tak či tak (dlaždice sa naťahujú overzoomom).
    ap.add_argument("--zoom-max", type=int, default=17,
                    help="najvyšší zoom, na ktorý sa vôbec pýtame dlaždíc "
                         "(odtiaľ `auto` skúša nadol)")
    ap.add_argument("--zoom-min", type=int, default=12)
    ap.add_argument("--max-tiles", type=int, default=60000,
                    help="strop na počet dlaždíc – `auto` pod neho zíde sám")
    # Číslo, nie `store_true`: voľby z jedného textového poľa chodia vždy ako
    # `kľúč=hodnota`, takže prepínač bez hodnoty by cez ne nešiel zadať.
    ap.add_argument("--block-tiles", type=int, default=8,
                    help="strana bloku v dlaždiciach pri obrysoch; menší blok "
                         "= menej pamäte a jemnejšie pokračovanie, ale viac "
                         "volaní GDALu (8 = 2048 px, 3 = 768 px)")
    # Vo workflowe sú z toho TRI joby, každý s vlastným stropom času – dokopy
    # sa to do jedného rozpočtu zmestiť nemusí a keď čas dôjde, padne aj to,
    # čo už bolo hotové. Medzivýsledky si podávajú cez cache:
    #
    #   stiahnut  dlaždice do cache
    #   vektor    raster tmavosti + obrysy po blokoch (`_rozrobene/…/bloky`)
    #   spojit    zlepenie blokov, švy, filter, vyhladenie → rock.gpkg
    #
    # `vsetko` je to isté v jednom kuse – tak sa to spúšťa z ruky.
    ap.add_argument("--phase", default="vsetko",
                    choices=("vsetko", "stiahnut", "vektor", "spojit"),
                    help="ktorú časť spraviť")
    ap.add_argument("--zoom-out", default="",
                    help="kam zapísať vybraný zoom (`zoom=17`) pre ďalší job")
    ap.add_argument("--fresh", type=int, default=0,
                    help="1 = zahodiť rozrobené z predošlého behu a počítať "
                         "všetko odznova (dlaždice ostávajú v cache)")
    ap.add_argument("--log-every", type=int, default=25,
                    help="po koľkých dlaždiciach vypísať riadok "
                         "(1 = každá, 0 = len každých 15 s)")
    ap.add_argument("--budget-min", type=float, default=100,
                    help="koľko minút smú trvať obrysy; `auto` pod to zíde "
                         "sám a beh sa nad tým zastaví (0 = bez stropu)")
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
    # 7 m² je ~11 pixelov na z17. Zámerne nízko: tmavé miesta v tieňovaní nie
    # sú súvislé steny, ale hustá sieť žliabkov a mikrotieňov – a práve tá
    # jemná štruktúra je to, čo z hillshade chceme (viď hlavičku súboru).
    ap.add_argument("--min-area", type=float, default=7.0,
                    help="najmenšia skalná plocha v m²")
    # Plné plochy: bez dier a bez druhého pásma. Viď `contour_blocks`
    # a `filter_stream` – dokopy z toho je „jedna skala = jedna sivá plocha".
    ap.add_argument("--plne", type=int, default=1,
                    help="1 = jedno pásmo a jedna trieda (žiadna plocha "
                         "vnútri inej), 0 = pásma steep/cliff ako predtým")
    # Zapĺňanie dier bolo kedysi súčasťou `--plne` a bola to chyba: diery sú
    # medzery medzi vláknami siete žliabkov, čiže presne tá štruktúra, pre
    # ktorú sa skaly z tieňovania robia. Zapnuté z nich spravilo súvislé
    # plochy, v ktorých nebolo vidieť nič.
    ap.add_argument("--zapln-diery", type=int, default=0,
                    help="1 = zaplniť diery (súvislé plochy namiesto siete)")
    ap.add_argument("--zlepit", type=int, default=0,
                    help="1 = zlepiť plochy rozseknuté hranicou bloku "
                         "(ST_Union, potrebuje spatialite)")
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
    fetcher = Fetcher(args.url, args.cache_dir, jobs=args.jobs, ua=args.ua,
                      log_every=args.log_every)

    if str(args.zoom).strip().lower() == "auto":
        z = probe_zoom(fetcher, args.bbox, args.zoom_max, args.zoom_min,
                       args.max_tiles, args.budget_min * 60)
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

    # Pracovný priečinok leží v cache dlaždíc, nie vedľa výstupu. To je celý
    # trik pokračovania: cache sa ukladá aj vtedy, keď job spadne alebo ho
    # zabije timeout (`if: always()` v shading-rocks.yml), takže hotové pásy
    # rastra, obrysy aj vyfiltrované polygóny prežijú a ďalší beh ich len
    # prevezme. Podpis v mene: iné prahy = iný medzivýsledok, takže sa dva
    # rôzne behy nemôžu pomiešať.
    podpis = (f"z{z}-d{args.dark}-a{args.dark_always}-r{args.rel}"
              f"-c{args.cliff}-l{args.local:g}-f{args.fill:g}-b{int(args.blur)}"
              f"-m{args.min_area:g}-h{args.min_hole:g}"
              # Plné plochy menia PÁSMA, teda aj obsah blokov – bez toho
              # by po prepnutí nadviazal na obrysy z iného nastavenia.
              f"{'-plne' if args.plne else ''}"
              f"-zd{int(bool(args.zapln_diery))}")
    tmp = os.path.join(args.cache_dir, "_rozrobene", podpis)
    # `fresh` znamená „nenadväzuj na rozrobené z PREDOŠLÉHO behu". To je vec
    # fáz, ktoré rozrobené vyrábajú. Fáza `spojit` ho len číta – a beží ako
    # samostatný job PO fáze `vektor`, takže by nezmazala starú prácu, ale tú,
    # ktorú pred pár minútami vyrobil job vedľa. Skončilo by to na „Chýbajú
    # obrysy blokov" a vyzeralo by to ako stratená cache. (Presne to sa aj
    # stalo, keď build začal posielať `fresh=1` do všetkých troch fáz.)
    if args.fresh and args.phase != "spojit":
        shutil.rmtree(tmp, ignore_errors=True)
    elif args.fresh:
        print("  `fresh` sa vo fáze `spojit` ignoruje: zlepuje sa to, čo "
              "vyrobila fáza `vektor` v tomto behu.", flush=True)
    os.makedirs(tmp, exist_ok=True)
    if os.listdir(tmp):
        print(f"── Rozrobené z predošlého behu ──────────────────────")
        print(f"  {tmp}")
        for f in sorted(os.listdir(tmp)):
            print(f"    {f}  {dir_mb(os.path.join(tmp, f)):.0f} MB")
        print("  hotové fázy sa preskočia; `options: fresh=true` to zahodí")
        print("─────────────────────────────────────────────────────", flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)

    t_all = time.time()
    # Prah je 0,5, nie 1: dáta sú celé čísla, takže izolínia v polovici kroku
    # ide presne stredom medzi „nie je tmavé" a „je tmavé" a obrys vyjde
    # sub-pixelový.
    cliff_level = 0.5 + args.cliff
    merc = math.cos(math.radians(lat_mid)) ** 2
    dl_s = sc_s = vec_s = 0.0

    if args.phase == "spojit":
        # Zlepovanie číta obrysy blokov, nie obrázky – sťahovať ich znova by
        # bolo len zbytočné klopanie na cudzí server. Čísla o dlaždiciach idú
        # z toho, čo si odložila fáza sťahovania.
        print("── Dlaždice: preskočené (fáza spojenia) ─────────────", flush=True)
        dl = nacitaj_stiahnute(args.cache_dir, n_tiles)
    else:
        print("── Sťahovanie dlaždíc ───────────────────────────────", flush=True)
        dl_s = fetcher.fetch_all(z, x0, y0, x1, y1)
        if fetcher.n_ok + fetcher.n_cached == 0:
            print("::error::Nestiahla sa ani jedna dlaždica – bez dát sa nedá "
                  "nič vektorizovať.", file=sys.stderr)
            return 1
        dl = zapis_stiahnute(args.cache_dir, fetcher, n_tiles)

        # Vybraný zoom von, nech ho ďalší job nemusí hádať znova. Pri `auto`
        # ho určuje sonda a rozpočet – deterministické to je, ale spoliehať sa
        # na to, že dva behy dopadnú rovnako, je zbytočné riziko.
        if args.zoom_out:
            with open(args.zoom_out, "a") as f:
                f.write(f"zoom={z}\n")

        if args.phase == "stiahnut":
            print("── Hotovo (len sťahovanie) ──────────────────────────")
            print(f"  zoom            z{z}")
            print(f"  dlaždice        {n_tiles} ({fetcher.bytes / 1048576:.0f} MB "
                  f"stiahnutých, {fetcher.n_cached} z cache)")
            print(f"  čas             {hms(dl_s)}")
            print("  Vektorizácia je vlastný job – dlaždice si vezme z cache.")
            print("─────────────────────────────────────────────────────", flush=True)
            return 0

    if args.phase != "spojit":
        print("── Raster tmavosti ──────────────────────────────────", flush=True)
        preview_rows = [] if args.preview else None
        tifs, sc_s = build_score_raster(fetcher, z, x0, y0, x1, y1, args, tmp,
                                        preview_rows)

        print("── Obrysy po blokoch ────────────────────────────────", flush=True)
        t_vec = time.time()
        try:
            n_blokov = obrysy(tifs, args, tmp, cliff_level)
        except RuntimeError as exc:
            # Napr. kontrola jednotiek. Hláška je zrozumiteľná, traceback nie.
            print(f"::error::{exc}", file=sys.stderr)
            return 2
        except TimeoutError:
            # Odhad bol vedľa. Povedať to s číslom a s tým, čo zmeniť, je
            # užitočnejšie než traceback – a stiahnuté dlaždice ostávajú
            # v cache, takže ďalší beh na nižšom zoome nesťahuje nič.
            print(f"::error::Obrysy sa nestihli do {args.budget_min:g} min. "
                  f"Skús nižší zoom (`zoom: {z - 1}`), menší výrez, alebo "
                  f"zdvihni rozpočet (`options: budget_min=…`). Dlaždice sú "
                  f"v cache, takže ďalší beh ich neťahá znova.",
                  file=sys.stderr)
            return 2
        vec_s = time.time() - t_vec

        # Náhľad a histogram vznikajú pri rastri tmavosti, nie z hotových
        # polygónov – patria teda sem a nie do jobu, ktorý bloky zlepuje.
        if args.preview:
            save_preview(preview_rows, args.preview)
        hist = histogram(preview_rows) if preview_rows else ""
        if hist:
            print("── Rozloženie odtieňov šedej ────────────────────────")
            print(hist)
            print("─────────────────────────────────────────────────────",
                  flush=True)

        if args.phase == "vektor":
            print("── Hotovo (len obrysy) ──────────────────────────────")
            print(f"  bloky           {n_blokov}")
            print(f"  čas             tmavosť {hms(sc_s)}, obrysy {hms(vec_s)}")
            print("  Zlepenie, filter a vyhladenie sú vlastný job – "
                  "rozrobené si vezme z cache.")
            print("─────────────────────────────────────────────────────",
                  flush=True)
            return 0

    print("── Spojenie blokov a filter ─────────────────────────", flush=True)
    t_sp = time.time()
    try:
        st = spoj(args, tmp, args.out, cliff_level, merc,
                  uzemie_km2=bbox_km2(args.bbox))
    except RuntimeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    sp_s = time.time() - t_sp
    if not st.get("n"):
        print("::warning::Nenašla sa ani jedna skalná plocha – prahy sú "
              "pravdepodobne prísne. Pozri náhľad a histogram z fázy obrysov.")
        empty_rock(args.out)

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
          f"obrysy {hms(vec_s)}, spojenie {hms(sp_s)}, "
          f"spolu {hms(time.time() - t_all)}")
    if args.phase == "spojit":
        print("  (čas sťahovania a obrysov je z ich vlastných jobov)")
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
                ("zoom", z), ("tiles", dl["tiles"]),
                ("tiles_missing", dl["tiles_missing"]),
                ("tiles_failed", dl["tiles_failed"]),
                ("mb_downloaded", dl["mb_downloaded"]),
                ("ua", args.ua), ("ua_profiles", dl["ua_profiles"]),
                ("cells", cells), ("px_m", f"{ground_res(z, lat_mid):.2f}"),
                ("dark", args.dark), ("dark_always", args.dark_always),
                ("local_m", f"{args.local:g}"), ("local_px", args.local_px),
                ("rel", args.rel),
                ("cliff_delta", args.cliff), ("blur", args.blur),
                ("fill_m", f"{args.fill:g}"),
                ("out_mb", f"{out_mb:.1f}"), ("mb_per_km2", f"{per_km2:.1f}"),
                ("min_area_m2", f"{args.min_area:g}"),
                ("plne", int(bool(args.plne))),
                ("zapln_diery", int(bool(args.zapln_diery))),
                ("zlepene", int(bool(args.zlepit))),
                ("min_hole_m2", f"{args.min_hole:g}"),
                ("simplify_m", f"{args.simplify:.2f}"), ("smooth", args.smooth),
                ("seconds", int(time.time() - t_all)),
            ]:
                f.write(f"{k}={v}\n")

    # Až TERAZ preč: rozrobené má zmysel držať len dovtedy, kým beh nedobehol.
    # Inak by cache dlaždíc rástla o medzivýsledky každého behu.
    shutil.rmtree(tmp, ignore_errors=True)
    print("Rozrobené zmazané – beh dobehol celý.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

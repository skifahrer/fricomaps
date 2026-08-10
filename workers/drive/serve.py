#!/usr/bin/env python3
"""
Google Drive ako slušný HTTP server pre GDAL.

PREČO TO EXISTUJE: DMR 5.0 v ETRS89 leží na Drive ako holý BigTIFF. Range
requesty naň fungujú (overené čítaním na offsete 20 GB aj 145 GB), takže by
GDAL cez `/vsicurl/` mal vedieť čítať jednotlivé dlaždice a nič nesťahovať.
Nevie – a je za tým jediná rozbitá hlavička:

    $ curl -sI 'https://drive.usercontent.google.com/download?id=…&confirm=t'
    HTTP/2 200
    content-length: 0            ← Drive na HEAD vracia nulu

GDAL si veľkosť súboru zisťuje práve cez HEAD, dostane nulu a súbor odmietne
(`GetFileSize(…)=0`, potom „not recognized as a supported file format").
S `CPL_VSIL_CURL_USE_HEAD=NO` sa síce otvorí, ale veľkosť si domyslí zle
(~16 MB) a všetko nad ňou padá na „Request at offset …, after end of file".
Odpoveď na Range GET je pritom po celý čas správna:

    content-range: bytes 0-16383/156108150990

Tento server teda opraví tú jednu hlavičku – veľkosť zistí raz cez
`Range: bytes=0-0` z `Content-Range` – a ďalej už len prepája Range requesty
na Drive. GDAL vidí obyčajný, dobre sa správajúci HTTP server na localhoste
a `/vsicurl/http://127.0.0.1:8787/dmr5.tif` funguje so všetkým, čo GDAL vie:
`gdal_translate -projwin`, `gdalwarp`, dlaždice naostro.

DVE CESTY K TÝM ISTÝM DÁTAM, a rozhoduje o nich prihlásenie:

    prihlásený   www.googleapis.com/drive/v3/files/<id>?alt=media
                 + `Authorization: Bearer …`   ← vlastník, vyšší limit
    verejný      drive.usercontent.google.com/download?id=<id>&confirm=t
                 bez hlavičiek                 ← denný limit na súbor

Verejný odkaz má denný strop sťahovania, ktorý zdieľajú VŠETCI, kto naň
siahnu, a keď sa vyčerpá, Drive prestane dávať dáta (beh 31315890474). Preto
sa prednostne používa prihlásenie vlastníka – token drží
`workers/drive/auth.py` a sem sa podáva ako `creds`. Bez neho sa nič nemení
a číta sa ako doteraz, len sa to výslovne vypíše.

DVE VECI ROZHODUJÚ O RÝCHLOSTI, a obe sú v tomto súbore:

  1. SPOJENIA SA MUSIA RECYKLOVAŤ. Prvá verzia otvárala na každý request nové
     HTTPS spojenie a celý TLS handshake. Výrez 6×6 km pri 1 m (2 209 dlaždíc)
     tak trval 103 s pri 109 MB dát – čiže ~1 MB/s, kým samotné pásmo dá
     75 MB/s. Nie je to šírka pásma, je to latencia × počet spojení. Pool
     nižšie drží spojenia otvorené (a to pre každý host zvlášť, lebo Drive
     vie odpovedať presmerovaním na inú adresu) a číta cez ne stále dokola.
  2. VIACNÁSOBNÝ RANGE SA MUSÍ VEDIEŤ. GDAL pri `GDAL_HTTP_MULTIRANGE=YES`
     pýta viac úsekov jednou hlavičkou (`Range: bytes=a-b,c-d,…`), lebo
     dlaždice, ktoré potrebuje, ležia roztrúsene. Server, ktorý pošle len
     prvý úsek, by GDALu ticho podstrčil zlé dáta. Preto sa tu odpovedá
     riadnym `multipart/byteranges` a jednotlivé úseky sa ťahajú súbežne –
     každý ako samostatný jednoduchý Range request na Drive.

PREČO NIE STIAHNUŤ CELÝ SÚBOR: `dmr5_etrs89.tif` má 145 GiB a runner má po
vyčistení voľných ~60 GB. Pyramídy (43 GiB) by sa ešte vošli, plný 1 m model
nie. Čítať výrez na diaľku je pritom lacnejšie: pohorie na 1 m stojí rádovo
stovky MB až jednotky GB, nie 145 GB.

Použitie:
    python3 workers/drive/serve.py --file=dmr5.tif=<Drive file id> --port=8787
    python3 workers/drive/serve.py --file=dmr5.tif=<id> --auth --print-url
"""
import argparse
import http.client
import http.server
import json
import os
import queue
import socket
import socketserver
import ssl
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (compatible; fricomaps-dem/1.0)"
# Verejná cesta: `download?id=…&confirm=t` je tá, ktorá už obišla stránku
# „Google Drive can't scan this file".
PUBLIC_HOST = "drive.usercontent.google.com"
# Prihlásená cesta: riadne Drive API. Range na `alt=media` funguje rovnako.
API_HOST = "www.googleapis.com"
# Koľko úsekov viacnásobného Range sa ťahá naraz – na CELÝ PROCES, nie na
# jednu požiadavku. Bolo to na požiadavku a to nie je strop, ale násobenie:
# pri `slope-chunks.py --jobs 6` bežalo šesť gdalwarpov naraz, každý si pýtal
# viacnásobný Range a z „12 vlákien" bolo 72 – a rástlo to s `--jobs`, teda
# presne s tým, čo sa ladí kvôli rýchlosti. Vlákna navyše nestoja len Drive:
# accept slučka shimu je obyčajná pythonovská slučka a keď sa k nej pri
# stovkách vlákien nedostane GIL, prestane preberať spojenia (viď frontu
# v `Server` nižšie).
#
# 24 je namerané (48 náhodných výrezov po 400 kB: 1 vlákno 1 143 ms/req,
# 8 vlákien 147 ms/req, 24 vlákien 68 ms/req). Nad ~32 začne Drive odpovedať
# 403 „rate limit" a exponenciálne čakanie zožerie viac, než tá súbežnosť
# získa – to je tá hranica, po ktorú sa toto číslo smie dvíhať.
FETCH_WORKERS = 24

_FETCH = None
_FETCH_LOCK = threading.Lock()


def fetch_pool():
    """Jeden zdieľaný bazén vlákien na sťahovanie úsekov.

    Zdieľaný preto, aby `FETCH_WORKERS` naozaj niečo ohraničoval, a jeden
    preto, aby sa vlákna nevyrábali znova pri každej požiadavke – tých sú za
    beh desaťtisíce.
    """
    global _FETCH
    with _FETCH_LOCK:
        if _FETCH is None:
            _FETCH = ThreadPoolExecutor(max_workers=FETCH_WORKERS,
                                        thread_name_prefix="drive-fetch")
    return _FETCH


def quota_hint(authed):
    """ČO ROBÍ DRIVE, KEĎ NECHCE DAŤ DÁTA – a čo s tým.

    Na verejnej ceste nevráti chybový kód, ale **HTTP 200 a HTML stránku**.
    V behu 31315890474 to bolo 2 009 B namiesto 32 768 – prekročený denný
    limit sťahovania súboru. Kým sa to bralo ako úspech, `gdalinfo` čakal na
    odpoveď, ktorá nikdy neprišla, a job visel 2 h 16 min a minul dva runnery.
    Prihlásená cesta je v tomto slušná: pošle 403 a JSON s dôvodom.
    """
    if authed:
        return ("prekročený limit sťahovania z Google Drive aj pre prihlásený "
                "účet. Over, či ten súbor prihlásený účet naozaj VLASTNÍ "
                "(`python3 workers/drive/dmr5.py --auth-check`) – na cudzí "
                "zdieľaný súbor platí ten istý denný strop ako na verejný "
                "odkaz. Ak vlastní, počkaj pár hodín; beh medzitým prejde na "
                "hrubší model (sonny), keď je zapnutý ugkk_fallback.")
    return ("prekročený limit sťahovania z Google Drive (verejný odkaz má "
            "denný strop na súbor a ten zdieľajú všetci, kto naň siahnu). "
            "Prihlás beh ako vlastníka dát – secret GDRIVE_CREDENTIALS, "
            "rozpis vo `workers/drive/auth.py --login` – vlastník má strop "
            "oveľa vyšší. Inak počkaj pár hodín, alebo nahraj kópiu modelu do "
            "iného priečinka a prepíš FOLDER_ID vo workers/drive/dmr5.py. Beh "
            "medzitým prejde na hrubší model (sonny), keď je zapnutý "
            "ugkk_fallback.")


def drive_refusal(body, authed=False):
    """Vráti popis odmietnutia, keď je telo HTML stránka namiesto dát."""
    head = body[:2048].lower()
    if b"<html" not in head and b"<!doctype" not in head:
        return None
    if b"quota" in head or b"too many" in head or b"limit" in head:
        return quota_hint(authed)
    return f"Drive vrátil HTML stránku ({len(body)} B), nie dáta"


def api_error(body):
    """`reason` z chybovej JSON odpovede Drive API, alebo None."""
    if not body[:64].lstrip().startswith(b"{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        for e in err.get("errors") or []:
            if e.get("reason"):
                return e["reason"]
        return str(err.get("status") or err.get("message") or "chyba bez dôvodu")
    if isinstance(err, str):
        return err
    return None


def hard_reason(reason, authed):
    """Popis pre dôvody, pri ktorých je opakovanie strata času.

    Limit ani chýbajúce právo sa o dvadsať sekúnd neposunú, tak sa naň nečaká
    šesťkrát pri každom bloku – prvý taký nález zastaví celý `Pool`. Naopak
    `rateLimitExceeded` a 5xx sú prechodné a tie sa opakovať MAJÚ.
    """
    if reason in ("downloadQuotaExceeded", "quotaExceeded", "dailyLimitExceeded",
                  "userRateLimitExceededUnreg"):
        return quota_hint(authed)
    if reason == "notFound":
        return ("Drive ten súbor nevidí (notFound). Prihlásený beh to hlási "
                "vtedy, keď na súbor nevidí použitý účet – over "
                "`python3 workers/drive/dmr5.py --auth-check`. Inak sa súbor "
                "z priečinka `FOLDER_ID` (workers/drive/dmr5.py) presunul "
                "alebo prestalo platiť zdieľanie.")
    if reason in ("forbidden", "insufficientFilePermissions", "cannotDownloadFile",
                  "insufficientPermissions", "appNotAuthorizedToFile",
                  "fileNotDownloadable", "cannotDownloadAbusiveFile"):
        return (f"Drive odmietol prístup k súboru ({reason}). Prihlás sa účtom, "
                "ktorý dáta vlastní (`python3 workers/drive/auth.py --login`), "
                "alebo súbor nasdieľaj pre kohokoľvek s odkazom.")
    return None


# ---------- spojenia ----------

_CTX = None
_CTX_LOCK = threading.Lock()


def _ssl_ctx():
    """Jeden overovací kontext pre celý proces (aj s vlastným CA z prostredia)."""
    global _CTX
    with _CTX_LOCK:
        if _CTX is None:
            ctx = ssl.create_default_context()
            ca = os.environ.get("CURL_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
            if ca and os.path.exists(ca):
                ctx.load_verify_locations(ca)
            _CTX = ctx
    return _CTX


def connect(host, timeout=180):
    """HTTPS spojenie na `host`, aj cez firemné proxy (CONNECT tunel).

    `http.client` je tu naschvál namiesto `requests`: runner nemá nič
    doinštalované a jedna závislosť navyše v pipeline, ktorá beží hodiny,
    stojí za to ušetriť. Používa to aj `drive-auth.py` – proxy a CA sa nemajú
    riešiť na dvoch miestach.
    """
    proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    if proxy:
        p = urllib.parse.urlsplit(proxy)
        conn = http.client.HTTPSConnection(p.hostname, p.port or 8080,
                                           timeout=timeout, context=_ssl_ctx())
        conn.set_tunnel(host, 443)
        return conn
    return http.client.HTTPSConnection(host, 443, timeout=timeout,
                                       context=_ssl_ctx())


class Pool:
    """Znovupoužiteľné HTTPS spojenia na Drive, po hostoch.

    Po hostoch preto, že ciest k dátam je viac: kanonická adresa (API alebo
    verejný odkaz) a adresa, na ktorú Drive vie presmerovať. Spojenie na jeden
    host sa nesmie použiť na druhý.
    """

    def __init__(self, creds=None, size=32):
        self.creds = creds
        self.size = size
        self.free = {}                  # host → LifoQueue voľných spojení
        self.lock = threading.Lock()
        # Neprázdne = Drive dáta odmietol dať a nemá zmysel sa pýtať ďalej.
        self.refused = None
        # file id → (host, cesta) z presmerovania. Podpísaná adresa platí
        # krátko, ale znova ju vypýtať znamená ďalší request na každý blok –
        # a request je tu tá drahá vec. Preto sa drží a pri prvom zlyhaní
        # zahodí.
        self.redirect = {}
        # Súbory, pri ktorých Drive žiada potvrdenie, že sa nedali preveriť
        # antivírusom. Verejná cesta to má v `confirm=t`, API v parametri
        # `acknowledgeAbuse` – ale ten smie poslať len vlastník, tak sa
        # nepridáva dopredu, len keď si o to Drive povie.
        self.ack = set()

    # ---- spojenia ----

    def _pipe(self, host):
        with self.lock:
            q = self.free.get(host)
            if q is None:
                q = self.free[host] = queue.LifoQueue()
        return q

    def _take(self, host):
        try:
            return self._pipe(host).get_nowait()
        except queue.Empty:
            return connect(host)

    def _put(self, host, conn):
        q = self._pipe(host)
        if q.qsize() < self.size:
            q.put(conn)
        else:
            conn.close()

    # ---- kam sa ide po dáta ----

    def target(self, file_id):
        """Kanonická adresa súboru: s prihlásením API, bez neho verejný odkaz."""
        if self.creds is not None:
            path = (f"/drive/v3/files/{urllib.parse.quote(file_id)}"
                    "?alt=media&supportsAllDrives=true")
            if file_id in self.ack:
                path += "&acknowledgeAbuse=true"
            return API_HOST, path
        return PUBLIC_HOST, (f"/download?id={urllib.parse.quote(file_id)}"
                             "&export=download&confirm=t")

    def _where(self, file_id):
        """(host, cesta, či je to zapamätané presmerovanie)."""
        with self.lock:
            hit = self.redirect.get(file_id)
        if hit:
            return hit[0], hit[1], True
        host, path = self.target(file_id)
        return host, path, False

    def _remember(self, file_id, location):
        """Ulož cieľ presmerovania (absolútny aj relatívny)."""
        parts = urllib.parse.urlsplit(location)
        host = parts.netloc or self.target(file_id)[0]
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        with self.lock:
            self.redirect[file_id] = (host, path)

    def _forget(self, file_id):
        with self.lock:
            self.redirect.pop(file_id, None)

    # ---- čítanie ----

    def get(self, file_id, rng, tries=6, want=None):
        """GET s hlavičkou Range; vráti (status, headers, telo ako bajty).

        `want` je koľko bajtov sa pýtalo. Bez neho sa dá overiť len stavový
        kód – a ten na verejnej ceste pri odmietnutí klame (viď
        `drive_refusal`).
        """
        last = None
        attempt = 0
        # Presmerovanie a výmena vypršaného tokenu nie sú zlyhania, na ktoré
        # sa čaká – nech nezožerú pokusy určené na prechodné chyby siete.
        extra, EXTRA_MAX = 0, 6
        renewed = False
        while attempt < tries:
            # Limit sťahovania nepustí ani o dvadsať sekúnd neskôr, tak ho
            # stačí naraziť raz za celý beh a nie raz za blok.
            if self.refused:
                raise RuntimeError(self.refused)
            host, path, cached = self._where(file_id)
            headers = {"Range": rng, "User-Agent": UA,
                       "Accept-Encoding": "identity"}
            token = None
            # Token ide LEN na kanonický API host. Adresa z presmerovania je
            # podpísaná v query a `Authorization` k nej nepatrí – rovnako to
            # robí `curl -L`, ktorý hlavičku pri zmene hosta zahodí.
            if self.creds is not None and host == API_HOST:
                token = self.creds.token()
                headers["Authorization"] = "Bearer " + token
            conn = self._take(host)
            try:
                conn.request("GET", path, headers=headers)
                resp = conn.getresponse()
                body = resp.read()
                status, hdrs = resp.status, resp.headers
                # 206 = rozsah dostal. 200 na Range znamená, že ho Drive
                # IGNOROVAL – buď je súbor kratší, než sa pýtalo, alebo je to
                # odmietnutie. Rozhodne dĺžka, nie kód.
                if status == 206 or (status == 200
                                     and (want is None or len(body) == want)):
                    self._put(host, conn)
                    return status, hdrs, body
                conn.close()
            except Exception as exc:                # noqa: BLE001
                last = exc
                try:
                    conn.close()
                except Exception:                   # noqa: BLE001
                    pass
                attempt += 1
                time.sleep(min(1.5 ** attempt, 20))
                continue

            # Zapamätané presmerovanie mohlo vypršať – ďalší pokus nech ide
            # znova na kanonickú adresu.
            if cached:
                self._forget(file_id)

            if status in (301, 302, 303, 307, 308) and extra < EXTRA_MAX:
                loc = hdrs.get("Location")
                if loc:
                    self._remember(file_id, loc)
                    extra += 1
                    continue

            if status == 401 and token is not None:
                # Access token vypršal alebo bol odvolaný. Vymeniť ho je
                # okamžitá vec, nie čakanie – a `renew` sa postará, aby ho
                # nešlo obnovovať raz za každé vlákno. Keď odmietne aj čerstvý
                # token, opakovanie už nemá čo zmeniť: je to zlé prihlásenie,
                # nie prechodná chyba, a čakať naň šesťkrát pri každom bloku
                # by len naťahovalo pád.
                if not renewed:
                    self.creds.renew(token)
                    renewed = True
                    continue
                why = api_error(body)
                hard = (f"Drive odmietol access token aj po obnove (HTTP 401"
                        + (f", {why}" if why else "") + "). Over "
                        "`python3 workers/drive/auth.py --check`: účet mohol "
                        "appke odvolať prístup, alebo secret "
                        "GDRIVE_CREDENTIALS patrí inému projektu, než v ktorom "
                        "bol token vyrobený.")
                self.refused = hard
                raise RuntimeError(hard)

            reason = api_error(body)
            if (reason == "cannotDownloadAbusiveFile" and self.creds is not None
                    and file_id not in self.ack and extra < EXTRA_MAX):
                # Drive veľký súbor nepreveril antivírusom a bez potvrdenia ho
                # nepustí. Pridá sa až teraz, keď si o to povedal.
                self.ack.add(file_id)
                extra += 1
                continue

            if reason:
                hard = hard_reason(reason, self.creds is not None)
                if hard:
                    self.refused = hard
                    raise RuntimeError(hard)
                last = f"HTTP {status} ({reason})"
            elif status == 200:
                why = drive_refusal(body, self.creds is not None)
                if why:
                    # Toto sa opakovaním nespraví – zapamätaj a padni hneď.
                    self.refused = why
                    raise RuntimeError(why)
                last = (f"HTTP 200 a {len(body)} B namiesto {want} – "
                        "Drive rozsah ignoroval")
            else:
                last = f"HTTP {status}"
            attempt += 1
            # Drive občas vráti 403 „rate limit"; exponenciálne čakanie ho
            # spoľahlivo prejde.
            time.sleep(min(1.5 ** attempt, 20))
        raise RuntimeError(f"Drive neodpovedal ani na {tries}. pokus: {last}")


def parse_ranges(header, size):
    """`bytes=a-b,c-,-d` → [(start, end), …], konce vrátane, orezané na súbor."""
    out = []
    for spec in header.split("=", 1)[1].split(","):
        spec = spec.strip()
        if not spec:
            continue
        a, _, b = spec.partition("-")
        if a == "":                            # „-500" = posledných 500 bajtov
            start, end = max(0, size - int(b)), size - 1
        else:
            start = int(a)
            end = int(b) if b else size - 1
        end = min(end, size - 1)
        if start <= end:
            out.append((start, end))
    return out


def make_handler(pool, files, stats):
    """`files` je meno v URL → (Drive file id, veľkosť).

    Menami sa to podáva preto, že GDAL si sidecary hľadá podľa mena vedľa
    hlavného súboru: keď je pod `/dmr5_etrs89.tif` model a pod
    `/dmr5_etrs89.tif.ovr` pyramídy, GDAL si ich nájde sám a pri hrubšom
    cieli číta z pyramíd namiesto zo 145 GiB rastra. Preto tu smie byť viac
    súborov naraz a preto sa NEnastavuje `GDAL_DISABLE_READDIR_ON_OPEN`.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass                  # inak by každá dlaždica bola riadok v logu

        def _entry(self):
            name = urllib.parse.unquote(self.path.lstrip("/"))
            return files.get(name)

        def do_HEAD(self):
            entry = self._entry()
            if not entry:
                # 404 nie je chyba: takto sa GDAL pýta, či sidecar existuje.
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            # Presne tá hlavička, ktorú Drive nevie: skutočná dĺžka.
            self.send_response(200)
            self.send_header("Content-Length", str(entry[1]))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()

        def _fetch(self, file_id, start, end):
            want = end - start + 1
            status, _, body = pool.get(file_id, f"bytes={start}-{end}", want=want)
            if len(body) != want:
                raise RuntimeError(
                    f"Drive vrátil {len(body)} B namiesto {want} (HTTP {status})")
            with stats["lock"]:
                stats["requests"] += 1
                stats["bytes"] += len(body)
            return body

        def send_response(self, *a, **kw):
            self.responded = True
            super().send_response(*a, **kw)

        def do_GET(self):
            self.responded = False
            entry = self._entry()
            if not entry:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            file_id, size = entry
            hdr = self.headers.get("Range")
            asked = bool(hdr and hdr.startswith("bytes="))
            ranges = parse_ranges(hdr, size) if asked else []
            # Rozsah, z ktorého po orezaní na súbor nič neostalo, NIE JE
            # „pošli celý súbor" – to je 145 GB namiesto 32 kB a presne to,
            # čo pravidlo 7 zakazuje. Podľa RFC 9110 je to 416.
            if asked and not ranges:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            whole = not ranges
            if whole:
                ranges = [(0, size - 1)]

            try:
                if whole:
                    self._send_stream(file_id, size, ranges[0])
                elif len(ranges) == 1:
                    self._send_single(file_id, size, *ranges[0])
                else:
                    self._send_multipart(file_id, size, ranges)
            except (BrokenPipeError, ConnectionResetError):
                pass              # GDAL zavrel spojenie – bežné a v poriadku
            except Exception as exc:            # noqa: BLE001
                # Zlyhania sa aj RÁTAJÚ, nielen vypisujú. Volajúci potom vie
                # povedať, či shim o požiadavke vôbec vedel: keď GDAL hlási
                # chybu a tu je nula, spojenie sa k shimu nedostalo (fronta,
                # sieť) a hľadať sa má inde než na Drive.
                with stats["lock"]:
                    stats["failed"] += 1
                print(f"  drive-serve: {self.path} {hdr} zlyhalo: {exc}",
                      file=sys.stderr, flush=True)
                # ODPOVEDAJ AJ NA CHYBU. Kým sa sem chodilo len vypísať,
                # `_send_single` spadol v `_fetch` EŠTE PRED hlavičkami, takže
                # GDAL čakal na odpoveď, ktorá nikdy neprišla – beh
                # 31315890474 visel 2 h 16 min na `gdalinfo`, minul dva
                # runnery a nevyrobil nič. Tiché nič je horšie než pád: 502
                # GDAL vráti ako chybu a job spadne v sekundách.
                if not self.responded:
                    try:
                        msg = str(exc).encode("utf-8")
                        self.send_response(502)
                        self.send_header("Content-Length", str(len(msg)))
                        self.send_header("Content-Type",
                                         "text/plain; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(msg)
                    except Exception:           # noqa: BLE001
                        pass
                else:
                    # Hlavičky sú vonku, stav sa už zmeniť nedá – aspoň nenechaj
                    # klienta čakať na zvyšok tela.
                    self.close_connection = True

        def _send_stream(self, file_id, size, rng):
            """Celý súbor po kúskoch – GDAL to nerobí, ale `curl` áno."""
            start, end = rng
            self.send_response(200)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            step = 32 << 20
            for a in range(start, end + 1, step):
                self.wfile.write(self._fetch(file_id, a, min(a + step - 1, end)))

        def _send_single(self, file_id, size, start, end):
            body = self._fetch(file_id, start, end)
            self.send_response(206)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(body)

        def _send_multipart(self, file_id, size, ranges):
            bodies = list(fetch_pool().map(
                lambda r: self._fetch(file_id, *r), ranges))
            boundary = "fricomaps_%d" % time.time_ns()
            parts, total = [], 0
            for (start, end), body in zip(ranges, bodies):
                head = (f"\r\n--{boundary}\r\n"
                        "Content-Type: application/octet-stream\r\n"
                        f"Content-Range: bytes {start}-{end}/{size}\r\n\r\n"
                        ).encode("ascii")
                parts.append((head, body))
                total += len(head) + len(body)
            tail = f"\r\n--{boundary}--\r\n".encode("ascii")
            total += len(tail)
            self.send_response(206)
            self.send_header("Content-Type",
                             f"multipart/byteranges; boundary={boundary}")
            self.send_header("Content-Length", str(total))
            self.end_headers()
            for head, body in parts:
                self.wfile.write(head)
                self.wfile.write(body)
            self.wfile.write(tail)

    return Handler


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    # FRONTA ČAKAJÚCICH SPOJENÍ. `socketserver` má predvolene 5 a to je pri
    # šiestich súbežných gdalwarpoch málo. Keď fronta pretečie, jadro SYN
    # jednoducho ZAHODÍ – ticho, bez chyby, na oboch stranách: shim sa
    # o takej požiadavke nikdy nedozvie a nemá čo zapísať do logu. Klient ju
    # potom retransmituje s exponenciálnym odstupom (1, 2, 4, 8 … s) a
    # `connect` sa vzdá až po vyše dvoch minútach; GDAL to vypíše ako
    # `response_code=0`, čo vyzerá ako chyba Drive, hoci sa požiadavka
    # k Drive ani neblížila.
    #
    # Presne to zhodilo beh 31338803278 dve časti pred koncom: 45 zo 47
    # spočítaných, potom 3,5 minúty ticha a pád. Preto sa berie to, čo
    # dovolí jadro (`SOMAXCONN`) – fronta nič nestojí, kým je prázdna.
    request_queue_size = socket.SOMAXCONN


def probe_size(pool, file_id):
    """Veľkosť z `Content-Range` jednobajtového GETu – HEAD sa nedá veriť."""
    status, headers, _ = pool.get(file_id, "bytes=0-0", want=1)
    cr = headers.get("Content-Range", "")
    if "/" not in cr:
        raise RuntimeError(
            f"Drive nevrátil Content-Range (HTTP {status}, {cr!r}). "
            + ("Vidí prihlásený účet na ten súbor? "
               "`python3 workers/drive/dmr5.py --auth-check`"
               if pool.creds is not None else
               "Je ten súbor zdieľaný pre kohokoľvek s odkazom?"))
    return int(cr.rsplit("/", 1)[1])


def serve(ids, port=8787, creds=None):
    """Spustí server na pozadí.

    `ids` je meno v URL → Drive file id, `creds` sú prihlasovacie údaje
    z `drive-auth.py` (alebo None = verejný odkaz). Vracia (základná url,
    {meno: veľkosť}, štatistiky).
    """
    pool = Pool(creds=creds)
    files, sizes = {}, {}
    for name, file_id in ids.items():
        files[name] = (file_id, probe_size(pool, file_id))
        sizes[name] = files[name][1]
    stats = {"requests": 0, "bytes": 0, "failed": 0, "lock": threading.Lock()}
    httpd = Server(("127.0.0.1", port), make_handler(pool, files, stats))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}", sizes, stats


def gdal_env(extra=None):
    """Prostredie, v ktorom GDAL cez tento shim číta rozumne.

    `no_proxy` je tu to podstatné: bez neho by GDAL posielal požiadavky na
    127.0.0.1 cez proxy zo `https_proxy` a nedostal by sa nikam.

    `GDAL_DISABLE_READDIR_ON_OPEN` sa tu ZÁMERNE nenastavuje: skryl by
    `.ovr` vedľa `.tif`, a práve tie pyramídy robia hrubšie výrezy lacnými.
    Shim na neexistujúci sidecar odpovie 404, čo je presne to, čo GDAL čaká.

    `GDAL_HTTP_MAX_RETRY` a krátky `CONNECTTIMEOUT` sú tu preto, že jedno
    stratené spojenie z desaťtisícov nesmie byť koncom hodinovej práce. GDAL
    predvolene neopakuje NIČ a na spojenie čaká, kým sa nevzdá jadro – teda
    vyše dvoch minút, ak sa SYN stratil. Dvadsať sekúnd a päť pokusov spraví
    z takého výpadku dvadsaťsekundový zádrhel namiesto padnutého jobu (beh
    31338803278). Hodnoty sa dajú prebiť z prostredia.
    """
    env = {
        **os.environ,
        "GDAL_HTTP_MULTIRANGE": "YES",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "GDAL_HTTP_VERSION": "1.1",
        "GDAL_HTTP_MAX_RETRY": os.environ.get("GDAL_HTTP_MAX_RETRY", "5"),
        "GDAL_HTTP_RETRY_DELAY": os.environ.get("GDAL_HTTP_RETRY_DELAY", "1"),
        "GDAL_HTTP_CONNECTTIMEOUT": os.environ.get(
            "GDAL_HTTP_CONNECTTIMEOUT", "20"),
        "GDAL_NUM_THREADS": "ALL_CPUS",
        "GDAL_CACHEMAX": os.environ.get("GDAL_CACHEMAX", "2048"),
        "VSI_CACHE": "TRUE",
        "VSI_CACHE_SIZE": os.environ.get("VSI_CACHE_SIZE", str(512 * 1024 * 1024)),
        "GDAL_PAM_ENABLED": "NO",
        "no_proxy": "127.0.0.1,localhost",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    env.update(extra or {})
    return env


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", action="append", required=True, metavar="MENO=ID",
                    help="meno v URL a Drive file id; dá sa opakovať, aby "
                         "sa .tif a jeho .ovr podávali vedľa seba")
    ap.add_argument("--auth", action="store_true",
                    help="čítať prihlásený ako vlastník (GDRIVE_CREDENTIALS)")
    ap.add_argument("--port", type=int, default=8787, help="0 = vyber voľný")
    ap.add_argument("--print-url", action="store_true")
    args = ap.parse_args()

    ids = {}
    for spec in args.file:
        name, _, file_id = spec.partition("=")
        if not file_id:
            ap.error(f"--file čakalo MENO=ID, dostalo {spec!r}")
        ids[name] = file_id

    creds = None
    if args.auth:
        # Až tu, a nie na začiatku súboru: `drive-auth.py` si spojenia berie
        # odtiaľto, tak by z toho pri importe zhora bol kruh.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "drive_auth", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "drive-auth.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        try:
            creds = mod.from_env()
            if creds is None:
                print("::error::--auth je zapnuté, ale v prostredí nie sú "
                      "prihlasovacie údaje (GDRIVE_CREDENTIALS).",
                      file=sys.stderr)
                return 2
            mod.whoami(creds)
        except mod.AuthError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 2
        print(f"drive-serve: {mod.describe(creds)}", flush=True)

    base, sizes, stats = serve(ids, args.port, creds=creds)
    for name, size in sizes.items():
        print(f"drive-serve: {base}/{name}  "
              f"({size:,} B = {size / 2**30:.2f} GiB)", flush=True)
    if args.print_url:
        print(base)
    try:
        while True:
            time.sleep(60)
            with stats["lock"]:
                print(f"  drive-serve: {stats['requests']:,} požiadaviek, "
                      f"{stats['bytes'] / 1e6:,.0f} MB", flush=True)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

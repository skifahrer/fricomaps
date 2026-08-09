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

DVE VECI ROZHODUJÚ O RÝCHLOSTI, a obe sú v tomto súbore:

  1. SPOJENIA SA MUSIA RECYKLOVAŤ. Prvá verzia otvárala na každý request nové
     HTTPS spojenie a celý TLS handshake. Výrez 6×6 km pri 1 m (2 209 dlaždíc)
     tak trval 103 s pri 109 MB dát – čiže ~1 MB/s, kým samotné pásmo dá
     75 MB/s. Nie je to šírka pásma, je to latencia × počet spojení. Pool
     nižšie drží spojenia otvorené a číta cez ne stále dokola.
  2. VIACNÁSOBNÝ RANGE SA MUSÍ VEDIEŤ. GDAL pri `GDAL_HTTP_MULTIRANGE=YES`
     pýta viac úsekov jednou hlavičkou (`Range: bytes=a-b,c-d,…`), lebo
     dlaždice, ktoré potrebuje, ležia roztrúsene. Server, ktorý pošle len
     prvý úsek, by GDALu ticho podstrčil zlé dáta. Preto sa tu odpovedá
     riadnym `multipart/byteranges` a jednotlivé úseky sa ťahajú súbežne.

PREČO NIE STIAHNUŤ CELÝ SÚBOR: `dmr5_etrs89.tif` má 145 GiB a runner má po
vyčistení voľných ~60 GB. Pyramídy (43 GiB) by sa ešte vošli, plný 1 m model
nie. Čítať výrez na diaľku je pritom lacnejšie: pohorie na 1 m stojí rádovo
stovky MB až jednotky GB, nie 145 GB.

Použitie:
    python3 workers/drive-serve.py --id=<Drive file id> --port=8787
    python3 workers/drive-serve.py --id=… --name=dmr5.tif --print-url
"""
import argparse
import http.client
import http.server
import os
import queue
import socketserver
import ssl
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (compatible; fricomaps-dem/1.0)"
HOST = "drive.usercontent.google.com"
# Koľko úsekov viacnásobného Range sa ťahá naraz. Nad ~16 začne Drive
# odpovedať 403 „rate limit" a exponenciálne čakanie potom zožerie viac,
# než tá súbežnosť získa.
FETCH_WORKERS = 12


def drive_path(file_id):
    """Cesta, ktorá už obišla stránku „Google Drive can't scan this file"."""
    return f"/download?id={file_id}&export=download&confirm=t"


class Pool:
    """Znovupoužiteľné HTTPS spojenia na Drive (aj cez firemné proxy).

    `http.client` je tu naschvál namiesto `requests`: runner nemá nič
    doinštalované a jedna závislosť navyše v pipeline, ktorá beží hodiny,
    stojí za to ušetriť.
    """

    def __init__(self, host=HOST, size=32):
        self.host = host
        self.free = queue.LifoQueue()
        self.size = size
        proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
        self.proxy = urllib.parse.urlsplit(proxy) if proxy else None
        self.ctx = ssl.create_default_context()
        ca = os.environ.get("CURL_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        if ca and os.path.exists(ca):
            self.ctx.load_verify_locations(ca)

    def _new(self):
        if self.proxy:
            conn = http.client.HTTPSConnection(
                self.proxy.hostname, self.proxy.port or 8080, timeout=180)
            conn.set_tunnel(self.host, 443)
        else:
            conn = http.client.HTTPSConnection(self.host, 443, timeout=180,
                                               context=self.ctx)
        return conn

    def get(self, path, rng, tries=6):
        """GET s hlavičkou Range; vráti (status, headers, telo ako bajty)."""
        last = None
        for attempt in range(tries):
            try:
                conn = self.free.get_nowait()
            except queue.Empty:
                conn = self._new()
            try:
                conn.request("GET", path, headers={
                    "Range": rng, "User-Agent": UA, "Accept-Encoding": "identity"})
                resp = conn.getresponse()
                body = resp.read()
                if resp.status in (200, 206):
                    if self.free.qsize() < self.size:
                        self.free.put(conn)
                    else:
                        conn.close()
                    return resp.status, resp.headers, body
                last = f"HTTP {resp.status}"
                conn.close()
            except Exception as exc:            # noqa: BLE001
                last = exc
                try:
                    conn.close()
                except Exception:               # noqa: BLE001
                    pass
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
    """`files` je meno v URL → (cesta na Drive, veľkosť).

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

        def _fetch(self, path, start, end):
            status, _, body = pool.get(path, f"bytes={start}-{end}")
            want = end - start + 1
            if len(body) != want:
                raise RuntimeError(
                    f"Drive vrátil {len(body)} B namiesto {want} (HTTP {status})")
            with stats["lock"]:
                stats["requests"] += 1
                stats["bytes"] += len(body)
            return body

        def do_GET(self):
            entry = self._entry()
            if not entry:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            path, size = entry
            hdr = self.headers.get("Range")
            ranges = parse_ranges(hdr, size) if hdr and hdr.startswith("bytes=") else []
            whole = not ranges
            if whole:
                ranges = [(0, size - 1)]

            try:
                if whole:
                    self._send_stream(path, size, ranges[0])
                elif len(ranges) == 1:
                    self._send_single(path, size, *ranges[0])
                else:
                    self._send_multipart(path, size, ranges)
            except (BrokenPipeError, ConnectionResetError):
                pass              # GDAL zavrel spojenie – bežné a v poriadku
            except Exception as exc:            # noqa: BLE001
                print(f"  drive-serve: {self.path} {hdr} zlyhalo: {exc}",
                      file=sys.stderr, flush=True)

        def _send_stream(self, path, size, rng):
            """Celý súbor po kúskoch – GDAL to nerobí, ale `curl` áno."""
            start, end = rng
            self.send_response(200)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            step = 32 << 20
            for a in range(start, end + 1, step):
                self.wfile.write(self._fetch(path, a, min(a + step - 1, end)))

        def _send_single(self, path, size, start, end):
            body = self._fetch(path, start, end)
            self.send_response(206)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(body)

        def _send_multipart(self, path, size, ranges):
            with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
                bodies = list(ex.map(lambda r: self._fetch(path, *r), ranges))
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


def probe_size(pool, path):
    """Veľkosť z `Content-Range` jednobajtového GETu – HEAD sa nedá veriť."""
    status, headers, _ = pool.get(path, "bytes=0-0")
    cr = headers.get("Content-Range", "")
    if "/" not in cr:
        raise RuntimeError(
            f"Drive nevrátil Content-Range (HTTP {status}, {cr!r}). "
            "Je ten súbor zdieľaný pre kohokoľvek s odkazom?")
    return int(cr.rsplit("/", 1)[1])


def serve(ids, port=8787):
    """Spustí server na pozadí.

    `ids` je meno v URL → Drive file id. Vracia (základná url, {meno: veľkosť},
    štatistiky).
    """
    pool = Pool()
    files, sizes = {}, {}
    for name, file_id in ids.items():
        path = drive_path(file_id)
        size = probe_size(pool, path)
        files[name] = (path, size)
        sizes[name] = size
    stats = {"requests": 0, "bytes": 0, "lock": threading.Lock()}
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
    """
    env = {
        **os.environ,
        "GDAL_HTTP_MULTIRANGE": "YES",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "GDAL_HTTP_VERSION": "1.1",
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
    ap.add_argument("--port", type=int, default=8787, help="0 = vyber voľný")
    ap.add_argument("--print-url", action="store_true")
    args = ap.parse_args()

    ids = {}
    for spec in args.file:
        name, _, file_id = spec.partition("=")
        if not file_id:
            ap.error(f"--file čakalo MENO=ID, dostalo {spec!r}")
        ids[name] = file_id

    base, sizes, stats = serve(ids, args.port)
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


if __name__ == "__main__":
    main()

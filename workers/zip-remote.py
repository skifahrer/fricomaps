#!/usr/bin/env python3
"""
Čítanie vzdialeného ZIPu cez HTTP Range – bez toho, aby sa stiahol celý.

PREČO: DMR 5.0 od ÚGKK je jeden archív
`https://opendata.skgeodesy.sk/static/LLS/DMR5/DMR5_0_sjtsk03_bpv.zip`
a má ~198 GB. GitHub runner má voľných ~60 GB na disku, takže sa ten súbor
nemá kam stiahnuť – ani raz, ani po častiach na to isté miesto. Klasické
„stiahni a rozbaľ“ tu neexistuje.

ZIP je ale na náhodný prístup stavaný: na konci má centrálny adresár so
zoznamom položiek a s ich presnými offsetmi. Keď server vie HTTP Range
(a `opendata.skgeodesy.sk` je statické úložisko, takže vie), dá sa:

  1. prečítať posledných pár desiatok kB → koniec centrálneho adresára,
  2. prečítať samotný centrálny adresár → zoznam VŠETKÝCH položiek,
  3. stiahnuť LEN byty jednej položky (alebo súvislého úseku položiek)
     a rozbaliť ich za behu.

Vďaka tomu sa 198 GB dá spracovať v N paralelných jobov po ~2 GB, kde každý
siahne iba na svoj úsek. To je celý princíp workflowu „Rozobrať DMR 5.0“.

ZIP64 JE POVINNÝ: pri 198 GB sú offsety väčšie než 4 GB, takže obyčajný
koniec centrálneho adresára nesie samé 0xFFFFFFFF a skutočné čísla sú
v ZIP64 zázname. Bez toho by sa archív javil ako prázdny alebo rozbitý.

Použitie z príkazového riadka:

    # čo je v archíve (bez sťahovania obsahu)
    python3 workers/zip-remote.py list URL --limit=50
    python3 workers/zip-remote.py list URL --json=manifest.json

    # rozbaliť konkrétne položky do adresára
    python3 workers/zip-remote.py extract URL --out=dir --index=0-99

Ako knižnica:

    from importlib import util
    rz = RemoteZip(url)
    for e in rz.entries():
        ...
    rz.extract_span(entries, on_file=lambda name, stream: ...)
"""
import argparse
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

# Rovnaký User-Agent ako sonda – niektoré CDN odmietajú prázdneho klienta.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.4 Safari/605.1.15")

EOCD_SIG = b"PK\x05\x06"
EOCD64_SIG = b"PK\x06\x06"
EOCD64_LOC_SIG = b"PK\x06\x07"
CDIR_SIG = b"PK\x01\x02"
LFH_SIG = b"PK\x03\x04"

# Koniec centrálneho adresára je posledných 22 bajtov + komentár (max 64 kB).
# Berieme 128 kB, aby sa doň zmestil aj ZIP64 záznam pred ním.
TAIL = 128 * 1024


class RemoteZipError(RuntimeError):
    pass


def _open(url, headers, timeout):
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


class RemoteZip:
    """ZIP na konci HTTP odkazu. Sťahuje len to, o čo sa vysloveně pýtaš."""

    def __init__(self, url, timeout=60, retries=5, verbose=True):
        self.url = url
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose
        self._entries = None
        self.size = self._probe_size()

    # ---------- HTTP ----------

    def _log(self, msg):
        if self.verbose:
            print(msg, flush=True)

    def _probe_size(self):
        """Veľkosť súboru a kontrola, že server naozaj vie Range.

        HEAD samotný nestačí: `Accept-Ranges` občas chýba aj tam, kde Range
        funguje, a naopak. Preto sa pýtame o jeden bajt a pozeráme sa, či
        príde 206 s `Content-Range` – to je jediný dôkaz.
        """
        last = None
        for attempt in range(self.retries):
            try:
                with _open(self.url, {"User-Agent": UA, "Range": "bytes=0-0"},
                           self.timeout) as r:
                    code = r.getcode()
                    cr = r.headers.get("Content-Range", "")
                    r.read(1)
                if code != 206 or "/" not in cr:
                    raise RemoteZipError(
                        f"server nevie HTTP Range (HTTP {code}, "
                        f"Content-Range: {cr or '—'}). Bez neho sa 198 GB "
                        f"archív spracovať nedá.")
                total = cr.rsplit("/", 1)[1].strip()
                if not total.isdigit():
                    raise RemoteZipError(f"nečitateľný Content-Range: {cr}")
                return int(total)
            except RemoteZipError:
                raise
            except Exception as exc:              # sieť, 5xx, timeout
                last = exc
                time.sleep(min(2 ** attempt, 30))
        raise RemoteZipError(f"{self.url} neodpovedá: {last}")

    def _stream(self, start, end):
        """Otvorí odpoveď na `bytes=start-end` (obe vrátane)."""
        hdr = {"User-Agent": UA, "Range": f"bytes={start}-{end}"}
        last = None
        for attempt in range(self.retries):
            try:
                r = _open(self.url, hdr, self.timeout)
                if r.getcode() != 206:
                    r.close()
                    raise RemoteZipError(
                        f"na Range odpovedal HTTP {r.getcode()} – server by "
                        f"poslal celý súbor ({self.size} B)")
                return r
            except RemoteZipError:
                raise
            except urllib.error.HTTPError as exc:
                if exc.code in (416,):
                    raise RemoteZipError(f"neplatný rozsah {start}-{end}") from exc
                last = exc
                time.sleep(min(2 ** attempt, 30))
            except Exception as exc:
                last = exc
                time.sleep(min(2 ** attempt, 30))
        raise RemoteZipError(f"rozsah {start}-{end} sa nepodarilo otvoriť: {last}")

    def get(self, start, length):
        """Načíta presne `length` bajtov od `start` do pamäte."""
        end = min(start + length, self.size) - 1
        buf = bytearray()
        want = end - start + 1
        while len(buf) < want:
            r = self._stream(start + len(buf), end)
            try:
                while len(buf) < want:
                    part = r.read(min(1 << 20, want - len(buf)))
                    if not part:
                        break
                    buf += part
            finally:
                r.close()
            if len(buf) < want:
                # Prenos sa pretrhol – pokračuje sa tam, kde skončil.
                self._log(f"  … prenos sa pretrhol na {len(buf)}/{want} B, "
                          f"pokračujem")
        return bytes(buf)

    def reader(self, start, end):
        """Prúd bajtov `start..end` (vrátane), ktorý sa sám obnoví."""
        return _ResumableReader(self, start, end)

    # ---------- centrálny adresár ----------

    def _find_directory(self):
        tail_len = min(TAIL, self.size)
        tail = self.get(self.size - tail_len, tail_len)
        pos = tail.rfind(EOCD_SIG)
        if pos < 0:
            raise RemoteZipError(
                "na konci súboru nie je koniec centrálneho adresára (PK\\x05\\x06) – "
                "buď to nie je ZIP, alebo server poslal chybovú stránku.")
        cd_count, cd_size, cd_off = struct.unpack_from("<2xHII", tail, pos + 8)
        base = self.size - tail_len

        # ZIP64: 0xFFFF/0xFFFFFFFF znamená „skutočné číslo je inde“. Pri
        # 198 GB to tak je vždy.
        loc = tail.rfind(EOCD64_LOC_SIG, 0, pos)
        if loc >= 0 and (cd_count == 0xFFFF or cd_size == 0xFFFFFFFF
                         or cd_off == 0xFFFFFFFF):
            (eocd64_off,) = struct.unpack_from("<Q", tail, loc + 8)
            if base <= eocd64_off < self.size:
                rec = tail[eocd64_off - base:]
            else:
                rec = self.get(eocd64_off, 56)
            if rec[:4] != EOCD64_SIG:
                raise RemoteZipError(
                    f"ZIP64 záznam na offsete {eocd64_off} nezačína PK\\x06\\x06")
            cd_count, cd_size, cd_off = struct.unpack_from("<QQQ", rec, 32)
        return cd_off, cd_size, cd_count

    def entries(self):
        """Zoznam položiek archívu (číta sa raz, potom z pamäte)."""
        if self._entries is not None:
            return self._entries
        cd_off, cd_size, cd_count = self._find_directory()
        self._log(f"Centrálny adresár: {cd_count} položiek, "
                  f"{cd_size / 1048576:.1f} MB na offsete {cd_off}")
        blob = self.get(cd_off, cd_size)
        out, p = [], 0
        while p + 46 <= len(blob) and blob[p:p + 4] == CDIR_SIG:
            (flags, method, _t, _d, crc, csize, usize, nlen, elen, clen,
             _disk, _ia, _ea, hoff) = struct.unpack_from("<8xHHHHIIIHHHHHII", blob, p)
            name = blob[p + 46:p + 46 + nlen].decode("utf-8", "replace")
            extra = blob[p + 46 + nlen:p + 46 + nlen + elen]
            csize, usize, hoff = _zip64_fix(extra, csize, usize, hoff)
            out.append({
                "i": len(out), "name": name, "method": method, "flags": flags,
                "crc": crc, "csize": csize, "usize": usize, "offset": hoff,
                "nlen": nlen,
            })
            p += 46 + nlen + elen + clen
        if len(out) != cd_count:
            self._log(f"::warning::v adresári je {len(out)} položiek, hlavička "
                      f"hlási {cd_count}")
        self._entries = out
        return out

    # ---------- rozbaľovanie ----------

    def head(self, entry, want=65536):
        """Začiatok jednej položky – bez sťahovania celého súboru.

        Používa to kalibrácia v `dmr5-plan.py`: z prvých riadkov sa dá zistiť
        súradnicový systém a to, čo v mene súboru znamenajú ktoré čísla.
        Preto sa sťahuje len toľko komprimovaných bajtov, koľko na to treba.
        """
        take = min(entry["csize"], max(want, 1 << 16))
        raw = self.get(entry["offset"], 30 + entry["nlen"] + 4096 + take)
        if raw[:4] != LFH_SIG:
            raise RemoteZipError(f"{entry['name']}: chýba lokálna hlavička")
        nlen, elen = struct.unpack_from("<HH", raw, 26)
        data = raw[30 + nlen + elen:30 + nlen + elen + take]
        if entry["method"] == 0:
            return data[:want]
        dec = zlib.decompressobj(-zlib.MAX_WBITS)
        try:
            return dec.decompress(data, want)
        except zlib.error as exc:
            raise RemoteZipError(f"{entry['name']}: nedá sa rozbaliť ({exc})")

    def extract_span(self, entries, on_file, hard_limit=None):
        """Rozbalí položky JEDNÝM prenosom, ak ležia za sebou.

        `entries` musia byť zo `self.entries()`. Sťahuje sa súvislý úsek od
        prvej po poslednú položku a lokálne hlavičky sa čítajú z prúdu, ako
        idú – to je jedna HTTP požiadavka na celú časť namiesto jednej na
        každý súbor (pri 20 000 malých súboroch je to rozdiel medzi minútami
        a hodinami).

        `on_file(entry, stream)` dostane rozbalený prúd; ak ho neprečíta celý,
        zvyšok sa preskočí. Vráti počet spracovaných položiek.
        """
        if not entries:
            return 0
        ents = sorted(entries, key=lambda e: e["offset"])
        start = ents[0]["offset"]
        # Koniec: dáta poslednej položky + rezerva na lokálnu hlavičku
        # a prípadný data descriptor (16 B pri ZIP64).
        last = ents[-1]
        end = min(last["offset"] + 30 + last["nlen"] + 4096 + last["csize"] + 24,
                  self.size) - 1
        span = end - start + 1
        if hard_limit and span > hard_limit:
            raise RemoteZipError(
                f"úsek má {span / 1048576:.0f} MB, strop je "
                f"{hard_limit / 1048576:.0f} MB")
        self._log(f"Úsek {start}–{end} ({span / 1048576:.1f} MB), "
                  f"{len(ents)} položiek")

        rd = self.reader(start, end)
        done = 0
        for e in ents:
            rd.skip_to(e["offset"])
            head = rd.read_exact(30)
            if head[:4] != LFH_SIG:
                raise RemoteZipError(
                    f"na offsete {e['offset']} nie je lokálna hlavička "
                    f"({e['name']}) – archív sa medzitým zmenil?")
            nlen, elen = struct.unpack_from("<HH", head, 26)
            rd.read_exact(nlen + elen)
            on_file(e, _EntryStream(rd, e))
            rd.drain_entry(e)
            done += 1
        rd.close()
        return done


def _zip64_fix(extra, csize, usize, hoff):
    """Doplní skutočné veľkosti/offset zo ZIP64 rozšírenia (ID 0x0001)."""
    p = 0
    while p + 4 <= len(extra):
        tag, ln = struct.unpack_from("<HH", extra, p)
        body = extra[p + 4:p + 4 + ln]
        if tag == 0x0001:
            q = 0
            # Poradie je dané: sú tam LEN tie polia, ktoré vonku pretiekli.
            if usize == 0xFFFFFFFF and q + 8 <= len(body):
                usize = struct.unpack_from("<Q", body, q)[0]; q += 8
            if csize == 0xFFFFFFFF and q + 8 <= len(body):
                csize = struct.unpack_from("<Q", body, q)[0]; q += 8
            if hoff == 0xFFFFFFFF and q + 8 <= len(body):
                hoff = struct.unpack_from("<Q", body, q)[0]; q += 8
        p += 4 + ln
    return csize, usize, hoff


class _ResumableReader:
    """Sekvenčné čítanie rozsahu, ktoré prežije pretrhnutý prenos.

    Pri 2 GB úseku z jedného servera je pretrhnutie normálna vec, nie
    výnimka – reader si pamätá absolútnu pozíciu a pri chybe si vypýta
    zvyšok znova od nej.
    """

    def __init__(self, rz, start, end):
        self.rz = rz
        self.pos = start
        self.end = end
        self.r = None
        self.fails = 0

    def _ensure(self):
        if self.r is None:
            self.r = self.rz._stream(self.pos, self.end)

    def read(self, n):
        if self.pos > self.end or n <= 0:
            return b""
        n = min(n, self.end - self.pos + 1)
        while True:
            try:
                self._ensure()
                part = self.r.read(n)
                if not part:
                    # Predčasný koniec – skúsiť znova od aktuálnej pozície.
                    raise IOError("prúd skončil skôr, než mal")
                self.pos += len(part)
                self.fails = 0
                return part
            except Exception as exc:
                self._reset()
                self.fails += 1
                if self.fails > self.rz.retries:
                    raise RemoteZipError(
                        f"čítanie od {self.pos} zlyhalo {self.fails}×: {exc}")
                time.sleep(min(2 ** self.fails, 30))

    def read_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            part = self.read(n - len(buf))
            if not part:
                raise RemoteZipError(f"chýba {n - len(buf)} B na konci úseku")
            buf += part
        return bytes(buf)

    def skip(self, n):
        while n > 0:
            part = self.read(min(n, 1 << 20))
            if not part:
                return
            n -= len(part)

    def skip_to(self, abs_pos):
        if abs_pos < self.pos:
            raise RemoteZipError(
                f"nedá sa vrátiť späť ({self.pos} → {abs_pos}); položky musia "
                f"ísť po offsete")
        self.skip(abs_pos - self.pos)

    def drain_entry(self, e):
        """Dočíta zvyšok dát položky, aby prúd sedel na ďalšiu hlavičku."""
        left = e.get("_left", 0)
        if left:
            self.skip(left)
            e["_left"] = 0

    def _reset(self):
        if self.r is not None:
            try:
                self.r.close()
            except Exception:
                pass
            self.r = None

    def close(self):
        self._reset()


class _EntryStream:
    """Rozbalený obsah jednej položky ako súborový prúd (iba `read`)."""

    def __init__(self, rd, entry):
        self.rd = rd
        self.e = entry
        entry["_left"] = entry["csize"]
        if entry["method"] == 0:
            self.dec = None
        elif entry["method"] == 8:
            self.dec = zlib.decompressobj(-zlib.MAX_WBITS)
        else:
            raise RemoteZipError(
                f"{entry['name']}: kompresia {entry['method']} sa nepodporuje "
                f"(vieme 0 = uložené a 8 = deflate)")
        self.buf = bytearray()
        self.eof = False

    def _fill(self):
        """Doplní výstupný buffer aspoň o jedno prečítanie zo siete."""
        while not self.buf and not self.eof:
            if self.e["_left"] <= 0:
                if self.dec is not None:
                    self.buf += self.dec.flush()
                self.eof = True
                break
            raw = self.rd.read(min(1 << 20, self.e["_left"]))
            if not raw:
                self.eof = True
                break
            self.e["_left"] -= len(raw)
            self.buf += raw if self.dec is None else self.dec.decompress(raw)

    def read(self, n=-1):
        if n is None or n < 0:
            out = bytearray()
            while True:
                self._fill()
                if not self.buf:
                    return bytes(out)
                out += self.buf
                del self.buf[:]
        self._fill()
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def __iter__(self):
        """Riadky – DMR 5.0 sú textové výškové body, tak sa to hodí."""
        rest = b""
        while True:
            chunk = self.read(1 << 20)
            if not chunk:
                if rest:
                    yield rest
                return
            rest += chunk
            lines = rest.split(b"\n")
            rest = lines.pop()
            for ln in lines:
                yield ln


# ---------- CLI ----------

def _parse_index(spec, n):
    """„0-99,150,200-“ → množina indexov."""
    if not spec:
        return list(range(n))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            out.extend(range(int(a or 0), (int(b) if b else n - 1) + 1))
        else:
            out.append(int(part))
    return [i for i in out if 0 <= i < n]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("cmd", choices=["list", "extract"])
    ap.add_argument("url")
    ap.add_argument("--limit", type=int, default=40, help="koľko vypísať")
    ap.add_argument("--json", default="", help="kam uložiť manifest")
    ap.add_argument("--index", default="", help="ktoré položky (0-9,20)")
    ap.add_argument("--out", default="out", help="kam rozbaliť")
    ap.add_argument("--timeout", type=float, default=60)
    args = ap.parse_args(argv)

    rz = RemoteZip(args.url, timeout=args.timeout)
    print(f"Veľkosť archívu: {rz.size / 1e9:.1f} GB ({rz.size} B)")
    ents = rz.entries()
    total_c = sum(e["csize"] for e in ents)
    total_u = sum(e["usize"] for e in ents)
    print(f"Položiek: {len(ents)}, komprimované {total_c / 1e9:.1f} GB, "
          f"rozbalené {total_u / 1e9:.1f} GB")

    if args.cmd == "list":
        for e in ents[:args.limit]:
            print(f"  [{e['i']:6d}] {e['name']}  "
                  f"{e['csize'] / 1048576:.2f} → {e['usize'] / 1048576:.2f} MB")
        if len(ents) > args.limit:
            print(f"  … a ďalších {len(ents) - args.limit}")
        if args.json:
            with open(args.json, "w") as f:
                json.dump({"url": args.url, "size": rz.size, "entries": ents},
                          f, ensure_ascii=False)
            print(f"Manifest: {args.json}")
        return 0

    pick = [ents[i] for i in _parse_index(args.index, len(ents))]
    os.makedirs(args.out, exist_ok=True)

    def write(e, stream):
        dest = os.path.join(args.out, os.path.basename(e["name"]) or f"e{e['i']}")
        with open(dest, "wb") as f:
            while True:
                b = stream.read(1 << 20)
                if not b:
                    break
                f.write(b)
        print(f"  ✓ {e['name']} → {dest}")

    n = rz.extract_span(pick, write)
    print(f"Rozbalených položiek: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

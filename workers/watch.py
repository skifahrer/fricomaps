#!/usr/bin/env python3
"""
Spustí príkaz a je pri ňom počuť: postup, tep, pamäť, rast výstupu.

PREČO: `gdal_contour` nad krajom beží desiatky minút a s `-q` je úplne ticho.
Z logu sa potom nedá odlíšiť „počíta" od „zaseklo sa" – a keď to po hodine
spadne na timeout, nevieš ani, ako ďaleko sa dostalo. To isté platí pre
`gdalwarp` nad mozaikou a pre `ogr2ogr` nad miliónmi čiar.

Dve veci, ktoré to rieši:

  1. GDAL píše postup ako „0...10...20..." BEZ konca riadku. GitHub Actions
     taký riadok neukáže, kým sa príkaz neskončí – čiže presne vtedy, keď
     už progress netreba. Tu sa číta po bajtoch a každá nová desiatka sa
     vypíše ako samostatný riadok, teda hneď.
  2. Keď príkaz nehlási nič (`-q`, alebo fáza bez progressu), beží popri
     ňom tep: koľko to už trvá, koľko má proces pamäte a ako rastie výstup.
     Ticho dlhšie než `--every` sekúnd tak nikdy nenastane.

Používa to `rock-areas.py` (odtiaľ to pochádza) aj kroky workflowu.

Použitie ako knižnica:
    from watch import run_watched, Heartbeat, hms

Použitie z shellu:
    python3 workers/watch.py --label="vrstevnice" --watch-file=dem/raw.gpkg \\
        -- gdal_contour -a ele -i 10 -f GPKG dem/clip.tif dem/raw.gpkg
"""
import argparse
import os
import re
import subprocess
import sys
import threading
import time


def hms(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def dir_mb(path):
    """Veľkosť súboru alebo celého priečinka v MB."""
    if os.path.isfile(path):
        return os.path.getsize(path) / 1048576
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1048576


def proc_rss_mb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def proc_cpu_s(pid):
    """Koľko sekúnd procesor tomu procesu naozaj venoval.

    Toto je tá otázka, na ktorú „beží 7 minút, pamäť 0,2 GB" neodpovedá:
    POČÍTA sa, alebo sa ČAKÁ? Keď je CPU blízko 100 %, je to výpočet a treba
    zmenšiť prácu; keď je blízko nule, visí to na I/O alebo na sieti a
    zmenšovanie územia nepomôže.
    """
    try:
        with open(f"/proc/{pid}/stat") as f:
            # 14. a 15. pole sú utime a stime v tikoch; meno procesu môže
            # obsahovať medzery a zátvorky, tak sa reže až za poslednou `)`.
            polia = f.read().rpartition(")")[2].split()
        tiky = int(polia[11]) + int(polia[12])
        return tiky / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError):
        return 0.0


def proc_io_mb(pid):
    """(prečítané, zapísané) MB – rozlíši „čítam raster" od „nerobím nič"."""
    try:
        vals = {}
        with open(f"/proc/{pid}/io") as f:
            for line in f:
                k, _, v = line.partition(":")
                vals[k] = int(v)
        return vals.get("read_bytes", 0) / 1048576, vals.get("write_bytes", 0) / 1048576
    except (OSError, ValueError):
        return 0.0, 0.0


class Heartbeat(threading.Thread):
    """Každých `every` sekúnd povie, že sa stále niečo deje – a čo."""

    def __init__(self, label, pid=None, tmp=None, every=30, max_rss_mb=0,
                 max_s=0):
        super().__init__(daemon=True)
        self.label, self.pid, self.tmp = label, pid, tmp
        self.every, self.max_rss_mb, self.max_s = every, max_rss_mb, max_s
        self.t0 = time.time()
        self.stop_flag = threading.Event()
        self.killed_for_memory = False
        self.killed_for_time = False
        # Posledné percento, ktoré GDAL nahlásil, a kedy. Dopĺňa to `run_watched`
        # a tep z toho počíta odhad konca – jediné číslo, ktoré počas
        # dlhého behu naozaj zaujíma.
        self.pct = 0
        self.pct_at = self.t0
        self._last_cpu = 0.0
        self._last_t = self.t0
        self._last_io = (0.0, 0.0)

    def run(self):
        while not self.stop_flag.wait(self.every):
            beh = time.time() - self.t0
            # S rozpočtom sa hlási aj to, koľko z neho je preč. Bez toho sa
            # z „beží 2:41:30" nedá poznať, či to smeruje do cieľa alebo do
            # steny – a to je jediné, čo počas dlhého behu potrebuješ vedieť.
            parts = [f"beží {hms(beh)}" + (
                f" z {hms(self.max_s)} ({100 * beh / self.max_s:.0f} %)"
                if self.max_s else "")]
            rss = proc_rss_mb(self.pid) if self.pid else 0
            if rss:
                parts.append(f"pamäť {rss / 1024:.1f} GB")

            # POČÍTA, ALEBO ČAKÁ? Bez tohto sa z tepu nedá povedať nič o tom,
            # prečo to trvá – len že to trvá.
            if self.pid:
                cpu = proc_cpu_s(self.pid)
                teraz = time.time()
                if cpu:
                    podiel = 100 * (cpu - self._last_cpu) / max(teraz - self._last_t, 1e-6)
                    parts.append(f"CPU {podiel:.0f} %")
                    self._last_cpu, self._last_t = cpu, teraz
                r, w = proc_io_mb(self.pid)
                dr, dw = r - self._last_io[0], w - self._last_io[1]
                if dr or dw:
                    parts.append(f"disk +{dr:.0f}/+{dw:.0f} MB")
                    self._last_io = (r, w)

            if self.tmp and os.path.exists(self.tmp):
                parts.append(f"výstup {dir_mb(self.tmp):.0f} MB")

            # Odhad konca z NAMERANÉHO postupu, nie z konštanty vopred.
            # Konštanta sa mýli aj osemdesiatnásobne (`gdal_contour` nad
            # jemným sklonom); percentá z bežiaceho procesu nie.
            if 0 < self.pct < 100:
                celkom = beh / (self.pct / 100.0)
                parts.append(f"podľa {self.pct} % skončí o ~{hms(celkom - beh)}")
            print(f"  … {self.label}: {', '.join(parts)}", flush=True)
            if self.max_rss_mb and rss > self.max_rss_mb:
                self.killed_for_memory = True
                print(f"::error::{self.label} zabral {rss / 1024:.1f} GB pamäte "
                      f"(strop {self.max_rss_mb / 1024:.1f} GB) – zastavujem, "
                      f"inak by runner spadol na OOM bez hlášky.", flush=True)
                try:
                    os.kill(self.pid, 9)
                except OSError:
                    pass
                return
            # Strop na čas. Bez neho zlý odhad znamená, že sa beh nezastaví
            # sám, ale až na timeoute celého jobu – teda po hodinách a bez
            # jediného použiteľného výstupu. (Presne to sa stalo behu
            # 31222472790: `gdal_contour` bežal 2:41 a zabil ho runner.)
            if self.max_s and beh > self.max_s:
                self.killed_for_time = True
                print(f"::error::{self.label} beží {hms(beh)}, rozpočet je "
                      f"{hms(self.max_s)} – zastavujem. Radšej to povedať "
                      f"teraz než na timeoute celého jobu.", flush=True)
                try:
                    os.kill(self.pid, 9)
                except OSError:
                    pass
                return

    def stop(self):
        self.stop_flag.set()


def run_watched(cmd, label, tmp=None, max_rss_mb=0, every=30, max_s=0):
    """Spustí príkaz, priebežne hlási, že žije, a prekladá progress GDALu.

    `max_rss_mb` a `max_s` sú stropy: po ich prekročení sa proces zastaví
    a vyletí `MemoryError`, resp. `TimeoutError` – nie tichý beh do timeoutu
    celého jobu.
    """
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    hb = Heartbeat(label, proc.pid, tmp, every=every, max_rss_mb=max_rss_mb,
                   max_s=max_s)
    hb.start()
    tail, line, last = b"", b"", -1
    try:
        while True:
            chunk = proc.stdout.read(1)
            if not chunk:
                break
            line += chunk
            tail = (tail + chunk)[-8:]  # posledných pár znakov stačí na percentá
            if chunk == b"\n":
                # Riadok, ktorý nie je len meradlo postupu (napr. Warning) –
                # ten sa nesmie stratiť.
                txt = re.sub(rb"[\d.\s]|- done\.", b"", line)
                if txt.strip():
                    print(f"  {label}: {line.decode(errors='replace').strip()}",
                          flush=True)
                line = b""
                continue
            m = re.findall(rb"(\d+)", tail)
            if m:
                pct = int(m[-1])
                # Musí rásť: „100" sa počas čítania po bajtoch objaví najprv
                # ako „1" a „10", a to nie je krok späť na 10 %.
                if pct > last and pct % 10 == 0 and pct <= 100:
                    last = pct
                    beh = time.time() - t0
                    # Tepu sa to podá, aby vedel dopočítať odhad aj medzi
                    # desiatkami – pri pomalom behu je medzi nimi aj pol hodiny.
                    hb.pct, hb.pct_at = pct, time.time()
                    zvysok = (f", zostáva ~{hms(beh / (pct / 100.0) - beh)}"
                              if 0 < pct < 100 else "")
                    print(f"  … {label}: {pct} % (beží {hms(beh)}{zvysok})",
                          flush=True)
    finally:
        proc.wait()
        hb.stop()
    if hb.killed_for_memory:
        raise MemoryError(label)
    if hb.killed_for_time:
        raise TimeoutError(label)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    size = f", {dir_mb(tmp):.0f} MB" if tmp and os.path.exists(tmp) else ""
    print(f"  {label}: hotovo za {hms(time.time() - t0)}{size}", flush=True)


def main():
    ap = argparse.ArgumentParser(
        description="Spustí príkaz a hlási jeho postup, tep a rast výstupu.")
    ap.add_argument("--label", default="príkaz")
    ap.add_argument("--watch-file", default="",
                    help="súbor alebo priečinok, ktorého rast sa má hlásiť")
    ap.add_argument("--every", type=float, default=30.0)
    ap.add_argument("--max-rss-gb", type=float, default=0.0)
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="príkaz za `--`")
    args = ap.parse_args()

    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        print("::error::watch.py: chýba príkaz za `--`.", file=sys.stderr)
        return 2
    try:
        run_watched(cmd, args.label, tmp=args.watch_file or None,
                    max_rss_mb=args.max_rss_gb * 1024, every=args.every)
    except MemoryError:
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"::error::{args.label} zlyhal (kód {exc.returncode}).",
              file=sys.stderr)
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

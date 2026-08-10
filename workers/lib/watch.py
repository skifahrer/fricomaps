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
     už progress netreba. Tu sa číta po bajtoch a každý nový krok postupu sa
     vypíše ako samostatný riadok, teda hneď. Kroky sú BODKY, nie desiatky:
     medzi desiatkami sú tri bodky, čiže 2,5 % na bodku – pri dvojhodinovej
     vektorizácii je to správa každé tri minúty namiesto každých dvanástich.
  2. Keď príkaz nehlási nič (`-q`, alebo fáza bez progressu), beží popri
     ňom tep: koľko to už trvá, kde je, kedy skončí, koľko má proces pamäte
     a CPU, koľko číta a zapisuje a ako rastie výstup. Ticho dlhšie než
     `--every` sekúnd tak nikdy nenastane.

Používa to `rock-areas.py` (odtiaľ to pochádza) aj kroky workflowu.

Použitie ako knižnica:
    from watch import run_watched, Heartbeat, hms

Použitie z shellu:
    python3 workers/lib/watch.py --label="vrstevnice" --watch-file=dem/raw.gpkg \\
        -- gdal_contour -a ele -i 10 -f GPKG dem/clip.tif dem/raw.gpkg
"""
import argparse
import os
import re
import shlex
import subprocess
import sys
import threading
import time


def hms(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def gb(mb):
    """Pamäť tak, aby sa dala prečítať – 0,0 GB nepovie o procese nič."""
    return f"{mb:.0f} MB" if mb < 1024 else f"{mb / 1024:.1f} GB"


def o_kolkej(za_s):
    """Hodina, keď sa to podľa doterajšieho tempa skončí.

    „Zostáva ~1:47:10" sa musí prirátať k času v hlavičke riadku, kým sa dá
    povedať „to je po obede". Hodina to povie rovno; v Actions je log v UTC,
    takže sedí na časy krokov vedľa.
    """
    return time.strftime("%H:%M", time.localtime(time.time() + za_s))


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
        # Sekunda je podlaha, nie odporúčanie: pri `every=0` by sa čakanie
        # vracalo hneď a z tepu by bola nekonečná slučka, ktorá zaplní log aj
        # CPU. „Ticho" sa tu robiť nedá – na tepe visí aj strop pamäte.
        self.every = max(float(every), 1.0)
        self.max_rss_mb, self.max_s = max_rss_mb, max_s
        self.t0 = time.time()
        self.stop_flag = threading.Event()
        self.killed_for_memory = False
        self.killed_for_time = False
        # Posledné percento, ktoré GDAL nahlásil, a kedy. Dopĺňa to `run_watched`
        # a tep z toho počíta odhad konca – jediné číslo, ktoré počas
        # dlhého behu naozaj zaujíma.
        self.pct = 0.0
        self.pct_at = self.t0
        # Predošlé hlásené percento – z neho sa počíta NEDÁVNE tempo. Bez neho
        # sa dá povedať len priemer od štartu, a ten pri spomaľujúcom procese
        # klame smerom nadol: `gdal_contour -p` nad jemným sklonom ide prvých
        # 20 % rýchlo a potom sa každý ďalší krok predlžuje, takže odhad
        # z priemeru sľubuje koniec, ktorý nepríde (beh 31418794845).
        self.prev_pct = 0.0
        self.prev_at = self.t0
        # Aby varovanie o spomalení nezaznelo pri každom tepe.
        self.spomalenie_ohlasene = False
        # Namerané čísla si tep drží aj pre záverečný riadok: keď proces
        # skončí, `/proc/<pid>` zmizne a spýtať sa už nie je koho.
        self.rss_mb = 0.0
        self.peak_rss_mb = 0.0
        self.cpu_s = 0.0
        self.io = (0.0, 0.0)
        self.out_mb = 0.0
        self._last = (self.t0, 0.0, (0.0, 0.0), 0.0)  # čas, cpu, io, výstup

    def tempo(self):
        """(priemerné, nedávne) tempo v %/min; nedávne je z posledného kroku."""
        beh = max(time.time() - self.t0, 1e-6)
        priemer = self.pct / (beh / 60.0)
        dt = self.pct_at - self.prev_at
        dp = self.pct - self.prev_pct
        nedavne = dp / (dt / 60.0) if dt > 1 and dp > 0 else 0.0
        return priemer, nedavne

    def sample(self):
        """Odmeria proces a vráti jednu vetu o tom, čo práve robí."""
        now = time.time()
        beh = now - self.t0
        dt = max(now - self._last[0], 1e-6)
        # S rozpočtom sa hlási aj to, koľko z neho je preč. Bez toho sa
        # z „beží 2:41:30" nedá poznať, či to smeruje do cieľa alebo do
        # steny – a to je jediné, čo počas dlhého behu potrebuješ vedieť.
        parts = [f"beží {hms(beh)}" + (
            f" z {hms(self.max_s)} ({100 * beh / self.max_s:.0f} %)"
            if self.max_s else "")]

        # Odhad konca z NAMERANÉHO postupu, nie z konštanty vopred.
        # Konštanta sa mýli aj osemdesiatnásobne (`gdal_contour` nad
        # jemným sklonom); percentá z bežiaceho procesu nie.
        if 0 < self.pct < 100:
            priemer, nedavne = self.tempo()
            zvysok = beh / (self.pct / 100.0) - beh
            # Keď je nedávne tempo výrazne pod priemerom, proces spomaľuje
            # a odhad z priemeru je lož. Vtedy sa ukáže oboje a odhad sa
            # počíta z toho, ako to ide TERAZ.
            if nedavne and priemer and nedavne < priemer / 2:
                z_nedavneho = (100.0 - self.pct) / nedavne * 60.0
                parts.append(
                    f"{self.pct:g} %, tempo kleslo {priemer / nedavne:.1f}× "
                    f"({priemer:.2f} → {nedavne:.2f} %/min), pri terajšom "
                    f"tempe zostáva ~{hms(z_nedavneho)}")
            else:
                parts.append(f"{self.pct:g} %, zostáva ~{hms(zvysok)} "
                             f"(koniec ~{o_kolkej(zvysok)})")
        elif self.pct >= 100:
            parts.append("100 % – dopisuje výstup")

        rss = proc_rss_mb(self.pid) if self.pid else 0.0
        if rss:
            self.rss_mb = rss
            self.peak_rss_mb = max(self.peak_rss_mb, rss)
            strop = (f", strop {gb(self.max_rss_mb)}" if self.max_rss_mb else "")
            parts.append(f"pamäť {gb(rss)} "
                         f"(špička {gb(self.peak_rss_mb)}{strop})")

        # POČÍTA, ALEBO ČAKÁ? Bez tohto sa z tepu nedá povedať nič o tom,
        # prečo to trvá – len že to trvá. Okamžitý podiel hovorí, čo robí
        # TERAZ, priemer za celý beh to zasadí do súvislostí (jedna fáza
        # čakania na sieť ešte neznamená, že sa nepočíta).
        cpu = proc_cpu_s(self.pid) if self.pid else 0.0
        if cpu:
            self.cpu_s = cpu
            parts.append(f"CPU {100 * (cpu - self._last[1]) / dt:.0f} % "
                         f"(priemer {100 * cpu / max(beh, 1e-6):.0f} %)")
        r, w = proc_io_mb(self.pid) if self.pid else (0.0, 0.0)
        if r or w:
            dr, dw = r - self._last[2][0], w - self._last[2][1]
            self.io = (r, w)
            parts.append(f"disk {r:.0f}/{w:.0f} MB "
                         f"(+{dr / dt:.1f}/+{dw / dt:.1f} MB/s)")

        mb = dir_mb(self.tmp) if self.tmp and os.path.exists(self.tmp) else 0.0
        if mb:
            # Rast výstupu je jediná stopa po postupe fázy, ktorá percentá
            # nehlási vôbec – „výstup 0 MB" dve hodiny je odpoveď.
            parts.append(f"výstup {mb:.0f} MB (+{(mb - self._last[3]) / dt:.1f} MB/s)")
            self.out_mb = mb

        self._last = (now, cpu, (r, w), mb)
        return ", ".join(parts)

    def run(self):
        while not self.stop_flag.wait(self.every):
            print(f"  … {self.label}: {self.sample()}", flush=True)
            rss = self.rss_mb
            beh = time.time() - self.t0
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
            # Strop na čas – ZAPÍNA SA, nepredpokladá sa. Má zmysel len tam,
            # kde zastavenie niečo zachráni: obrysy tieňovania sa počítajú po
            # blokoch a hotové bloky sa po zastavení uložia, takže ďalší beh
            # nadviaže. Kde je výpočet JEDEN nedeliteľný priechod (skaly zo
            # sklonu), zastavenie nezachráni nič – zahodí hodiny práce a
            # nevyrobí ani neúplný výstup, tak sa tam `max_s` nepodáva a beží
            # sa, kým to nie je hotové.
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


# Meradlo postupu GDALu je JEDINÝ riadok zložený len z číslic a bodiek
# („0...10...20..."), takže sa dá odlíšiť od hlášky, ktorá tiež obsahuje čísla
# aj bodky (`Warning 1: ... 3.5`). Kým platí toto, čítajú sa percentá; prvý iný
# znak z riadku spraví hlášku a percentá sa v ňom už nehľadajú.
POSTUP = re.compile(rb"[\d.]+")


def percenta(line):
    """Percento z meradla postupu – aj MEDZI desiatkami.

    Medzi číslami sú tri bodky, čiže jedna bodka je 2,5 %. Čítať len tie
    desiatky znamenalo pri hodinovom `gdal_contour` správu raz za dvanásť
    minút; takto je štvornásobne hustejšia a nestojí to nič navyše.
    """
    m = re.match(rb"^.*?(\d+)(\.*)$", line, re.S)
    if not m:
        return None
    return min(100.0, int(m.group(1)) + 2.5 * len(m.group(2)))


def run_watched(cmd, label, tmp=None, max_rss_mb=0, every=30, max_s=0):
    """Spustí príkaz, priebežne hlási, že žije, a prekladá progress GDALu.

    `max_rss_mb` je strop pamäte: po jeho prekročení sa proces zastaví
    a vyletí `MemoryError` – lepšie než OOM, po ktorom v logu nie je nič.
    `max_s` je strop času a je VYPNUTÝ, kým ho niekto nezapne (vyletí
    `TimeoutError`); patrí len tam, kde sa po zastavení dá nadviazať.
    """
    t0 = time.time()
    every = max(float(every), 1.0)   # 0 by z tepu spravila nekonečnú slučku
    stropy = [f"pamäť do {gb(max_rss_mb)}"] if max_rss_mb else []
    stropy.append(f"čas do {hms(max_s)}" if max_s else
                  "bez stropu času (dobehne, aj keď to potrvá dlhšie, "
                  "než sa čakalo)")
    print(f"▶ {label}: {' '.join(shlex.quote(str(c)) for c in cmd)}", flush=True)
    print(f"  {label}: {', '.join(stropy)}, tep každých {every:g} s",
          flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    hb = Heartbeat(label, proc.pid, tmp, every=every, max_rss_mb=max_rss_mb,
                   max_s=max_s)
    hb.start()
    # Bodky sú husté ZÁMERNE, ale krátky príkaz nemá zaplniť log štyridsiatimi
    # riadkami o ničom: desiatky idú vždy, medzikroky len keď je medzi nimi
    # aspoň `krok_s` ticha. Pri hodinovom behu tak vypadne riadok každé
    # 2,5 %, pri dvadsaťsekundovom len desiatky.
    krok_s = max(5.0, every / 3.0)
    line, last, last_at = b"", -1.0, 0.0
    try:
        while True:
            chunk = proc.stdout.read(1)
            if not chunk:
                break
            if chunk == b"\n":
                # Riadok, ktorý nie je len meradlo postupu (napr. Warning) –
                # ten sa nesmie stratiť.
                txt = re.sub(rb"[\d.\s]|- done\.", b"", line)
                if txt.strip():
                    print(f"  {label}: {line.decode(errors='replace').strip()}",
                          flush=True)
                line, last, last_at = b"", -1.0, 0.0
                continue
            predtym, line = line, line + chunk
            if chunk == b".":
                # Nová bodka = ďalších 2,5 %, a číslo pred ňou je už celé.
                pct = percenta(line) if POSTUP.fullmatch(line) else None
            elif not chunk.isdigit() and predtym[-1:].isdigit() \
                    and POSTUP.fullmatch(predtym):
                # Číslo sa práve dopísalo (za ním medzera z „100 - done."):
                # inak by posledné percento nikdy nezaznelo.
                pct = percenta(predtym)
            else:
                continue          # číslica sa ešte dopisuje, alebo je to hláška
            if pct is None or pct <= last:
                continue
            last = pct
            teraz = time.time()
            beh = teraz - t0
            # Tepu sa to podá, aby vedel dopočítať odhad aj medzi bodkami –
            # pri pomalom behu je medzi nimi aj štvrť hodiny.
            hb.prev_pct, hb.prev_at = hb.pct, hb.pct_at
            hb.pct, hb.pct_at = pct, teraz
            # SPOMAĽUJE? Povedať to nahlas a HNEĎ, nie až keď to niekto po
            # hodine zruší. `gdal_contour -p` nad jemným sklonom nezrýchli
            # späť – keď tempo spadne na štvrtinu, je to signál, že zadanie
            # je nad možnosti jedného priechodu (rozpis pri
            # `CONTOUR_SRC_CELLS_PER_S` v contours-rocks/rock-plan.py).
            priemer, nedavne = hb.tempo()
            if (not hb.spomalenie_ohlasene and beh > 300
                    and nedavne and priemer and nedavne < priemer / 4):
                hb.spomalenie_ohlasene = True
                print(f"::warning::{label}: tempo kleslo {priemer / nedavne:.0f}× "
                      f"({priemer:.2f} → {nedavne:.2f} %/min pri {pct:g} %). "
                      f"Pri terajšom tempe zostáva ~"
                      f"{hms((100.0 - pct) / nedavne * 60.0)} a ďalej sa to "
                      f"bude predlžovať – jeden priechod `gdal_contour -p` sa "
                      f"nedá prerušiť, takže to buď dobehne, alebo padne na "
                      f"strope jobu. Zváž hrubší sklad (`rock_res`) alebo "
                      f"menší výrez (`area`).", flush=True)
            if pct % 10 and teraz - last_at < krok_s:
                continue
            last_at = teraz
            if 0 < pct < 100:
                zvysok = beh / (pct / 100.0) - beh
                tempo = (f", tempo {pct / (beh / 60):.1f} %/min"
                         if beh > 60 else "")
                kam = (f"{tempo}, zostáva ~{hms(zvysok)} "
                       f"(koniec ~{o_kolkej(zvysok)})")
            else:
                kam = ", dopisuje výstup"
            print(f"  … {label}: {pct:g} % (beží {hms(beh)}{kam})", flush=True)
    finally:
        proc.wait()
        hb.stop()
    if hb.killed_for_memory:
        raise MemoryError(label)
    if hb.killed_for_time:
        raise TimeoutError(label)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    took = time.time() - t0
    # Namerané čísla na koniec: bez nich sa odhady nemajú z čoho opraviť
    # a „trvalo to dlho" je jediné, čo po behu ostane.
    konce = [f"hotovo za {hms(took)}"]
    if tmp and os.path.exists(tmp):
        konce.append(f"výstup {dir_mb(tmp):.0f} MB")
    if hb.peak_rss_mb:
        konce.append(f"špička pamäte {gb(hb.peak_rss_mb)}")
    if hb.cpu_s:
        konce.append(f"CPU {hms(hb.cpu_s)} ({100 * hb.cpu_s / max(took, 1e-6):.0f} %)")
    if any(hb.io):
        konce.append(f"disk {hb.io[0]:.0f} MB čítania / {hb.io[1]:.0f} MB zápisu")
    print(f"✔ {label}: {', '.join(konce)}", flush=True)


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

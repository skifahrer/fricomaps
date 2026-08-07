#!/usr/bin/env python3
"""
S-JTSK: to, čo musia vedieť aj plán, aj spracovanie časti DMR 5.0.

Archív `DMR5_0_sjtsk03_bpv.zip` je v S-JTSK [JTSK03], výšky Bpv. Krovákovo
zobrazenie má ale DVE oficiálne podoby a ÚGKK nikde nepíše, ktorú v tých
textových súboroch používa:

  EPSG:8352  JTSK03 / Krovak             osi (juh, západ), obe hodnoty KLADNÉ
                                         (Bratislava ≈ 1 280 715 / 573 779)
  EPSG:8353  JTSK03 / Krovak East North  osi (východ, sever), obe ZÁPORNÉ
                                         (Bratislava ≈ −573 779 / −1 280 715)

Poradie stĺpcov v texte je navyše ďalšia neznáma. Preto sa to NEHÁDA:
zoberú sa prvé riadky, vyskúšajú sa všetky štyri kombinácie a berie sa tá,
pri ktorej body padnú do Slovenska. Tá istá funkcia potom prepočíta každý
bod v celom archíve, takže sa raz zistená orientácia nemôže rozísť.

Všetko, čo z tohto modulu vyjde, je (east, north) v EPSG:8353 – jedna
podoba, v ktorej sa ďalej počíta mriežka a ktorú GDAL priamo pozná.
"""
import math

# EPSG:8353 = JTSK03 / Krovak East North. Rovnaká mriežka ako EPSG:5514
# (rozdiel S-JTSK vs JTSK03 je pod 0,5 m), ale menom sedí na „sjtsk03“.
EN_EPSG = 8353

# Obálka Slovenska v EPSG:8353 s rezervou ~20 km na každú stranu. Slúži
# len na rozhodnutie „ktorá kombinácia stĺpcov dáva zmysel“, nie na orez.
EN_BBOX = (-625000.0, -1350000.0, -140000.0, -1120000.0)

# Štyri možné výklady dvojice čísel z riadku. Prvý, ktorý padne do obálky,
# vyhráva – iný sa netrafí, lebo východ a sever sú od seba rádovo ďaleko.
ORIENTATIONS = (
    ("east_north", lambda a, b: (a, b)),            # už EPSG:8353
    ("north_east", lambda a, b: (b, a)),            # prehodené stĺpce
    ("krovak_yx", lambda a, b: (-a, -b)),           # EPSG:8352, poradie Y X
    ("krovak_xy", lambda a, b: (-b, -a)),           # EPSG:8352, poradie X Y
)


def in_slovakia(east, north):
    w, s, e, n = EN_BBOX
    return w <= east <= e and s <= north <= n


def detect_orientation(pairs):
    """Z dvojíc (a, b) vyberie výklad stĺpcov. Vráti (meno, funkcia) alebo None."""
    pairs = [p for p in pairs if p is not None]
    if not pairs:
        return None
    for name, fn in ORIENTATIONS:
        if all(in_slovakia(*fn(a, b)) for a, b in pairs):
            return name, fn
    return None


def parse_numbers(line):
    """Čísla z jedného riadku textu. Zvládne medzery, tabulátory, bodkočiarky
    aj čiarky ako oddeľovač; desatinná čiarka sa berie ako bodka len tam, kde
    nie je oddeľovačom (t. j. keď je oddeľovač bodkočiarka alebo tabulátor)."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    line = line.strip()
    if not line:
        return []
    if ";" in line or "\t" in line:
        parts = line.replace("\t", ";").split(";")
        parts = [p.strip().replace(",", ".") for p in parts]
    else:
        parts = line.replace(",", " ").split()
    out = []
    for p in parts:
        try:
            out.append(float(p))
        except ValueError:
            return []           # riadok s textom (hlavička) – nie je to bod
    return out


def parse_point(line, orient):
    """Riadok → (east, north, výška) v EPSG:8353, alebo None."""
    v = parse_numbers(line)
    if len(v) < 3:
        return None
    e, n = orient(v[0], v[1])
    return e, n, v[2]


# ---------- prepočet na WGS84 ----------
# pyproj je v pipeline vždy (workflow ho inštaluje), ale plán sa dá spustiť
# aj lokálne bez neho – vtedy sa výrez zadáva rovno v S-JTSK.

def transformer(to_wgs=False):
    from pyproj import Transformer
    if to_wgs:
        return Transformer.from_crs(f"EPSG:{EN_EPSG}", "EPSG:4326", always_xy=True)
    return Transformer.from_crs("EPSG:4326", f"EPSG:{EN_EPSG}", always_xy=True)


def wgs_bbox_to_en(bbox):
    """(W, S, E, N) vo WGS84 → obálka (east_min, north_min, east_max, north_max).

    Krovák je kužeľové zobrazenie, takže obdĺžnik vo WGS84 nie je obdĺžnik
    v S-JTSK – preto sa neberú len rohy, ale aj body po okraji.
    """
    t = transformer()
    w, s, e, n = bbox
    xs, ys = [], []
    steps = 16
    for i in range(steps + 1):
        f = i / steps
        for lon, lat in ((w + (e - w) * f, s), (w + (e - w) * f, n),
                         (w, s + (n - s) * f), (e, s + (n - s) * f)):
            x, y = t.transform(lon, lat)
            if math.isfinite(x) and math.isfinite(y):
                xs.append(x)
                ys.append(y)
    if not xs:
        raise ValueError(f"bbox {bbox} sa nedá prepočítať do S-JTSK")
    return min(xs), min(ys), max(xs), max(ys)

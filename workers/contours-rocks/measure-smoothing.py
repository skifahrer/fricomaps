#!/usr/bin/env python3
"""
Koľko hladenia vrstevníc je akurát – merané, nie odhadnuté.

NIE JE TO ČASŤ PIPELINE. Nevolá to žiadny workflow; je to nástroj, ktorým sa
vyberali tri hodnoty v `env:` build-map.yml (`CONTOUR_DEM_LOWPASS`,
`CONTOUR_SIMPLIFY`, `CONTOUR_SMOOTH`). Leží tu preto, že bez neho sa čísla
v tých komentároch nedajú overiť – a raz sa to už vypomstilo (nižšie).

PREČO VZNIKOL. Nastavenie sa ladilo trikrát a zakaždým podľa inej tabuľky,
ktorú nebolo z čoho zopakovať. Druhé ladenie meralo na teréne, ktorý mal
LEN hladký svah a šum – vyhladenie DEM v okne 5×5 tam vyšlo ako čistý zisk,
lebo v takom teréne nie je čo pokaziť. V skutočnom teréne sú aj tvary široké
pár metrov (rebro, žľab, terasa) a práve tie okno 5×5 zmazalo: vrstevnice
z toho boli oblé a nedržali sa terénu. Preto tu terén tvary MÁ a meria sa,
koľko z nich na čiare ostane.

ČO SA MERIA (`--help` vypíše aj nastavenia):
  bodov     … koľko bodov má výsledná čiara (≈ veľkosť dlaždíc)
  lom       … priemerný uhol zlomu medzi susednými segmentmi (zubatosť)
  >30°      … podiel ostrých lomov – to je to, čo pri max zoome vidno
  odchýlka  … vzdialenosť od izolínie toho istého terénu BEZ šumu (vernosť)
  tvar      … koľko % z amplitúdy reálneho tvaru na čiare ostalo

A NAKONIEC TO ISTÉ PO MRIEŽKE DLAŽDICE. Vektorová dlaždica má súradnice
v celých číslach na mriežke `extent` (4096, Planetiler ju meniť nevie), takže
hotová čiara sa pri zápise zaokrúhli – pri z14 na 0,391 m, pri z16 na 0,098 m.
Nad svojím maxzoomom sa vrstevnice už len naťahujú (overzoom), a práve tie
zaokrúhlené súradnice vidno pri max zoome mapy ako schodíky: čiara, ktorá má
sama 1,6 % ostrých lomov, ich má po mriežke z14 rovných 11,5 %. Bez tohto
stĺpca tabuľka tvrdí, že je všetko hladké, kým v mape je vidieť zuby – a presne
tak to raz dopadlo.

MODEL PIPELINE. Vyhladenie DEM (priemer v okne a späť na mriežku),
`gdal_contour` (marching squares s lineárnou interpoláciou na hrane bunky),
`-simplify` (Douglas–Peucker) a Chaikin zo `smooth-shapes.py` – vrátane
konzervovaných koncov otvorenej čiary. GDAL netreba, aby sa to dalo spustiť
aj mimo runnera; rozdiel oproti `cubicspline` v gdalwarpe je pod úrovňou
toho, o čom tieto čísla rozhodujú.

Použitie:
    python3 workers/contours-rocks/measure-smoothing.py             # tabuľka pre default a okolie
    python3 workers/contours-rocks/measure-smoothing.py --seed=7    # iný šum (čísla sú stabilné)
"""
import argparse
import math

import numpy as np

NX, NY = 640, 320          # 1 m mriežka
LAT = 49.1                 # zemepisná šírka (Tatry) – kvôli mriežke dlaždice
EXTENT = 4096              # súradnicová mriežka vektorovej dlaždice
SLOPE = 0.10               # 10 % svah – izolínia je potom graf y(x)
# Reálne tvary terénu: (vlnová dĺžka v metroch, amplitúda výšky v metroch).
# Na 10 % svahu robí amplitúda 1,2 m výkyv čiary 12 m do strany.
FEATS = [(60.0, 1.20), (25.0, 0.50), (12.0, 0.22)]
NOISE = 0.15               # mikroreliéf: kry, balvany, šum merania
LEVEL = 16.0


def feature(x):
    out = np.zeros_like(x, dtype=float)
    for lam, amp in FEATS:
        out += amp * np.sin(2 * math.pi * x / lam + lam)
    return out


def terrain(rng=None):
    X, Y = np.meshgrid(np.arange(NX, dtype=float), np.arange(NY, dtype=float))
    Z = SLOPE * Y + feature(X)
    return Z if rng is None else Z + rng.normal(0.0, NOISE, Z.shape)


def lowpass(Z, win):
    """Priemer v okne `win`×`win` a späť na pôvodnú mriežku (dva gdalwarpy)."""
    if win <= 1:
        return Z
    ny, nx = Z.shape
    my, mx = ny // win, nx // win
    C = Z[:my * win, :mx * win].reshape(my, win, mx, win).mean(axis=(1, 3))
    cy = (np.arange(my) + 0.5) * win - 0.5
    cx = (np.arange(mx) + 0.5) * win - 0.5
    tmp = np.vstack([np.interp(np.clip(np.arange(nx), cx[0], cx[-1]), cx, row)
                     for row in C])
    return np.column_stack([
        np.interp(np.clip(np.arange(ny), cy[0], cy[-1]), cy, col)
        for col in tmp.T])


# ---------- marching squares: tak trasuje izolíniu aj gdal_contour ----------
def _edge(p, q, zp, zq, level):
    t = (level - zp) / (zq - zp)
    return (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))


def contour(Z, level):
    ny, nx = Z.shape
    segs = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            z = (Z[j, i], Z[j, i + 1], Z[j + 1, i + 1], Z[j + 1, i])
            c = ((float(i), float(j)), (float(i + 1), float(j)),
                 (float(i + 1), float(j + 1)), (float(i), float(j + 1)))
            if all(v >= level for v in z) or all(v < level for v in z):
                continue
            pts = [_edge(c[k], c[(k + 1) % 4], z[k], z[(k + 1) % 4], level)
                   for k in range(4)
                   if (z[k] >= level) != (z[(k + 1) % 4] >= level)]
            if len(pts) == 2:
                segs.append((pts[0], pts[1]))
            elif len(pts) == 4:          # sedlo – rozhodne stred bunky
                if (sum(z) / 4 >= level) == (z[0] >= level):
                    segs += [(pts[0], pts[1]), (pts[2], pts[3])]
                else:
                    segs += [(pts[1], pts[2]), (pts[3], pts[0])]
    return longest_chain(segs)


def longest_chain(segs):
    """Najdlhšia súvislá čiara – tá cez celý výrez, nie odrobinky pri kraji."""
    def key(p):
        return round(p[0], 6), round(p[1], 6)

    adj = {}
    for si, (a, b) in enumerate(segs):
        adj.setdefault(key(a), []).append((si, 0))
        adj.setdefault(key(b), []).append((si, 1))
    used = [False] * len(segs)
    best = []
    for start in range(len(segs)):
        if used[start]:
            continue
        chain = list(segs[start])
        used[start] = True
        for koniec in (0, 1):
            while True:
                tip = chain[-1] if koniec else chain[0]
                nxt = next(((si, s) for si, s in adj.get(key(tip), [])
                            if not used[si]), None)
                if nxt is None:
                    break
                si, side = nxt
                used[si] = True
                other = segs[si][1 - side]
                chain.append(other) if koniec else chain.insert(0, other)
        if len(chain) > len(best):
            best = chain
    return best


# ---------- to isté, čo robí ogr2ogr -simplify a smooth-shapes.py ----------
def simplify(pts, tol):
    if tol <= 0 or len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        (x0, y0), (x1, y1) = pts[a], pts[b]
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy) or 1e-12
        dmax, imax = 0.0, a
        for i in range(a + 1, b):
            x, y = pts[i]
            d = abs(dy * x - dx * y + x1 * y0 - y1 * x0) / norm
            if d > dmax:
                dmax, imax = d, i
        if dmax > tol:
            keep[imax] = True
            stack += [(a, imax), (imax, b)]
    return [p for p, k in zip(pts, keep) if k]


def chaikin(pts, passes):
    """Otvorená čiara s konzervovanými koncami – ako v smooth-shapes.py."""
    pts = list(pts)
    for _ in range(passes):
        out = [pts[0]]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            out.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            out.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        out.append(pts[-1])
        pts = out
    return pts


# ---------- metriky ----------
def tile_step(z, lat=LAT):
    """Krok mriežky dlaždice v metroch pri danom zoome."""
    return 40075017 * math.cos(math.radians(lat)) / (2 ** z) / EXTENT


def quantize(pts, step):
    """Zaokrúhlenie na mriežku dlaždice – to isté, čo spraví zápis do MVT."""
    return [(round(x / step) * step, round(y / step) * step) for x, y in pts]


def bends(pts):
    out = []
    for (xa, ya), (xb, yb), (xc, yc) in zip(pts, pts[1:], pts[2:]):
        ux, uy, vx, vy = xb - xa, yb - ya, xc - xb, yc - yb
        nu, nv = math.hypot(ux, uy), math.hypot(vx, vy)
        if nu < 1e-12 or nv < 1e-12:
            continue
        out.append(math.degrees(math.acos(
            max(-1.0, min(1.0, (ux * vx + uy * vy) / (nu * nv))))))
    return np.array(out or [0.0])


def resample(pts, step=0.5):
    """Rovnomerne po dĺžke – inak by metriky vážili husto obsadené úseky."""
    P = np.asarray(pts)
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(P, axis=0).T))]
    s = np.arange(0, d[-1], step)
    return np.c_[np.interp(s, d, P[:, 0]), np.interp(s, d, P[:, 1])]


def metrics(pts):
    R = resample(pts)
    R = R[(R[:, 0] > 20) & (R[:, 0] < NX - 20)]     # okraje výrezu nerátať
    dev = math.sqrt(float(np.mean((R[:, 1] - (LEVEL - feature(R[:, 0]))
                                   / SLOPE) ** 2)))
    x = R[:, 0]
    cols = [np.ones_like(x), x]
    for lam, _ in FEATS:
        cols += [np.sin(2 * math.pi * x / lam), np.cos(2 * math.pi * x / lam)]
    coef = np.linalg.lstsq(np.vstack(cols).T, R[:, 1], rcond=None)[0]
    tvary = [100.0 * math.hypot(coef[2 + 2 * k], coef[3 + 2 * k])
             / (amp / SLOPE) for k, (_, amp) in enumerate(FEATS)]
    return dev, tvary


def run(Z, win, quarters, passes, label, maxzoom):
    pts = chaikin(simplify(contour(lowpass(Z, win), LEVEL), quarters / 4.0),
                  passes)
    ang = bends(pts)
    dev, tvary = metrics(pts)
    # A to isté po mriežke dlaždice – tak, ako to skončí v .pmtiles.
    qang = bends(quantize(pts, tile_step(maxzoom)))
    print(f"{label:50s} {len(pts):5d} {ang.mean():6.1f}° "
          f"{100 * float(np.mean(ang > 30)):5.1f}% {dev:6.2f} m "
          + " ".join(f"{t:4.0f}%" for t in tvary)
          + f"  │ {qang.mean():6.1f}° {100 * float(np.mean(qang > 30)):5.1f}%")


def main():
    ap = argparse.ArgumentParser(
        description="Meranie hladenia vrstevníc na simulovanom teréne.")
    ap.add_argument("--seed", type=int, default=20260810)
    ap.add_argument("--maxzoom", type=int, default=14,
                    help="maxzoom dlaždíc s vrstevnicami (mriežka 4096)")
    args = ap.parse_args()

    Z = terrain(np.random.default_rng(args.seed))
    lam = "  ".join(f"{int(l)} m" for l, _ in FEATS)
    krok = tile_step(args.maxzoom)
    print(f"terén: svah {SLOPE:.0%}, šum σ = {NOISE} m, tvary {lam}, "
          f"seed {args.seed}")
    print(f"dlaždice: maxzoom z{args.maxzoom} → mriežka {krok:.3f} m "
          f"(extent {EXTENT}, šírka {LAT}°)")
    print(f"{'nastavenie':50s} {'bodov':>5s} {'lom':>7s} {'>30°':>6s} "
          f"{'odchýlka':>8s}  tvary (λ 60 / 25 / 12 m)"
          f"  │ po mriežke z{args.maxzoom}")
    run(terrain(), 1, 0, 0, "referencia (terén bez šumu, bez úprav)", args.maxzoom)
    print()
    for win, q, p, note in [
        (1, 1, 1, "  ← predtým"),
        (5, 2, 2, "  ← august (oblé)"),
        (3, 2, 2, ""),
        (3, 1, 1, ""),
        (3, 1, 2, "  ← teraz"),
        (3, 1, 3, ""),
        (7, 2, 2, ""),
    ]:
        okno = f"okno {win}×{win}" if win > 1 else "bez vyhladenia"
        run(Z, win, q, p, f"{okno}, {q}/4 bunky, {p}× Chaikin{note}", args.maxzoom)


if __name__ == "__main__":
    main()

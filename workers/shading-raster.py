#!/usr/bin/env python3
"""
Skaly z tieňovania, 2/3: z dlaždíc raster tmavosti.

ČO JE TU. Mozaika dlaždíc → pole „ako tmavé je to tu oproti okoliu": pásové
čítanie, pole osvetlenia (pozadie) na zmenšenej mriežke, prahy a zápis rastra
po pásoch. Sťahovanie dlaždíc je vo `shading-tiles.py`, obrysy vo
`shading-vector.py`, plán a CLI v `shading-rocks.py`.

PREČO ZVLÁŠŤ: `shading-rocks.py` mal 2023 riadkov (pravidlo 5 v CLAUDE.md).
Rez je na hranici fázy, ktorá tam už bola vyznačená komentárom.

Spúšťa sa ako modul, nie z príkazovej riadky:
    raster = load("shading_raster", "shading-raster.py")
"""
import importlib.util
import math
import os
import sys
import time

import numpy as np
from PIL import Image

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


# Mriežka, `run()` a `Heartbeat` sú z tej spodnej vrstvy – berú sa odtiaľ, nie
# sa píšu druhýkrát (pravidlo 1).
tiles = load("shading_tiles", "shading-tiles.py")
WEBMERC, R, TILE = tiles.WEBMERC, tiles.R, tiles.TILE
run = tiles.run
tile_res, ground_res = tiles.tile_res, tiles.ground_res

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from watch import hms, dir_mb, Heartbeat  # noqa: E402

# Na akom zmenšení sa počíta pole osvetlenia (pozadie). Pozadie je hladká
# funkcia – na 8× menšej mriežke vyzerá rovnako a je 64× lacnejšie.
BG_DOWN = 8


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


def _rank_box(a, r, ufunc):
    """Bežiace min/max v okne (2r+1)² – separovateľne, po osiach.

    Dva prechody po (2r+1) posunoch namiesto (2r+1)² ako v `box_blur_u8`:
    pri r=4 je to 18 operácií na pixel a nie 81. Namerané 140 mil. px/s,
    čiže na z17 nad Vysokými Tatrami (0,91 mld. px) okolo 7 sekúnd.
    """
    if r <= 0:
        return a
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r, r)
        ap = np.pad(a, pad, mode="edge")
        acc = None
        for d in range(2 * r + 1):
            sl = [slice(None), slice(None)]
            sl[axis] = slice(d, d + a.shape[axis])
            v = ap[tuple(sl)]
            acc = v if acc is None else ufunc(acc, v)
        a = acc
    return a


def open_mask(score, r):
    """Morfologické OTVORENIE masky tmavosti: erózia, potom dilatácia.

    ČO TO RIEŠI. Prah nad hillshade nenájde len steny – nájde aj hustú sieť
    vlásočnicových rýh a mikrotieňov cez celý svah. Pri pohľade na pixely to
    vyzerá správne (červená maska naozaj leží na rozčlenenom teréne), lenže
    z tých vlákien sa vektorizáciou stane JEDEN prepojený polygón cez celý
    výrez a v mape z neho pri z14 a nižšie nie je sieť, ale rovnomerná sivá
    deka. Namerané na výreze pri Gerlachu (2 km², z17, dark=125):

        bez otvorenia   21,6 % plochy, najväčší útvar 30,6 ha, pri z14 je
                        20,7 % pixelov z väčšiny zaliatych → súvislý záves
        r = 2 (1,6 m)   15,4 %
        r = 4 (3,1 m)    9,5 %, pri z14 už čitateľné samostatné telesá

    Erózia zmaže všetko užšie než 2r+1 pixelov, dilatácia vráti prežitým
    jadrám ich pôvodný rozsah. Stena teda ostane stenou, vlásočnica zmizne –
    a nie podľa plochy (na tú je celá sieť jeden veľký útvar), ale podľa
    ŠÍRKY, čo je presne to, čím sa stena od ryhy líši.

    Polomer je v METROCH na zemi (`--open`), takže to isté nastavenie platí
    na každom zoome rovnako.
    """
    if r <= 0:
        return score
    keep = (score > 0).astype(np.uint8)
    keep = _rank_box(keep, r, np.minimum)   # erózia
    keep = _rank_box(keep, r, np.maximum)   # dilatácia
    score = score.copy()
    score[keep == 0] = 0
    return score


def score_band(gray, dark, always, local_px, rel, blur, fill_px=0, every=0,
               open_px=0):
    """Šedá → „tmavosť" (Byte): o koľko je pixel pod referenciou.

    ref   = clip(pozadie − rel, always, dark)   (bez pozadia rovno `dark`)
    score = clip(ref − šedá, 0, 255)

    `open_px` (input `open`) potom vyhodí všetko užšie než 2×open_px – to sú
    vlásočnicové ryhy a mikrotiene, z ktorých je v mape sivá deka. Viď
    `open_mask`; stena to nechá stenou.

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

    if open_px > 0:
        # Až tu, na hotovej maske: pred prahom by sa mazalo z plynulej
        # tmavosti a `dark_always` by sa nemal ako uplatniť, po vektorizácii
        # už je celá sieť jeden polygón a šírka sa z neho nedá vytiahnuť.
        faza(f"otvorenie {open_px} px", t_f)
        out = open_mask(out, open_px)
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
    pad_tiles = (int(math.ceil(max(local_px, args.fill_px, 2 * args.open_px)
                               / 2.0 / TILE))
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
                                        args.fill_px, every=args.heartbeat,
                                        open_px=args.open_px)
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



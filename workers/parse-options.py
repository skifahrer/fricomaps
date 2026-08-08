#!/usr/bin/env python3
"""
Rozloží voľné `kľúč=hodnota` z inputu `options` na jednotlivé nastavenia
a z troch výberov zdrojov odvodí, čo sa vlastne ide počítať.

PREČO: `workflow_dispatch` dovolí **najviac 10 inputov**. Mali sme ich 26,
a workflow sa preto prestal načítať – beh skončil ako „failure" s nula jobmi,
lebo GitHub ten súbor ani neprijal. Deväť najpoužívanejších vecí ostalo
samostatnými inputmi, zvyšok sa píše do jedného poľa:

    rock_res=1 rock_maxzoom=15 trails=false

Nie je to len obchádzka limitu: formulár s 26 poľami sa aj tak nedal použiť.
Takto sú v ňom veci, ktoré meníš pri každom behu, a ostatné majú rozumné
predvolené hodnoty. Ktoré to sú, sa časom mení: `rock_res` (mriežka na obrys
skál) sa prestavuje len s iným zdrojom výšok, kým veľkosť rýchleho testu
(`test_km2`) pri každom ladení – tak si vymenili miesto.

TRI VÝBERY ZDROJA namiesto jedného `dem_source` a zoznamu `layers`:
`contour_source` (vrstevnice), `rock_source` (skaly) a `shading_source`
(tieňovanie a 3D terén). Každý z nich vie aj `ziadne`, takže vypnutie vrstvy
je hodnota vo výbere a nie druhé pole vedľa neho – dve polia na tú istú vec
sa vždy raz rozídu („generuj vrstevnice, zdroj žiadny"). Vrstva sa tým pádom
zapína tam, kde sa vyberá jej zdroj.

Neznámy kľúč je chyba, nie ticho ignorovaná hodnota – preklep v `rock_slop=55`
by inak znamenal, že sa celý beh spustí s iným nastavením, než si myslíš.

Použitie:
    python3 workers/parse-options.py --options="rock_res=1" \\
        --rebuild=skaly --contour-source=sonny --rock-source=dmr5 \\
        --shading-source=sonny --test-km2=4 --out=$GITHUB_OUTPUT
"""
import argparse
import json
import os
import shlex
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

# kľúč: (predvolená hodnota, popis)
DEFAULTS = {
    "crop_bbox": ("", "orezať región na west,south,east,north"),
    "area_bbox": ("", "vlastný výrez W,S,E,N namiesto pohoria z výberu"),
    # Stred testovacieho štvorca. Samotná veľkosť (`test_km2`) je input vo
    # formulári – mení sa pri každom behu, kým miesto skoro nikdy.
    "test_at": ("", "stred testovacieho štvorca `lon,lat` (prázdne = stred výrezu)"),
    "size_limit_mb": ("900", "rozpočet celej stránky v MB"),
    "auto_shrink": ("true", "znížiť zoom dlaždíc, keď sa nezmestia"),
    "ugkk_fallback": ("true", "keď 1 m LiDAR nie je, počítať zo Sonnyho"),
    "ugkk_urls": ("", "priame URL na ÚGKK dáta (posledná záchrana)"),
    "contour_maxzoom": ("14", "max zoom dlaždíc s vrstevnicami"),
    # Skaly majú od vrstevníc oddelený .pmtiles, takže aj vlastný maxzoom.
    # 16 je tvrdý strop Planetilera; vyššie zoomy rieši overzoom, takže sa
    # skaly zobrazujú do maximálneho zoomu mapy tak či tak – z vyššieho
    # maxzoomu je ostrejší tvar, nie väčší rozsah zoomov.
    "rock_maxzoom": ("16", "max zoom dlaždíc so skalami (strop Planetilera je 16)"),
    # Plné plochy: skala je jedna súvislá plocha bez dier a v jednej
    # triede. V mape sa kreslí jednou sivou bez priehľadnosti, takže by
    # sa každý prekryv a každá diera prejavili ako škvrna.
    "rock_plne": ("1", "1 = skaly ako plné plochy, 0 = s dierami a triedou cliff"),
    # Mriežka na obrys skál. Bol to samostatný input, ale strop je desať
    # a rýchly testovací beh (`test_km2`) sa mení pri každom ladení, kým
    # mriežku má zmysel prestaviť len s iným zdrojom výšok – `auto` ju vyberie
    # z bunky DEM a rozpočtu času a vypíše do logu, prečo práve tú.
    "rock_res": ("auto", "mriežka na obrys skál v metroch, alebo `auto`"),
    "contour_smoothing": ("0", "zjemnenie DEM v oblúkových sekundách"),
    "trails_maxzoom": ("14", "max zoom dlaždíc so značenými trasami"),
    "terrain_maxzoom": ("13", "max zoom výškových dlaždíc (jemnejšie 20 m DEM neunesie)"),
    # Značené trasy sú jediná vrstva bez výberu zdroja – berú sa z toho istého
    # PBF ako mapa, takže niet z čoho vyberať. Zapínač je preto tu a nie
    # štvrtý výber vo formulári, na ktorý už aj tak nie je miesto.
    "trails": ("true", "generovať značené trasy z OSM relácií"),
    # Ktorý asset s hotovými skalami z tieňovaných dlaždíc použiť (platí len
    # pri `rock_source: tienovanie`). Prázdne = najnovší pre daný výrez,
    # takže stačí pustiť ten workflow a potom build – nič sa neprepisuje.
    # Samotný `rock_source` je samostatný input, nie voľba: prepína celý
    # zdroj skál a to sa má dať vybrať vo formulári, nie napísať do textu.
    "rock_img_asset": ("", "presné meno assetu so skalami z tieňovania (prázdne = spočítať v tomto behu)"),
    # Ladenie pipeline, ktorú si build pri `rock_source: tienovanie` volá sám
    # (shading-rocks.yml). Prahy majú vlastné predvolené hodnoty tam; sem
    # patrí len to, čo mení cenu behu (zoom) a voľné prepínače skriptu.
    "rock_img_zoom": ("auto", "zoom dlaždíc tieňovania (auto = najvyšší, čo sa zmestí do stropu)"),
    "rock_img_options": ("", "prepínače pre výpočet skál z tieňovania, napr. \"fill=40 min_hole=5\""),
    # Bol to samostatný input, ale strop je desať a tri výbery zdroja sú
    # užitočnejšie: `maxzoom` je od začiatku 16 (tvrdý limit Planetilera)
    # a znižuje sa len pri ladení veľkosti.
    "maxzoom": ("16", "max zoom mapových dlaždíc – Planetiler zvládne najviac 16"),
    "custom_pbf_url": ("", "vlastný región – URL na .osm.pbf"),
    "custom_name": ("", "vlastný región – zobrazované meno"),
    "custom_bbox": ("", "vlastný región – bbox W,S,E,N"),
}

# Voľby, ktoré sa presťahovali medzi inputy (alebo naopak). Bez tohto by
# `rock_source=…` spadlo na „neznáma voľba" a zoznam známych kľúčov by
# nepovedal, kam sa podela – pritom je vo formulári o pár riadkov vyššie.
MOVED = {
    "rock_source": "je samostatný input vo formulári (výber zdroja skál), "
                   "nie voľba",
    "test_km2": "je samostatný input vo formulári (rýchly test na pár km², "
                "predvolene 4; ostrý beh je 0), nie voľba",
    "dem_source": "sa rozpadol na tri inputy vo formulári – `contour_source`, "
                  "`rock_source` a `shading_source`, každá vrstva má svoj "
                  "zdroj",
    "layers": "už nie je: vrstva sa zapína tým, že jej vo formulári vyberieš "
              "zdroj (`ziadne` = negenerovať). Trasy sa vypínajú voľbou "
              "`trails=false`",
    "rocks": "už nie je: skaly sa vypínajú výberom `rock_source: ziadne`",
}

# Hodnota vo výbere, ktorá vrstvu vypne. Slovom, nie prázdnym reťazcom –
# v rozbaľovacom zozname má byť vidieť, že „nič" je vedomá voľba.
NONE = "ziadne"

# Skaly majú okrem výškových modelov ešte jeden zdroj, ktorý DEM vôbec
# nečíta: hotové polygóny z workflowu „Skaly z tieňovaných dlaždíc".
ROCK_FROM_SHADING = "tienovanie"

# `rebuild` je jeden výber namiesto troch zaškrtávatiek – tri booleany boli
# tri inputy a limit je desať.
REBUILD = {
    "nic": (),
    "vrstevnice": ("contours_rebuild",),
    "skaly": ("rocks_rebuild",),
    "teren": ("terrain_rebuild",),
    "vsetko": ("contours_rebuild", "rocks_rebuild", "terrain_rebuild"),
}


def dem_sources(path=None):
    """Zdroje z workers/dem-sources.json → {kľúč: [pre ktoré vrstvy]}."""
    path = path or os.path.join(_HERE, "dem-sources.json")
    with open(path) as f:
        raw = json.load(f)
    return {k: v.get("for", []) for k, v in raw.items() if not k.startswith("_")}


def pick_source(what, value, allowed):
    """Skontroluje hodnotu jedného výberu zdroja; vráti ju, alebo None pri chybe."""
    value = (value or NONE).strip()
    if value in allowed:
        return value
    print(f"::error::Neznámy zdroj „{value}“ pre {what}. Známe: "
          f"{', '.join(allowed)}", file=sys.stderr)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--options", default="")
    ap.add_argument("--rebuild", default="nic")
    ap.add_argument("--contour-source", default=NONE,
                    help="zdroj výšok pre vrstevnice, alebo `ziadne`")
    ap.add_argument("--rock-source", default=NONE,
                    help="zdroj skál: výškový model, `tienovanie`, alebo `ziadne`")
    ap.add_argument("--shading-source", default=NONE,
                    help="zdroj výšok pre tieňovanie a 3D terén, alebo `ziadne`")
    ap.add_argument("--test-km2", default="0",
                    help="rýchly test na štvorci s toľkými km² (0 = ostrý beh)")
    ap.add_argument("--dem-sources", default="",
                    help="cesta k dem-sources.json (default vedľa skriptu)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    values = {k: v for k, (v, _) in DEFAULTS.items()}
    changed = {}

    # shlex, nie split(): hodnota môže byť v úvodzovkách, napr.
    # custom_name="Rakúsko juh"
    for token in shlex.split(args.options or ""):
        if "=" not in token:
            print(f"::error::Voľba „{token}“ nemá tvar kľúč=hodnota.", file=sys.stderr)
            return 1
        k, v = token.split("=", 1)
        k = k.strip()
        if k in MOVED:
            print(f"::error::„{k}“ {MOVED[k]}. Vymaž to z `options` "
                  f"a nastav vo formulári.", file=sys.stderr)
            return 1
        if k not in DEFAULTS:
            print(f"::error::Neznáma voľba „{k}“. Známe voľby: "
                  f"{', '.join(sorted(DEFAULTS))}", file=sys.stderr)
            return 1
        values[k] = v
        changed[k] = v

    # ---------- rýchly test na pár km² ----------
    # Číslo z formulára ide do mena cache aj do kľúča uložených výsledkov
    # (`…_test4`) a inde sa porovnáva s „0" ako s reťazcom, takže sa tu
    # normalizuje: prázdne pole je 0 a `4.0` aj `4` dajú to isté „4".
    # Nečíslo je chyba – `test_km2: štyri` by inak ticho spustilo ostrý beh
    # na celý kraj namiesto minútového testu.
    test_km2 = (args.test_km2 or "0").strip() or "0"
    try:
        n = float(test_km2)
    except ValueError:
        print(f"::error::test_km2 musí byť číslo (0 = ostrý beh na celý "
              f"výrez), nie „{test_km2}“.", file=sys.stderr)
        return 1
    if n < 0:
        print(f"::error::test_km2 nemôže byť záporné („{test_km2}“). "
              f"0 = ostrý beh na celý výrez.", file=sys.stderr)
        return 1
    values["test_km2"] = f"{n:g}"

    # ---------- tri výbery zdroja ----------
    # Čo sa smie kde vybrať, hovorí `for` v dem-sources.json – ten istý
    # zoznam, aký stráži `Lint workflows` proti výberom vo formulári.
    srcs = dem_sources(args.dem_sources or None)
    contour_src = pick_source(
        "vrstevnice (contour_source)", args.contour_source,
        [NONE] + [k for k, f in srcs.items() if "contours" in f])
    rock_src = pick_source(
        "skaly (rock_source)", args.rock_source,
        [NONE, ROCK_FROM_SHADING] + [k for k, f in srcs.items() if "rocks" in f])
    shading_src = pick_source(
        "tieňovanie (shading_source)", args.shading_source,
        [NONE] + [k for k, f in srcs.items() if "shading" in f])
    if contour_src is None or rock_src is None or shading_src is None:
        return 1

    values["contour_source"] = contour_src
    values["rock_source"] = rock_src
    values["shading_source"] = shading_src
    # Zdroj skál, ktorý je výškový model – teda ten, z ktorého sa má počítať
    # sklon. Pri `tienovanie` a `ziadne` je prázdny a nikto nesmie sťahovať DEM.
    values["rock_dem"] = rock_src if rock_src in srcs else ""

    values["contour_lines"] = "true" if contour_src != NONE else "false"
    values["rocks"] = "true" if rock_src != NONE else "false"
    values["terrain"] = "true" if shading_src != NONE else "false"
    # `contours` je brána celého jobu, nie vrstva: vrstevnice aj skaly z neho
    # vychádzajú do jedného .pmtiles, takže beží aj vtedy, keď sú zapnuté len
    # skaly (a naopak).
    values["contours"] = ("true" if contour_src != NONE or rock_src != NONE
                          else "false")
    # Trasy nemajú výber zdroja – zapínajú sa voľbou, ktorá už je v DEFAULTS.
    # Čokoľvek iné než true/false je chyba: `trails=1` by inak trasy ticho
    # vyplo a zistilo by sa to až tým, že v mape nie sú.
    if values["trails"] not in ("true", "false"):
        print(f"::error::Voľba „trails“ musí byť true alebo false, "
              f"nie „{values['trails']}“.", file=sys.stderr)
        return 1

    if args.rebuild not in REBUILD:
        print(f"::error::Neznáme rebuild „{args.rebuild}“. Známe: "
              f"{', '.join(REBUILD)}", file=sys.stderr)
        return 1
    for flag in ("contours_rebuild", "rocks_rebuild", "terrain_rebuild"):
        values[flag] = "true" if flag in REBUILD[args.rebuild] else "false"

    lines = [f"opt_{k}={v}" for k, v in values.items()]
    if args.out:
        with open(args.out, "a") as f:
            f.write("\n".join(lines) + "\n")

    print("Nastavenia:")
    for k in sorted(values):
        mark = "  ←" if k in changed else ""
        d = DEFAULTS.get(k, ("", "z inputov formulára (zdroje / rebuild / test)"))[1]
        print(f"  {k:<20} {values[k] or '(prázdne)':<24} {d}{mark}")
    if changed:
        print(f"\nZmenené oproti predvolenému: {', '.join(sorted(changed))}")
    if args.rebuild != "nic":
        print(f"Pregenerovať: {args.rebuild}")
    print(f"\nVrstevnice: {contour_src}   Skaly: {rock_src}   "
          f"Tieňovanie: {shading_src}   Trasy: {values['trails']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Rozloží voľné `kľúč=hodnota` z inputu `options` na jednotlivé nastavenia.

PREČO: `workflow_dispatch` dovolí **najviac 10 inputov**. Mali sme ich 26,
a workflow sa preto prestal načítať – beh skončil ako „failure" s nula jobmi,
lebo GitHub ten súbor ani neprijal. Deväť najpoužívanejších vecí ostalo
samostatnými inputmi, zvyšok sa píše do jedného poľa:

    rock_slope=55 rock_res=1 contour_interval=5

Nie je to len obchádzka limitu: formulár s 26 poľami sa aj tak nedal použiť.
Takto sú v ňom veci, ktoré meníš pri každom behu, a ostatné majú rozumné
predvolené hodnoty.

Neznámy kľúč je chyba, nie ticho ignorovaná hodnota – preklep v `rock_slop=55`
by inak znamenal, že sa celý beh spustí s iným nastavením, než si myslíš.

Použitie:
    python3 workers/parse-options.py --options="rock_slope=55" \\
        --rebuild=skaly --out=$GITHUB_OUTPUT
"""
import argparse
import shlex
import sys

# kľúč: (predvolená hodnota, popis)
DEFAULTS = {
    "crop_bbox": ("", "orezať región na west,south,east,north"),
    "area_bbox": ("", "vlastný výrez W,S,E,N namiesto pohoria z výberu"),
    "size_limit_mb": ("900", "rozpočet celej stránky v MB"),
    "auto_shrink": ("true", "znížiť zoom dlaždíc, keď sa nezmestia"),
    "ugkk_fallback": ("true", "keď 1 m LiDAR nie je, počítať zo Sonnyho"),
    "ugkk_urls": ("", "priame URL na ÚGKK dáta (posledná záchrana)"),
    "contour_maxzoom": ("14", "max zoom dlaždíc s vrstevnicami"),
    "contour_smoothing": ("0", "zjemnenie DEM v oblúkových sekundách"),
    "trails_maxzoom": ("14", "max zoom dlaždíc so značenými trasami"),
    "terrain_maxzoom": ("13", "max zoom výškových dlaždíc (jemnejšie 20 m DEM neunesie)"),
    "rocks": ("true", "počítať skalné plochy"),
    # Ktorý asset s hotovými skalami z tieňovaných dlaždíc použiť (platí len
    # pri `rock_source: lidar_images`). Prázdne = najnovší pre daný výrez,
    # takže stačí pustiť ten workflow a potom build – nič sa neprepisuje.
    # Samotný `rock_source` je samostatný input, nie voľba: prepína celý
    # zdroj skál a to sa má dať vybrať vo formulári, nie napísať do textu.
    "rock_img_asset": ("", "presné meno assetu so skalami z tieňovania (prázdne = najnovší pre výrez)"),
    # Bol to samostatný input, ale strop je desať a `rock_source` je
    # užitočnejší: `maxzoom` je od začiatku 16 (tvrdý limit Planetilera)
    # a znižuje sa len pri ladení veľkosti.
    "maxzoom": ("16", "max zoom mapových dlaždíc – Planetiler zvládne najviac 16"),
    "custom_pbf_url": ("", "vlastný región – URL na .osm.pbf"),
    "custom_name": ("", "vlastný región – zobrazované meno"),
    "custom_bbox": ("", "vlastný región – bbox W,S,E,N"),
}

# Voľby, ktoré sa presťahovali medzi inputy. Bez tohto by `rock_source=…`
# spadlo na „neznáma voľba" a zoznam známych kľúčov by nepovedal, kam sa
# podela – pritom je vo formulári o pár riadkov vyššie.
MOVED = {
    "rock_source": "je teraz samostatný input vo formulári "
                   "(výber „dem“ / „lidar_images“), nie voľba",
}

# Čo sa má generovať. Tri zaškrtávatka by boli tri inputy z desiatich, takže
# je to jedno pole – ale s čitateľnými menami, nie ako skryté kľúče.
LAYERS = ("contours", "terrain", "trails")

# `rebuild` je jeden výber namiesto troch zaškrtávatiek – tri booleany boli
# tri inputy a limit je desať.
REBUILD = {
    "nic": (),
    "vrstevnice": ("contours_rebuild",),
    "skaly": ("rocks_rebuild",),
    "teren": ("terrain_rebuild",),
    "vsetko": ("contours_rebuild", "rocks_rebuild", "terrain_rebuild"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--options", default="")
    ap.add_argument("--rebuild", default="nic")
    ap.add_argument("--layers", default=",".join(LAYERS),
                    help="čo generovať, oddelené čiarkou")
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

    want = {x.strip().lower() for x in args.layers.split(",") if x.strip()}
    unknown = want - set(LAYERS)
    if unknown:
        print(f"::error::Neznáme vrstvy: {', '.join(sorted(unknown))}. "
              f"Známe: {', '.join(LAYERS)}", file=sys.stderr)
        return 1
    for lay in LAYERS:
        values[lay] = "true" if lay in want else "false"

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
        d = DEFAULTS.get(k, ("", "z rebuild / layers"))[1]
        print(f"  {k:<20} {values[k] or '(prázdne)':<24} {d}{mark}")
    if changed:
        print(f"\nZmenené oproti predvolenému: {', '.join(sorted(changed))}")
    if args.rebuild != "nic":
        print(f"Pregenerovať: {args.rebuild}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

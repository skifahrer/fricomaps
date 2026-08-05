#!/usr/bin/env python3
"""
Vyrieši input `area` na bbox, kľúč a meno – na jednom mieste.

Potrebuje to `plan` (aby vedel, čo sa má zrkadliť), `check-dem` (aby vedel,
čo hľadať v releasi), `contours` (aby vedel, čo počítať) aj mirror ÚGKK.
Kým to bolo napísané v shelli vnútri jedného kroku, nedalo sa to zdieľať.

Vstup je buď názov pohoria z workers/areas.json, alebo bbox `W,S,E,N`,
alebo prázdno (= celý región). Vždy sa pretne s bboxom regiónu: mimo neho
nie sú ani dáta, ani mapa.

Použitie:
    python3 workers/resolve-area.py --region-bbox=W,S,E,N --area=vysoke_tatry
    → key=…, name=…, bbox=…, km2=… na stdout vo formáte key=value
"""
import argparse
import json
import math
import os
import re
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region-bbox", required=True)
    ap.add_argument("--area", default="")
    ap.add_argument("--areas", default="workers/areas.json")
    ap.add_argument("--out", default="", help="kam zapísať (default stdout)")
    args = ap.parse_args()

    region = [float(v) for v in args.region_bbox.split(",")]
    raw = (args.area or "").strip()
    # Vo formulári sa „celý región" nedá vyjadriť prázdnou položkou výberu,
    # tak má vlastný názov. Tu je to to isté ako prázdno.
    if raw == "cely_region":
        raw = ""

    if not raw:
        key, name, bbox = "cely", "celý región", region
    elif "," in raw:
        key, name = "vyrez", f"vlastný výrez {raw}"
        bbox = [float(v) for v in raw.split(",")]
    else:
        areas = json.load(open(args.areas))
        if raw not in areas or raw.startswith("_"):
            known = ", ".join(k for k in areas if not k.startswith("_"))
            print(f"::error::Neznámy výrez '{raw}'. Známe výrezy "
                  f"({args.areas}): {known}. Alebo zadaj bbox W,S,E,N.",
                  file=sys.stderr)
            return 1
        key = re.sub(r"[^a-zA-Z0-9]", "_", raw)
        name = areas[raw]["name"]
        bbox = areas[raw]["bbox"]

    # Prienik s regiónom – mimo neho nie sú ani dáta, ani mapa.
    w, s = max(region[0], bbox[0]), max(region[1], bbox[1])
    e, n = min(region[2], bbox[2]), min(region[3], bbox[3])
    if e <= w or n <= s:
        print(f"::error::Výrez '{raw}' neleží v regióne ({args.region_bbox}) – "
              f"neprekrývajú sa. Vyber iný región alebo iný výrez.",
              file=sys.stderr)
        return 1

    km2 = (e - w) * 111.32 * math.cos(math.radians((s + n) / 2)) * (n - s) * 110.54
    out = [f"key={key}", f"name={name}", f"bbox={w},{s},{e},{n}",
           f"km2={km2:.0f}", f"cells_1m={km2 * 1e6:.0f}"]
    text = "\n".join(out) + "\n"
    if args.out:
        with open(args.out, "a") as f:
            f.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

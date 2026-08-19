#!/usr/bin/env python3
"""
Podoba mapy sveta: čo z `world.yml` sa naozaj postaví.

Odpovedá na jednu otázku – „čo je v tejto podobe" – a odpovedá na ňu raz.
Číselník je `workers/data/world-variants.json`, tu sa z neho skladá:

  * SCHÉMA pre Planetiler. `world.yml` ostáva jediný popis toho, čo sa
    z podkladov vyrába; táto podoba z neho len VYBERIE vrstvy a zapíše kópiu,
    ktorú Planetiler dostane. Druhá schéma vedľa by bola druhá pravda o tých
    istých vrstvách a raz by sa rozišli (pravidlo 1 v CLAUDE.md).
  * ZDROJE, ktoré treba stiahnuť. Vypadnú z toho, na čo sa vybrané vrstvy
    odkazujú – takže `basic` nesťahuje 60 MB vodných polygónov ani ich
    niekoľkominútový prevod, a nemusí to nikde stáť napísané druhýkrát.
  * MENO A ROZPOČET: kľúč regiónu (`svet` / `svet_basic`), `MAP_LAYERS` do
    `obsah.json` a `maps.json`, strop veľkosti.

Použitie:
    python3 workers/world/variant.py --variant=basic \\
        --schema-out=/tmp/world-basic.yml --out=/tmp/variant.json
    python3 workers/world/variant.py --list        # čo sa dá postaviť
"""
import argparse
import json
import os
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKERS = os.path.dirname(_HERE)
_DATA = os.path.join(_WORKERS, "data")
SCHEMA = os.path.join(_HERE, "world.yml")
VARIANTS = os.path.join(_DATA, "world-variants.json")


def nacitaj(path=VARIANTS):
    """Číselník podôb bez metadát (kľúče s `_` sú komentár a číselníky)."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return raw


def podoby(raw=None):
    raw = raw if raw is not None else nacitaj()
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def vyber(nazov, raw=None):
    """Podoba podľa mena; neznáme meno je chyba, nie ticho tá predvolená."""
    raw = raw if raw is not None else nacitaj()
    p = podoby(raw)
    if nazov not in p:
        raise SystemExit(
            f"::error::Podobu mapy sveta „{nazov}“ nepoznám. Sú: "
            f"{', '.join(sorted(p))} (zoznam je vo `workers/data/"
            f"world-variants.json`).")
    return p[nazov]


def schema_pre(vrstvy, doc=None):
    """`world.yml` orezaný na vybrané vrstvy – aj so zdrojmi, ktoré ostali.

    Vrstva, ktorú číselník žiada a schéma nemá, je chyba: znamenalo by to, že
    sa vrstva zo schémy vyhodila alebo premenovala a podoba o tom nevie –
    a Planetiler by ticho postavil mapu bez nej.
    """
    doc = doc if doc is not None else yaml.safe_load(open(SCHEMA, encoding="utf-8"))
    maju = {l["id"] for l in doc.get("layers") or []}
    chyba = [v for v in vrstvy if v not in maju]
    if chyba:
        raise SystemExit(
            f"::error::Podoba pýta vrstvy {chyba}, ktoré `workers/world/"
            f"world.yml` nemá (má {sorted(maju)}). Zrovnaj `layers` "
            f"vo `workers/data/world-variants.json` so schémou.")
    out = dict(doc)
    out["layers"] = [l for l in doc["layers"] if l["id"] in vrstvy]
    # Zdroj, na ktorý sa už nikto neodkazuje, ide preč – a práve o to ide:
    # zo `sources` vypadne to, čo netreba ani sťahovať.
    treba = {f.get("source") for l in out["layers"]
             for f in (l.get("features") or [])}
    out["sources"] = {k: v for k, v in (doc.get("sources") or {}).items()
                      if k in treba}
    return out, sorted(s for s in treba if s)


def map_layers(vrstvy, raw=None):
    """Slovenské mená vrstiev do `obsah.json` a `maps.json`.

    Skladá sa z `_nazvy`, nie z druhého zoznamu pri každej podobe: inak by raz
    pribudla vrstva a katalóg by o nej mlčal.
    """
    raw = raw if raw is not None else nacitaj()
    nazvy = raw.get("_nazvy") or {}
    # Poradie je poradie v `_nazvy`, NIE v schéme: zoznam vrstiev ide do
    # `maps.json` a preskladať ho pri každej zmene schémy by znamenalo zmenu
    # v katalógu, za ktorou nič nie je.
    out = []
    for vrstva, mena in nazvy.items():
        if vrstva.startswith("_") or vrstva not in vrstvy:
            continue
        for meno in mena or []:
            if meno not in out:
                out.append(meno)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="plna")
    ap.add_argument("--schema-out", default="",
                    help="kam zapísať schému pre Planetiler")
    ap.add_argument("--out", default="",
                    help="kam zapísať JSON s hodnotami pre build.sh")
    ap.add_argument("--list", action="store_true", help="vypíš podoby a skonči")
    args = ap.parse_args()

    raw = nacitaj()
    if args.list:
        for k, v in sorted(podoby(raw).items()):
            print(f"{k:<8} {v.get('label', '')}")
        return 0

    p = vyber(args.variant, raw)
    vrstvy = p["layers"]
    doc, zdroje = schema_pre(vrstvy)
    hodnoty = {
        "variant": args.variant,
        "label": p.get("label", args.variant),
        "region": p["region"],
        "layers": vrstvy,
        "sources": zdroje,
        "map_layers": ",".join(map_layers(vrstvy, raw)),
        "limit_mb": int(p.get("limit_mb", 250)),
        "glyphs": p.get("glyphs", "cele"),
    }

    if args.schema_out:
        with open(args.schema_out, "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(hodnoty, f, ensure_ascii=False, indent=2)

    vsetky = {l["id"] for l in yaml.safe_load(
        open(SCHEMA, encoding="utf-8"))["layers"]}
    vynechane = sorted(vsetky - set(vrstvy))
    print(f"Podoba `{args.variant}`: {hodnoty['label']}")
    print(f"  vrstvy    {', '.join(vrstvy)}"
          + (f"   (vynechané: {', '.join(vynechane)})" if vynechane else ""))
    print(f"  zdroje    {', '.join(zdroje)}")
    print(f"  región    {hodnoty['region']}   "
          f"vrstvy v katalógu: {hodnoty['map_layers'] or '(žiadne)'}")
    print(f"  strop     {hodnoty['limit_mb']} MB     fonty: {hodnoty['glyphs']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

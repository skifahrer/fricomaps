#!/usr/bin/env python3
"""
Blok „Nastavenia tohto behu" do súhrnu behu.

PREČO: formulár *Run workflow* sa vždy otvorí s predvolenými hodnotami –
GitHub si nepamätá, s čím si beh spustil naposledy, a ani to nevie: hodnoty
z minulého behu nie sú nikde v API. Keď teda chceš beh zopakovať a zmeniť
jedinú vec (typicky `rebuild`), ostatné polia musíš nastaviť znova – a nemáš
ich odkiaľ odpísať, lebo v Actions ich vidno len ako rozklikávací detail
behu. Toto je ten zoznam, na jednom mieste a na skopírovanie.

Predvolené hodnoty sa čítajú z workflowu, nie sú tu napísané druhýkrát –
inak by sa raz rozišli a súhrn by tvrdil, že si nič nemenil. Čo je iné než
default, je označené: presne tie polia treba pri opakovaní prekliknúť.

Použitie:
    python3 workers/summary-inputs.py \\
        --inputs="$INPUTS_JSON" --workflow=.github/workflows/build-map.yml
"""
import argparse
import json
import sys

import yaml


def defaults(path):
    """Predvolené hodnoty inputov priamo z workflowu → {pole: hodnota}."""
    with open(path) as f:
        doc = yaml.safe_load(f)
    # `on:` YAML načíta ako True (je to booleanovské kľúčové slovo), takže
    # sa kľúč hľadá oboma spôsobmi – rovnako to robí aj Lint workflows.
    on = doc[[k for k in doc if k is True or k == "on"][0]]
    inputs = (on.get("workflow_dispatch") or {}).get("inputs") or {}
    return {k: text(v.get("default", "")) for k, v in inputs.items()}


def text(v):
    """Hodnota na text tak, ako ju vidí beh.

    `type: boolean` je v YAMLe naozajstný boolean, takže by z neho Python
    spravil „True" – ale v `inputs` behu je to reťazec „true". Bez tohto by
    každý switch v tabuľke svietil ako zmenený, hoci je na predvolenej
    hodnote.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", default="",
                    help="JSON s hodnotami inputov behu (toJSON(inputs))")
    ap.add_argument("--workflow", default=".github/workflows/build-map.yml")
    args = ap.parse_args()

    try:
        values = json.loads(args.inputs or "{}")
    except json.JSONDecodeError as e:
        print(f"::warning::Nastavenia behu sa nepodarilo prečítať ({e}).",
              file=sys.stderr)
        return 0
    if not values:
        return 0

    try:
        deflt = defaults(args.workflow)
    except (OSError, KeyError, TypeError) as e:
        print(f"::warning::Predvolené hodnoty sa nepodarilo prečítať ({e}).",
              file=sys.stderr)
        deflt = {}

    riadky, zmenene = [], []
    for k, v in values.items():
        v = text(v)
        d = deflt.get(k)
        if d is None:
            # Default sa nepodarilo prečítať – povedať „default" by bola
            # výmysel; hodnota samotná je aj tak to hlavné, čo tu treba.
            stav = "—"
        elif v == d:
            stav = "default"
        else:
            stav = "**iné než default**"
            zmenene.append(k)
        riadky.append("| `{}` | {} | {} |".format(
            k, f"`{v}`" if v else "*(prázdne)*", stav))

    out = ["## Nastavenia tohto behu", "",
           "| pole | hodnota | |", "|---|---|---|"] + riadky + [""]
    if zmenene:
        out += ["Formulár *Run workflow* sa vždy otvorí s predvolenými "
                "hodnotami, takže pri opakovaní behu treba znova nastaviť: "
                + ", ".join(f"**{k}**" for k in zmenene) + ".", ""]
    elif deflt:
        out += ["Všetko na predvolených hodnotách – taký beh sa opakuje "
                "samotným *Run workflow*, nič netreba prekliknúť.", ""]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

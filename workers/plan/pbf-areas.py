#!/usr/bin/env python3
"""
Koľko plôch Planetiler z tohto PBF zahodí CELÝCH – a ktoré to sú.

PREČO TO EXISTUJE. Viacpolygónová plocha (`type=multipolygon` alebo
`type=boundary`) je v OSM relácia a jej tvar skladajú členské cesty. Keď
v súbore niektorá z nich CHÝBA, Planetiler prstence nezavrie a objekt zahodí
CELÝ – nie tú časť, čo vytŕča, ale celý. V mape tým zmizne CHKO aj s tou
polovicou, ktorá v kraji leží, a nie je to na čom spozorovať: build je zelený,
dlaždice vzniknú, len je v nich o les menej. Presne toto sa dialo, kým sa kraj
sťahoval ako hotový `{kraj}-latest.osm.pbf` z osm.fr (rozpis v hlavičke
`workers/plan/pbf.sh`).

Odkedy sa kraj reže z rodičovského extraktu, ich má byť pár – a musí byť
VIDIEŤ koľko, lebo tento druh chyby sa inak neohlási. Číslo ide do logu aj do
súhrnu behu; keď medzi obeťami je POMENOVANÁ plocha, píše sa `::warning::` aj
s menom, nech sa dá porovnať s mapou.

NIE JE TO TVRDÁ CHYBA a je to zámer: na hranici so zahraničím ostanú plochy,
ktorých členovia nie sú v slovenskom extrakte vôbec (Dunaj, Nationalpark
Donau-Auen) a doplniť by ich vedel až extrakt Európy. Pád na nich by znamenal,
že sa Bratislavský kraj nedá postaviť.

ČÍTA SA IBA `osmium`, BEZ pyosmium: dve volania, spolu ~1 s na 37 MB PBF
(namerané). `tags-filter -R` vytiahne samotné relácie (204 kB) a `cat -t way`
dá zoznam id ciest, ktoré v súbore SÚ. Doinštalovať kvôli tomuto knižnicu do
jobu na kritickej ceste by sa nezaplatilo.

Použitie:
    python3 workers/plan/pbf-areas.py data/region.osm.pbf
    python3 workers/plan/pbf-areas.py data/region.osm.pbf --summary=$GITHUB_STEP_SUMMARY
"""
import argparse
import re
import subprocess
import sys
import tempfile

# Čím je plocha plochou NA MAPE. Relácií `type=boundary` sú stovky (kataster,
# farnosti, štatistické oblasti) a tie v mape aj tak nie sú – zaujímajú nás
# tie, ktoré kreslí štýl: krajinná pokrývka, voda a ochrana prírody.
AREA_KEYS = ("landuse", "natural", "leisure", "boundary", "waterway", "place")
VIDITELNE = {
    "forest", "wood", "scrub", "heath", "grass", "grassland", "meadow",
    "farmland", "orchard", "vineyard", "water", "wetland", "bay", "reservoir",
    "park", "garden", "nature_reserve", "protected_area", "national_park",
    "military", "quarry", "residential", "industrial", "golf_course", "pitch",
}

_ESC = re.compile(r"%([0-9a-fA-F]+)%")


def unesc(text):
    """OPL escapuje medzeru, čiarku a spol. ako `%20%` – späť na znak."""
    return _ESC.sub(lambda m: chr(int(m.group(1), 16)), text)


def opl_fields(riadok):
    """Riadok OPL → `{písmeno: zvyšok}`. Medzery v hodnotách sú escapované."""
    out = {}
    for pole in riadok.split(" "):
        if pole:
            out[pole[0]] = pole[1:]
    return out


def tags(pole):
    out = {}
    if not pole:
        return out
    for kus in pole.split(","):
        k, _, v = kus.partition("=")
        out[unesc(k)] = unesc(v)
    return out


def way_ids(pbf):
    """Id ciest, ktoré v súbore NAOZAJ sú."""
    ids = set()
    p = subprocess.Popen(["osmium", "cat", "-t", "way", "-f", "opl", pbf],
                         stdout=subprocess.PIPE, text=True)
    for riadok in p.stdout:
        medzera = riadok.find(" ")
        ids.add(int(riadok[1:medzera if medzera > 0 else None]))
    if p.wait() != 0:
        raise SystemExit(f"::error::`osmium cat` nad {pbf} zlyhal.")
    return ids


def rozbite(pbf):
    """`[(chýba, členov, id, druh, meno)]` pre plochy, ktoré Planetiler zahodí."""
    with tempfile.NamedTemporaryFile(suffix=".osm.pbf") as tmp:
        subprocess.run(
            ["osmium", "tags-filter", "-R", "--overwrite", "-o", tmp.name, pbf,
             "r/type=multipolygon", "r/type=boundary"],
            check=True, stdout=subprocess.DEVNULL)
        opl = subprocess.run(["osmium", "cat", "-f", "opl", tmp.name],
                             check=True, capture_output=True, text=True).stdout

    su = way_ids(pbf)
    von = []
    for riadok in opl.splitlines():
        if not riadok.startswith("r"):
            continue
        f = opl_fields(riadok)
        t = tags(f.get("T", ""))
        if t.get("type") not in ("multipolygon", "boundary"):
            continue
        druh = next((t[k] for k in AREA_KEYS if k in t), "")
        if druh not in VIDITELNE:
            continue
        cesty = [int(m[1:].split("@")[0]) for m in f.get("M", "").split(",")
                 if m.startswith("w")]
        if not cesty:
            continue
        chyba = [i for i in cesty if i not in su]
        if chyba:
            von.append((len(chyba), len(cesty), riadok.split(" ")[0],
                        druh, t.get("name", "")))
    von.sort(reverse=True)
    return von


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf")
    ap.add_argument("--summary", default="", help="kam pripísať riadok do súhrnu behu")
    ap.add_argument("--top", type=int, default=10, help="koľko obetí vypísať")
    args = ap.parse_args()

    von = rozbite(args.pbf)
    pomenovane = [v for v in von if v[4]]

    if not von:
        print("Plochy, ktoré by Planetiler zahodil celé: ŽIADNE ✓")
    else:
        print(f"Plochy, ktoré Planetiler zahodí CELÉ (chýba im člen): {len(von)}")
        for chyba, clenov, rid, druh, meno in von[:args.top]:
            print(f"  {rid:<12} {druh:16} chýba {chyba:4}/{clenov:<5} "
                  f"{meno or '(bez mena)'}")
        if len(von) > args.top:
            print(f"  … a ďalších {len(von) - args.top}")

    if pomenovane:
        mena = ", ".join(m for *_, m in pomenovane[:5] if m)
        print(f"::warning::V mape nebudú tieto plochy, a to CELÉ – nemajú "
              f"v PBF všetkých členov, takže ich Planetiler zahodí aj s tou "
              f"časťou, ktorá v mape leží (pomenovaných {len(pomenovane)}): "
              f"{mena}. Čakané sú tie, ktorých členovia nie sú ani "
              f"v rodičovskom extrakte, teda presahujúce za hranicu ŠTÁTU "
              f"(doplnil by ich až extrakt Európy). Čokoľvek iné znamená, že "
              f"rez v `workers/plan/pbf.sh` nedopĺňa členov – skontroluj "
              f"`-s smart -S types=multipolygon,boundary`.")

    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(f"\n**Plochy zahodené pre chýbajúcich členov:** {len(von)}"
                    f" (pomenovaných {len(pomenovane)})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

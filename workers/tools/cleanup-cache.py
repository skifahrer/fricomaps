#!/usr/bin/env python3
"""
Upratovanie GitHub cache: zmaže VŠETKY záznamy, lebo sa už nepoužívajú.

PREČO TO EXISTUJE. GitHubová cache má na repozitár strop **10 GB** a keď sa
naplní, nič nepovie – ticho vyhodí najstaršie záznamy (LRU). Táto pipeline do
nej ukladala DEM dlaždice, sklad častí sklonu, vrstevnice, tieňovanie
a dlaždice tieňovania, čo sú na jeden výrez desiatky GB. Výsledok bol, že si
záznamy vyhadzovali navzájom a hodinové výpočty sa rátali odznova bez toho,
aby bolo na čom to vidieť.

Odteraz cache leží na Google Drive (`workers/drive/cache.py`), takže tá
GitHubová je len zvyšok, ktorý zaberá miesto. Tento skript ju vyprázdni.

PREČO TO NEJDE INAK: cache nie je súbor v repozitári, takže sa nedá zmazať
pull requestom. `gh cache delete` mimo Actions síce ide, ale s tokenom, ktorý
na to má právo – vnútri behu ho `GITHUB_TOKEN` má, keď mu workflow dá
`actions: write`. Preto je to workflow.

Beží ako `workers/tools/cleanup-cache.py`; čo robiť, hovorí prostredie:
    DRY_RUN=true | false           (default: false)
    KEEP=<predpona>                nemazať kľúče s touto predponou (default: nič)
Očakáva `gh` a GITHUB_REPOSITORY od runnera.
"""
import json
import os
import subprocess
import sys

REPO = os.environ["GITHUB_REPOSITORY"]
DRY = os.environ.get("DRY_RUN", "false").lower() == "true"
KEEP = os.environ.get("KEEP", "").strip()
SUMMARY = os.environ.get("GITHUB_STEP_SUMMARY", "")


def human(n):
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def gh(path, method=None):
    """Jedno volanie API. Vráti rozparsovaný JSON, alebo None pri chybe."""
    cmd = ["gh", "api", "-H", "Accept: application/vnd.github+json"]
    if method:
        cmd += ["-X", method]
    cmd.append(path)
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        # Chyba jedného volania nemá zhodiť celé upratovanie – vypíše sa
        # a ide sa ďalej. Pri mazaní je 404 dokonca v poriadku (už je preč).
        print(f"::warning::{method or 'GET'} {path}: {p.stderr.strip()[:160]}")
        return None
    return json.loads(p.stdout) if p.stdout.strip() else {}


def pages(path, key, cap=50):
    """Postránkovo stiahne zoznam; `cap` je poistka proti nekonečnu.

    Mazanie počas listovania posúva stránky pod rukami, preto sa NAJPRV
    načíta celý zoznam a až potom sa maže.
    """
    out, page = [], 1
    sep = "&" if "?" in path else "?"
    while page <= cap:
        d = gh(f"{path}{sep}per_page=100&page={page}")
        if not d:
            break
        items = d[key] if isinstance(d, dict) else d
        out += items
        if len(items) < 100:
            break
        page += 1
    return out


def usage():
    """Koľko cache repozitár práve drží (podľa GitHubu, nie podľa nášho súčtu)."""
    d = gh(f"/repos/{REPO}/actions/cache/usage") or {}
    return (int(d.get("active_caches_size_in_bytes") or 0),
            int(d.get("active_caches_count") or 0))


def main():
    print(f"Repozitár: {REPO}   "
          f"{'LEN VÝPIS (nič sa nemaže)' if DRY else 'ostro'}"
          + (f"   nechávam kľúče s predponou `{KEEP}`" if KEEP else ""))

    pred_b, pred_n = usage()
    print(f"\nGitHub cache pred: {pred_n} záznamov, {human(pred_b)} "
          f"(strop na repozitár je 10 GB)")

    caches = pages(f"/repos/{REPO}/actions/caches", "actions_caches")
    smeti = [c for c in caches if not (KEEP and c["key"].startswith(KEEP))]
    nechane = len(caches) - len(smeti)
    velkost = sum(int(c.get("size_in_bytes") or 0) for c in smeti)

    print(f"\nZáznamov na zmazanie: {len(smeti)} z {len(caches)}, "
          f"{human(velkost)}")
    for c in sorted(smeti, key=lambda c: -int(c.get("size_in_bytes") or 0)):
        print(f"  {human(int(c.get('size_in_bytes') or 0)):>9}  "
              f"{(c.get('last_accessed_at') or '')[:19]}  {c['key']}")
    if nechane:
        print(f"  (nechávam {nechane} záznamov s predponou `{KEEP}`)")

    zmazane = 0
    if not DRY:
        for c in smeti:
            if gh(f"/repos/{REPO}/actions/caches/{c['id']}",
                  method="DELETE") is not None:
                zmazane += 1
            else:
                print(f"::warning::záznam `{c['key']}` sa nepodarilo zmazať")
        po_b, po_n = usage()
        print(f"\nZmazaných: {zmazane} z {len(smeti)}. "
              f"GitHub cache po: {po_n} záznamov, {human(po_b)}")

    if SUMMARY:
        with open(SUMMARY, "a") as f:
            f.write("### GitHub cache\n\n")
            f.write("Len výpis, nič sa nemazalo.\n\n" if DRY else "")
            f.write("| vec | hodnota |\n|---|--:|\n")
            f.write(f"| záznamov pred | {pred_n} |\n")
            f.write(f"| veľkosť pred | {human(pred_b)} (strop 10 GB) |\n")
            f.write(f"| {'na zmazanie' if DRY else 'zmazaných'} | "
                    f"{len(smeti) if DRY else zmazane} |\n")
            f.write(f"| {'uvoľnilo by sa' if DRY else 'uvoľnené'} | "
                    f"{human(velkost)} |\n")
            if smeti:
                f.write("\n<details><summary>Zoznam záznamov</summary>\n\n")
                for c in sorted(smeti,
                                key=lambda c: -int(c.get("size_in_bytes") or 0)):
                    f.write(f"- `{c['key']}` – "
                            f"{human(int(c.get('size_in_bytes') or 0))}\n")
                f.write("\n</details>\n")
            f.write("\nBuild si cache berie z Google Drive "
                    "(`workers/drive/cache.py`), takže tieto záznamy už nikto "
                    "nehľadá.\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

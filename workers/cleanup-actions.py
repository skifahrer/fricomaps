#!/usr/bin/env python3
"""
Upratovanie záložky Actions: zmaže behy, ktoré nie sú záznamom o ničom.

PREČO TO NEJDE INAK: behy a vetvy nie sú súbory v repozitári, takže sa
nedajú zmazať pull requestom ani z lokálu – token mimo Actions na to nemá
právo (`Resource not accessible by integration`, HTTP 403). Vnútri behu ho
ale `GITHUB_TOKEN` má, keď mu workflow dá `actions: write` (behy) a
`contents: write` (vetvy). Preto je to workflow, ktorý sa spustí ručne.

ČO SA POVAŽUJE ZA SMETI:

1. **Behy zrušených workflowov.** Keď sa súbor workflowu zmaže, jeho behy
   ostanú a s nimi aj položka v ľavom zozname Actions – navždy. Zmizne až
   vtedy, keď má nula behov. Sem patria sondy `zz-*`, ktorými sa hľadalo,
   prečo GitHub odmietal `build-map.yml`, aj staršie zrušené workflowy.

2. **Behy odmietnutých súborov.** Keď je súbor workflowu neplatný (napr. nad
   stropom 128 KiB), GitHub to neohlási ako chybu – pri pushi vyrobí beh
   BEZ JOBOV, pomenovaný cestou k súboru. Vyzerá to, že sa workflow spustil
   sám po mergi, hoci má len `workflow_dispatch`. Spoznať sa dajú presne
   podľa toho mena: `.github/workflows/nieco.yml` namiesto `name:` z obsahu.

3. (voliteľne) **Vetvy `claude/*`, ktoré sú celé v hlavnej vetve.** Teda tie,
   ktorých práca je zmergovaná a nemajú oproti nej ani jeden vlastný commit.

Beží ako `workers/cleanup-actions.py`; čo robiť, hovorí prostredie:
    MODE=behy | behy_a_vetvy       (default: behy)
    DRY_RUN=true | false           (default: false)
Očakáva `gh` a GITHUB_REPOSITORY / GITHUB_RUN_ID od runnera.
"""
import json
import os
import subprocess
import sys

REPO = os.environ["GITHUB_REPOSITORY"]
MODE = os.environ.get("MODE", "behy")
DRY = os.environ.get("DRY_RUN", "false").lower() == "true"
SELF_RUN = os.environ.get("GITHUB_RUN_ID", "")
SUMMARY = os.environ.get("GITHUB_STEP_SUMMARY", "")


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


def pages(path, key, cap=20):
    """Postránkovo stiahne zoznam; `cap` je poistka proti nekonečnu."""
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


def main():
    print(f"Repozitár: {REPO}   režim: {MODE}   "
          f"{'LEN VÝPIS (nič sa nemaže)' if DRY else 'ostro'}")

    # ---------- ktoré workflowy ešte majú svoj súbor ----------
    workflows = pages(f"/repos/{REPO}/actions/workflows", "workflows")
    zive, zrusene = {}, {}
    for w in workflows:
        # `dynamic/pages/...` je GitHubov vlastný workflow pre Pages, ten
        # súbor v repe nemá a mazať ho nesmieme.
        if not w["path"].startswith(".github/workflows/"):
            continue
        (zive if os.path.exists(w["path"]) else zrusene)[w["id"]] = w["path"]
    print(f"\nWorkflowy: {len(zive)} so súborom, {len(zrusene)} zrušených")
    for path in sorted(zrusene.values()):
        print(f"  zrušený: {path}")

    # ---------- čo zmazať ----------
    smeti = []   # (id, dôvod, popis)
    for wid, path in zrusene.items():
        for r in pages(f"/repos/{REPO}/actions/workflows/{wid}/runs", "workflow_runs"):
            smeti.append((r["id"], "zrušený workflow", f"{path} #{r['run_number']}"))

    for wid, path in zive.items():
        for r in pages(f"/repos/{REPO}/actions/workflows/{wid}/runs", "workflow_runs"):
            # Meno = cesta k súboru → GitHub ten súbor neprečítal, čiže beh
            # bez jobov. Behy so skutočným menom sú normálna história.
            if r["name"].startswith(".github/workflows/"):
                smeti.append((r["id"], "odmietnutý súbor", f"{path} #{r['run_number']}"))

    # Rozbehnutý beh sa mazať nedá a ten svoj by sme si podrezali sami.
    smeti = [s for s in smeti if str(s[0]) != SELF_RUN]

    print(f"\nBehov na zmazanie: {len(smeti)}")
    for _, dovod, popis in sorted(smeti, key=lambda s: s[2]):
        print(f"  [{dovod}] {popis}")

    zmazane = 0
    if not DRY:
        for rid, _, popis in smeti:
            if gh(f"/repos/{REPO}/actions/runs/{rid}", method="DELETE") is not None:
                zmazane += 1
            else:
                print(f"::warning::beh {popis} sa nepodarilo zmazať")
        print(f"\nZmazaných behov: {zmazane} z {len(smeti)}")

    # ---------- vetvy ----------
    vetvy = []
    if MODE == "behy_a_vetvy":
        base = (gh(f"/repos/{REPO}") or {}).get("default_branch", "master")
        for b in pages(f"/repos/{REPO}/branches", None):
            name = b["name"]
            if name == base or not name.startswith("claude/"):
                continue
            cmp_ = gh(f"/repos/{REPO}/compare/{base}...{name}")
            if not cmp_:
                continue
            # `behind` = vetva nemá oproti hlavnej ani jeden vlastný commit,
            # `identical` = je to presne tá istá špička. Oboje je zmergované
            # alebo prázdne; `ahead` a `diverged` sa nechávajú na pokoji.
            if cmp_.get("status") in ("behind", "identical"):
                vetvy.append((name, cmp_["status"]))
            else:
                print(f"  nechávam vetvu {name} ({cmp_.get('status')}, "
                      f"vlastných commitov: {cmp_.get('ahead_by')})")

        print(f"\nVetiev na zmazanie: {len(vetvy)}")
        for name, st in vetvy:
            print(f"  {name} ({st})")
        if not DRY:
            for name, _ in vetvy:
                gh(f"/repos/{REPO}/git/refs/heads/{name}", method="DELETE")

    # ---------- súhrn ----------
    if SUMMARY:
        with open(SUMMARY, "a") as f:
            f.write("## Upratovanie Actions\n\n")
            f.write("Len výpis, nič sa nemazalo.\n\n" if DRY else "")
            f.write("| čo | koľko |\n|---|--:|\n")
            f.write(f"| zrušené workflowy (ostali po nich behy) | {len(zrusene)} |\n")
            f.write(f"| behy na zmazanie | {len(smeti)} |\n")
            if not DRY:
                f.write(f"| **skutočne zmazaných behov** | **{zmazane}** |\n")
            if MODE == "behy_a_vetvy":
                f.write(f"| zlúčené vetvy `claude/*` | {len(vetvy)} |\n")
            if smeti:
                f.write("\n<details><summary>Zoznam behov</summary>\n\n")
                for _, dovod, popis in sorted(smeti, key=lambda s: s[2]):
                    f.write(f"- `{popis}` – {dovod}\n")
                f.write("\n</details>\n")
            f.write("\nPoložka workflowu zmizne z ľavého zoznamu Actions až "
                    "vtedy, keď nemá ani jeden beh – preto sa mažú všetky.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

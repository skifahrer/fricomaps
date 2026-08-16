#!/usr/bin/env python3
"""
Kto púšťa Planetiler, musí mať v jobe `actions/setup-java` – a tú istú verziu.

PREČO TO EXISTUJE. Planetiler je JAR preložený pre Javu 21 (class file 65)
a `ubuntu-latest` má predvolene 17 (61). Bez kroku `actions/setup-java` sa
`java -jar planetiler.jar` nespustí vôbec:

    UnsupportedClassVersionError: com/onthegomap/planetiler/Main has been
    compiled by a more recent version of the Java Runtime (class file version
    65.0), this version of the Java Runtime only recognizes class file
    versions up to 61.0

Ten krok je ale v YAMLe pri KAŽDOM jobe zvlášť (`setup-java` je akcia, tá sa
do bashu vo `workers/` stiahnuť nedá), takže je to päť kópií jednej vety – a
šiesty volajúci na ňu zabudol. Beh 31948543768: mapa sveta stiahla 100 MB
podkladov a padla až za nimi. V jobe `contours` by to bolo až za DEM, teda po
desiatkach minút. To je pravidlo 1 z CLAUDE.md v tej podobe, v akej sa proti
nemu nedá brániť inak než kontrolou: jedna odpoveď, ale zapísať sa musí na
šiestich miestach.

ČO SA KONTROLUJE:

  1. každý job, ktorý (aj cez `workers/*.sh`, ktoré si volá) púšťa Planetiler,
     má krok `actions/setup-java`,
  2. jeho `java-version` sa rovná `JAVA_MIN` vo `workers/lib/planetiler.sh` –
     tam je to číslo raz a odtiaľ ho číta aj kontrola v samotnom skripte,
  3. žiadny `setup-java` v repozitári si nedrží inú verziu (inak by sa joby
     rozišli ticho: jeden stavia na 21, druhý na 17).

Job, ktorý má `setup-java` a Planetiler nepúšťa, sa nehlási – Java môže raz
byť aj pod niečím iným. Chýbajúci krok je tvrdá chyba, prebytočný nie.

Spustiť sa dá aj lokálne:
    python3 workers/lint/planetiler.py
"""
import glob
import os
import re
import sys

import yaml

SCRIPT = "workers/lib/planetiler.sh"
JE_TO_PLANETILER = re.compile(r"planetiler\.jar|lib/planetiler\.sh")
# SPUSTENÝ skript, nie spomenutý. Cesta musí byť prvé slovo príkazu (nanajvýš
# za `bash`, `sh`, `source` alebo `.`) – inak by sa za volajúceho rátal aj job
# `lint`, ktorý si tie isté cesty len ČÍTA (krok „Vrstvy podávajú kľúč výrezu"
# grepuje `workers/contours-rocks/build.sh`, ale nepúšťa ho).
CESTA_SKRIPTU = re.compile(
    r"^\s*(?:(?:bash|sh|source|\.)\s+)?(workers/[\w./-]+\.sh)\b", re.M)


def bez_komentarov(s):
    """Riadok, ktorý sa začína `#`, nič nepúšťa – ani v bashi, ani v `run:`."""
    return "\n".join(l for l in s.split("\n") if not re.match(r"^\s*#", l))


def s_volanymi(text, videne=None):
    """Text kroku plus text každého `workers/*.sh`, ktorý sa v ňom spomína.

    Mapa sveta púšťa Planetiler cez `workers/world/build.sh`, ktorý si volá
    `workers/lib/planetiler.sh` – keby sa kontrola pozerala len na `run:`,
    práve ten job by jej ušiel. Ide sa do hĺbky, nie o úroveň nižšie."""
    videne = videne if videne is not None else set()
    out = [text]
    for cesta in CESTA_SKRIPTU.findall(bez_komentarov(text)):
        if cesta in videne or not os.path.exists(cesta):
            continue
        videne.add(cesta)
        with open(cesta, encoding="utf-8") as f:
            out.append(s_volanymi(f.read(), videne))
    return "\n".join(out)


bad = []

with open(SCRIPT, encoding="utf-8") as f:
    zdroj = f.read()
m = re.search(r'^JAVA_MIN="\$\{JAVA_MIN:-(\d+)\}"', zdroj, re.M)
if not m:
    print(f"::error file={SCRIPT}::nemá riadok `JAVA_MIN=\"${{JAVA_MIN:-<číslo>}}\"`. "
          f"Je to jediné miesto, kde je napísané, akú Javu Planetiler chce – "
          f"bez neho sa nedá overiť, či ju joby naozaj nastavujú.")
    sys.exit(1)
CHCE = m.group(1)
print(f"{SCRIPT}: Planetiler chce Javu {CHCE}")

for path in sorted(glob.glob(".github/workflows/*.yml")):
    with open(path, encoding="utf-8") as f:
        wf = yaml.safe_load(f) or {}
    for job_name, job in (wf.get("jobs") or {}).items():
        steps = job.get("steps") or []

        java = [s for s in steps
                if "actions/setup-java" in str(s.get("uses") or "")]
        for st in java:
            ver = str((st.get("with") or {}).get("java-version") or "")
            if ver != CHCE:
                bad.append(
                    f"{path}: job '{job_name}' nastavuje Javu {ver or '?'}, ale "
                    f"Planetiler chce {CHCE} (`JAVA_MIN` v {SCRIPT}). Dve verzie "
                    f"vedľa seba znamenajú, že jeden job stavia inak než ostatné.")

        pusta = any(JE_TO_PLANETILER.search(bez_komentarov(s_volanymi(
            str(s.get("run") or "")))) for s in steps)
        if pusta and not java:
            bad.append(
                f"{path}: job '{job_name}' púšťa Planetiler, ale nemá krok "
                f"`actions/setup-java`. Runner má Javu 17 a jar je preložený "
                f"pre {CHCE} – `java -jar` spadne na UnsupportedClassVersionError, "
                f"a to až po tom, čo job odpracoval všetko pred ním. Pridaj "
                f"`- uses: actions/setup-java@v5` s `distribution: temurin` a "
                f"`java-version: \"{CHCE}\"` (tak, ako to majú joby v build-map.yml).")

for b in bad:
    print(f"::error::{b}")
print(f"Planetiler a Java: {len(bad)} chýb")
sys.exit(1 if bad else 0)

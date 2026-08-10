#!/usr/bin/env python3
"""
Kontrola: token vlastníka Drive sa dostane všade, kde sa z Drive číta.

PREČO. Z Drive sa v pipeline číta na štyroch miestach (DMR 5.0 vo výreze aj
v dlaždiciach, Sonnyho priečinok, sklon pre skaly), leží na ňom cache buildu,
sklad hotových dát aj hotové mapy. Keď na jedno miesto token nepríde, nič
nespadne – len sa číta verejným odkazom s denným limitom, prípadne sa cache
nenájde a nič neuloží. Build je zelený a počíta hodiny odznova; presne ten druh
tichej chyby, na ktorý sú tieto kontroly (pravidlo 8).

KONTROLUJE SA Z OBOCH STRÁN: či volajúci podáva `secrets: inherit` tam, kde
volaný workflow z Drive číta, a či ho volaný vôbec deklaruje – `workflow_call`
nededí secrets sám.

`DRIVE_CLIENT` medzi secrets NIE JE: `client_id` nie je tajný údaj, je to
repository variable a `vars.*` sa v tom istom repozitári čítajú priamo. Preto
sa tu overuje len dvojica `DRIVE_SECRET`/`DRIVE_REFRESH` – a nekompletná sa
NESMIE brať ako „veď tam niečo je", lebo `drive-auth.py` na polovici údajov
padne, a to až v tom trojhodinovom behu.

Použitie:
    python3 workers/lint/drive-token.py
"""
import glob, re, sys, yaml

# Prihlásenie sa dá podať dvoma tvarmi: všetko v jednom secrete,
# alebo po kusoch. Nekompletná dvojica/trojica sa NESMIE brať ako
# „veď tam niečo je" – `drive-auth.py` na polovicu údajov spadne,
# a keby to kontrola prepustila, spadol by až ten trojhodinový beh.
#
# Druhá skupina má len DVA prvky: `client_id` (DRIVE_CLIENT) medzi
# secrets nepatrí – nie je to tajné, je to repository variable a
# `vars.*` sa v tomto repozitári číta priamo, bez podávania cez
# `workflow_call`/`secrets: inherit`. Táto kontrola preto overuje
# len to, čo sa cez secrets naozaj musí prevliecť.
BLOB = "GDRIVE_CREDENTIALS"
GROUPS = (("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET",
           "GDRIVE_REFRESH_TOKEN"),
          ("DRIVE_SECRET", "DRIVE_REFRESH"))

def authed(names):
    """Dá sa z týchto premenných prihlásiť?"""
    return (BLOB in names
            or any(all(k in names for k in g) for g in GROUPS))

def why_not(names):
    for g in GROUPS:
        have = [k for k in g if k in names]
        if have:
            return (f"z {'/'.join(g)} tam je len "
                    f"{', '.join(have)} – prihlásenie s polovicou "
                    f"údajov `drive-auth.py` odmieta")
    return f"chýba {BLOB} alebo {'/'.join(GROUPS[1])}"

# Volanie workera sa hľadá na ZAČIATKU riadku v `run:`, nie kdekoľvek v texte:
# tie isté mená spomínajú ako DÁTA aj iné kontroly, ktoré so sieťou nemajú nič
# (`print(f"workers/contours-rocks/slope-chunks.py …")`), a tie sa hlásiť nesmú.
#
# Interpret je NEPOVINNÝ: `run: workers/dem/check.sh` je celý riadok bez `bash`
# pred ním a kým to regex vyžadoval, ten krok cez sieť kontrolou prešiel. Pred
# cestou smú stáť len shellové kľúčové slová a operátory (`elif python3 …`) –
# nie `\S*`, ktoré by chytilo aj cestu vnútri reťazca.
CMD = re.compile(r"^\s*(?:(?:if|elif|then|else|do|!|&&|\|\|)\s+)*"
                 r"(?:\w+=\$\()?(?:(?:python3?|bash|sh)\s+)?"
                 r"(?:\./)?[\w./-]*"
                 r"(?:dmr5-drive|slope-chunks|contours-build"
                 r"|terrain-build|check-dem|fetch-dem"
                 r"|drive-folder|drive-cache|drive-store"
                 r"|publish-map|publish-results)"
                 r"\.(?:py|sh)\b", re.M)
# Cache leží na Drive, takže KAŽDÝ krok s ňou je krok, ktorý sa musí
# vedieť prihlásiť. Bez tokenu sa nič nestratí, len sa nič nenájde
# a nič neuloží – build počíta hodiny odznova a nikde to vidieť nie
# je. Presne ten druh tichej chyby, na ktorý sú tieto kontroly.
CACHE = "./.github/actions/cache-"
# Workflowy, ktoré samy z Drive čítajú, takže im volajúci MUSÍ
# prihlásenie podať. `update-dem.yml` sem pribudol s Sonnym: jeho
# dlaždice sú tiež na Drive a ťahali sa anonymne, kým bol token
# zapojený len do cesty k DMR 5.0. `shading-rocks.yml` s cache:
# gigabajty stiahnutých dlaždíc sú tiež na Drive.
CALLED = ("./.github/workflows/dmr5-drive" + ".yml",
          "./.github/workflows/update-dem" + ".yml",
          "./.github/workflows/shading-rocks" + ".yml")
bad = 0

for path in sorted(glob.glob(".github/workflows/*.yml")):
    d = yaml.safe_load(open(path)) or {}
    top = d.get("env") or {}
    for name, job in (d.get("jobs") or {}).items():
        job = job or {}
        # Volaný workflow si secret vyzdvihne sám, ale volajúci mu ho
        # musí podať – `workflow_call` nededí nič automaticky.
        if job.get("uses") in CALLED:
            called = job["uses"].rsplit("/", 1)[1]
            sec = job.get("secrets")
            if not (sec == "inherit"
                    or (isinstance(sec, dict) and authed(sec))):
                print(f"::error file={path}::job '{name}' volá "
                      f"{called} bez `secrets: inherit`, takže "
                      f"doplnenie by z Drive čítalo verejným odkazom "
                      f"s denným limitom.")
                bad += 1
            continue
        jenv = job.get("env") or {}
        for step in job.get("steps") or []:
            step = step or {}
            cache = str(step.get("uses") or "").startswith(CACHE)
            if not cache and not CMD.search(str(step.get("run") or "")):
                continue
            names = set(top) | set(jenv) | set(step.get("env") or {})
            if authed(names):
                continue
            print(f"::error file={path}::krok "
                  f"'{step.get('name', '?')}' v jobe '{name}' "
                  + ("pracuje s cache na Drive" if cache else
                     "číta z Drive")
                  + f", ale prihlásiť sa z toho nedá: "
                  f"{why_not(names)}. "
                  + ("Cache by sa nenašla ani neuložila a build by "
                     "počítal všetko odznova"
                     if cache else
                     "Bežal by na verejnom dennom limite")
                  + " – doplň to do `env:` toho kroku, jobu alebo "
                    "celého workflowu.")
            bad += 1

# A druhá strana toho istého: volaný workflow to musí prijať.
for called in CALLED:
    d = yaml.safe_load(open(called[2:]))
    on = d[[k for k in d if k is True or k == "on"][0]]
    decl = (on.get("workflow_call") or {}).get("secrets") or {}
    if not authed(decl):
        print(f"::error file={called[2:]}::`workflow_call` "
              f"nedeklaruje prihlásenie na Drive ({why_not(decl)}), "
              f"takže mu ho volajúci nemá ako podať.")
        bad += 1
print(f"prihlásenie na Drive: {bad} chýb")
sys.exit(1 if bad else 0)

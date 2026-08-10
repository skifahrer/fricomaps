# CLAUDE.md

Pokyny pre Clauda (a poznámky pre ľudí) k tomuto repozitáru.

## Čo to je

Mapová aplikácia: vektorové mapy Slovenska z OSM dát. Jedna pipeline, jeden
formát (PMTiles), spoločné štýly pre web aj mobil. Detailne
[`README.md`](README.md), a čo robí každý krok pipeline –
[`docs/pipeline.md`](docs/pipeline.md), 1700 riadkov. **Prečítaj tú kapitolu,
ktorej sa dotýkaš, skôr než čokoľvek zmeníš.** Väčšina neintuitívnych vecí tam
má napísané, prečo je taká, a k tomu číslo behu, ktorý ju spôsobil.

```
app/ios/            iOS (SwiftUI + MapLibre Native)
backend/            NestJS API (regióny)
poc/web/            web viewer (MapLibre GL JS + PMTiles) + dev mode
workers/            výkonné časti pipeline (Python / bash / mjs)
docs/               návrhy a podrobný popis pipeline
.github/workflows/  CI: výškové modely, build mapy, deploy na Pages
.github/actions/    cache-restore a cache-save (cache leží na Google Drive)
```

## Jazyk

**Kód, komentáre, mená krokov, hlášky aj commit messages sú po slovensky.**
Identifikátory (premenné, funkcie, mená súborov) sú anglické. Toto je vedomé
a platí to pre celý repozitár – nepíš nové veci po anglicky preto, že je to
zvykom inde.

Hlášky pre používateľa musia povedať aj **čo s tým** („zvoľ 5 m, alebo si
vyber pohorie"), nie len že sa niečo nepodarilo.

## Pravidlá, ktoré tu platia

Sú to pravidlá vypísané krvou – každé z nich vzniklo z konkrétneho spadnutého
behu. Čísla behov sú v komentároch pri kóde.

**1. Jedna otázka, jedna odpoveď, jedno miesto.** Keď si tú istú vec počítajú
dve miesta, raz sa rozídu – a je to tichý druh chyby, lebo obe strany vyzerajú
samy o sebe správne. Preto existuje `workers/dem-target.py` („ktorý release
a ktoré assety"), `workers/resolve-area.py` („čo je výrez") a `workers/parse-
options.py` („čo je vo formulári"). Keď potrebuješ odpoveď, ktorú niekto už
pozná, **podaj si ju**, neprepočítavaj.

  - Beh 31307163093: kontrola hľadala výrez v `dem-ugkk`, kým tieňovanie
    sťahovalo dlaždice z `dem-dmr5`. Dve pravdy o jednej veci.
  - To isté, len drahšie: `mirror-dmr5-area` dostával kľúč pohoria a riešil si
    ho z `areas.json` druhýkrát, takže rýchly test na 2 km² čítal z Drive
    541 km² Vysokých Tatier. Odvtedy sa podáva **bbox** toho, čo si beh naozaj
    vypýtal, a meno assetu zvlášť.

**2. Meno assetu je sľub o rozsahu.** `N49E020.tif` hovorí „tento celý stupeň
je tu" a `ugkk-vysoke_tatry.tif` „celé Vysoké Tatry sú tu". Keby pod tým menom
ležal len prienik s bboxom, ďalší beh by kontrolou prešiel („je tam") a mapa by
ticho skončila v polovici. Keď rozsah nie je celý, **musí sa zmeniť meno** –
preto má testovací výrez v kľúči príponu `_test2`.

**3. Veľký `run:` blok patrí do `workers/`, nie do YAMLu.** Súbor s workflowom
má strop 128 KiB a **GitHub nad ním workflow ticho neprijme** – po pushi
vznikne beh bez jobov, s červeným krížikom a prázdnym logom, ktorý vyzerá, že
sa spustil sám. `build-map.yml` už cez ten strop raz prešiel; odvtedy je z neho
graf jobov (~90 KiB) a bash je v trinástich `workers/*.sh`. **Nevracaj ho tam** –
rozpis patrí do `workers/*.sh`, `workers/*.py` alebo `docs/pipeline.md`
a v YAMLe ostane odkaz naň. (`Lint workflows` varuje od 120 KiB.)

Pri sťahovaní bloku do skriptu sú dve tiché chyby: `${{ výraz }}` sa zmení na
`$PREMENNÚ` a tá sa zabudne dopísať do `env:` kroku (skript potom beží
s prázdnym reťazcom a **nespadne**), alebo sa premenuje `id` kroku, na ktorý
sa odkazujú výstupy jobu (job ticho vráti prázdno). Stráži to krok *„Skripty vo
workers dostávajú svoje env"*. Blok, ktorý má viac jobov, patrí do JEDNÉHO
súboru – dve kópie sa vždy raz rozídu (`contours-site.sh`,
`fetch-planetiler.sh`).

**4. Dlhý krok musí hovoriť, čo robí a ako ďaleko je.** Hodina ticha v logu sa
nedá odlíšiť od zaseknutého behu. Pred drahou časťou vypíš **plán s odhadom**
(trojhodinový job, ktorý spadne na timeout, minie celý rozpočet a nevyrobí nič),
počas nej **postup** – `[7/12] … zostáva ~5 min` – a na konci namerané čísla
oproti odhadu. Odhady rob z merania a to meranie napíš do komentára.

**5. Rozdeľuj joby a kroky.** Strop času platí na job, takže dlhé fázy majú byť
každá vo svojom. V rámci jobu radšej viac malých krokov než jeden veľký: z mena
kroku, ktorý spadol, má byť hneď vidieť, či nesedelo zadanie, zlyhala sieť, došlo
miesto na disku alebo upload.

**6. Drahý medzivýsledok sa ukladá hneď, ako vznikne.** `actions/cache` ukladá
až v post-kroku a len keď job dobehne úspešne – takže sa používa `cache/restore`
hore a `cache/save` s `if: always()` hneď po tom, čo dáta vzniknú. Časti, ktoré
sa počítajú dlho (sklon, bloky z Drive), sa zapisujú cez `.part` a premenovanie,
takže **zrušený beh nezahodí hotovú prácu** a ďalší dopočíta len zvyšok.

**7. Nikdy nesťahuj viac, než treba.** DMR 5.0 má 145 GB a runner ~60 GB
voľných; všetko sa číta cez HTTP Range po blokoch. Keď sa pýtaš na územie,
pýtaj sa presne na to, ktoré beh potrebuje – nie na obdĺžnik z `areas.json`,
v ktorom leží.

**8. Tichý omyl je horší než pád.** Keď doplnenie nedoplní, nesmie zazelenať
(`what: dmr5` v `update-dem.yml` je preto chyba). Keď GDAL nemá mriežku geoidu,
nesmie ticho nechať elipsoidické výšky (`ERROR_ON_MISSING_VERT_SHIFT=YES`).
Keď sa použije náhradný model, `dem-source.txt` musí niesť, čo sa NAOZAJ
použilo.

## DMR 5.0: jeden workflow, dva joby

Model je na Google Drive ako dva holé BigTIFFy v jednom priečinku a berie sa
**len odtiaľ**:

| súbor | meno v Actions | volá to Build map? |
|---|---|---|
| `dmr5-drive.yml` | DMR 5.0 z Drive (ETRS89) | **áno**, a to **dvoma jobmi** |

Kedysi k tomu bola záloha z archívu ÚGKK (`dmr5.yml`): ten istý model, ale
198 GB ZIP, v ktorom je raster jedným deflate prúdom – nedá sa v ňom skočiť
dopredu, takže platilo „čítaj raz a sekvenčne" a výrez na juhu Slovenska stál
prechod celým súborom. Na Drive sú súbory holé, Range funguje na ľubovoľnom
offsete a číta sa len to, čo výrez pretína. **Tá záloha je zrušená** – odkedy
Drive púšťa spoľahlivo, neoplácalo sa udržiavať druhú cestu s opačnými
pravidlami.

**Zdroj je PRIEČINOK, nie dve file id.** Vo `workers/dmr5-drive.py` je jediné
číslo (`FOLDER_ID`) a súbory sa v ňom hľadajú podľa mena – presun modelu inam
je zmena jedného čísla namiesto dvoch id na štyroch miestach v hláškach.

**Číta sa prihlásený ako vlastník dát, a inak sa nečíta vôbec.** Čo je
v priečinku, povie len Drive API a to anonymné požiadavky neobsluhuje –
verejný odkaz (denný limit na súbor, zdieľaný so všetkými, kto naň siahnu) už
k DMR 5.0 nevedie. Token vlastníka je v secrete `GDRIVE_CREDENTIALS` (alebo po
troch: `DRIVE_CLIENT`/`DRIVE_SECRET`/`DRIVE_REFRESH`), drží ho
`workers/drive-auth.py` (`--login` z počítača, `drive-login.yml` z telefónu –
tam sa token nikde nevypíše, lebo log public repozitára vidí ktokoľvek;
`dmr5-drive.py --auth-check` overí). Bez secretu beh spadne hneď a s návodom.

**Rozsah tokenu je `drive`, teda aj zápis** – nie preto, že by pipeline
zapisovala dáta, ale preto, že na Drive leží aj cache buildu (nižšie).
Readonly token DMR 5.0 číta ďalej, len sa pod ním nič neuloží.

**Ten strop visí na VLASTNÍKOVI súboru, nie na tom, kto sťahuje.** Na DMR 5.0
(naše vlastné súbory) prihlásenie strop dvíha; na cudzí priečinok zdieľaný
odkazom – Sonny – nie, a nesmie sa tváriť, že áno (`drive-folder.py` preto
vypíše, koľko súborov účet nevlastní). Aj tam sa ale prihlásiť oplatí: Drive
API povie dôvod odmietnutia rovno, kým verejná cesta vráti HTTP 200 a HTML
stránku. Proti Sonnyho stropu chráni zrkadlo v releasi, nie token.

**Token sa musí dostať na všetky miesta, kde sa z Drive číta** –
`workflow_call` nededí secrets sám:

| kde | čo | ako |
|---|---|---|
| `dmr5-drive.yml` | DMR 5.0 | `secrets: inherit` z `build-map.yml` |
| `update-dem.yml` | Sonnyho priečinok | `secrets: inherit` z `build-map.yml` |
| `shading-rocks.yml` | cache stiahnutých dlaždíc | `secrets: inherit` z `build-map.yml` |
| job `contours` | vrstevnice z `dmr5` | `env:` workflowu |
| job `rocks` | sklon z `dmr5` rovno z Drive | `env:` workflowu |
| každý krok s cache | cache buildu | `env:` workflowu |
| krok `Publikuj mapu na Drive` | hotová mapa ako ZIP | `env:` workflowu |

Odkedy je na Drive aj cache, je tých miest priveľa na to, aby stáli pri každom
jobe – preto je prihlásenie v `env:` celého workflowu.

`Lint workflows` to stráži staticky, z oboch strán, a hlási aj nekompletnú
trojicu secretov (polovica údajov nie je „veď tam niečo je" – `drive-auth.py`
na nej padne).

**Keď Drive nepustí, DMR 5.0 sa v tom behu nedoplní** – a nesmie to byť tiché:
`drive-serve.py` vráti 502 s vysvetlením, beh spadne v sekundách a so zapnutým
`ugkk_fallback` prejde na hrubší model, čo `dem-source.txt` aj atribúcia
povedia. Prvá vec, čo s tým: prihlásiť sa ako vlastník. Až potom nahrať kópiu
do iného priečinka a prepísať `FOLDER_ID` vo `workers/dmr5-drive.py`.

Tie **dva joby** sú `mirror-dmr5-area` a `mirror-dmr5-tiles` – dve volania
jedného workflowu, lebo DMR 5.0 má dve podoby a chýbať môžu naraz:

```
výrez     ugkk-<kľúč>.tif  → dem-ugkk   plné 1 m   vrstevnice, skaly
dlaždice  N49E020.tif      → dem-dmr5   5 m        tieňovanie (celý región)
```

Skaly z `dmr5` si DEM **nedopĺňajú vôbec**: `workers/slope-chunks.py` číta
z Drive rovno tie časti, ktoré územie pretína, a odkladá si ich do skladu.

## Cache je na Drive, nie v GitHube

GitHubová cache má na repozitár **10 GB** a keď sa naplní, **nič nepovie** –
ticho vyhodí najstaršie záznamy. Jeden výrez do nej ukladal desiatky GB (DEM
dlaždice, sklad častí sklonu, vrstevnice, tieňovanie, dlaždice tieňovania),
takže si záznamy vyhadzovali navzájom a hodiny výpočtu sa rátali odznova bez
toho, aby to bolo na čom vidieť – build je zelený, len trvá hodinu namiesto
minút. To je pravidlo 8 v čistej podobe.

| čo | kde |
|---|---|
| `.github/actions/cache-restore` / `cache-save` | náhrada za `actions/cache/*` |
| `workers/drive-cache.py` | celý formát a pravidlá (aj `--check`, `--list`, `--prune`) |
| `cleanup-cache.yml` | zmaže GitHub cache a preriedi tú na Drive (týždenne) |

**Sémantika ostala tá istá ako v GitHube**, nech platí to, čo je pri kľúčoch
napísané: `cache-hit` len pri PRESNEJ zhode kľúča, `restore-keys` sú PREDPONY
a berie sa NAJNOVŠÍ záznam, existujúci kľúč sa NEPREPISUJE (preto majú kľúče,
ktoré sa majú dať dopĺňať, v sebe `github.run_id`). Zhoda sa hľadá podľa
plného kľúča z `description`, nie podľa mena súboru – v mene sú znaky mimo
`[A-Za-z0-9._-]` nahradené podčiarkovníkom a dva rôzne kľúče by mohli vyzerať
rovnako.

Dve veci, ktoré GitHub robil sám a Drive nie: **nič sa nemaže samo** (na to je
`--prune` a týždenný workflow) a **bez prihlásenia to nefunguje** (krok vtedy
spadne s návodom; `Lint workflows` stráži, že token dostane každý cache krok).
Nový `uses: actions/cache…` tá istá kontrola odmietne.

## Hotová mapa ide na Drive ako ZIP

Okrem Pages sa každý build publikuje aj do priečinka na Drive – celý `_site`
v jednom ZIPe (`workers/publish-map.py`, vypína to voľba `publish=false`):

```
<koreň>/slovensko/presovsky/vysoke_tatry/<mapa>.zip
         krajina  kraj      výsek        (úrovne, čo nedávajú zmysel, sa vynechajú)
```

**Meno je sľub o obsahu** (pravidlo 2 v inej podobe): nesie výrez, zoom, ktoré
vrstvy v mape sú a Z ČOHO sú spočítané – a to podľa toho, čo joby NAOZAJ
použili, nie čo bolo vo formulári. Vrstva, ktorá tam nie je, sa píše tiež
(`bez_skal`), lebo mlčanie sa dá čítať aj ako „zabudlo sa to dopísať". Rýchly
test má v mene `test2km2`, inak by mapa z 2 km² vyzerala ako celá.

Dátum, čas a číslo behu na konci robia meno jedinečným, takže sa dva behy
nikdy neprepíšu. Publikuje sa len mapa, ktorá prešla kontrolou pred nasadením;
rozbitý ZIP v priečinku vyzerá presne ako dobrý.

## Než niečo pushneš

```bash
# actionlint – chytí to, čo GitHub inak zamlčí
curl -sSL https://github.com/rhysd/actionlint/releases/download/v1.7.7/actionlint_1.7.7_linux_amd64.tar.gz \
  | tar xz actionlint && ./actionlint

# veľkosť workflowov (strop 128 KiB, varovanie od 120 KiB)
wc -c .github/workflows/*.yml

# bash vo workers (shellcheck nie je v CI, actionlint ho volá len na `run:`)
for f in workers/*.sh; do bash -n "$f" || echo "CHYBA $f"; done

# kontroly z Lint workflows sa dajú spustiť aj lokálne (bez sťahovania actionlintu)
python3 - <<'PY'
import subprocess, sys, yaml
d = yaml.safe_load(open(".github/workflows/lint-workflows.yml"))
for st in d["jobs"]["lint"]["steps"]:
    if st.get("run") and st.get("name") != "actionlint":
        print("=====", st["name"])
        subprocess.run(["bash", "-c", st["run"]])
PY

# workery sa dajú spustiť aj lokálne – hodnoty berú z prostredia práve preto
python3 workers/resolve-area.py --region-bbox=18.7,48.8,20.6,49.6 --area=vysoke_tatry
python3 workers/dem-target.py --source=dmr5 --area-key=vysoke_tatry --bbox=20.1,49.1,20.2,49.2
BBOX=… AREA_KEY=… AREA_BBOX=… SRC_CONTOURS=dmr5 workers/check-dem.sh
REGION_KEY=… BASE_URL=… ICONS_NAME=… … workers/build-site.sh   # a tak ďalej
```

`Lint workflows` (`.github/workflows/lint-workflows.yml`) beží pri každom pushi
do `.github/workflows/**` a kontroluje aj veci, ktoré actionlint nevie: veľkosť
súboru, zdvojené zátvorky v `run:`, dĺžku popisov inputov, súlad výberov
s `areas.json` a `dem-sources.json`, existenciu `needs.*.outputs.*`
a `steps.*.outputs.*`, to, že každý `workers/*.sh` dostane env, ktoré číta,
že cesta k DMR 5.0 ostane celá a že cache ostane na Drive (žiadne
`actions/cache`, každý cache krok sa vie prihlásiť). **Keď opravíš tichú chybu,
pridaj naň kontrolu** – tak sú tam všetky ostatné.

## Commity a PR

Commit message je **jedna veta po slovensky o tom, čo sa zmenilo vecne** – nie
zoznam súborov. Pozri `git log`: „Skaly po častiach: sklad sklonu, ktorý prežije
zrušený beh", „Z dvoch výberov výškového modelu jeden: `dmr5` si podobu berie
podľa rozsahu".

**PR zakladaj vždy, aj keď oň nikto nepožiadal.** Hotová práca na vetve, ku
ktorej PR nie je, sa nemá ako dostať do mastera – a z histórie tohto repozitára
je vidieť, že tadiaľ ide všetko (#54 … #71). Nečakaj na vyzvanie: keď je zmena
dokončená a pushnutá, otvor k nej PR.

Do popisu PR patrí to isté, čo do commit message – **čo sa zmenilo vecne a
prečo** – a k tomu ako sa to overilo (`Lint workflows`, lokálne spustené
workery, číslo behu). Keď ostalo niečo nedokončené alebo neoverené, napíš to
tam; tichý PR, ktorý vyzerá hotovo, je tá istá trieda chyby ako tichý omyl
v behu.

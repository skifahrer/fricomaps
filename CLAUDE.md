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
sa spustil sám. `build-map.yml` je tesne pod stropom (`Lint workflows` to
stráži a varuje už od 120 KiB), takže **doň nepridávaj dlhé komentáre ani
skripty** – rozpis patrí do `workers/*.sh`, `workers/*.py` alebo
`docs/pipeline.md` a v YAMLe ostane odkaz naň.

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

## DMR 5.0: dva workflowy, ktoré nie sú duplikát

Toto mätie najčastejšie, tak nech je to na jednom mieste:

| súbor | meno v Actions | volá to Build map? |
|---|---|---|
| `dmr5-drive.yml` | DMR 5.0 z Drive (ETRS89) | **áno**, a to **dvoma jobmi** |
| `dmr5.yml` | DMR 5.0 z archívu ÚGKK (záloha, ručne) | **nie, nikdy** |

Je to **ten istý model z dvoch zdrojov s opačnými pravidlami čítania**: archív
ÚGKK je ZIP, v ktorom je raster jedným deflate prúdom (nedá sa skočiť dopredu →
„čítaj raz a sekvenčne"), kým na Drive sú holé BigTIFFy s Range na ľubovoľnom
offsete (číta sa len to, čo výrez pretína). Zliať ich do jedného job grafu by
znamenalo, že polovica pravidiel v ňom vždy klame.

Tie **dva joby** sú `mirror-dmr5-area` a `mirror-dmr5-tiles` – dve volania
jedného workflowu, lebo DMR 5.0 má dve podoby a chýbať môžu naraz:

```
výrez     ugkk-<kľúč>.tif  → dem-ugkk   plné 1 m   vrstevnice, skaly
dlaždice  N49E020.tif      → dem-dmr5   5 m        tieňovanie (celý región)
```

Skaly z `dmr5` si DEM **nedopĺňajú vôbec**: `workers/slope-chunks.py` číta
z Drive rovno tie časti, ktoré územie pretína, a odkladá si ich do skladu.

## Než niečo pushneš

```bash
# actionlint – chytí to, čo GitHub inak zamlčí
curl -sSL https://github.com/rhysd/actionlint/releases/download/v1.7.7/actionlint_1.7.7_linux_amd64.tar.gz \
  | tar xz actionlint && ./actionlint

# veľkosť workflowov (strop 128 KiB, varovanie od 120 KiB)
wc -c .github/workflows/*.yml

# workery sa dajú spustiť aj lokálne – hodnoty berú z prostredia práve preto
python3 workers/resolve-area.py --region-bbox=18.7,48.8,20.6,49.6 --area=vysoke_tatry
python3 workers/dem-target.py --source=dmr5 --area-key=vysoke_tatry --bbox=20.1,49.1,20.2,49.2
BBOX=… AREA_KEY=… AREA_BBOX=… SRC_CONTOURS=dmr5 workers/check-dem.sh
```

`Lint workflows` (`.github/workflows/lint-workflows.yml`) beží pri každom pushi
do `.github/workflows/**` a kontroluje aj veci, ktoré actionlint nevie: veľkosť
súboru, zdvojené zátvorky v `run:`, dĺžku popisov inputov, súlad výberov
s `areas.json` a `dem-sources.json`, existenciu `needs.*.outputs.*` a to, že
cesta k DMR 5.0 ostane celá. **Keď opravíš tichú chybu, pridaj naň kontrolu** –
tak sú tam všetky ostatné.

## Commity a PR

Commit message je **jedna veta po slovensky o tom, čo sa zmenilo vecne** – nie
zoznam súborov. Pozri `git log`: „Skaly po častiach: sklad sklonu, ktorý prežije
zrušený beh", „Z dvoch výberov výškového modelu jeden: `dmr5` si podobu berie
podľa rozsahu".

PR nezakladaj, kým oň niekto nepožiada.

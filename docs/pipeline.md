# Pipeline: od OSM dát po mapu v prehliadači

Tento dokument popisuje, čo robí každý krok, aké formáty medzi sebou putujú
a **prečo** je to práve takto. Stručný prehľad je v [README](../README.md);
tu je detail.

---

## Prečo vôbec nejaká pipeline

OpenStreetMap je jedna obrovská databáza bodov, ciest a relácií s voľnými
značkami (`highway=primary`, `natural=wood`, …). Prehliadač z nej priamo
kresliť nevie – bola by to stovka gigabajtov a žiadna štruktúra na to, čo sa
má zobraziť na akom zoome. Medzi surovými dátami a mapou preto stojí reťaz
konverzií, kde každý krok niečo **zjednoduší a usporiada**:

```
OSM planéta          surové dáta, ~80 GB
   │  regionálny orez
   ▼
{región}.osm.pbf     dáta jedného územia, desiatky MB
   │  Planetiler + schéma OpenMapTiles
   ▼
{región}.pmtiles     vektorové dlaždice po zoomoch, jeden súbor
   │  MapLibre + style.json
   ▼
obraz na displeji
```

Kľúčové je, že **dáta a vzhľad sú oddelené**. Dlaždice hovoria „tu je cesta
triedy `primary`", štýl hovorí „cesty `primary` kresli oranžovo, 6 px, od
zoomu 6". Zmena farieb preto nevyžaduje prepočet dlaždíc – to je celý základ
[developer módu](../README.md#developer-mode--ladenie-mapy-v-prehliadači).

---

## Formáty, ktoré sa v pipeline stretnú

| formát | čo to je | prečo práve on |
|---|---|---|
| **`.osm.pbf`** | OpenStreetMap dáta zabalené v Protocol Buffers | XML export tých istých dát je asi 10× väčší a pomalšie sa parsuje; `.pbf` je de facto štandard pre distribúciu OSM |
| **`.mbtiles` / `.pmtiles`** | kontajner s vektorovými dlaždicami | `.mbtiles` je SQLite databáza – potrebuje server, ktorý z nej dlaždice vyberá. **`.pmtiles`** je jeden statický súbor s vlastným indexom: prehliadač si z neho HTTP `Range` requestom vypýta presne tie bajty, ktoré potrebuje. Vďaka tomu beží mapa na obyčajnom statickom hostingu (GitHub Pages), bez akéhokoľvek backendu |
| **MVT (Mapbox Vector Tile)** | obsah jednej dlaždice: geometrie + atribúty v Protocol Buffers | vektor, nie obrázok – to isté dáta sa dajú vykresliť v ľubovoľných farbách, otočiť, nakloniť a priblížiť bez rozmazania |
| **schéma OpenMapTiles** | dohoda, aké vrstvy a atribúty v dlaždiciach sú (`transportation`, `water`, `poi`, `class`, `subclass`, …) | bez nej by si každý štýl vyžadoval vlastné dlaždice; s ňou funguje mapa s hocijakým štýlom postaveným na tej istej schéme |
| **`style.json`** | MapLibre štýl – zoznam vrstiev, filtrov a farieb | jediný zdroj vzhľadu pre web aj iOS |
| **sprite (PNG + JSON)** | atlas ikoniek a index ich pozícií | jeden request namiesto dvesto |
| **SDF sprite** | to isté, ale alfa kanál nesie *signed distance field* | umožňuje ikonu **prefarbiť** a nastaviť jej obrys priamo v štýle |
| **glyfy (`.pbf`)** | predpočítané rastre písmen po rozsahoch 256 znakov | mapa nemusí mať font, sťahuje len tie znaky, ktoré naozaj kreslí |
| **GeoTIFF / COG** | výškový model ako raster | COG (Cloud Optimized GeoTIFF) sa dá čítať po častiach cez HTTP |
| **GPKG (GeoPackage)** | vektorová geodatabáza v SQLite | medzikrok medzi GDAL a Planetilerom, unesie atribúty aj veľké množstvo geometrií |

---

## Workflow „Build map": desať jobov

Build nie je jeden dlhý job, ale **desať samostatných**. Dôvod je praktický:
kým bolo všetko v jednom, [beh 30948662582](https://github.com/skifahrer/fricomaps/actions/runs/30948662582)
strávil tri hodiny na skalách, narazil na `timeout-minutes` a zahodil aj mapu,
tieňovanie aj ikonky – hoci s nimi nebolo nič zlé. Teraz má každá časť vlastný
timeout, vlastnú cache a keď spadne, ostatné dobehnú.

```
                    ┌──────────────┐
                    │  plan        │  región, bbox, PBF
                    └─┬───┬───┬──┬─┘
              ┌───────┘   │   │  └────────────────┐
              ▼           ▼   ▼                   ▼
      ┌──────────────┐ ┌──────────┐        ┌─────────────┐
      │ check-dem    │ │ trails   │        │ tiles       │  Planetiler
      └──────┬───────┘ │ značené  │        │             │  → .pmtiles
             ▼         │ trasy    │        └──────┬──────┘
      ┌──────────────┐ ├──────────┤               │
      │ mirror-dem   │ │ features │               │   ┌─────────────┐
      └──────┬───────┘ │ prvky    │               │   │ assets      │
             ▼         │ mimo     │               │   │ ikonky+fonty│
      ┌──────────────┐ │ schémy   │               │   └──────┬──────┘
      │ keys         │ └────┬─────┘               │          │
      └──┬────────┬──┘      │                     │          │
         ▼        ▼         │                     │          │
  ┌───────────┐ ┌──────────┐│                     │          │
  │ contours  │ │ terrain  ││                     │          │
  │ vrstevnice│ │ tieňovanie                      │          │
  │ + skaly   │ │ + 3D     ││                     │          │
  └─────┬─────┘ └────┬─────┘│                     │          │
        └────────────┴──────┴──────┬──────────────┴──────────┘
                                   ▼
                            ┌─────────────┐
                            │ deploy      │  zloží _site, nasadí, súhrn
                            └─────────────┘
```

| job | čo robí | timeout | beží súbežne s |
|---|---|--:|---|
| **plan** | overí Pages, vyrieši región/bbox, stiahne (a nacacheuje) PBF | 30 min | — |
| **check-dem** | sú v release zvoleného zdroja dlaždice pre bbox? spočíta `demkey` | — | tiles, assets |
| **mirror-dem** | keď chýbajú, spustí *Stiahnuť výškové dáta* so zvoleným `source` | — | tiles, assets |
| **keys** | poskladá kľúče cache, pri `*_rebuild` zmaže staré záznamy | 10 min | tiles, assets |
| **contours** | DEM → vrstevnice + skaly → `{región}-contours.pmtiles` | 180 min | terrain, tiles, assets |
| **terrain** | DEM → terrarium PNG dlaždice | 120 min | contours, tiles, assets |
| **trails** | OSM relácie trás → `{región}-trails.pmtiles` | 60 min | úplne so všetkým |
| **features** | prvky mimo schémy OpenMapTiles → `{región}-features.pmtiles` | 90 min | úplne so všetkým |
| **tiles** | PBF → `{región}.pmtiles` (Planetiler) | 150 min | contours, terrain, assets |
| **assets** | SDF sprity a glyfy | 30 min | úplne so všetkým |
| **deploy** | zlepí `_site`, štýly, manifest, kontrola, Pages, smoke test, súhrn | 45 min | — |

Čo tým vzniklo a čo to stálo:

- **Kratší beh.** Skaly (~40 min), tieňovanie (~10) a mapa (~20) bežali za
  sebou, teraz naraz – z ~85 minút je ~55.
- **Pád nezahodí zvyšok.** Keď spadnú skaly, mapa aj tieňovanie sú hotové
  a `deploy` nasadí mapu bez skál. `deploy` beží s `!cancelled()` a trvá len
  na tom, aby prešli **dlaždice a ikonky** – bez nich by mapa nebola mapa.
- **Cena: artefakty.** Joby si kusy `_site` posielajú cez
  `upload-artifact`/`download-artifact`, čo je pri ~500 MB dlaždíc pár minút
  navyše. Balenie je vypnuté (`compression-level: 0`), lebo `.pmtiles` aj
  `.png` sú už komprimované.
- **Cena: rozpočet sa musí deliť dopredu.** Kým bolo všetko v jednom jobe,
  dostali dlaždice „čo zvýšilo po vrstevniciach". Teraz v čase, keď Planetiler
  rozhoduje o zoome, ešte nikto nevie, aké budú vrstevnice veľké – tak sa
  rozpočet delí **podielom** (`BUDGET_CONTOURS_PCT` 25 %, `BUDGET_TERRAIN_PCT`
  12 %, `BUDGET_TRAILS_PCT` 3 %, `BUDGET_ASSETS_MB` 40 MB, zvyšok
  dlaždiciam). Podiely sú s rezervou
  nad namerané hodnoty (vrstevnice 187 MB = 21 %, terén 96 MB = 11 % z 900 MB)
  a `deploy` na konci aj tak overí, že súčet naozaj sedí.
- **Meranie krokov.** Každý job si píše riadky do `steps-out/<job>.tsv`
  a odloží ich artefaktom; `deploy` ich zlepí a zoradí podľa **poradového
  čísla**, nie podľa času – joby bežia súbežne, takže čas by hovoril len
  o tom, ktorý runner bol rýchlejší.
- **Spoločné sťahovanie DEM.** Vrstevnice, skaly aj tieňovanie potrebujú
  výškové dlaždice; kým to bol jeden job, stačilo raz. Teraz je to
  [`workers/fetch-dem.sh`](../workers/fetch-dem.sh) – jedna kópia pre všetkých,
  aby sa časom nerozišli. Dlaždice idú do `dem/<zdroj>/` a cache má v kľúči
  zdroje daného jobu: odkedy si každá vrstva vyberá model sama, spoločný kľúč
  by vracal cudziu mozaiku.

- **Veľké `run:` bloky patria do `workers/`.** Súbor s workflowom má strop
  veľkosti (128 KiB) a nad ním ho GitHub **neprijme** – bez chybovej hlášky,
  bez logu: po pushi sa v Actions objaví beh **bez jobov**, pomenovaný cestou
  k súboru, s červeným krížikom. Vyzerá to, že sa workflow spustil sám po
  mergi, hoci má len `workflow_dispatch`. Presne to sa stalo, keď
  `build-map.yml` narástol na 131 194 B (o 122 bajtov nad strop) – a nezachytí
  to ani actionlint, ani oficiálna JSON schéma. Preto je výpočet vrstevníc
  a skál vo [`workers/contours-build.sh`](../workers/contours-build.sh)
  a text súhrnu vo [`workers/summary.sh`](../workers/summary.sh); hodnoty im
  workflow podáva cez `env:`. Veľkosť stráži `Lint workflows` (chyba nad
  128 KiB, varovanie nad 120 KiB).

Ďalej detailne, čo z toho je zaujímavé a **prečo** je to tak.

### `plan` – kontrola Pages

`GITHUB_TOKEN` nemá práva Pages zapnúť, takže sa to musí raz spraviť ručne.
Kontrola je **hneď na začiatku** zámerne: keby bola na konci, zistili by sme
to až po hodinách tilovania.

### `plan` – stiahnutie PBF iba daného regiónu

Zdrojom sú regionálne exporty [osm.fr](https://download.openstreetmap.fr/extracts/),
rezané po **skutočných administratívnych hraniciach** a aktualizované denne.
Sťahuje sa len zvolený región – celá planéta má ~80 GB, kraj 36–63 MB.

Voliteľný `crop_bbox` oreže PBF ešte viac (`osmium extract --bbox`). Menšie
územie = výrazne menší výsledok, takže sa doň zmestí vyšší zoom.

### `plan` – Pages si beh prepne sám na Actions

Na stránke má byť **mapa, nie README**, a rozhoduje o tom jediné nastavenie
repozitára: `build_type`. Keď je `legacy`, zdroj Pages je **vetva**, nie
Actions – a vtedy popri nás beží zabudovaný Jekyll builder („pages build and
deployment"). Ten pri KAŽDOM pushi do vetvy nasadí koreň repozitára, teda
README, a mapu, ktorú nasadil tento workflow, prepíše.

Navonok to vyzerá, že sa mapa „sama pokazila": beh Build map je zelený,
nasadenie prebehlo, a na stránke je README. V Actions je to vidieť ako beh
`pages build and deployment` s eventom `dynamic`, ktorý sa spustí po merge –
hoci Build map je len `workflow_dispatch`. Stalo sa to po mergoch #50, #51,
#52 a znova po #54 a #55.

Prvý krok behu preto nastavenie **nielen kontroluje, ale aj opravuje**:

| stav na začiatku | čo krok spraví |
|---|---|
| `build_type: workflow` | nič (jedno GET volanie) |
| `build_type: legacy` | `PUT /repos/{owner}/{repo}/pages` s `build_type=workflow` |
| Pages vôbec nie sú zapnuté | `POST /repos/{owner}/{repo}/pages` s `build_type=workflow` |
| prepnúť sa nepodarilo | `::warning::`, beh **pokračuje** a mapu nasadí |

Po zápise sa hodnota **prečíta znova** a až tá rozhoduje. Keby `PUT` prešlo
a nastavenie ostalo staré, beh by dobehol do zelena a na stránke by aj tak
bolo README – čiže presne tá chyba, ktorú to má riešiť, len tichšia.

**A `PUT` neprejde.** Job má `permissions: pages: write` aj na úrovni
workflowu, a aj tak vracia API chybu (beh 31265537441):

```
Pages berie zdroj z vetvy (build_type=legacy) – prepínam na GitHub Actions…
::error::… a tokenu sa to nepodarilo prepnúť.
```

Dáva to zmysel: `build_type` je nastavenie **repozitára**, nie obsah stránky,
takže naň `GITHUB_TOKEN` právo nemá – `pages: write` dovolí nasadzovať, nie
prestavovať zdroj. Odpoveď API sa preto **vypisuje do logu** a nezahadzuje sa
do `/dev/null`, ako to bolo predtým; bez nej sa z behu nedalo zistiť, či je to
chýbajúce právo alebo niečo iné.

**Prečo to beh nezhadzuje.** Zastaviť sa hneď v tretej sekunde znelo rozumne,
ale stálo to celý deň behov: mapa nebola ŽIADNA. Pritom nasadenie funguje aj
pri zdroji z vetvy — mapa na stránke po behu **je**, len ju prepíše najbližší
push do `master`. Mapa, ktorá vydrží do ďalšieho mergu, je lepšia než nič,
a opraviť to z CI aj tak nejde. Beh preto pokračuje, do logu dá `::warning::`
a do súhrnu blok s jednorazovým návodom.

Na konci behu to ešte raz overí smoke test: stiahne koreň nasadenej stránky
a hľadá v ňom `id="map"`. Keď tam nie je a `build_type` je `legacy`, je to
`::warning::` — príčinu poznáme z prvého kroku a červený beh by k nej nič
nepridal. Keď je `build_type` `workflow` a mapa na koreni aj tak chýba, je to
`::error::`: vtedy je niečo inak, než čakáme. Ostatné kontroly pýtajú súbory, ktoré README nemá
(dlaždice, štýly, sprity) – tie by prepísanú stránku nechytili, keby na nej
z predošlého nasadenia ostali. Toto je tá jediná otázka, na ktorej
návštevníkovi záleží: čo vidí, keď otvorí adresu.

### `plan` – rýchly test (switch `test`)

Switch `test` vyreže zo stredu zvoleného výrezu **štvorec s 2 km²** a pustí
na ňom celý build – vrstevnice, skaly aj tieňovanie. Z desiatok minút sú
minúty, takže sa dá prah alebo interval overiť za jeden beh.

**Predvolene je zapnutý**, ostrý beh na celý výrez ho chce odškrtnúť. Je to
switch vo formulári a nie voľba v `options`, lebo sa preklikáva pri každom
behu; miesto uvoľnila mriežka `rock_res` (desať inputov je strop), z ktorej
je naopak voľba. Veľkosť (`test_km2=5`) a stred (`test_at=lon,lat`) ostali
voľbami – tie sa prestavujú zriedka. `test_km2` bez zapnutého switchu je
chyba: inak by to bolo číslo, ktoré nič nerobí.

Ďalej v pipeline z toho ide jedno číslo (`opt_test_km2`): 0 = ostrý beh,
inak strana štvorca v km². Vypočíta ho `parse-options.py` zo switchu
a veľkosti, takže sa nikde inde nemusí riešiť „zapnuté a koľko".

Technicky je to **to isté orezanie regiónu ako `crop_bbox`**, len bbox
nezadávaš ty. To je celý trik: orezaním REGIÓNU (a nie len výrezu) sa zmenší
aj to, čo sa inak počíta na celý kraj – tieňovanie a mapové dlaždice. Výrez
sa potom pretína s už orezaným regiónom, takže vyjde ten istý štvorec bez
druhého výpočtu. Oboje naraz je chyba: obe veci orezávajú to isté.

Dve veci, na ktoré si treba dať pozor a sú vyriešené:

- **Kľúč.** Do mien cache aj uložených výsledkov ide `…_test2`, takže si
  testovací beh nesadne na to, čo počítal ostrý.
- **Pregenerúva sa vždy všetko.** `parse-options.py` pri zapnutom teste
  prebije `rebuild` a zapne všetky tri príznaky (`contours_rebuild`,
  `rocks_rebuild`, `terrain_rebuild`), takže sa cache pre ten kľúč najprv
  zmaže a všetko sa spočíta nanovo. Testom sa ladí, a ladiť na výsledku
  z cache znamená ladiť ducha; kľúč síce nesie nastavenia aj otlačok
  skriptov, ale nie všetko. Cache ostrého behu tým netrpí – v kľúči je
  `bboxkey` a ten je pri teste bboxom štvorca.

  Platí to aj pre **podpipeline skál z tieňovania**: tá si odkladá rozrobené
  obrysy, takže by po zmene prahu nadviazala na polovicu starého výsledku.
  Build jej preto pri teste posiela `fresh=1` (vpredu, nech to vlastné
  `rock_img_options` vedia prebiť). Stiahnuté JPG dlaždice sa nezahadzujú –
  to sú vstupné dáta z cudzieho dobrovoľníckeho servera, nie výsledok.
  Rovnako ostávajú PBF, DEM dlaždice, Planetiler a glyfy: vstupy majú v kľúči
  dátum alebo otlačok zdroja, takže cez ne starý výsledok neprejde.
- **Skaly z tieňovania.** Tie počíta vlastný workflow, ktorý si výrez rieši
  sám – v testovacom režime mu preto ide dole rovno **bbox štvorca**, nie
  meno pohoria. Jeho vlastný prienik je s bboxom Slovenska, nie s regiónom,
  takže by mu pri niektorých kombináciách vyšiel iný štvorec – a to by
  znamenalo skaly mimo mapy.

Beh do súhrnu vypíše, **kde ten štvorec je**: obrázok s okolím (podklad je
tieňovanie z freemap.sk, červený štvorec = testované územie, modrý = celý
výrez), súradnice a odkaz, ktorý otvorí hotovú mapu presne tam. Robí to
`workers/test-locator.py`. Bez toho je „nenašlo ani jednu skalu" nečitateľné:
nevie sa, či sú prísne prahy, alebo len štvorec padol na lúku pod lesom.

Obrázok sa nasadí spolu so stránkou (`_site/kde-to-je.png`), preto ho súhrn
vie priamo ukázať – z artefaktu by sa musel sťahovať. Odkaz do mapy má tvar
`#map=16/49.17/20.11&region=…`; poloha v adrese je vlastnosť viewra
(`hash: "map"` v MapLibre), takže sa rovnakým odkazom dá poslať aj ľubovoľné
iné miesto a `F5` nehodí mapu späť na celý región.

**Samotná mapa sa otvorí na štvorci aj bez odkazu.** Rýchly test oreže celý
región, takže je ten štvorec bboxom regiónu v manifeste a viewer sa naň
nastaví ako na hocijaký iný región – nie je to zvláštna vetva v kóde.
Zvláštna vetva je len jedna, a rieši opačný problém: keď poloha v adrese
mieri **mimo nasadeného bboxu** (hash z minulej návštevy, starý odkaz),
viewer ju zahodí (`dropPosFromHash`) a nechá rozhodnúť bbox. Bez toho by
`F5` po testovacom builde otvoril mapu nad prázdnom dvadsať kilometrov
vedľa – a to vyzerá ako pokazený build, nie ako stará adresa. Manifest nesie
pri regióne aj `test_km2`, takže to viewer vie aj napísať do panelu
(`Rýchly test: 2 km² zo stredu výrezu`).

### `contours` a `terrain` – vrstevnice, skaly a tieňovanie z DEM

**OpenStreetMap výškové dáta neobsahuje** – má len bodový tag `ele` na
vrcholoch a sedlách. Terén preto musí prísť odinakiaľ:

| zdroj | kľúč vo výberoch | čo to je | odkiaľ | stav |
|---|---|---|---|---|
| **Sonny's LiDAR DTM 20m** | `sonny` (default) | *model terénu* z LiDARu – bez stromov a striech, mriežka 20×20 m, výška po 0,1 m | náš release `dem-sonny` (zrkadlo, viď [Stiahnuť výškové dáta](#druhý-workflow-update-dem)) | overené |
| **ÚGKK DMR 3.5** | `dmr35` | otvorené dáta ÚGKK, mriežka presne 10×10 m | náš release `dem-dmr35` (jeden 2,3 GB ZIP z `opendata.skgeodesy.sk`) | overené |
| **ÚGKK DMR 5.0 (LLS)** | `dmr5` | ten istý 1 m LiDAR, ale prevzorkovaný na 5 m, aby sa celé Slovensko zmestilo do releasu | náš release `dem-dmr5` (viď [Pripraviť DMR 5.0](#štvrtý-workflow-pripraviť-dmr-50)) | naplniť |
| **ÚGKK DMR 5.0** | `ugkk` | slovenský **1 m LiDAR** – najpodrobnejší dostupný model terénu | náš release `dem-ugkk`, jeden COG na výrez (`area`) | naplniť |

**Zdroj sa vyberá zvlášť pre každú vrstvu.** Formulár má tri výbery –
`contour_source` (vrstevnice), `rock_source` (skaly) a `shading_source`
(tieňovanie a 3D terén) – a každý ponúka ten istý zoznam modelov plus
`ziadne`, ktorým sa vrstva vypne. Kým to bol jeden `dem_source` pre všetko,
nedalo sa povedať to, čo dáva zmysel najčastejšie: skaly z najjemnejšieho
modelu (aj keď ho máme len na výrez, `ugkk`) a tieňovanie z hrubšieho, ktorý
pokrýva celý región.

Keď majú vrstevnice a skaly iný model, job si stiahne oba – každý do
`dem/<zdroj>/` s vlastným `all.vrt`, takže sa dve mozaiky nikdy neprebijú.
Pri rovnakom modeli sa druhé volanie `fetch-dem.sh` netrafí do siete vôbec.

`shading_source` **nemá `ugkk`**: 1 m LiDAR máme len na výrez, kým tieňovanie
sa robí vždy na celý región. Predtým sa to riešilo tichým prepnutím na
Sonnyho v jobe s terénom – teraz sa taká voľba nedá ani zadať.

`rock_source` má navyše hodnotu `tienovanie`: to je [piaty workflow](#piaty-workflow-skaly-z-tieňovaných-dlaždíc).
Výškový model číta aj ona – tmavé plochy z hillshade sú len maska nad pásmom
sklonu, takže `rock_dem` je pri nej `sonny`.

Zoznamy vo formulári stráži `Lint workflows` proti kľúču `for`
v [`workers/dem-sources.json`](../workers/dem-sources.json) – zdroj sa nedá
pridať do jedného a zabudnúť v druhom.

> **ÚGKK je zatiaľ otvorená otázka.** Že majú verejný ArcGIS adresár služieb
> (`zbgis.skgeodesy.sk/zbgis/rest/services`), je isté. Ktorá z tých služieb je
> DMR 5.0 v plnom rozlíšení – a či vôbec nejaká – zdokumentované nie je;
> oficiálne sa DMR 5.0 dáva cez ZBGIS Mapový klient (interaktívny export do
> 400 km²) a cez vládny cloud, čo sa v pipeline použiť nedá.
>
> Hádať sa to nedá, tak to zrkadlo skúša **tri cesty za sebou** a berie prvú,
> ktorá dá skutočný výškový raster (kontroluje sa veľkosť bunky aj dátový typ,
> takže 10 m model ani obrázok neprejdú): priame URL → ArcGIS `exportImage`
> (vrátane služieb objavených v ich adresári) → WCS `GetCoverage`. Priame URL
> sú istota: v ZBGIS Mapovom klientovi *Terén → Export údajov → DMR 5.0* si
> vyberieš územie do 400 km², odkazy vložíš do `ugkk_urls` a zrkadlo ich
> stiahne, zlepí a odloží.
>
> **1 m ide len na výrez.** Celý kraj má pri 1 m 16 miliárd buniek, teda 64 GB
> vo Float32 – to sa nezmestí ani do release assetu (strop 2 GB). Build to
> odmietne v prípravnom jobe, nie po hodine sťahovania.
>
> Licencia ÚGKK je voľná aj komerčne, ale **podmienená uvedením zdroja** –
> atribúcia je preto v `poc/web/themes.js` natvrdo.

Iný zdroj sa nepoužíva. **Copernicus GLO-30 ako záloha je zámerne vypnutý** –
je to model *povrchu*, takže vrstevnice by v lese viedli po korunách stromov
a skaly by vychádzali z vegetácie. Keby sa ním chýbajúce dlaždice ticho
dopĺňali, časť mapy by klamala a nebolo by vidieť ktorá. Kde dlaždica nie je,
tam radšej nebude terén – build to vypíše ako varovanie so zoznamom a zlyhá
až vtedy, keď pre dané územie nie je ani jedna dlaždica.

```
DEM dlaždice 1°×1° pre bbox (N49E019.tif)
  │  gdalbuildvrt   … zlepí dlaždice do jedného virtuálneho rastra
  │
  ├─ vrstevnice ─────────────────────────────────────────────
  │    gdalwarp       … oreže na bbox (voliteľne zjemní, viď nižšie)
  │    gdal_contour   … vytrasuje izolínie po `contour_interval` metroch
  │    ogr2ogr        … dopočíta atribút `level`
  │
  ├─ skaly ──────────────────────────────────────────────────
  │    workers/rock-areas.py
  │    a) sklon PO ČASTIACH (pamäťovo drahé), na disk:
  │      gdalwarp -t_srs EPSG:3035 … do metrickej projekcie, mriežka `rock_res`
  │      gdaldem slope             … sklon v stupňoch
  │      gdal_translate -ot Int16  … stotiny °, aby sa mozaika zmestila
  │      gdalbuildvrt              … mozaika sklonu celého územia
  │    b) vektorizácia NARAZ nad mozaikou:
  │      gdal_contour -p -fl …     … izolínie sklonu ako plochy (s dierami)
  │      -explodecollections       … samostatné skaly
  │      filter najmenšej plochy   … + `class`, `slope`, `area`
  │      -simplify                 … preč so schodíkmi po hranách buniek
  │      smooth-polygons.py        … zaoblenie rohov, čo po zjednodušení
  │                                  ostali ostré (Chaikin, 2 prechody)
  │
  └─ tieňovanie a 3D ────────────────────────────────────────
       workers/build-terrain.py … terrarium PNG dlaždice
                                 → terrain/{z}/{x}/{y}.png
                                 → release `dem-terrain` (.tar.zst)
  │
  │  planetiler generate-custom --schema=workers/contours.yml
  ▼
{región}-contours.pmtiles   (vrstvy `contour` a `rock`)
```

- **`level`** rozdelí vrstevnice na `major` (po 100 m), `mid` (50 m) a
  `minor` (10 m). Vďaka tomu ich štýl vie zapínať postupne podľa zoomu a
  kresliť rôzne hrubo – inak by na malých mierkach splynuli do plochy.
- **Zjemnenie (`contour_smoothing`, default 0 = vypnuté).** DEM je v 1″
  (~30 m). Priemerovanie na hrubšiu mriežku (`gdalwarp -tr … -r average`)
  vyhladí šum a vrstevnice sú „krajšie", ale zároveň zje detail terénu.
  Predvolene sa preto **netrasuje z ničoho zjemneného**, ale z plného
  rozlíšenia; kto chce hladšie krivky, nastaví napr. `2` (pôvodné správanie).
- **Prečo sa skaly nepočítajú z hustoty vrstevníc.** Husté vrstevnice sú len
  *dôsledok* veľkého sklonu – ich hustota navyše závisí od zvoleného intervalu
  a od zoomu, na ktorom sa pozeráš. Rovnaká informácia je v DEM priamo a
  presnejšie, preto sa počíta sklon (`gdaldem slope`) a prahuje sa on.
  Sklon sa musí počítať v **metrickej projekcii**: v stupňoch je 1° po dĺžke
  u nás asi o tretinu kratší než 1° po šírke, takže by vyšiel skreslený podľa
  smeru svahu.
- **Tvar skaly je tvar terénu.** Obrys je izolínia sklonu – presne tá čiara,
  kde svah prekročí prah. Vzniká tak zubatý pás pod hrebeňom, oblúk okolo
  žľabu, ostrov brala v suti. Do augusta 2026 tu bola mriežka štvorčekov
  (`rock_piece`); je preč, lebo skaly štvorcové nie sú.
- **Jedna trieda, jedna sivá.** Skala je v mape jedna plocha v jednej sivej
  bez priehľadnosti: jedno pásmo, teda žiadna plocha vnútri inej. Diery
  **ostávajú** – zapĺňanie (`ST_BuildArea(ST_ExteriorRing(geom))`) je vlastná
  voľba `rock_zapln_diery` a je vypnutá, lebo zo skál robí súvislé klaksy bez
  tvaru.

  Priehľadnosť by totiž znamenala, že každý prekryv je vidieť – dve plochy
  cez seba vyjdú tmavšie než jedna. Plná farba to rieši na úrovni kreslenia,
  takže sa plochy nemusia strážiť proti sebe.

  `options: rock_plne=0` vráti pôvodné správanie: dve pásma (`steep` od
  prahu, `cliff` od `--cliff`) a **diery** tam, kde je vnútri steny miesto
  s menším sklonom (polica, terasa, zarastený stupeň). Robí ich priamo
  `gdal_contour -p`: pásmo `[prah, ∞)` je polygón s vnútornými prstencami
  tam, kde hodnota pod prah klesla.
- **Vektorizuje sa naraz, nie po častiach – a je to nutné.** Pôvodne sa každá
  časť územia vektorizovala zvlášť, orezala (`-clipsrc`) a výsledky sa lepili
  cez `ST_Union`. To diery ničí: diera prerezaná hranicou časti sa zmení na
  zárez v okraji a späť sa už nezlepí. Namerané na syntetickom teréne
  (prstencová terasa v kuželi):

  | postup | plôch | dier |
  |---|--:|--:|
  | celý raster naraz (referencia) | 2 | 2 |
  | po častiach + `ST_Union` | 4 | **0** |
  | **sklon po častiach, vektorizácia naraz** | **2** | **2** |

  Preto sa **po častiach počíta len raster sklonu** – to je tá pamäťovo drahá
  časť – zapíše sa na disk a `gdal_contour` ide jedným priechodom nad celou
  mozaikou. Výsledok potom nezávisí od toho, na koľko častí sa počítalo:
  overené pri 1, 12 aj 60 častiach je zhodný do posledného m².
- **Mozaika sklonu je Int16 v stotinách stupňa – a to je dôvod, prečo obrys
  nie je zubatý.** Pôvodne to bol `Byte` s krokom 0,5°, čo je na prahovanie
  „dosť" len na prvý pohľad: pri hrubom kroku vznikajú v poli sklonu plošiny
  a izolínia po nich chodí po hranách buniek, teda schodíkmi. Namerané na tom
  istom území:

  | kvantizácia | plôch | bodov na plochu | raster |
  |---|--:|--:|--:|
  | Byte 0,5° | 481 | 844 | 5,5 MB |
  | **Int16 0,01°** | **319** | **1 328** | **26,6 MB** |
  | Float32 (presne) | 321 | 1 320 | 122,1 MB |

  Byte navyše plochy *rozbíjal* – 481 namiesto 319, lebo plošiny na prahu
  vyrábajú falošné úlomky. Int16 je prakticky zhodný s presným `Float32` pri
  štvrtinovej veľkosti. Prahy sa do `gdal_contour` dávajú vynásobené stovkou.
- **Obrys sa zjednodušuje o štvrtinu bunky** (`ROCK_SIMPLIFY: -1`). To zmaže
  schodíky po hranách buniek, ale čiaru neposunie o viac než štvrtinu mriežky:
  bodov na obrys klesne 5,7× (423 763 → 74 395) a **počet plôch sa nezmení
  vôbec**. `0` to vypne.
- **…a potom sa rohy zaoblia** (`ROCK_SMOOTH: 2`, `workers/smooth-polygons.py`).
  Zjednodušenie má vedľajší účinok, ktorý bolo vidieť pri max zoome: schodíky
  zmizli, ale to, čo po nich ostalo, sú **ostré rohy**. Zubatosť teda nerobil
  raster, ale práve to zjednodušenie. Namerané na jednom území (326 plôch,
  mriežka 4 m, prah 50°):

  | úprava | bodov | priemerný lom | lomov > 60° |
  |---|--:|--:|--:|
  | bez úprav | 640 021 | 4,6° | 0,1 % |
  | `-simplify 0,5 m` | 91 256 | **28,5°** | 0,9 % |
  | + 1× Chaikin | 181 975 | 14,3° | 0,4 % |
  | **+ 2× Chaikin (default)** | **363 341** | **7,7°** | **0,1 %** |

  Chaikinovo orezávanie rohov nahradí každý roh dvomi bodmi v 1/4 a 3/4 hrany,
  takže sa jeden lom rozdelí na dva polovičné. Dva prechody dajú hladší obrys
  než pôvodný raster a stále o 43 % menej bodov než nezjednodušený originál.
  Diery ostávajú dierami – zaobľuje sa každý prstenec zvlášť.

  **Čo sa neosvedčilo:** vyhladiť raster sklonu (priemer 3×3) pred
  vektorizáciou. Obrys sa síce zjemní, ale priemerovanie zrazí špičky sklonu
  a okolo prahu z toho vznikne množstvo drobných úlomkov – z 326 plôch bolo
  naraz **1668**. Preto sa hladí až hotová geometria, nie raster.
- **Časti sa počítajú s presahom** niekoľkých pixelov, aby sklon na okraji
  nebol zrezaný, a zapisujú sa až orezané presne na svoju hranicu. Hranice sú
  prichytené na mriežku, takže dlaždice mozaiky na seba sadnú bez medzery aj
  bez prekryvu. Merané ~2,5 mil. buniek/s → kraj pri 2 m okolo 30 minút.
- **Veľkosť plôch neurčuje mriežka, ale prah sklonu.**
  Súvislá stena nad prahom je jedna plocha, nech ju počítaš na akejkoľvek
  mriežke. Namerané na výreze Vysokých Tatier pri mriežke 2 m:

  | prah | plôch | plocha spolu | priemerná | najväčšia |
  |---|---|---|---|---|
  | 40° | 1 299 | 2 931 ha | 22 567 m² | **428 ha** |
  | 45° | 1 019 | 1 710 ha | 16 788 m² | 82 ha |
  | **50° (default)** | **719** | **884 ha** | **12 295 m²** | **38 ha** |
  | 55° | 402 | 389 ha | 9 698 m² | 30 ha |
  | 60° | 208 | 131 ha | 6 301 m² | 18 ha |

  Pri 40° má najväčšia súvislá plocha 428 ha – to už nie je skala, ale celý
  strmý svah. Preto je predvolený prah 50°.
- **Skala je vždy pásmo sklonu – aj pri `rock_source: tienovanie`.** Tmavé
  plochy z hillshade sa nepoužijú ako skaly, ale ako **maska** (`--clip`):
  pred vektorizáciou sa do dlaždíc sklonu vypáli mimo nich nula
  (`gdal_rasterize -i -burn 0`), takže pásmo nad prahom tam nevznikne. Von
  ide prienik „tmavé **a** strmé". Bez toho pokryli samotné tmavé plochy na
  testovacom výreze v Tatrách 34 % územia a v mape z nich bola jedna sivá
  deka; podrobne v časti [„Piaty workflow"](#piaty-workflow-skaly-z-tieňovaných-dlaždíc).
- **Poistka proti sivej deke.** Keď skaly vyjdú na viac než **40 %** počítaného
  územia, beh zakričí `::warning::`. Toľko skál nikde nie je – Vysoké Tatry
  majú pri 50° 1,6 % – takže je to spoľahlivý podpis chyby (zlý prah,
  prehodená maska), nie prísna hranica. Skaly sa kreslia jednou sivou bez
  priehľadnosti, takže sa taká chyba v mape neprejaví ako „veľa skál", ale
  ako plocha, v ktorej nie je vidieť nič.
- **Testovací výrez** (`area`). Terén je najdrahšia časť buildu, tak sa dá
  počítať len na kuse regiónu – pri ladení prahu, mriežky alebo zdroja netreba
  čakať polhodinu na celý kraj. Platí na **vrstevnice aj skaly**; sťahovanie
  Sonnyho sa neobmedzuje (dlaždice sú v cache pod kľúčom celého regiónu
  a čiastočné stiahnutie by sa nabudúce vrátilo ako keby bolo úplné), ÚGKK
  naopak ide len na výrez. Input berie buď názov pohoria zo
  [`workers/areas.json`](../workers/areas.json) (`vysoke_tatry`, `tatry`,
  `slovensky_raj`, …), alebo bbox `W,S,E,N`. Po orezaní na Prešovský kraj:

  | `rock_area` | plocha | skaly |
  |---|--:|--:|
  | *(prázdne)* | 16 103 km² | ~30 min |
  | `tatry` | 1 032 km² | ~2 min |
  | `vysoke_tatry` | 541 km² | ~1 min |
  | `belianske_tatry` | 177 km² | <1 min |

  Výrez sa vždy pretne s bboxom regiónu (mimo neho nie je ani DEM, ani mapa)
  a keď sa neprekrývajú vôbec, build to povie rovno a zastaví sa. Je aj
  **v mene uloženého assetu** (`rock-{región}-{výrez}-…`) **a v kľúči cache**,
  takže sa skaly z Tatier nikdy nevydávajú za skaly celého kraja. Že sú skaly
  len na výreze, hlási build ako `::warning::` aj v súhrne – taký beh nie je
  na nasadenie, je na ladenie.
- **Koľko to bude trvať, sa povie dopredu.** Skript ešte pred prvým
  `gdalwarp`om vypíše plán – rozmer územia, počet buniek, koľko častí sa
  preskočí, odhad času sklonu aj obrysov, veľkosť mozaiky a špičku pamäte –
  a keď je odhad nad rozpočtom (`ROCK_BUDGET_MIN`, default 100 min), **vôbec
  sa nezačne** a povie, čo zmenšiť. Trojhodinový beh, ktorý spadne na timeout
  jobu, minie celý rozpočet a nevyrobí nič; toto to zastaví za pár sekúnd.
  Konštanty sú namerané na runneri: sklon 5,1 mil. buniek/s, obrysy
  3,5 mil./s.

  | územie | `rock_res` | buniek | odhad |
  |---|--:|--:|--:|
  | Prešovský kraj | 1 m | 19,60 mld. | 2:37:21 ✗ |
  | Prešovský kraj | **2 m** | 5,27 mld. | 0:42:18 ✓ |
  | Prešovský kraj | 3 m | 2,57 mld. | 0:20:38 ✓ |
  | Tatry | 1 m | 1,34 mld. | 0:10:46 ✓ |
  | Vysoké Tatry | 1 m | 0,71 mld. | 0:05:44 ✓ |
  | Belianske Tatry | 1 m | 0,23 mld. | 0:01:49 ✓ |

- **Počas výpočtu je vidieť, čo sa deje.** Pri počítaní sklonu ide po každej
  časti riadok s odpracovaným časom, odhadom zvyšku a veľkosťou mozaiky;
  `gdal_contour` hlási percentá a nezávisle od neho beží *tep* každých 30 s
  (`ROCK_HEARTBEAT_S`) s časom behu, pamäťou procesu a miestom na disku.
  Predtým bola vektorizácia hodinu a pol úplne ticho a z logu sa nedalo
  odlíšiť „počíta" od „zaseklo sa".
- **Časti mimo územia sa preskočia.** EPSG:3035 je pootočená voči poludníkom,
  takže obdĺžnik opísaný bboxu je v metroch väčší než región – pri Prešovskom
  kraji 208×111 km namiesto 200×82 km. Časti, ktoré do bboxu vôbec
  nezasahujú, sa nepočítajú (26 zo 170 pri 1 m).
- **Poistka na pamäť.** Keď `gdal_contour` prekročí `ROCK_MAX_RSS_GB`
  (default 12 GB), tep ho zastaví s hláškou – lepšie než tiché zabitie
  runnera na OOM, po ktorom v logu nie je nič.
- **Aký je to detail a kto ho vyberá.** `rock_res: auto` (default) nechá
  mriežku vybrať `rock-areas.py`: zoberie najjemnejšiu z rebríčka
  0,5 / 1 / 1,5 / 2 / 3 / 4 / 5 / 8 / 10 / 15 / 20 m, ktorá naraz

  1. **sa zmestí do rozpočtu času** (`ROCK_BUDGET_MIN`, default 100 min) – to
     je ten istý odhad, ktorý inak beh zastaví, len použitý dopredu, a
  2. **má pri danom DEM ešte zmysel** – dolný strop je desatina bunky
     zdrojového modelu, najmenej 0,5 m.

  Ten druhý strop je dôležitejší, než sa zdá: **Sonny má pre Slovensko bunku
  ~20 m**, takže pri ňom auto vždy skončí na 2 m. Jemnejšia mriežka by len
  interpolovala medzi tými istými výškami – stála by štvornásobok času a
  nepridala ani jeden nový tvar terénu. Reálny skok v detaile prinesie až iný
  zdroj (`rock_source: ugkk`, 1 m LiDAR → auto ide na 0,5 m).

  Výber sa celý vypíše do logu, aj s tým, koľko by ktorá mriežka trvala.
  Namiesto čísla sa dá `rock_res` zadať aj natvrdo – je to voľba, nie input
  vo formulári: `options: rock_res=1`.

  Najmenšia ponechaná plocha je **jedna bunka vybranej mriežky** (pri 2 m
  teda 4 m²) – menší útvar už nie je tvar terénu, ale rohy jedinej bunky.
  Presné čísla za konkrétny beh píše `rock-areas.py` do
  `contours-out/rock-stats.txt` a build ich vypíše v [súhrne](#súhrn-buildu).
- **Skaly sú vidieť všade, kde sú.** Vrstva `rock` ide do dlaždíc od **z9**
  (predtým z13) a štýl ich kreslí od z9, obrys od z11. Nižšie zoomy to
  nezaťaží: Planetiler na nich zahadzuje prvky menšie než pixel, takže
  z prehľadu ostanú len veľké steny a detaily pribúdajú s priblížením. Na
  najvyššom zoome je ten filter zámerne vypnutý
  (`--min_feature_size_at_max_zoom=0`), aby neodpadli ani tie najmenšie.
- **Prečo `gdal_contour -p`, a nie polygonizácia rastra.** Polygonizácia by
  obkreslila pixely, teda schodíky; izolínia sklonu má body interpolované
  medzi bunkami, takže je okraj hladký a bodov výrazne menej.
- **`class`** rozlišuje `steep` (nad prahom `rock_slope`, default 50°) a
  `cliff` (o `ROCK_CLIFF_PLUS` = 15° viac) – štýl z toho kreslí svetlejšiu
  a tmavšiu sivú. Atribút `area` (plocha v m²) je v dlaždiciach tiež, nech sa
  dá v štýle rozlíšiť bralo od odrobinky.
- **Hotové skaly sa neprepočítavajú.** Ukladajú sa do releasu `dem-rocks` ako
  `rock-{región}-s{prah}-g{mriežka}.gpkg.zst`; ďalší build s tými istými
  nastaveniami ich len stiahne (sekundy namiesto desiatok minút). Iné
  nastavenia = iné meno assetu, takže sa nikdy nepomiešajú.
- **Vrstevnice aj skaly sú vektor** vo vektorových dlaždiciach – žiadne
  rastre. Na najvyššom zoome ide geometria do dlaždíc bez zjednodušovania
  (`--simplify_tolerance_at_max_zoom=0`), takže obrys skaly aj priebeh
  vrstevnice sedia presne tam, kam ich položil DEM.
- **Cache.** Vrstevnice aj skaly závisia len od územia, obsahu releasu s DEM,
  intervalu, maxzoomu, zjemnenia a prahu sklonu – nie od toho, čo sa zmenilo
  v OSM. Sú preto nacacheované podľa týchto parametrov a pri ďalšom builde
  mapy sa nepočítajú znova.
- **Vlastný `.pmtiles`** (nie súčasť mapových dlaždíc) práve preto, aby sa
  dali cacheovať zvlášť a aby ich štýl vedel vypnúť bez prebuildu mapy.

#### Tieňovanie reliéfu a 3D terén

MapLibre potrebuje výšky ako pyramídu PNG dlaždíc (kódovanie *terrarium*),
z GeoTIFFu čítať nevie. [`workers/build-terrain.py`](../workers/build-terrain.py)
ich vyrobí z toho istého Sonny DEM – každý zoom prevzorkuje z DEM nanovo
priemerom, lebo priemerovať sa musí *výška*, nie zakódovaná farba.

Sú drahé na výpočet, ale závisia len od územia, takže sa raz uložia do releasu
`dem-terrain` ako jeden `.tar.zst` na región a maxzoom; ďalší build ich už len
stiahne. Input `terrain_rebuild` ich vynúti prepočítať nanovo.

#### Čo všetko sa cachuje

Build sťahuje viac vecí, než len DEM, a všetky majú vlastnú cache:

| čo | kľúč | prečo |
|---|---|---|
| `{región}.osm.pbf` | región + orez + **dátum** | osm.fr exporty sú denné, v ten istý deň netreba sťahovať znova |
| `planetiler.jar` | dátum | 89 MB pri každom behu |
| DEM dlaždice | otlačok releasu + bbox | desiatky MB na dlaždicu |
| výškové dlaždice | otlačok releasu + bbox + maxzoom | drahé na výpočet |
| vrstevnice a skaly | + interval, prah, mriežka, kúsok | hodiny výpočtu |
| hotové skaly (release `dem-rocks`) | región + nastavenia v mene assetu | desiatky minút výpočtu; `rocks_rebuild` ich prepočíta |
| glyfy a sprity | hash zoznamu zdrojov | menia sa len so zmenou kódu |
| zdroje Planetileru | pevný | water polygons, Natural Earth |

Všetky okrem zdrojov Planetileru sú rozdelené na `actions/cache/restore` hore
a `actions/cache/save` hneď za krokom, ktorý dáta vyrobí. Obyčajné
`actions/cache` totiž zapisuje až v post-kroku a **iba keď celý job dobehne
úspešne** – keď build spadne o hodinu neskôr na niečom úplne inom, zahodí sa
aj to, čo sa medzitým vypočítalo, a ďalší beh začína zase od nuly. Save kroky
majú preto `if: always()` a ukladajú len vtedy, keď restore netrafil a súbory
naozaj vznikli. Sprity sa ukladajú ešte **pred** zapečením vzorov do atlasu,
aby sa do cache nedostal už dopečený sprite.

Vrstevnice sa robia **pred** mapovými dlaždicami zámerne – viď [rozpočet
veľkosti](#rozpočet-veľkosti).

#### Pregenerovanie

Cache aj release existujú preto, aby sa to isté nepočítalo dvakrát. Keď sa
zmenia nastavenia, zmení sa kľúč a prepočíta sa to samo. Keď to treba
prepočítať **nanovo aj pri rovnakých nastaveniach**, slúžia na to inputy:

| `rebuild` | čo pregeneruje |
|---|---|
| `vrstevnice` | vrstevnice **aj skaly** – zmaže cache `contours-…` a trasuje z DEM odznova |
| `skaly` | skaly – zmaže cache aj asset v release `dem-rocks` (vrstevnice sa prepočítajú s nimi, sú lacné) |
| `teren` | tieňovanie a 3D terén – zmaže cache aj asset v release `dem-terrain` |
| `vsetko` | všetko z toho naraz |

**Rýchly test (switch `test`) prebíja `rebuild` a pregenerúva vždy všetko** –
viď [rýchly test](#plan--rýchly-test-switch-test). Vo formulári teda `rebuild`
pri zapnutom teste nič nemení.

Mechanika je dôležitá, lebo nie je zrejmá: **cache sa v GitHube nedá
prepísať.** Kľúč, ktorý raz existuje, si drží starý obsah a `cache/save` naň
len upozorní, že už tam je. Keby sa teda `rebuild` len „prepočítal a uložil",
uloženie by nič nespravilo a ďalší build by dostal späť starú verziu. Preto:

1. build má právo `actions: write`,
2. krok *Pregenerovanie – zmaž staré cache* zmaže príslušný záznam
   (`gh cache delete`) hneď na začiatku,
3. restore sa pri pregenerovaní **preskočí**, takže výpočet beží,
4. save uloží novú verziu pod ten istý kľúč.

Kľúče sa počítajú na jednom mieste (krok *Kľúče cache*) a používa ich restore,
save aj mazanie – keby boli napísané trikrát, stačí ich raz zabudnúť opraviť
a cache sa ticho rozsype: ukladala by sa pod iným kľúčom, než sa hľadá.

> **Príznaky sú reťazce, nie booleany.** Výstup jobu je vždy text a vo výraze
> je pravdivý každý neprázdny reťazec – teda aj `"false"`. `if: ${{ x }}`
> preto platí vždy a `if: ${{ !x }}` nikdy. Presne to sa tu aj stalo:
> podmienky restore boli písané ako `!needs.plan.outputs.opt_contours_rebuild`
> a znamenali „nikdy nereštoruj", takže sa cache nikdy nepoužila a každý beh
> počítal vrstevnice, skaly aj tieňovanie odznova. Navonok to nevyzerá ako
> chyba – build je zelený, len trvá hodinu namiesto minút. Preto sa všade
> porovnáva s `'true'` (resp. `!= 'true'`) doslova.

Ostatné cache (PBF, Planetiler, DEM dlaždice, glyfy, sprity) sa
nepregenerúvajú vôbec – sú to stiahnuté dáta, nie výpočet, a majú v kľúči buď
dátum, alebo otlačok zdroja.

### `trails` – značené trasy z OSM relácií

**Trasa nie je cesta.** V OSM je značená trasa `type=route` **relácia**, ktorá
zbiera cudzie cesty a sama nesie značenie: farbu pásika (`osmc:symbol`,
`colour`), sieť (`network=rwn` a spol.), názov, `ref`. Schéma OpenMapTiles
relácie trás **nemá** – v dlaždiciach ostane iba cesta (`class=path`), a z tej
sa nedá zistiť, či po nej vedie červená turistická, dve cyklotrasy, alebo nič.
Preto majú trasy vlastný krok a vlastný `.pmtiles`:

```
data/region.osm.pbf
  → osmium tags-filter r/route=hiking,foot,…   len relácie trás a ich členovia
  → workers/trail-routes.py (pyosmium)         relácie → línie s pruhmi
  → data/trails.geojson
  → planetiler generate-custom --schema=workers/trails.yml
  → {región}-trails.pmtiles
```

**Prečo predfilter.** Index polôh uzlov nad celým Slovenskom (~380 MB PBF) by
zobral niekoľko GB pamäte. `osmium tags-filter` nechá len relácie trás **aj
s členmi** (cesty vrátane ich uzlov), čo je zlomok veľkosti – a až nad tým
beží pyosmium.

**Jedna línia na dvojicu (cesta, trasa).** Po jednej ceste vedie bežne viac
trás naraz. Každá dostane vlastnú kópiu geometrie a vlastný **pruh** (`off`
= 0,5 · 1,5 · 2,5 …), takže štýl ich cez `line-offset` rozostrie **vedľa**
cesty:

```
── cesta ────────────────    zostane vidieť, aká to je cesta
━━ červená (off 0,5) ━━━━
━━ modrá   (off 1,5) ━━━━    druhá trasa po tej istej ceste
```

Poradie pruhov závisí len od vlastností trasy (sieť → druh → farba → id
relácie), nie od poradia členov v relácii – dôležitejšia trasa je vždy bližšie
k ceste a dve trasy si na susedných úsekoch pruhy neprehodia.

**Smer čiary sa normalizuje.** `line-offset` posúva podľa smeru geometrie,
takže dva susedné úseky nakreslené proti sebe by mali pásik raz vľavo a raz
vpravo. Každá línia sa preto otočí tak, aby začínala na západnejšom konci.

**Duplikáty sa zahadzujú.** Nadradená trasa (superroute) a jej časť sú v OSM
dve relácie na tých istých cestách. Bez toho by vedľa seba boli dva rovnaké
pásiky – čo nie je informácia, ale chyba v mape.

**Farba.** Berie sa z `osmc:symbol` (prvé pole je farba pásika na strome), inak
z `colour`/`color`. Pomenované farby (`red`, `blue`, …) idú do dlaždíc ako
meno, nie ako hex – štýl si k nim priradí farbu z palety, takže „červená
značka" vyzerá v každej téme ako červená značka a v developer móde sa dá
doladiť. Hex sa na pomenovanú farbu zaokrúhli, keď je dosť blízko (`#e01b24`
je červená), inak ide do dlaždíc tak, ako je, a štýl ho použije priamo.

**`tier` riadi, od akého zoomu je trasa v dlaždiciach:** medzinárodná
a národná od z8, regionálna od z10, miestna od z12. Diaľkovú trasu má zmysel
vidieť aj z prehľadu, miestny okruh až vtedy, keď je vidieť aj cesta pod ním.
Keď trasa sieť nemá, rozhodne `distance` (nad 150 km = národná, nad 50 km =
regionálna).

**Zlepovanie úsekov.** Schéma má `tile_post_process: merge_line_strings` –
trasa je poskladaná z desiatok krátkych ciest a na 200-metrovom úseku sa
nezmestí ani slovo názvu. Zlepia sa len úseky s **rovnakými atribútmi**, teda
tej istej trasy v tom istom pruhu.

Job sa **necachuje**: celé je to pár minút a závisí od PBF, ktoré sa mení
denne – cache by sa trafila len v ten istý deň. Vypína sa voľbou
`options: trails=false` (jediná vrstva bez výberu zdroja – ide z toho istého
PBF ako mapa, takže niet z čoho vyberať), zoom dlaždíc riadi `trails_maxzoom`
(default 14).

### `features` – čo schéma OpenMapTiles vôbec nemá

**Schéma sa pozerá len na tridsať kľúčov.** V celom
`openmaptiles/planetiler-openmaptiles` sa slovo `embankment` nevyskytuje ani
raz – a rovnako `barrier` ako línia, `power`, `man_made=cutline`, `piste:type`,
`natural=cave_entrance` či `man_made=tower`. Nie je to nastavenie, ktoré by sa
dalo zapnúť: tie prvky v základných dlaždiciach jednoducho **nie sú**. Preto sa
z toho istého PBF ťahajú druhýkrát, vlastnou schémou:

```
data/region.osm.pbf
  → osmium tags-filter --expressions=workers/features-filter.txt
  → data/features.osm.pbf                      (Andorra: 3,4 MB → 198 kB)
  → planetiler generate-custom --schema=workers/features.yml
  → {región}-features.pmtiles
```

Štyri vrstvy, `class` rozlišuje čo to je:

| vrstva | čo v nej je | od zoomu |
|---|---|--:|
| `feature_line` | násyp, zárez, múr, hradby, plot, živý plot, elektrické vedenie, priesek, nadzemné potrubie, stromoradie, priehradný múr, hať, výmoľ | 11–15 |
| `feature_area` | parkovisko, skládka, halda, hospodársky dvor, skleníky, opustený priemysel, kamenné pole | 11–14 |
| `feature_point` | prameň, vodopád, jaskyňa, závrt, rozhľadňa, stožiar, vodojem, kríž pri ceste, pomník, archeologické nálezisko, štôlňa, útulňa, horský priechod, núdzový bod, geodetický bod | 11–15 |
| `piste` | zjazdovka, bežkárska trať, skialp, sánkarská dráha – čiara aj plocha, s obťažnosťou | 11 |

**Zoomy sú tu hlavné rozhodnutie, nie estetika.** Plotov je v OSM viac než
všetkých ciest dokopy, takže idú až od z15; vedenie vysokého napätia je
v otvorenej krajine orientačný bod na kilometre, takže od z11. Je to priamo
veľkosť súboru.

**Prečo predfilter.** Bez neho by Planetiler prečítal celý región druhýkrát,
aj s indexom polôh uzlov. Zoznam tagov je vo `workers/features-filter.txt`
vedľa schémy, nech sa obe menia na jednom mieste, a je zámerne **širší** než
schéma – filter je hrubé sito, presné rozhodnutie robí `features.yml`. Overené,
že nič nestráca: nad Andorrou dá filtrovaný aj nefiltrovaný PBF presne tie isté
počty prvkov vo všetkých štyroch vrstvách.

**Zjazdovka je raz čiara a raz plocha.** Uzavretá cesta s `piste:type` vyjde
ako plocha AJ ako čiara, takže dostane výplň s obrysom; otvorená len čiaru
(os zjazdovky). Obe idú do vrstvy zámerne – presne tak to má vyzerať.

**Násyp aj bralo sa kreslia zúbkami.** Kolmé čiarky MapLibre nevie, takže sú
z druhej čiary: širokej, prerušovanej a odsunutej nabok (`line-offset`).
Kladný offset je vpravo v smere čiary a presne tam je podľa konvencie OSM
dolná strana.

Job sa **necachuje** a beží súbežne so všetkým ostatným. Vypína sa voľbou
`options: features=false`, zoom dlaždíc riadi `features_maxzoom` (default 15 –
nižšia hodnota ticho zahodí triedy s vyšším `min_zoom`, job na to upozorní).
Podiel na rozpočte stránky je `BUDGET_FEATURES_PCT` (4 %).

**Čo do `features` NEPATRÍ, hoci to tak vyzerá:** `natural=cliff`, `ridge`
a `arete`. Tie v základných dlaždiciach **sú** – Planetiler ich dáva ako línie
do vrstvy `mountain_peak` (od z13). Chýbala len kresba v štýle; teraz sú tam
ako bralné hrany so zúbkami a hrebene čiarkovane.

### `tiles` – PBF → PMTiles (Planetiler)

Jadro pipeline. [Planetiler](https://github.com/onthegomap/planetiler) prečíta
`.osm.pbf`, aplikuje **profil OpenMapTiles** a vygeneruje dlaždice pre zoomy
0 až `maxzoom`. Čo pritom robí:

- **triedi značky do vrstiev** – z `highway=primary` spraví feature vo vrstve
  `transportation` s `class=primary`; z `natural=wood` plochu v `landcover`,
- **zjednodušuje geometriu podľa zoomu** – na z6 nemá zmysel kresliť každý
  ohyb cesty, bod od bodu by aj tak padol do toho istého pixela,
- **zahadzuje drobnosti na malých mierkach** – lavička na z8 je šum,
- **spája susedné plochy** tam, kde by inak vznikla mozaika.

Parametre, ktoré tu používame, a prečo:

| parameter | prečo |
|---|---|
| `--maxzoom` / `--render_maxzoom` | Planetiler má tvrdý strop **16** (`PlanetilerConfig.MAX_MAXZOOM`); vyššia hodnota build zhodí. Priblíženie na z20 rieši overzoom v MapLibre |
| `--min_feature_size_at_max_zoom=0` | na najvyššom zoome nezahadzuj nič – overzoom by inak zväčšoval dieravé dáta |
| `--simplify_tolerance_at_max_zoom=0` | rovnako: presná geometria, aby overzoomovaná dlaždica nebola hranatá |
| `--transportation_z13_paths=true` | všetky chodníky a cestičky (dôležité pre turistiku) |
| `--building_merge_z13=false` | samostatné budovy namiesto zlepencov – potrebné pre 3D |
| `--languages=sk,en` | do dlaždíc idú len tie jazykové varianty názvov, ktoré naozaj použijeme |

### `assets` – sady ikoniek → SDF sprity

Z každého zdroja v [`poc/web/icon-sources.js`](../poc/web/icon-sources.js) sa
stiahne hotový sprite (PNG + JSON) a prerobí sa
([`workers/build-sdf-sprite.mjs`](../workers/build-sdf-sprite.mjs)):

1. **Odstránenie podkladu.** Ikony nie sú čisté symboly – osm-liberty kreslí
   každý v bielom koliesku, osm-bright so svetlým halom. Podklad sa nedá
   odfiltrovať farbou (niektoré symboly sú svetlejšie než obrys kolieska),
   preto sa odčíta. Skript skúša stratégie v poradí:
   - **šablóna skupiny** – koliesko je vo všetkých ikonách rovnakej veľkosti
     identické, takže najčastejšia hodnota po pixeloch cez celú skupinu dá
     presné pozadie a zvyšok je symbol,
   - **vlastná farba podkladu** – pre „odznaky" typu biele „P" na modrom
     štvorci, kde je symbol oproti šablóne rovnaký ako pozadie,
   - **oddelenie podľa jasu** – pre symboly so svetlým halom bez kolieska,
   - **celá silueta** – keď nesedí nič (jednofarebné ikony ako šípka).
2. **Prevod na SDF.** Alfa kanál sa prepočíta na *signed distance field*
   (konvencia mapbox/tiny-sdf: `alfa = 255 − 255·(d/radius + 0.25)`, hrana
   symbolu leží na hodnote 0,75, ktorú hľadá shader MapLibre). Až vďaka tomu
   sa dá ikone v štýle nastaviť `icon-color` a `icon-halo-color`.
3. **Preskladanie atlasu** s rámikom okolo každej ikony, aby mal obrys kam
   kresliť.

Nasadzujú sa **všetky** sady naraz (sú malé), takže sa dajú v developer móde
prepínať naživo; vybraná sa zapečie do statického štýlu pre iOS.

### `assets` – glyfy (fonty)

Balík predpočítaných glyfov Noto Sans sa kopíruje **na naše Pages**. Verejná
služba `fonts.openmaptiles.org` je jediný bod zlyhania, pri ktorom by sa mapa
vykreslila úplne bez nápisov – to sa nechce.

### `deploy` – generovanie `style.json`

[`workers/build-styles.mjs`](../workers/build-styles.mjs) zavolá ten istý
generátor ([`poc/web/themes.js`](../poc/web/themes.js)), aký používa web, a
vyrobí statické štýly pre každú kombináciu **typ mapy × téma**. Naviaže ich na
**reálne dostupné assety**: zoznam ikon berie zo sprite indexu a fontstacky
z adresára s glyfmi, takže štýl nikdy neodkazuje na niečo, čo na Pages nie je.

Súbory sú `styles/{región}-{typ mapy}-{téma}.json` – pri piatich typoch máp
([`poc/web/map-types.js`](../poc/web/map-types.js): turistická, lyžiarska,
cestná, historická, základná) a štyroch témach je to 20 štýlov, dokopy
niekoľko MB. Predvolený typ (turistická) sa zapíše aj pod pôvodným menom
`{región}-{téma}.json`, aby fungovali odkazy, ktoré typy máp nepoznajú –
napríklad smoke test a staršie verzie iOS aplikácie.

Sem sa zároveň zapečú [úpravy z developer
módu](../README.md#cesta-úprav-do-zdrojáku) (`poc/web/style-overrides.json`)
– farby, viditeľnosť vrstiev, rozsahy zoomu, druhy čiar, vzory, okraje, sada
ikoniek a tieňovanie reliéfu, a to aj tie, ktoré platia len pre jeden typ
mapy (`maps.<typ>`).

### `deploy` – vzory do spritu

Vzory plôch a čiar sú **generované z predpisu, ktorý je zároveň názvom
obrázka** (`pat:trees:2f5a28:22:12`). Web si ich dokreslí sám cez
`styleimagemissing`, statický štýl pre iOS ich ale potrebuje v sprite –
[`workers/add-sprite-patterns.mjs`](../workers/add-sprite-patterns.mjs) preto
prejde hotové štýly, pozbiera použité názvy a dopečie ich do atlasu.

### `deploy` – manifest a viewer

`tiles/manifest.json` je to jediné, čo si web načíta na začiatku: kde sú
dlaždice, aký majú maxzoom, bbox regiónu, aké sady ikoniek sú nasadené a kedy
sa mapa vygenerovala.

### `deploy` – kontrola pred nasadením

Prejde sa hotový `style.json` a overí sa, že **všetko, na čo odkazuje, naozaj
existuje**: sprite, fontstacky, pevne zadané mená ikon, vzory, `.pmtiles`,
vrstevnice a značené trasy. Bez toho by sa chyba prejavila až ako biela mapa
v prehliadači.

### `deploy` – nasadenie a smoke test

Po nasadení si pipeline **sama overí, že mapa funguje**: `manifest.json`,
`style.json`, sprite, glyfy a – najdôležitejšie – `Range` request na
`.pmtiles`, ktorý musí vrátiť **HTTP 206**. Keby hosting Range requesty
nepodporoval, `.pmtiles` sa nedá čítať a mapa zostane prázdna.

### `deploy` – súhrn buildu

Posledný krok napíše do záložky **Summary** prehľad celého behu. Beží
s `if: always()`, takže je aj (hlavne) vtedy, keď build spadol – z padnutého
behu je tak vidieť, kam sa dostal a čo stihol.

Meranie funguje tak, že si každý dlhý krok pripíše riadok „poradie, názov,
sekundy, čo spravil" do `steps-out/<job>.tsv`, job to odloží artefaktom
a `deploy` z toho poskladá tabuľku. Radí sa podľa **poradia v pipeline**, nie
podľa času – joby bežia súbežne, takže čas by hovoril len o tom, ktorý runner
bol rýchlejší:

| krok | trvanie | výsledok |
|---|--:|---|
| PBF regiónu | 0:00:12 | Prešovský kraj, 63M (z cache) |
| DEM dlaždice (Sonny) | 0:01:44 | 9 z 21 dlaždíc, 412M |
| Vrstevnice (gdal_contour) | 0:04:31 | interval 10 m, 218M |
| Skalné plochy | 0:36:07 | 41 802 plôch, sklon ≥ 50°, mriežka 2 m (výpočet) |
| Vrstevnice a skaly → PMTiles | 0:06:12 | maxzoom 14, 187M |
| Značené trasy z OSM | 0:01:38 | ~1 400 trás, ~39 000 úsekov, ~6 000 ciest s viac trasami |
| Značené trasy → PMTiles | 0:00:44 | maxzoom 14, ~9M |
| Tieňovanie a 3D terén | 0:00:31 | 24 118 PNG dlaždíc do z13, 96 MB (release dem-terrain) |
| Mapové dlaždice (Planetiler) | 0:18:20 | maxzoom 16, 421 MB |
| Ikonky (SDF sprity) | 0:00:09 | sady: maki temaki osm-bright, štýl používa temaki (z cache) |

*(Ukážkové čísla – líšia sa podľa regiónu a nastavení.)*

Za tabuľkou nasledujú ešte dve časti:

- **Skalné plochy – aký to je detail.** Počet samostatných plôch, mriežka
  obrysu, bunka zdrojového DEM (a poznámka, že práve tá je stropom skutočného
  detailu), najmenšia ponechaná plocha, skutočne najmenšia/priemerná/najväčšia
  a koľko km² skalného terénu spolu. Čísla píše `rock-areas.py` do
  `contours-out/rock-stats.txt`; ten je súčasťou cache, takže súhrn ich má aj
  pri behu, kde sa nič nepočítalo.
- **Značené trasy – čo sa našlo v OSM.** Koľko relácií trás územie má, koľko
  z nich je pomenovaných, po koľkých cestách vedú, koľko z tých ciest nesie
  viac trás naraz (a koľko najviac), rozdelenie na turistické/cyklo/MTB/
  lyžiarske/jazdecké a zoznam farieb značiek. Čísla píše `trail-routes.py`.
- **Cache.** Riadok za riadkom, čo prišlo z cache a čo sa naozaj počítalo –
  takže sa hneď vidí, či mal beh trvať hodinu, alebo minútu. Plus návod, ktorý
  input čo pregeneruje.

---

## Rozpočet veľkosti

GitHub Pages zvládne stránku do ~1 GB a do toho sa musia zmestiť dlaždice
**aj vrstevnice, fonty a sprity**. Preto je rozpočet jeden a spoločný
(`size_limit_mb`, default 900):

1. **Vrstevnice sa robia prvé** a majú strop 40 % rozpočtu. Keď ho prekročia,
   prepočítajú sa o zoom nižšie priamo z hotového GPKG – sekundy, DEM sa
   znovu nesťahuje.
2. **Dlaždice dostanú, čo zvýšilo** (rozpočet − obsadené − rezerva na fonty
   a sprity) a `auto_shrink` ich zmenší na zoom, ktorý sa doň vojde.
3. Keďže nižší zoom zmenší dlaždice zhruba 3,5×, ide sa **rovno o toľko
   zoomov, koľko treba** (najviac o dva naraz), aby sa nerobili zbytočné
   hodinové behy Planetileru.
4. Záverečná kontrola porovná súčet s tým istým rozpočtom a vypíše, čo koľko
   zaberá.

Vďaka tomu build na veľkosti nepadne až na konci. Ak chceš väčší detail,
ubrať treba **územiu** (`crop_bbox`, jeden kraj) alebo **vrstevniciam**
(`contour_interval` 20 m, `contour_maxzoom` 12, prípadne úplne vypnúť).

---

## Prečo zoom končí na 16, keď sa mapa priblíži na 20

Planetiler nevie vygenerovať dlaždice nad zoom 16. Nie je to problém:
MapLibre dlaždicu zo z16 na vyšších zoomoch **prepočíta** (overzoom) – je to
vektor, takže sa nerozmaže, len sa nezobrazí viac detailu, než v dlaždici je.
Aby overzoom vyzeral ostro, generuje sa najvyšší zoom bez zjednodušovania
geometrie a bez zahadzovania malých prvkov.

Čo je vidieť na akom zoome, riadi **štýl**, nie dlaždice:

| zoom | správanie |
|---|---|
| < 14 | mapa sa orezáva – vrstvy sa zapínajú postupne podľa `minzoom`. Cesty sa kreslia už od z4 vlasovými čiarami bez obrysu, aby bola sieť čitateľná aj na malých mierkach |
| 14–15 | plný detail, POI filtrované na `rank <= 24` |
| 16+ | všetko bez filtra, 3D budovy |
| 17+ | navyše súpisné čísla domov |

---

## Druhý workflow: „Stiahnuť výškové dáta"

> **Spúšťa sa aj sám.** „Build map" má pred sebou úlohu *Kontrola výškového
> modelu*: zistí, ktoré 1° dlaždice pokrývajú jeho bbox, a porovná ich
> s assetmi releasu. Keď tam nie je ani jedna, zavolá tento workflow ako
> `workflow_call` a až potom sa tiluje – iný zdroj výšok totiž nemáme, takže
> by build aj tak zlyhal. Keď časť dlaždíc chýba (rohové bunky bboxu bývajú
> za hranicou, kde produkt dáta nemá), nespúšťa sa nič a build len napíše,
> kde terén nebude. Otlačok obsahu releasu ide do kľúča cache vrstevníc,
> takže po doplnení terénu sa nevrátia staré vrstevnice.

Zrkadlí výškový model **Sonny's LiDAR DTM** do releasu `dem-sonny`. Sonny ho
distribuuje cez Google Drive – ten nemá stabilné priame URL na súbory v
zdieľanom priečinku a pri väčšom počte stiahnutí odpovedá limitom, takže sa
z neho nedá sťahovať v každom builde mapy.

```
Google Drive priečinok (napr. Slovensko, model 20m)
  │  gdown --folder     … stiahne celý priečinok naraz
  │  7z                 … rozbalí .zip / .7z
  │  workers/dem-tiles.py … GeoTIFF → dlaždice 1°×1° vo WGS84
  │  (alebo .hgt priamo … to je už 1° dlaždica, len bez hlavičky)
  ▼
release `dem-sonny`: N49E019.tif … + meta.json
```

- **Ktorý model.** Sonny má 1″/3″ ako `.hgt` (20×30 m, výška po celých
  metroch) a 20m/50m ako GeoTIFF (20×20 m, výška po 0,1 m). Berieme **20m** –
  a nie kvôli vodorovnej mriežke, ale kvôli tomu zvislému kroku: z metrových
  schodov vychádza schodíkovitý sklon. Namerané na tom istom území Vysokých
  Tatier: z 1 m dát 5 293 skalných plôch so 101 bodmi na obrys, z 0,1 m dát
  2 138 plôch so 195 bodmi – pri rovnakej celkovej ploche skál (4 218 vs
  4 223 ha). Metrové dáta teda tú istú stenu rozdrobia na falošné kúsky.
- **Rezanie na dlaždice** ([`workers/dem-tiles.py`](../workers/dem-tiles.py)).
  20m model môže byť jeden GeoTIFF na celú krajinu a v metrickej projekcii;
  build mapy ale sťahuje len dlaždice pre svoj bbox a lepí ich `gdalbuildvrt`,
  ktorý rôzne projekcie v jednom VRT neunesie. Skript preto rozsah prepočíta
  do stupňov, mriežku z metrov na stupne (po dĺžke cez `cos(šírky)`) a vyreže
  dlaždice `N49E019.tif`. Prevzorkúva sa **bilineárne** – pri prakticky
  rovnakej mierke to stačí a na okrajoch dát nič „neprestrelí" mimo rozsah
  skutočných výšok, ako to robí kubické.
- **Meno .hgt dlaždice** sa berie z názvu súboru (konvencia SRTM: juhozápadný
  roh), takže je jedno, ako sú súbory v priečinku pomenované navyše.
- **Škálované výšky sa rozbalia.** Desatiny metra sa v GeoTIFFe dajú uložiť aj
  ako celé čísla so `scale` (napr. decimetre so `scale=0.1`). `gdalwarp` škálu
  neuplatňuje, takže bez rozbalenia (`gdal_translate -unscale`) by boli výšky
  desaťkrát väčšie – a sklon by potom ukázal skalu úplne všade. Skript to
  zistí z hlavičky a rovno vypíše rozsah výšok zdroja; keď nevyzerá ako metre
  nad morom, workflow varuje.
- **Prázdne dlaždice sa nepublikujú** – ak po vyrezaní neostane ani jeden
  platný pixel, dlaždica sa zahodí.
- **`.hgt` je surové pole int16 bez hlavičky.** GDAL ho pozná pri štandardných
  veľkostiach (1201², 3601²); pri neštandardnej mriežke (0,5″ = 7201²) si
  workflow georeferenciu poskladá sám cez VRT – krok mriežky je `1/(n−1)`,
  lebo vzorky sú v uzloch, nie v stredoch buniek.
- **Prevod na GeoTIFF** je zámerný: je menší (bezstratová kompresia) a
  `gdalbuildvrt` v builde mapy ho zlepí bez ďalších pomôcok.
- Ak by Drive limitoval, dá sa namiesto neho vyplniť `direct_urls` (priame
  odkazy na mirror).

## Tretí workflow: „Uložiť úpravy štýlu do zdrojáku"

Protikus developer módu – vezme stiahnutý `style-overrides.json`, prežene ho
**tou istou validáciou ako prehliadač** (`normalizeOverrides`) a commitne do
repozitára. Neznáma farba, neplatný hex, neprepísateľná vlastnosť či
prehodený rozsah zoomu skončia varovaním a vyhodia sa, takže do zdrojáku sa
nedostane nič, čo by štýl rozbilo.

## Štvrtý workflow: „Pripraviť DMR 5.0"

Zdroj: `https://opendata.skgeodesy.sk/static/LLS/DMR5/DMR5_0_sjtsk03_bpv.zip`,
**197,7 GB** (197 707 257 567 B, zmerané behom 31182614668), S-JTSK [JTSK03],
výšky Bpv.

Čo je vnútri, prečítal beh 31184095104 (`mode: len plán`):

```
dmr5_0/dmr5_jtsk03.tif        151,43 GB   celé Slovensko, 1 m, JEDEN raster
dmr5_0/dmr5_jtsk03.tif.ovr     46,28 GB   prehľadové úrovne (pyramídy)
dmr5_0/dmr5_jtsk03.tfw                    world file
dmr5_0/dmr5_jtsk03.tif.aux.xml, .xml      metadáta
INFO_slk.txt, INFO_eng.txt, 4× PDF        licencie a popis
prehlad_lokalit_lls_1_cyklus/*.shp …      prehľad lokalít zberu
```

**Nie sú to výškové body po blokoch, je to jeden súvislý GeoTIFF.** Po
položkách archívu sa teda deliť nedá – ale deliť netreba.

Toto je jediný workflow v repozitári, ktorý **nesmie stiahnuť svoj vstup**.
Runner má voľných ~60 GB, artefakt aj asset releasu majú strop 2 GB na súbor.
Nič z toho sa nedá obísť väčšou trpezlivosťou.

Nemusí sa. GDAL vie otvoriť raster priamo vo vzdialenom ZIPe:

```
/vsizip//vsicurl/https://opendata.skgeodesy.sk/…/DMR5_0_sjtsk03_bpv.zip/dmr5_0/dmr5_jtsk03.tif
```

ZIP sa číta cez HTTP Range (centrálny adresár dá offset položky), GeoTIFF je
dlaždicovaný 512×512 s DEFLATE, takže si GDAL vypýta len tie dlaždice, ktoré
potrebuje. Na disku nepristane nič.

```
plan     posledných 128 kB → koniec centrálneho adresára
         (ZIP64: pri 198 GB sú offsety nad 4 GB, skutočné čísla sú v ZIP64
          zázname, nie v obyčajnej hlavičke)
         centrálny adresár → inventár VŠETKÝCH položiek do súhrnu behu
         → nájde hlavný raster (položka s aspoň polovicou bajtov)

model    16 bajtov hlavičky → kde leží adresár dlaždíc
         gdalinfo cez /vsizip//vsicurl/ → rozmery, mriežka, CRS, pyramídy
         celá krajina: JEDEN gdal_translate -tr <grid> -r average
                       → dem-tiles.py → N49E019.tif → release `dem-dmr5`
         pohorie:      gdal_translate -projwin (sekvenčne) → gdalwarp
                       → ugkk-<pohorie>.tif → release `dem-ugkk`
         → do súhrnu napíše, s čím spustiť Build map
```

**Formulár má tri polia:** `area` (dropdown – celé Slovensko alebo pohorie
z `areas.json`), `grid_m` (dropdown) a `mode`. URL archívu je v `env`, release
a to, ako sa výsledok volá vo výberoch Build map, sa odvodia z územia.

Do augusta 2026 tu bola aj cesta cez rozdelenie archívu na časti (matrix joby,
`workers/dmr5-chunk.py`, `workers/sjtsk.py`) a k nej šesť ďalších inputov.
Stála na predpoklade, že archív sú výškové body po blokoch – nie sú, je to
jeden súvislý raster, takže nebolo čo deliť. Zmazané.

- **Cena čítania je daná pozíciou v súbore.** Položka v ZIPe je zabalená
  deflate-om a v deflate prúde sa nedá skočiť dopredu – dá sa doň len rozbaliť
  od začiatku. Zmerané na napodobenine (44 MB ZIP, dlaždicovaný DEFLATE
  GeoTIFF, vlastný HTTP server s Range): výrez na začiatku rastra 0,5 MB (1 %),
  výrez na konci 44,1 MB (100 %), celý raster 1 m → 5 m 37,8 MB s `.ovr`
  a 46,1 MB bez neho. Pri výreze je preto sever lacný a juh drahý.
- **Jeden prechod, nie viac.** Celá krajina sa prevzorkúva jedným
  `gdal_translate -tr` a na 1° dlaždice sa krája až hotový malý raster.
  Krájať dlaždice priamo zo zdroja by stálo N× cestu od začiatku súboru.
- **Čítať sa musí dopredu.** Výrez ide v dvoch krokoch: `gdal_translate
  -projwin` sekvenčne na disk, potom `gdalwarp` z disku do WGS84. Warp
  priamo nad vzdialeným zdrojom si dlaždice pýta v poradí cieľovej mriežky
  a každý skok späť v deflate prúde znamená rozbaľovanie od začiatku člena.
  Okno sa pritom oreže na skutočný rozsah rastra – `-projwin` by presah
  doplnil NULAMI a nula je platná výška, takže by z toho v mape bol pás
  mora, nie diera.
- **Pyramídy miesto rastra, keď to ide.** Pri cieli aspoň 2× hrubšom než
  zdroj sa číta z `.ovr` (46 GB) a nie z hlavného rastra (151 GB). `.ovr` je
  obyčajný TIFF bez georeferencie – tá sa mu dolepí z rodiča cez VRT.
  Vyberá sa výslovne, nie s dôverou, že si ho GDAL nájde sám: keby ho
  neuvidel, prečítal by celý raster a nikto by sa to nedozvedel.
- **Sidecary sa nesmú schovať.** `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`
  šetrí požiadavky, ale skryje `.ovr` aj `.tfw`. Preto sa tá premenná
  v `dmr5-raster.py` zámerne NEnastavuje.
- **Heartbeat každých 30 s.** Prenesené bajty zo sieťovky, rýchlosť, odhad
  zvyšku a veľkosť výstupu. GDAL kreslí percentá cez `\r`, čo sa v logu
  GitHub Actions neobjaví, takže hodinový krok je inak úplne ticho a nedá sa
  odlíšiť od zaseknutého behu.
- **Najprv 16 bajtov, potom GDAL.** Hlavička TIFFu nesie offset adresára
  dlaždíc (IFD). Keď je na začiatku, súbor sa otvorí za sekundu; keď je na
  konci – a zapisovatelia ho tam bežne dávajú – GDAL sa k nemu prehryzie len
  rozbalením celého člena, teda 151 GB ešte pred prvým pixelom. Nad obyčajným
  súborom je to jedno, `fseek` na koniec je zadarmo; v deflate člene ZIPu nie.
  Behy 31191478190 a 31197330753 sa zasekli presne tu; ten druhý po 87
  minútach ticha skončil s `Terminate orphan process: pid (2977) (gdalinfo)`,
  teda `gdalinfo` stále bežal a nedostal sa ani k prvému pixelu. Teraz sa
  offset IFD hlavného rastra aj `.ovr` prečíta a vypíše ako prvé, `gdalinfo`
  má strop (`--probe-timeout`, predvolene 15 min) a beží pod ním heartbeat.
- **Keď sa hlavný raster neotvorí, ide sa cez pyramídy.** `.ovr` má 46 GB
  namiesto 151 GB. Georeferencia sa poskladá z `.tfw` (veľkosť pixela ×
  pomer zmenšenia, ten istý ľavý horný roh) – z rodiča prísť nemôže, ten sa
  neotvára. Overené, že to dá rovnaký `geoTransform` aj rovnaké výšky ako
  cesta cez rodiča. Cena je rozlíšenie: z pyramíd je najjemnejšie 2 m.
  Pri hľadaní sidecarov sa skúšajú obe konvencie – `.tif.ovr` (prípona sa
  pridá) aj `.tfw` (prípona sa nahradí); world file je vždy ten druhý prípad.
- **Sonda ide bez sidecarov.** `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` len
  pri sonde: keby bol drahý niektorý zo sidecarov (`.ovr` má 46 GB), vyzeralo
  by to ako problém hlavného súboru. Keď sa ukáže, že raster nemá
  georeferenciu v sebe, sonda sa zopakuje aj so sidecarmi (`.tfw`,
  `.aux.xml`).
- **Rozlíšenie stropuje release, nie zdroj.** Pri 1 m má jedna 1°×1° dlaždica
  ~48 GB, takže celé Slovensko ide na 5 m (`dem-dmr5`) a plné metrové
  rozlíšenie sa robí na výrez (`dem-ugkk`, jeden COG). Celé Slovensko pod 3 m
  workflow odmietne v prvej minúte, nie po ôsmich hodinách.
- **Inventár je hlavný výstup prvého behu.** `mode: len plán` vypíše do súhrnu
  všetky mená, veľkosti a spôsob kompresie (uložené vs deflate) a zoznam
  priečinkov. Nič sa nefiltruje podľa prípon – ten filter v prvej verzii ticho
  zahodil 16 z 19 súborov a v logu nebolo ani jedno meno, takže sa nedalo
  zistiť, že archív je v skutočnosti jeden raster.
- **Územie vyberá dropdown, nie písanie.** `area` je `choice` s tými istými
  pohoriami ako `workers/areas.json`; lint to stráži, aby sa zoznamy
  nerozišli. Z výberu sa odvodí mriežka, release aj kľúč zdroja pre Build
  map – nemá zmysel, aby ich vypĺňal človek, keď z územia jednoznačne
  vyplývajú.

Kód: [`workers/zip-remote.py`](../workers/zip-remote.py) (čítanie vzdialeného
ZIP64), [`workers/dmr5-plan.py`](../workers/dmr5-plan.py) (inventár archívu)
a [`workers/dmr5-raster.py`](../workers/dmr5-raster.py) (samotný model).

**Tento workflow sa nespúšťa sám.** Ostatné výškové zdroje si `Build map`
doplní ako svoju úlohu; 198 GB a desiatky paralelných jobov ale nemajú byť
vedľajší účinok buildu mapy, takže `dmr5` sa pustí raz vedome. Build to
napíše aj s tým, čo presne spustiť.

## Piaty workflow: „Skaly z tieňovaných dlaždíc"

**Vyrába MASKU, nie skaly.** Skaly sú v tejto pipeline vždy pásmo sklonu nad
prahom (`workers/rock-areas.py`); tento workflow sa výšok nedotkne a hľadá
**tmavé plochy** v hotovom hillshade z freemap.sk. Pri `rock_source:
tienovanie` sa tie dve veci **pretnú**: skala je to, čo je zároveň tmavé
a zároveň strmé.

> ⚠️ Kým sa tmavé plochy brali ako hotové skaly, bola z nich v mape **jedna
> sivá deka**. Namerané na testovacom výreze v Tatrách (2 km², `dark 125`):
> 2 223 plôch, **0,68 km² = 34 % územia**, najväčšia 30,6 ha. Pre porovnanie
> skaly zo sklonu: Vysoké Tatry pri 50° majú 884 ha z 541 km², teda **1,6 %**.
> Príčina nie je v prahoch: hillshade je osvetlený z jednej strany, takže
> tmavý je **každý odvrátený svah** – aj úplne mierny. Tmavosť teda hovorí
> „sem nesvieti", nie „tu je stena".

```
job „Stiahnuť dlaždice" (strop 2 h)
───────────────────────────────────────────────────────────────────────
XYZ dlaždice   https://sk-hires-shading.tiles.freemap.sk/{z}/{x}/{y}.jpg
   │           paralelné sťahovanie s trvalým spojením, disková cache
   ├─► artefakt `dlazdice-tienovania-…`   samotné JPG
   └─► cache + výstupy `bbox`, `key`, `zoom` pre ďalšie joby

job „Obrysy po blokoch" (strop 3 h)      ← toto je tá drahá časť
───────────────────────────────────────────────────────────────────────
mozaika šedej v EPSG:3857     dlaždice sú v ňom natívne → 1 px = 1 px,
   │                          žiadne prevzorkovanie
   ▼
raster „tmavosti" (Byte)      score = clip(ref − šedá, 0, 255)
   │                          ref   = clip(pozadie − rel, dark_always, dark)
   │                          po pásoch dlaždicových riadkov, s presahom,
   │                          na disk ako komprimovaný GTiff
   ├─► artefakt `nahlad-…`     PNG mozaika vedľa masky + histogram
   ▼
gdal_contour -p -fl 0,5 -fl 256             PO BLOKOCH (block_tiles=8,
   │                          teda 2048 px) → JEDNO pásmo ako polygóny
   └─► cache `_rozrobene/…/bloky/b00000.geojsonl…`

job „Skaly z tieňovania" (strop 1 h)
───────────────────────────────────────────────────────────────────────
zlepenie blokov do jedného prúdu
   ▼
filter plôch (diery sa nekreslia) → -simplify → smooth-polygons.py
   ▼
rock.gpkg (EPSG:4326, vrstva `rock`, triedy steep/cliff)
   ├─► release `dem-rocks-img`   pre Build map (výber `rock_source: tienovanie`)
   ├─► artefakt `skaly-obrazok-…`  iba polygóny (GPKG + GeoJSON)
   └─► artefakt `cisla-…`          namerané hodnoty behu

a v „Build map" ešte JEDEN krok navyše
───────────────────────────────────────────────────────────────────────
rock-areas.py --clip=<ten asset>            sklon zo Sonnyho, orezaný maskou
   ▼                                        (gdal_rasterize -i -burn 0)
data/rock.gpkg = tmavé A strmé
```

**Prečo tri joby a nie tri kroky.** Strop času platí na JOB. Sťahovanie
z dobrovoľníckeho servera je desiatky minút a obrysy ďalšiu hodinu; dokopy
sa to do jedného rozpočtu zmestiť nemusí a keď dôjde čas, padne aj to, čo už
bolo hotové. Rozdelené má každá časť celý svoj rozpočet a v Actions je vidieť,
na ktorej beh práve je – z jedného trojhodinového jobu sa to prečítať nedalo.

Dva vedľajšie efekty, pre ktoré to stojí za to aj bez stropu času:

- **Každý job odloží svoj výsledok hneď.** Obrázky sú v artefakte po stiahnutí,
  náhľad po rastri tmavosti – teda aj vtedy, keď to za nimi nedobehne. Predtým
  sa oboje odkladalo až na konci, čiže presne keď to bolo najmenej treba.
- **Zmena `min_area` je posledný job (minúty), nie celý výpočet.** Obrysy sú
  v cache a zlepovanie s filtrom sa dá pustiť nad nimi znova.

Ako si dáta podávajú: dlaždice majú vlastný kľúč cache (`shading-tiles-…`),
rozrobené druhý (`shading-vektor-…`) – tretí job obnovuje len ten druhý,
takže nesťahuje gigabajty JPEGov, ktoré nepotrebuje. Kľúče nesú číslo behu,
takže sa ďalší job trafí presne na to, čo uložil predošlý; `restore-keys`
hľadá po predpone, takže sa dá nadviazať aj na starší beh. Zvolený zoom ide
medzi jobmi ako výstup – pri `auto` sa sonda nepúšťa trikrát.

**Jedna trieda a jedna sivá.** Výstupom je jedno pásmo, teda žiadna plocha
vnútri inej. Dôvod je v kreslení: v mape sa skaly kreslia plnou farbou bez
priehľadnosti, takže by sa každý prekryv prejavil ako tmavšia škvrna.

**Diery v plochách ostávajú.** Krátko sa zapĺňali spolu s tým prechodom na
jednu triedu a bola to chyba: diery sú medzery medzi vláknami siete žliabkov,
čiže presne tá štruktúra, pre ktorú sa skaly z tieňovania robia. Zaplnené
z nich boli súvislé plochy, v ktorých nebolo vidieť nič. `zapln_diery=1` to
vráti, ak by to niekto naozaj chcel. Priehľadnosť totiž znamená, že dve plochy cez
seba vyjdú tmavšie než jedna – a stačí na to plocha rozseknutá hranicou bloku
alebo `cliff` ležiaci v diere `steep`u. Plná farba to rieši na úrovni
kreslenia, takže sa plochy nemusia ani zlepovať (`zlepit=1` to vráti), ani
strážiť proti sebe. Vedľajší efekt, ktorý sa počíta: jedno pásmo namiesto
dvoch je polovica prstencov na obtiahnutie, a `gdal_contour` je tá najdrahšia
fáza celého behu. `options: plne=0` vráti pôvodné správanie.

**Zoom dlaždíc končí na 17.** Server dá aj vyššie, ale na z18 sú to
štvornásobne dlaždice a obrysy rastú ešte rýchlejšie – 3,62 mld. pixelov
bežalo 2 h 41 min a nedopočítalo sa. Mapa z toho nemá nič: skaly majú vlastný
`.pmtiles` a zobrazujú sa do maximálneho zoomu tak či tak, takže z vyššieho
zdroja by bol ostrejší tvar, nie väčší rozsah zoomov.

**Testovací režim** (výber `test`) vyreže zo stredu výrezu štvorec s pár km².
Nie je to iný algoritmus, len menší bbox – celé je to jeden prepínač
v `resolve-area.py`. Kľúč dostane príponu `_test2`, takže si testovací
výsledok nesadne do tej istej cache ani na ten istý asset ako ostrý. Beh
navyše vypíše obrázok, kde ten štvorec leží (viď nižšie).

### Pásmo pod prahom sa musí zahodiť

`gdal_contour -p` nevyrobí len pásmo, ktoré si pýtaš – vyrobí **všetky**.
Pri `-fl 0,5 -fl 256` sú to dve: `[0; 0,5)` a `[0,5; 256)`. To prvé je
„všetko, čo skala nie je" a je to jeden obrovský polygón na každý blok.

Keď prejde do výsledku, mapu prekryje **súvislá plocha bez detailu a bez
obrysov** – skaly v nej síce sú, ale nevidno ich, lebo pozadie má tú istú
sivú. Presne to sa dialo pri `rock_source: tienovanie`; namerané na
testovacom výreze: skaly „pokrývali" 1,44 km² z 1,44 km² územia.

Filter ho preto zahadzuje podľa `dmin` (`min_level`, dolná hranica pásma
skál). Skaly z DEM tým nikdy netrpeli – `rock-areas.py` má
`WHERE smin >= prah` priamo v SQL; v ceste cez tieňovanie ten filter chýbal.

Ako poistka beh **kričí**, keď skaly vyjdú na viac než 60 % územia. Toľko
skál nie je nikde, takže je to spoľahlivý podpis tejto chyby:

```
::warning::Skaly pokrývajú 1.44 km² z 1.44 km² územia (100 %). Toľko skál
nikde nie je – vyzerá to, že do výsledku prešlo pásmo POD prahom (pozadie).
```

### Prečo `gdal_contour`, a nie `gdal_polygonize`

`gdal_polygonize` by obrys viedol po hranách pixelov (schodíky) a potreboval
by python bindings GDALu. `gdal_contour -p` nad poľom tmavosti interpoluje
medzi celými stupňami šedej, takže je obrys hladký a sub-pixelový – a je to
**ten istý nástroj a tá istá sémantika dier**, akú má `rock-areas.py`:
pásmo `[prah, ∞)` je polygón s vnútornými prstencami tam, kde hodnota pod prah
klesla. Svetlá polica vnútri steny tak ostane dierou.

Prah je `0,5`, nie `1`: dáta sú celé čísla, takže izolínia v polovici kroku
ide presne stredom medzi „nie je tmavé" a „je tmavé". Horná úroveň `256` je
nad maximom `Byte` – bez nej niektoré verzie GDALu najvrchnejšie pásmo
nevytvoria.

### Prečo je pozadie priemer len tých svetlejších pixelov

Prvá verzia brala obyčajný priemer v okne. Na skúšobných dátach z toho
vyšiel z veľkej súvislej tmavej plochy **iba prstenec**: okno sa celé zmestilo
dovnútra nej, pozadie kleslo na tmavosť samotnej plochy a rozdiel voči nemu
vyšiel nula. Opravené dvomi prechodmi – najprv hrubý priemer, potom priemer
len z pixelov nad ním – a dolným stropom `dark_always`, pod ktorým už o okolí
nikto nehlasuje.

Pozadie sa počíta na **8× zmenšenom** obraze a späť sa roztiahne. Je to pole
osvetlenia, nie detail; na osemnásobne menšej mriežke vyzerá rovnako a je 64×
lacnejšie na pamäť aj čas. Okno `local` je v **metroch na zemi**, nie
v pixeloch, takže to isté nastavenie platí na z17 aj z18 rovnako.

### Čo to stojí a čo z toho drží efektivitu

| vec | ako |
|---|---|
| sťahovanie | `jobs` vlákien (default 12) s trvalým spojením – pri 12 000 dlaždiciach je nový TLS handshake na každú z nich väčšina času |
| hlavičky | každý request z náhodného profilu skutočného prehliadača (9 profilov); `ua=project` sa vráti k menu projektu |
| opakované ladenie prahov | dlaždice sú v cache behu (`actions/cache`), druhý beh nestiahne ani jednu |
| chýbajúca dlaždica (404) | značka na disku, pri ďalšom behu sa neskúša znova; v mozaike ostane 255 = určite nie skala |
| pamäť | mozaika sa nikdy nedrží celá – pás dlaždicových riadkov podľa `band_cells` (default 150 mil. px) |
| disk | pás ide na disk ako `Byte` + DEFLATE; pole tmavosti je väčšinou nula, takže z 800 MB ostanú desiatky |
| zrno JPEGu | `blur` (3×3 na šedej) pred prahom – bez neho vzniklo 4× viac úlomkov, ktoré by aj tak vypadli na `min_area`, len by ich najprv musel niekto vektorizovať |
| strop zadania | `max_tiles` (60 000); `zoom: auto` pod neho zíde sám a povie, prečo |

Vysoké Tatry na z17: ~12 000 dlaždíc (~300 MB), mozaika 0,8 mld. pixelov,
jednotky minút. z18 je štvornásobok všetkého (~0,4 m na pixel).

### Prečo sa nakoniec vektorizuje po blokoch

Pôvodne to bol jeden priechod `gdal_contour` nad celou mozaikou – z rovnakého
dôvodu ako pri skalách z DEM: diera prerezaná hranicou časti sa zmení na
zárez v okraji a späť sa už nezlepí.

**Nedobehlo to.** `gdal_contour -p` skladá uzavreté prstence a v zrnitom
JPEGu ich je toľko, že to rastie rýchlejšie než lineárne: 3,62 mld. pixelov
(z18 na Vysokých Tatrách) bežalo **2 h 41 min a nedopočítalo sa**, pričom
pamäť ostala na 0,7 GB – čiže to nebola pamäť, ale čas.

Odvtedy sa vektorizuje po blokoch (`block_tiles=8`, teda 2048 px): ohraničená
pamäť, priebežný výstup a pokračovanie po páde. Pôvodná námietka platí ďalej,
preto sa plochy dotýkajúce sa hranice bloku na konci zlepia cez `ST_Union`
(spatialite) – a len tie, ktoré sa jej naozaj dotýkajú, nie všetko so
všetkým. Keď spatialite chýba, beh pokračuje a povie to; v skalách budú
vidieť rovné rezy.

### Ako sa pipeline predstavuje

Hlavičky sa berú z deviatich profilov skutočných prehliadačov a vyberajú sa
náhodne na **každý request**. Profil je celý – `User-Agent`, `Sec-CH-UA`,
platforma aj `Accept-Language` sedia dokopy; Chrome, ktorý o sebe v
`Sec-CH-UA` tvrdí, že je Firefox, nie je maskovanie, ale rozbitá hlavička.
Firefox a Safari `Sec-CH-UA` neposielajú vôbec, tak ho nemajú ani ich profily.

Trvalé spojenie sa tým nezahadzuje, takže server vidí jedno TCP spojenie,
cez ktoré chodí viac „prehliadačov". Dokonalé maskovanie to nie je a ani sa
oň nesnažíme – ide o to, aby dávka nevyzerala ako jeden skript s jednou
hlavičkou.

Dôsledok, ktorý treba mať na pamäti: berie to freemap.sk možnosť rozoznať
automat od človeka, a je to dobrovoľnícky server. Slušnosť preto musí
zabezpečiť objem – `jobs` ostáva 12, dlaždice sa cachujú a `zoom: auto` má
strop na počet dlaždíc. `ua=project` vráti hlavičku, ktorá sa priznáva.

Dve veci, ktoré k prehliadačovitým hlavičkám patria, lebo inak by ticho
rozbili beh:

- **`Accept-Encoding` bez `br`/`zstd`.** `http.client` telo nerozbaľuje,
  takže si to robíme sami a v stdlib je len gzip a deflate. Sľúbiť brotli
  a nevedieť ho prečítať by znamenalo uložiť na disk nečitateľné bajty.
- **Kontrola prvých bajtov.** Chybová stránka s kódom 200 je pri dlaždicových
  službách bežná; uložiť ju ako `.jpg` by znamenalo tichú dieru v mozaike
  a v cache navždy. Čo nezačína magickými bajtmi obrázka, ide na retry.

### Ako sa to dostane do mapy

Build map tmavé plochy **nehľadá** – ale zavolá si na to túto pipeline. Pri
`rock_source: tienovanie` sa v behu objaví job *Skaly z tieňovania*, ktorý je
`workflow_call` na `shading-rocks.yml`: stiahne dlaždice, nájde plochy,
nahrá ich do releasu, a job s vrstevnicami si ich potom vytiahne **ako
masku**. Platí teda to isté, čo pri výškových modeloch – **`Build map` je
jediné, čo spúšťaš**.

Sklon sa počíta aj tu: `rock_source: tienovanie` znamená
`rock_dem = sonny`, takže sa v behu stiahne Sonny a `rock-areas.py` pustí
svoj obvyklý výpočet – len s `--clip` na stiahnutý asset. **Orezáva sa
v rastri**, nie prienikom hotových polygónov: pred vektorizáciou sa do
každej dlaždice sklonu vypáli mimo masky nula
(`gdal_rasterize -i -burn 0`), takže pásmo nad prahom tam vôbec nevznikne.
Prienik dvoch vrstiev by bol n×m a nad desiatkami tisíc tmavých plôch by
bežal dlhšie než celý zvyšok výpočtu; takto je to jeden `gdal_rasterize` na
dlaždicu a všetko za ním (diery, `min_area`, zjednodušenie, zaoblenie)
ostáva nezmenené.

**Čo tým maska pridáva:** tvar. Hires tieňovanie je z 1 m LiDARu, kým sklon
zo Sonnyho má bunku 20 m – obrys tak drží detail, ktorý by samotný DEM nikdy
nedal, a sklon rozhoduje, kde skala vôbec je. Mriežka `rock_res` je zároveň
strop toho detailu (maska sa vypaľuje na ňu), takže `rock_res=auto` pri
Sonnym dá 2 m.

Meno assetu je aj v mene uloženého assetu skál (`…-c<8 znakov hashu>-…`),
takže sa orezané skaly z releasu nikdy nevydajú za neorezané ani naopak.

```
rock_source: tienovanie                    stiahne dlaždice, nájde masku
                                           a oreže ňou sklon (zo Sonnyho)
  + options: rock_img_zoom=18              iný zoom dlaždíc
  + options: rock_img_options="fill=40"    prepínače pre hľadanie masky
  + options: rock_img_asset=rockimg-…      presne táto hotová maska
                                           (vtedy sa dlaždice nesťahujú)
  + rock_slope: 45                         od akého sklonu je to skala –
                                           platí aj tu
```

Stiahnuté JPG dlaždice vypadnú ako artefakt `dlazdice-tienovania-{výrez}-z{zoom}`.
Sú to tie isté obrázky, z ktorých sa skaly hľadali – dovtedy sa dali vidieť
len v cache behu. Sú nekomprimované v ZIPe (JPG sa balí zbytočne) a pri z18
na veľké pohorie to je aj vyše gigabajtu, preto sa držia 14 dní a nie 90 ako
polygóny.

Keby v release aj tak nič nebolo, build to povie (`::error::`) a nespadne
späť na neorezané skaly z DEM – to by bola tichá zámena jedného zdroja za
druhý. Zdroj skál je aj v kľúči cache vrstevníc, takže sa
`dem` a `shading` nemôžu navzájom vrátiť z cache.

Kód: [`workers/shading-rocks.py`](../workers/shading-rocks.py). Formát
výstupu je zhodný so skalami z DEM (vrstva `rock`, EPSG:4326, `class`
= `steep`/`cliff`, `area` v m²) – práve preto sa dá použiť ako `--clip`
pre `rock-areas.py`. V mape má potom výsledok atribút `slope` (je to pásmo
sklonu); `dark` nesie len surový asset v release.

### Tmavé nie je plocha, ale sieť

Najdôležitejšie zistenie zo skutočnej dlaždice: tmavé miesta v tejto vrstve
**nie sú súvislé steny**, ale hustá sieť žliabkov, ryhiek a mikrotieňov
v rozčlenenom teréne. Maska vyzerá ako filigrán, nie ako klaksa.

To je vlastnosť, nie chyba – tá jemná štruktúra je práve to, čo z hillshade
chceme, a je to detail, aký zo sklonu 20 m DEM nikdy nevznikne. Pipeline je
podľa toho nastavená:

| vec | hodnota | prečo |
|---|---|---|
| `fill` | **0 (vypnuté)** | spriemerovanie tmavosti v okolí zo siete spraví súvislú plochu; merané: `fill=40` dá 10 útvarov a 35 % pokrytie namiesto 78 útvarov a 15 % |
| `min_area` | 7 m² | `200` zmazal práve tie drobné útvary, o ktoré ide, a `50` v tom pokračoval o stupeň jemnejšie; 7 m² je ~11 pixelov na z17, teda blízko hranice, pod ktorou je to už len zrno JPEGu |
| `min_hole` | 10 m² | medzery medzi vláknami siete SÚ tá štruktúra |
| `simplify` | 1 px | pod pixel je už len zrno JPEGu |
| `smooth` | 1× Chaikin | druhý prechod zdvojnásobí body za obrys, ktorý nikto nerozozná |

Merané na výreze 1260×1933 px z Vysokých Tatier, prepočítané na z18:

| nastavenie | plôch | dier | dáta |
|---|--:|--:|--:|
| `min_area 200`, `min_hole 50`, simplify ½ px, Chaikin 2× | 16 | 89 | 3,95 MB/km² |
| **`min_area 50`, `min_hole 10`, simplify 1 px, Chaikin 1×** | **78** | **392** | **1,97 MB/km²** |

Jemnejšie filtre a hrubšie zjednodušenie dali **súčasne viac štruktúry aj
polovičné dáta**. Predvolené `min_area` je preto dnes ešte nižšie – 7 m²;
tabuľka je nameraná pri 200 a 50 a nechávame ju tak, ako bola nameraná.

**Počet útvarov neexploduje, body áno.** Sieť je pospájaná – 16 útvarov
pokrylo 15 % výrezu. Cena je v bodoch obrysu, takže beh píše do súhrnu
`MB na km² skál`; práve to číslo rozhoduje, či sa vrstva zmestí do rozpočtu
mapy, nie počet plôch.

### Farba sa zatiaľ nepoužíva

Tá vrstva je **farebný** hillshade: žltozelený nádych, tiene ťahajú do modra
(sýtosť ~34, `B−R` od −95 do +50). Čítame ju ako jas (`convert("L")`, luma
601), kde modrý kanál váži najmenej – modré tiene sa tým ešte prehĺbia, čo
nám vyhovuje. Odtieň ako **druhý, nezávislý signál** (tieň vs. osvetlený
terén nezávisle od jasu) je zatiaľ nevyužitá páka; na jednom výreze sa nedalo
overiť, či pomáha, tak sa nepridával naslepo.

### Čo od toho čakať

Hillshade je obraz sklonu, ale **osvetleného z jednej strany**. To má dva
dôsledky a oba sú systematické:

* **Chýbajú juhovýchodné steny.** Rovnako strmá stena otočená k slnku je na
  hillshade najsvetlejšia zo všetkého. Tie táto cesta prehliadne a orezanie
  sklonu ich nevráti – čo nie je v maske, to sa do mapy nedostane.
* **Prebytočné sú mierne odvrátené svahy.** Tie sú tmavé bez toho, aby boli
  skala; na testovacom výreze z nich vyšlo 34 % územia. Práve toto rieši
  orezanie sklonom.

Zato má maska rozlíšenie, na aké si sami sklon nespočítame (1 m LiDAR proti
20 m Sonnymu). Je to teda spresnenie tvaru nad hlavnou cestou, nie jej
náhrada: bez `rock-areas.py` by z tmavých plôch nebola mapa skál.

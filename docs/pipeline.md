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
| **mirror-dmr5-area** | chýbajúci výrez DMR 5.0 v plnom rozlíšení → *DMR 5.0 z Drive* | — | tiles, assets |
| **mirror-dmr5-tiles** | chýbajúce 1° dlaždice DMR 5.0 (5 m) → *DMR 5.0 z Drive* | — | tiles, assets |
| **keys** | poskladá kľúče cache, pri `*_rebuild` zmaže staré záznamy | 10 min | tiles, assets |
| **contours** | DEM → vrstevnice + skaly → `{región}-contours.pmtiles` | 180 min | terrain, tiles, assets |
| **terrain** | DEM → terrarium PNG dlaždice | 120 min | contours, tiles, assets |
| **trails** | OSM relácie trás → `{región}-trails.pmtiles` | 60 min | úplne so všetkým |
| **features** | prvky mimo schémy OpenMapTiles → `{región}-features.pmtiles` | 90 min | úplne so všetkým |
| **tiles** | PBF → `{región}.pmtiles` (Planetiler) | 150 min | contours, terrain, assets |
| **assets** | SDF sprity a glyfy | 30 min | úplne so všetkým |
| **deploy** | zlepí `_site`, štýly, manifest, kontrola, Pages, smoke test, súhrn | 45 min | — |

> Tie dva `mirror-dmr5-*` joby sú **dve volania jedného workflowu**
> (`dmr5-drive.yml`), nie dve pipeline – DMR 5.0 má dve podoby a chýbať môžu
> naraz. Iná cesta k tomuto modelu nie je; záloha z archívu ÚGKK bola zrušená.

### Čo z buildu je v `workers/` a prečo

`build-map.yml` má strop **128 KiB** a po zlúčení dvoch PR ho prekročil
o 444 B – GitHub taký súbor neprijme a nepovie to. Preto sa najväčšie `run:`
bloky sťahujú do `workers/`, kde sa dajú aj spustiť ručne:

| skript | čo robí | bolo |
|---|---|---|
| [`fetch-pbf.sh`](../workers/fetch-pbf.sh) | PBF regiónu: stiahnutie, orez, kľúč a bbox | 5,6 kB v YAMLe |
| [`build-site.sh`](../workers/build-site.sh) | viewer + `manifest.json` do `_site` | 5,0 kB |
| [`tiles-build.sh`](../workers/tiles-build.sh) | Planetiler → `{región}.pmtiles` s rozpočtom | 3,5 kB |
| [`terrain-build.sh`](../workers/terrain-build.sh) | tieňovanie: cache → release → prepočet | 5,2 kB |
| [`contours-site.sh`](../workers/contours-site.sh) | hotové vrstevnice a skaly do `_site` | **2× tá istá kópia** |
| [`trails-build.sh`](../workers/trails-build.sh) | značené trasy → `-trails.pmtiles` | 2,9 kB |
| [`features-build.sh`](../workers/features-build.sh) | krajinné prvky → `-features.pmtiles` | 2,8 kB |
| [`cache-keys.sh`](../workers/cache-keys.sh) | kľúče cache pre celý build | 2,8 kB |
| [`icons-build.sh`](../workers/icons-build.sh) | SDF sprity zo sád ikoniek | 2,6 kB |
| [`glyphs-fetch.sh`](../workers/glyphs-fetch.sh) | glyfy k sebe na Pages | 1,3 kB |
| [`check-site.sh`](../workers/check-site.sh) | štýl neodkazuje na nič, čo v `_site` nie je | 3,6 kB |
| [`smoke-test.sh`](../workers/smoke-test.sh) | nasadená mapa naozaj odpovedá (a PMTiles cez `206`) | 3,2 kB |
| [`fetch-planetiler.sh`](../workers/fetch-planetiler.sh) | Planetiler do `planetiler.jar` | **4× tá istá kópia** |

Dokopy je z **124 KiB súbor s 90 KiB** a v YAMLe ostal graf jobov: čo od čoho
závisí, čo je podmienené a čo si čo podáva. Bash sa číta vedľa, v súboroch,
ktoré sa dajú spustiť lokálne.

**Vytiahnutý blok potrebuje `env:`, a to je tichá chyba tohto presunu.**
`${{ výraz }}` sa v skripte zmení na `$PREMENNÚ` a keď sa tá zabudne dopísať do
`env:` kroku, skript nespadne – beží s prázdnym reťazcom a vyrobí prázdny bbox,
prázdny kľúč cache alebo asset menom `ugkk-.tif`. Rovnako tiché je premenovanie
kroku, na ktorého `id` sa odkazujú výstupy jobu. Oboje stráži `Lint workflows`
krokom *„Skripty vo workers dostávajú svoje env"*.

Dva z nich sú duplicita, nie veľkosť. Planetiler si sťahujú štyri joby
(`tiles`, `contours`, `trails`, `features`), každý má vlastný runner a vlastnú
cache, takže sa to spraviť raz a podať ďalej nedá. Kým to boli štyri kópie
jedného bloku, bola to štvornásobná príležitosť, aby sa rozišli – verzia sa
zmení na jednom mieste a tri joby ticho stavajú z inej. To isté platilo pre
`contours-site.sh`: krok „Zaraď vrstevnice do webu" majú joby `contours`
a `rocks` oba, lebo obidva vychádzajú z jedného výpočtu a každý si z neho berie
svoju polovicu. Tá druhá kópia už aj prišla o všetky komentáre – presne tak sa
kópie začínajú rozchádzať.

Popri tom sa zliali dva rady takmer rovnakých vetiev: kontrola „má štýl zdroj
`contours`/`rocks`/`trails`/`features`, a je k nemu súbor?" a to isté v smoke
teste boli 4 + 4 skoro identické bloky, teraz sú z toho dva cykly nad tabuľkou.

**Keď presunieš `run:` blok do `workers/`, over, či ho nesledovala nejaká
kontrola.** `Lint workflows` hľadá volania `fetch-dem.sh`, aby vrstvy podávali
kľúč výrezu tak, ako to čaká `check-dem.sh` – a presun tieňovania do skriptu jej
ten súbor vzal spod rúk. Preto sa každá vrstva hľadá vo **viacerých kandidátoch**
a stačí, že ju nájde jeden; inak by presun kontrolu ticho umlčal.

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

Switch `test` vyreže zo stredu zvoleného výrezu **štvorec s 2 km²** a spočíta
na ňom to drahé – vrstevnice, skaly a tieňovanie. Z desiatok minút sú minúty,
takže sa dá prah alebo interval overiť za jeden beh.

**Predvolene je zapnutý**, ostrý beh na celý výrez ho chce odškrtnúť. Je to
switch vo formulári a nie voľba v `options`, lebo sa preklikáva pri každom
behu; miesto uvoľnila mriežka `rock_res` (desať inputov je strop), z ktorej
je naopak voľba. Veľkosť (`test_km2=5`) a stred (`test_at=lon,lat`) ostali
voľbami – tie sa prestavujú zriedka. `test_km2` bez zapnutého switchu je
chyba: inak by to bolo číslo, ktoré nič nerobí.

Ďalej v pipeline z toho ide jedno číslo (`opt_test_km2`): 0 = ostrý beh,
inak strana štvorca v km². Vypočíta ho `parse-options.py` zo switchu
a veľkosti, takže sa nikde inde nemusí riešiť „zapnuté a koľko".

**Mapa sa pritom neorezáva.** Príprava vydá dva bboxy a celý zvyšok pipeline
sa delí podľa toho, ktorý si vypýta:

| výstup prípravy | čo je to | kto ho číta |
|---|---|---|
| `bbox` | celý región (prípadne orezaný `crop_bbox`) | PBF, manifest, viewer – teda mapa: cesty, vodstvo, trasy, prvky |
| `dem_bbox` | pri teste štvorec, inak `bbox` | `check-dem`, kľúče cache, `contours`, `terrain` – teda všetko z výškového modelu |
| `test_bbox` | štvorec (prázdne bez testu) | obrázok „kde to je", súhrn, manifest, `bounds` tieňovania v štýle |

Kedysi to bolo **to isté orezanie regiónu ako `crop_bbox`**, len s bboxom,
ktorý nezadávaš ty – teda aj orezané PBF. Ušetrilo to pár minút Planetilera
a stálo použiteľnosť výsledku: 2 km² skál viseli nad prázdnom, bez ciest
a bez okolia, na ktorom by bolo vidno, či sedia. Prešovský kraj má teda pri
teste ostať prešovským krajom. `crop_bbox` je odvtedy na to, keď chceš orezať
naozaj aj mapu, a dá sa s testom kombinovať: najprv sa oreže región, štvorec
sa ráta až z toho, čo ostane.

Výrez (`area`) sa pretína s `dem_bbox`, nie s celým regiónom – vyjde teda ten
istý štvorec bez druhého výpočtu a `contours-build.sh` počíta presne to, čo
skontroloval `check-dem`.

Dve veci, na ktoré si treba dať pozor a sú vyriešené:

- **Kľúč.** Do mien cache aj uložených výsledkov ide `…_test2`, takže si
  testovací beh nesadne na to, čo počítal ostrý.
- **Pregenerúva sa vždy všetko.** `parse-options.py` pri zapnutom teste
  prebije `rebuild` a zapne všetky tri príznaky (`contours_rebuild`,
  `rocks_rebuild`, `terrain_rebuild`), takže sa cache pre ten kľúč najprv
  zmaže a všetko sa spočíta nanovo. Testom sa ladí, a ladiť na výsledku
  z cache znamená ladiť ducha; kľúč síce nesie nastavenia aj otlačok
  skriptov, ale nie všetko. Cache ostrého behu tým netrpí – v kľúči je
  `dem_bboxkey` a ten je pri teste bboxom štvorca. (Cache PBF je spoločná
  s ostrým behom, a to je správne: PBF je pri teste ten istý celý región.)

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

**Samotná mapa sa otvorí na štvorci aj bez odkazu.** Manifest nesie pri
regióne okrem `bbox` (celý kraj) aj `test_bbox` a `test_km2`; viewer sa pri
štarte nastaví na `test_bbox`, keď je (`initialBounds` v `poc/web/app.js`),
a do panelu napíše, že vrstevnice, skaly a tieňovanie sú len na tých 2 km².
Bez toho by sa štvorec hľadal očami v štyroch tisícoch km² kraja – a kraj bez
skál by vyzeral ako pokazený build.

Posúvať sa dá kamkoľvek, mapa je celá. Polohu z adresy viewer zahodí
(`dropPosFromHash`), len keď mieri **mimo nasadeného regiónu** – hash
z minulej návštevy alebo starý odkaz môže byť z iného kraja a MapLibre by
mapu otvoril nad prázdnom.

Tieňovanie dostane v štýle `bounds` toho štvorca (`--dem-bounds` do
`workers/build-styles.mjs`, `demBounds` v `poc/web/themes.js`). Vlastné
výškové dlaždice sú totiž pri teste len tam, kým mapa je celý kraj – bez
hranice by z každého posunu mapy padali stovky 404. `.pmtiles` (vrstevnice,
skaly) si hranicu nesú v hlavičke samy, raster nie.

### `contours` a `terrain` – vrstevnice, skaly a tieňovanie z DEM

**OpenStreetMap výškové dáta neobsahuje** – má len bodový tag `ele` na
vrcholoch a sedlách. Terén preto musí prísť odinakiaľ:

| zdroj | kľúč vo výberoch | čo to je | odkiaľ | stav |
|---|---|---|---|---|
| **Sonny's LiDAR DTM 20m** | `sonny` (default) | *model terénu* z LiDARu – bez stromov a striech, mriežka 20×20 m, výška po 0,1 m | náš release `dem-sonny` (zrkadlo, viď [Stiahnuť výškové dáta](#druhý-workflow-update-dem)) | overené |
| **ÚGKK DMR 3.5** | `dmr35` | otvorené dáta ÚGKK, mriežka presne 10×10 m | náš release `dem-dmr35` (jeden 2,3 GB ZIP z `opendata.skgeodesy.sk`) | overené |
| **ÚGKK DMR 5.0** | `dmr5` | slovenský **LiDAR** – najpodrobnejší model terénu. S výrezom (`area`) plné **1 m** z releasu `dem-ugkk`, bez neho dlaždice na **5 m** z `dem-dmr5`. Rozhoduje rozsah, nie ďalší výber | plní [DMR 5.0 z Drive](#štvrtý-workflow-dmr-50-z-drive-etrs89) | naplniť |

**Zdroj sa vyberá zvlášť pre každú vrstvu.** Formulár má tri výbery –
`contour_source` (vrstevnice), `rock_source` (skaly) a `shading_source`
(tieňovanie a 3D terén) – a každý ponúka ten istý zoznam modelov plus
`ziadne`, ktorým sa vrstva vypne. Kým to bol jeden `dem_source` pre všetko,
nedalo sa povedať to, čo dáva zmysel najčastejšie: skaly z najjemnejšieho
modelu a tieňovanie z hrubšieho, ktorý pokrýva celý región.

Keď majú vrstevnice a skaly iný model, job si stiahne oba – každý do
`dem/<zdroj>/` s vlastným `all.vrt`, takže sa dve mozaiky nikdy neprebijú.
Pri rovnakom modeli sa druhé volanie `fetch-dem.sh` netrafí do siete vôbec.

**`dmr5` má dve podoby a rozhoduje rozsah, nie ďalší výber.** S vyplneným
`area` si vezme `ugkk-<vyrez>.tif` v plnom metrovom rozlíšení, bez neho
dlaždice na 5 m. Je to ten istý LiDAR; pri 1 m má jedna 1°×1° dlaždica ~48 GB
a strop assetu je 2 GB, takže celý región v metri sa nemá kam uložiť.

Boli to dva zdroje, `dmr5` a `ugkk`. Rozdiel medzi nimi nebol v modeli, len
v tom, ako je uložený – a jediné, čo z toho v praxi plynulo, bolo, že sa dalo
zadať `ugkk` bez výrezu (a beh spadol na strážcovi) alebo `dmr5` na pohorie
(a build ticho vzal 5 m tam, kde bol meter). Preto je z nich jeden. Tieňovanie
sa robí vždy na celý región, takže tam `dmr5` vyjde na 5 m verziu a nemusí
sa zo zoznamu vynechávať.

`rock_source` má navyše hodnotu `tienovanie`: to je [piaty workflow](#piaty-workflow-skaly-z-tieňovaných-dlaždíc),
ktorá výškový model nečíta vôbec.

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
  │    workers/slope-chunks.py
  │    a) sklon PO ČASTIACH (pamäťovo drahé), každá časť do SKLADU:
  │      gdalwarp -t_srs EPSG:3035 … do metrickej projekcie, mriežka `rock_res`
  │                                  (pri dmr5 rovno z Drive cez HTTP Range)
  │      gdaldem slope             … sklon v stupňoch
  │      gdal_translate -ot Int16  … stotiny °, aby sa mozaika zmestila
  │      → sklad: slope-chunks/ (cache) + release dem-slope (trvalý)
  │      gdalbuildvrt              … mozaika sklonu celého územia
  │    workers/rock-areas.py
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

#### Sklad častí sklonu

Skaly pre pohorie sa dovtedy počítali takto: job `Doplniť DMR 5.0 (výrez)`
prečítal z Drive **celý** výrez naraz a uložil ho ako jeden COG
(`ugkk-<pohorie>.tif`, do 2 GB), a až z neho sa rátal sklon. Jednotka práce aj
jednotka uloženia bola „celé územie", takže to bolo všetko alebo nič – beh
[31310604408](https://github.com/skifahrer/fricomaps/actions/runs/31310604408)
čítal Vysoké Tatry hodinu, niekto ho zrušil a ostala **nula**.

Teraz je jednotkou **časť**:

```
územie (napr. vysoke_tatry)
   │  workers/slope-chunks.py
   ├─ rozdelí na časti absolútnej mriežky EPSG:3035 (4096² px)
   ├─ pre každú časť:
   │    v sklade? → vezmi           (cache → release dem-slope)
   │    nie?      → prečítaj z Drive len jej okno (HTTP Range),
   │                gdaldem slope, Int16 → ulož do skladu
   └─ gdalbuildvrt nad časťami → mozaika bez švov
        │  workers/rock-areas.py – JEDEN priechod gdal_contour
        ▼
      rock.gpkg → rocks.pmtiles
```

**Prečo je jednotkou sklon, a nie hotové skaly.** Vektorizovať po častiach sa
skúšalo a nefunguje: diera prerezaná hranicou časti sa zmenila na zárez
v okraji a späť sa už nezlepila – z dvoch plôch s dierami vyšli štyri bez
dier. Sklon je pritom presne tá drahá časť (čítanie z Drive + warp +
`gdaldem`), kým vektorizácia je jeden lacný priechod nad hotovou mozaikou.

**Mriežka častí je absolútna**, ukotvená v počiatku EPSG:3035 – nie v bboxe
územia. To je ten rozdiel, vďaka ktorému má sklad zmysel: tá istá zem padne
vždy do tej istej časti s tým istým menom. Overené – všetkých 14 častí
Vysokých Tatier pri 2 m je podmnožinou 36 častí Tatier, takže neskorší beh na
`tatry` ich už nepočíta.

**Prah sklonu v mene časti nie je.** Uplatňuje sa až pri vektorizácii, takže
zmena `rock_slope` sklad použije a preráta len tú lacnú časť – minúty namiesto
hodiny čítania z Drive.

| situácia | čo sa stane |
|---|---|
| druhý beh na tom istom pohorí | všetko zo skladu, nič sa nečíta |
| zrušený beh, znovuspustenie | dopočíta sa len to, čo chýba |
| zmazaná cache (nový runner) | časti prídu z releasu `dem-slope` |
| zmena `rock_slope` | sklad sa použije, prepočíta sa len vektorizácia |
| `rocks_rebuild` | prepočíta sa všetko (`--rebuild`) |
| testovací beh (`test`) | sklad sa použije, ale nič sa doň neuloží |
| vypadne spojenie na jednej časti | časť sa skúsi znova (`--tries`, 3×) |

**Jedna stratená časť nesmie zhodiť beh.** Sieť medzi GDALom a shimom vypadne
raz za desaťtisíce požiadaviek a dovtedy to znamenalo koniec: beh
[31338803278](https://github.com/skifahrer/fricomaps/actions/runs/31338803278)
mal 45 častí zo 47 hotových a spadol na dvoch. Časť sa počíta minútu, takže
druhý pokus stojí minútu – pád stojí celý job. Pokusy sú tri; trvalé chyby
(zlé zadanie, plný disk) sa opakovaním nespravia, tak sa nečaká dlho. Keď sa
časť nepodarí ani na tretí raz, beh spadne a **vypíše, čoho sa to týkalo
a koľko je v sklade** – aby bolo z logu vidieť, že hotová práca sa nezahodila.

**A počas počítania je počuť tep** (`--heartbeat`, predvolene 30 s, berie sa
z `ROCK_HEARTBEAT_S`): koľko častí beží, ako dlho tá najstaršia, a koľko sa
už prečítalo z Drive v koľkých požiadavkách. Riadok na hotovú časť stačí, kým
časti trvajú desiatky sekúnd; len čo sa jedna zasekne, je v logu ticho
a zaseknutý beh vyzerá presne ako pomalý. Počet požiadaviek je tu to hlavné
číslo – keď rastie, číta sa; keď stojí, čaká sa.

Dve vrstvy skladu zámerne: cache je rýchla, ale GitHub ju po siedmich dňoch
bez použitia zmaže a repo má strop 10 GB; release nevyprší. Cache sa ukladá
pod **prefix + číslo behu** a obnovuje cez `restore-keys` – pri pevnom kľúči
by ju prvý beh zabral a časti dopočítané neskôr by sa už nikdy neuložili.

Skaly z `dmr5` si tým pádom **DEM vôbec nesťahujú**: `check-dem` pre vrstvu
`rocks` nič nedopĺňa a `slope-chunks.py` si číta priamo z Drive po častiach.

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
  (`ROCK_HEARTBEAT_S`). Ten hovorí, **prečo** to trvá, nie len že to trvá:

  ```
  … gdal_contour: beží 0:05:30, pamäť 0.2 GB, CPU 99 %, disk +0/+12 MB,
    výstup 0 MB, podľa 20 % skončí o ~0:22:00
  ```

  `CPU %` rozlíši „počíta" od „visí na I/O" – pri 99 % pomôže len menej práce,
  pri 0 % je problém inde. `disk +čítané/+zapísané` ukáže, či sa vôbec hýbe.
  A **odhad konca je z nameraných percent**, nie z konštanty: tá sa pri
  `gdal_contour` mýlila aj 78× a odhad „0:00:19" pred behom, ktorý trval
  štvrť hodiny, je horší než žiadny.
- **Rozpočet sa stráži aj na nameranom čase.** `ROCK_BUDGET_MIN` (default
  100 min) sa dovtedy kontroloval len ako odhad *pred* spustením – a keďže
  odhad stojí na tej istej rozbitej konštante, prepustil čokoľvek. Teraz sa
  zvyšok rozpočtu podáva tepu ako `max_s`, takže beh, ktorý sa doňho nezmestí,
  zastaví sám seba s hláškou, čo zmenšiť. Sklon v sklade pritom ostáva.
- **Mozaika sa pred vektorizáciou oreže na územie.** Sklad má **absolútnu**
  mriežku častí – to je jeho zmysel, lebo tá istá zem tak padne vždy do tej
  istej časti a časti sa dajú znovu použiť. Mozaika je potom ale zjednotenie
  CELÝCH častí, nie územia: pri strane časti 4 096 m môže 2 km² štvorec
  pretínať štyri z nich, čiže **67 miliónov buniek namiesto dvoch**.
  `gdal_contour` toľko aj vektorizoval a plochy navyše nikto neorezal – končili
  v mape mimo výrezu, ktorý si beh vypýtal. Reže sa VRT, nie dáta (zápis do
  XML, nie kopírovanie rastra), takže to stojí milisekundy a časti v sklade
  ostávajú nedotknuté. Spolu s mriežkou vyššie je to na tom teste **34× menej
  práce**: 25 min → necelá minúta.
- **Časti mimo územia sa preskočia.** EPSG:3035 je pootočená voči poludníkom,
  takže obdĺžnik opísaný bboxu je v metroch väčší než región – pri Prešovskom
  kraji 208×111 km namiesto 200×82 km. Časti, ktoré do bboxu vôbec
  nezasahujú, sa nepočítajú (26 zo 170 pri 1 m).
- **Poistka na pamäť.** Keď `gdal_contour` prekročí `ROCK_MAX_RSS_GB`
  (default 12 GB), tep ho zastaví s hláškou – lepšie než tiché zabitie
  runnera na OOM, po ktorom v logu nie je nič.
- **Aký je to detail a kto ho vyberá.** `rock_res: auto` (default) nechá
  mriežku vybrať `rock-areas.py`: zoberie najjemnejšiu z rebríčka
  1 / 1,5 / 2 / 3 / 4 / 5 / 8 / 10 / 15 / 20 m, ktorá naraz

  1. **sa zmestí do rozpočtu času** (`ROCK_BUDGET_MIN`, default 100 min) – to
     je ten istý odhad, ktorý inak beh zastaví, len použitý dopredu, a
  2. **má pri danom DEM ešte zmysel** – dolný strop je desatina bunky
     zdrojového modelu, najmenej 1 m.

  Ten druhý strop je dôležitejší, než sa zdá: **Sonny má pre Slovensko bunku
  ~20 m**, takže pri ňom auto vždy skončí na 2 m. Jemnejšia mriežka by len
  interpolovala medzi tými istými výškami – stála by štvornásobok času a
  nepridala ani jeden nový tvar terénu. Reálny skok v detaile prinesie až iný
  zdroj (`rock_source: dmr5` s výrezom, 1 m LiDAR → auto ide na 1 m).

  > **Ten absolútny strop bol 0,5 m a pri DMR 5.0 to bola chyba.** Model má
  > bunku 1 m, takže z `max(0.5, 0.1)` vyšlo 0,5 m – dvojnásobné
  > prevzorkovanie v každej osi, čiže štvornásobok buniek bez jediného nového
  > metra terénu. Pixel dlaždice má pri z16 (kam skaly idú) 1,57 m a pri z18
  > 0,39 m, takže tá polovica metra nie je vidieť ani teoreticky – zaplatila sa
  > ale plnou cenou. [Beh 31334778253](https://github.com/skifahrer/fricomaps/actions/runs/31334778253)
  > strávil na **2 km²** štvrť hodiny a nedošiel ani do tretiny. Hladší obrys,
  > kvôli ktorému to prevzorkovanie bolo, robia `--simplify` a `--smooth`
  > (Chaikin) za zlomok ceny: zaoblujú hotové čiary, nie milióny buniek navyše.

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
  │  workers/drive-folder.py … prihlásený, cez Drive API (bez tokenu gdown)
  │  7z                 … rozbalí .zip / .7z
  │  workers/dem-tiles.py … GeoTIFF → dlaždice 1°×1° vo WGS84
  │  (alebo .hgt priamo … to je už 1° dlaždica, len bez hlavičky)
  ▼
release `dem-sonny`: N49E019.tif … + meta.json
```

- **Sťahuje sa prihlásene** ([`workers/drive-folder.py`](../workers/drive-folder.py)).
  Kým to robil `gdown --folder --no-cookies`, chodila požiadavka anonymne –
  a na verejný odkaz platí denný strop sťahovania zdieľaný so všetkými, kto
  naň siahnu. Prihlásená cesta ide cez Drive API: obsah priečinka sa vypíše
  z `files.list` (namiesto parsovania HTML stránky, ktoré `gdown` robí a ktoré
  sa mení), súbory sa čítajú cez `Range` tým istým `Pool`-om ako DMR 5.0,
  takže odmietnutie príde ako 403 s dôvodom a nie ako HTML stránka s HTTP 200.
  Jednotkou práce je **súbor**: hotový sa preskočí, rozrobený sa dopočíta
  z `.part`, takže zrušený beh nezahodí, čo už stiahol. Bez tokenu sa nemení
  nič – použije sa `gdown` ako predtým a do logu aj do súhrnu ide `::warning::`,
  že platí verejný limit.
- **Prihlásenie tu strop NEDVÍHA, a nesmie sa tváriť, že áno.** Denný limit je
  viazaný na **vlastníka** súboru, nie na toho, kto sťahuje: na DMR 5.0 (naše
  vlastné súbory) je strop rádovo vyšší, na Sonnyho cudzí priečinok zdieľaný
  odkazom platí ten istý ako predtým. `drive-folder.py` preto pri každom behu
  vypíše, koľko súborov účet nevlastní. Skutočná poistka proti Sonnyho stropu
  je práve to zrkadlo v releasi: stiahne sa raz sem a build mapy už na Drive
  nesiaha.

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

## Štvrtý workflow: „DMR 5.0 z Drive (ETRS89)"

> **Toto si volá Build map sám**, a to dvoma jobmi naraz, lebo model má dve
> podoby. Je to **jediná cesta k DMR 5.0**: záloha z archívu ÚGKK (`dmr5.yml`,
> 198 GB ZIP so sekvenčným čítaním) bola zrušená, keď sa ukázalo, že Drive
> púšťa spoľahlivo a Range na ľubovoľnom offsete je rádovo lacnejší.

DMR 5.0 leží na Google Drive ako **dva holé BigTIFFy**. Plní dva release
(`dem-ugkk`, `dem-dmr5`), takže `fetch-dem.sh` ani `Build map` nemusia vedieť,
odkiaľ dáta prišli.

```
dmr5_etrs89.tif      145,39 GiB   423 518 × 207 589 px, 1 m, LZW, dlaždice 128²
dmr5_etrs89.tif.ovr   43,35 GiB   pyramídy 2, 4, 8, 16, 32, 64, 128, 256 m
CRS EPSG:3046 (ETRS89 / TM zone N34), origin X 191 148, Y 5 497 220
```

Tri veci, ktoré ten workflow rieši, a všetky sú zmerané:

1. **Drive vracia na `HEAD` `content-length: 0`**, takže GDAL súbor odmietne
   (`GetFileSize()=0`). S `CPL_VSIL_CURL_USE_HEAD=NO` sa otvorí, ale veľkosť
   si domyslí zle (~16 MB) a všetko nad ňou padá na „after end of file".
   [`workers/drive-serve.py`](../workers/drive-serve.py) je HTTP server na
   localhoste, ktorý tú hlavičku opraví a podáva **oba** súbory pod jedným
   menom – GDAL si tak nájde `.ovr` ako sidecar a pri hrubšom cieli číta
   z pyramíd. Otvorenie 145 GiB: 8 s, 9 požiadaviek, 0,3 MB.

2. **Limituje latencia, nie pásmo.** 48 náhodných výrezov po 400 kB:
   1 vlákno 1 143 ms/req, 8 vlákien 147 ms/req, 24 vlákien 68 ms/req. Preto
   sa okno krája na bloky **prichytené na cieľovú mriežku** (inak by susedné
   bloky mali navzájom posunuté mriežky a `gdalbuildvrt` by ich zlepil so
   švom) a číta sa súbežne.

3. **Výšky sú elipsoidické**, nie Bpv – je to ETRS89 verzia. Maximum v súbore
   2 697,03 m vs. Gerlach 2 654,4 m n. m. je tých +42,6 m geoidu. Predvolene
   sa odčíta EGM2008 (`-s_srs EPSG:3046+4937 -t_srs EPSG:4326+3855`,
   s `ERROR_ON_MISSING_VERT_SHIFT=YES`, aby chýbajúca mriežka nebola tichý
   omyl). Po prevode vyjde na Gerlachu 2 653,92 m. Na skaly by na tom
   nezáležalo – sklon sa geoidom nemení – na vrstevnice áno.

4. **Odmietnutie príde ako HTTP 200.** Keď Drive dáta dať nechce – typicky
   prekročený denný limit sťahovania súboru – nevráti chybový kód, ale
   **stránku v HTML so stavom 200**. Na `Range` request sa to pozná podľa
   dvoch vecí naraz: odpoveď je `200` (nie `206`, čiže rozsah ignoroval)
   a je kratšia, než sa pýtalo. V behu
   [31315890474](https://github.com/skifahrer/fricomaps/actions/runs/31315890474)
   to bolo 2 009 B namiesto 32 768.

   Kým to shim bral ako úspech, `_send_single` spadol v `_fetch` **ešte pred
   hlavičkami** a chybu len vypísal do logu – takže GDAL čakal na odpoveď,
   ktorá nikdy neprišla. Job visel **2 h 16 min** na `gdalinfo`, minul dva
   runnery a nevyrobil nič; v logu bol jeden riadok a potom ticho. Odteraz
   shim **odpovie vždy**: 502 s vysvetlením, GDAL to vráti ako chybu a job
   spadne v sekundách. Prvé takéto odmietnutie navyše zastaví celý `Pool` –
   limit sa opakovaním nepohne, tak sa naň nečaká šesťkrát pri každom bloku.

   Z toho istého testu vypadla druhá diera: rozsah, z ktorého po orezaní na
   súbor nič neostalo, sa bral ako „pošli celý súbor" – teda 145 GB namiesto
   32 kB. Teraz je z toho `416`, ako káže RFC 9110.

5. **Fronta čakajúcich spojení bola päť.** `socketserver` má
   `request_queue_size = 5` a to je pri šiestich súbežných gdalwarpoch
   (`slope-chunks.py --jobs 6`) málo. Keď fronta pretečie, jadro SYN
   **zahodí – ticho, bez chyby na oboch stranách**: shim sa o takej
   požiadavke nikdy nedozvie a nemá čo napísať do logu, klient ju
   retransmituje s exponenciálnym odstupom (1, 2, 4, 8 … s) a `connect` sa
   vzdá až po vyše dvoch minútach. GDAL to vypíše ako
   `Request for … failed with response_code=0`, čo vyzerá ako chyba Drive,
   hoci sa požiadavka k Drive ani neblížila.

   Beh [31338803278](https://github.com/skifahrer/fricomaps/actions/runs/31338803278)
   na tom padol **dve časti pred koncom**: 45 zo 47 spočítaných za 5:52,
   potom 3,5 minúty ticha a pád. Teraz je fronta `socket.SOMAXCONN` (nič
   nestojí, kým je prázdna), GDAL má `GDAL_HTTP_MAX_RETRY=5`
   a `GDAL_HTTP_CONNECTTIMEOUT=20` – z takého výpadku je dvadsaťsekundový
   zádrhel namiesto padnutého jobu.

   Prispieval k tomu aj bazén vlákien na sťahovanie úsekov: bol na **jednu
   požiadavku**, takže `FETCH_WORKERS = 12` nebolo 12, ale 12 × počet
   súbežných gdalwarpov (namerané 80 naraz) – a rástlo to práve s `--jobs`,
   teda s tým, čo sa ladí kvôli rýchlosti. Vlákna navyše nestoja len Drive:
   accept slučka shimu je obyčajná pythonovská slučka a pri stovkách vlákien
   sa k nej GIL nedostane dosť často. Bazén je odteraz jeden na proces
   (24 vlákien, namerané 68 ms/req).

   Shim si zlyhané požiadavky aj **ráta** a `slope-chunks.py` to vypisuje.
   Keď GDAL hlási chybu a tu je nula, požiadavka sa k shimu nedostala –
   hľadať sa má v sieti pod ním, nie na Drive.

Namerané: výrez 5,2 × 5,6 km pri 1 m trvá 1,2 min, stiahne 0,11 GB v 697
požiadavkách; skaly z neho (`rock-areas.py`, `--res=2 --slope=50`) dajú
4 514 plôch a 5,08 km² na 29 km² územia.

Kód: [`workers/drive-serve.py`](../workers/drive-serve.py) (shim nad Drive),
[`workers/drive-auth.py`](../workers/drive-auth.py) (prihlásenie) a
[`workers/dmr5-drive.py`](../workers/dmr5-drive.py) (okno, bloky, výstup).

### Prihlásenie ako vlastník dát (secret `GDRIVE_CREDENTIALS`)

Bod 4 vyššie – „odmietnutie príde ako HTTP 200" – nie je náhoda ani porucha.
Verejný odkaz („ktokoľvek s odkazom") má **denný limit sťahovania na súbor**
a ten limit **nezdieľajú len naše behy**: zdieľa ho každý, kto na ten odkaz
siahne. Keď sa vyčerpá, DMR 5.0 sa v tom behu nedoplní **vôbec**, lebo je to
jediná cesta k nemu. Nedá sa to vyriešiť opakovaním ani iným zdrojom.

Prihlásený **vlastník** má na svoje vlastné súbory strop rádovo vyšší a nedelí
sa o neho s cudzími klientmi. Dôraz na *vlastné*: strop visí na vlastníkovi
súboru, nie na tom, kto sťahuje, takže na **cudzí** priečinok zdieľaný odkazom
(Sonny) prihlásenie strop nedvihne. Aj tam sa ale oplatí – Drive API povie
dôvod odmietnutia rovno, kým verejná cesta vráti HTTP 200 a HTML stránku.
Preto sú tie isté dáta dostupné dvoma cestami a rozhoduje o nich prítomnosť
tokenu:

| | cesta | limit |
|---|---|---|
| prihlásený | `www.googleapis.com/drive/v3/files/<id>?alt=media` + `Authorization: Bearer` | vlastníkov, vysoký |
| verejný | `drive.usercontent.google.com/download?id=<id>&confirm=t` | denný na súbor, zdieľaný |

Bez secretu sa **nemení nič** – číta sa ako predtým, len sa výslovne vypíše,
že platí verejný limit. To „výslovne" je tu to podstatné: tichý návrat
k verejnému limitu (zmazaný secret, preklep v mene) by sa inak zistil až tým,
že Drive po pol dni prestane púšťať dáta. Preto to hlási krok **Prihlásenie na
Drive** na začiatku behu, riadok `prístup:` v logu čítania aj riadok
`prístup` v súhrne – rovnaká logika ako `dem-source.txt`: nesie sa, čo sa
NAOZAJ použilo. To isté platí pre Sonnyho: `Stiahnuť výškové dáta` píše cestu
k dátam do riadku `cesta k dátam` v súhrne behu.

**Kam všade sa token musí dostať.** `workflow_call` nededí secrets sám, takže
volajúci ich musí podať – a čítanie z Drive je na štyroch miestach, z ktorých
dve nie sú tam, kde by ich človek čakal:

| kde | čo číta | ako sa tam token dostane |
|---|---|---|
| `dmr5-drive.yml` | DMR 5.0 (výrez aj dlaždice) | `secrets: inherit` z `build-map.yml` |
| `update-dem.yml` | Sonnyho priečinok | `secrets: inherit` z `build-map.yml` |
| job `contours` v `build-map.yml` | vrstevnice z DMR 5.0 | `env:` priamo v jobe |
| job `rocks` v `build-map.yml` | sklon z DMR 5.0 (`slope-chunks.py`) | `env:` priamo v jobe |

Stráži to staticky `Lint workflows` (krok *Token vlastníka Drive sa dostane
všade, kde sa z Drive číta*): pozerá aj na volajúceho, aj na volaného, a hlási
aj **nekompletnú** trojicu – tá sa nesmie brať ako „veď tam niečo je", lebo
`drive-auth.py` na polovicu údajov (správne) padne.

**Čo treba raz nastaviť** (potom sa na to nesiaha, kým sa token neodvolá):

1. Google Cloud Console → projekt → povoľ **Google Drive API**.
2. *OAuth consent screen*: typ **External**, publishing status
   **In production**. Status „Testing" je tá pasca – refresh token v ňom platí
   **7 dní** a pipeline by potom raz do týždňa spadla na `invalid_grant`.
   Overenie appky Google nežiada, kým je jej jediným používateľom vlastník
   dát; pri prihlásení sa preklikáva „Advanced → Go to … (unsafe)".
3. *Credentials* → **OAuth client ID**, typ **Desktop app**.
4. Na vlastnom počítači (treba prehliadač, na runneri to nemá čo robiť):
   `python3 workers/drive-auth.py --login --client-id=… --client-secret=…`
   Beží pri tom loopback server na `127.0.0.1`, lebo Google zrušil
   „out of band" tok; bez prehliadača na tom stroji je `--manual`.
5. Vypísaný JSON vlož ako repository secret **`GDRIVE_CREDENTIALS`**.

Rozsah práv je `drive.readonly` – pipeline z Drive iba číta, takže token
v secrets nemôže na Drive nič zmeniť ani zmazať.

Prihlásenie sa dá podať aj **po troch secretoch** namiesto jedného –
`GDRIVE_CLIENT_ID`/`GDRIVE_CLIENT_SECRET`/`GDRIVE_REFRESH_TOKEN`, alebo
kratšie `DRIVE_CLIENT`/`DRIVE_SECRET`/`DRIVE_REFRESH`. Nie je to rozmar:
`client_secret` Google po zatvorení dialógu **druhýkrát neukáže**, takže keď
už raz v secrets leží, nemá sa prepisovať len preto, aby sa zlepil do jedného
JSONu. Nekompletná trojica je **chyba** (nie „tak teda verejne") a `Lint
workflows` ju zachytí staticky.

### Bez počítača: workflow „Prihlásenie na Drive (jednorazové)"

`--login` potrebuje prehliadač a loopback server na tom istom stroji, takže
z telefónu nepobeží. Na to je
[`drive-login.yml`](../.github/workflows/drive-login.yml), ktorý tú rolu
rozdelí: **prehliadač je telefón, shell je runner.**

```
beh 1 (bez `code`)  →  súhrn behu: klikateľný odkaz na prihlásenie
   telefón          →  prihlásiš sa, povolíš; prehliadač skončí na
                        http://127.0.0.1:8731/?code=… a nemá to kde otvoriť
beh 2 (`code` = tá adresa)
   → --exchange     →  refresh token do súboru (0600), NIE na výstup
   → gh secret set  →  priamo do secretu DRIVE_REFRESH
   → --auth-check   →  e-mail účtu a `✓` pri oboch súboroch
```

**Token nevidí ani jeho vlastník** a to je zámer: tento repozitár je public,
takže log behu, súhrn aj artefakty vidí ktokoľvek na internete – a refresh
token je čítací prístup na celý Drive. Preto sa z `--exchange` nedostane na
stdout vôbec (`--out` je povinné) a do `gh secret set` ide po stdine zo
súboru, nie argumentom ani cez `echo`.

Cena za to je **jedno právo, ktoré `GITHUB_TOKEN` nemá**: zapísať secret.
Treba naň dočasný fine-grained PAT v secrete `DRIVE_PAT` (jediné oprávnenie
*Secrets: Read and write*, jediný repozitár, expirácia týždeň), ktorý sa po
behu zmaže. Workflow to kontroluje **pred** výmenou kódu – kód z Google platí
pár minút a je jednorazový, tak nech nepadne až po ňom.

Alternatíva bez PATu je Googlom hostovaný **OAuth Playground** (vlastné
credentials, scope `drive.readonly`, *Access type: Offline*, *Force prompt:
Consent*) – token vidíš na obrazovke telefónu a prepíšeš ho do secretu. Chce
si to druhého klienta typu *Web application* s redirect URI
`https://developers.google.com/oauthplayground`, lebo desktopový smie mať len
loopback.

Nech token vznikne akokoľvek, `GDRIVE_CREDENTIALS` prijme okrem JSONu aj **tri
riadky**, aby sa na mobilnej klávesnici neskladali zátvorky:

```
client_id=…apps.googleusercontent.com
client_secret=GOCSPX-…
refresh_token=1//…
```

Oddeľovač smie byť `=` alebo `:` a delí sa na **prvom** výskyte – v tokene
`1//0gAb=cD` sa `=` bežne vyskytuje a delenie na poslednom by z neho odrezalo
kus.

**Kam všade sa ten secret musí dostať**, je na prekvapenie viac miest než
jedno, a práve preto to stráži `Lint workflows`:

```
dmr5-drive.yml    env na jobe `model`        → sonda, plán, čítanie blokov
build-map.yml     mirror-dmr5-area/-tiles    → secrets: inherit
build-map.yml     job `rocks` (a `contours`) → GDRIVE_CREDENTIALS v env kroku
```

Ten tretí riadok je ten, čo sa dá prehliadnuť: skaly z `dmr5` si DEM
nedopĺňajú vôbec (`slope-chunks.py --drive` číta sklon po častiach **rovno
z Drive**), takže najväčší čitateľ zo 145 GB rastra sedí v úplne inom jobe než
ten, ktorý model dopĺňa. Kontrola *„Token vlastníka Drive sa dostane všade,
kde sa z Drive číta"* preto hľadá v každom workflowe kroky, ktoré volajú
`dmr5-drive.py`, `slope-chunks.py` alebo `contours-build.sh`, a žiada pri
každom ten secret; a pri volaniach `dmr5-drive.yml` žiada `secrets: inherit`.

**Overenie bez čítania dát:** `python3 workers/dmr5-drive.py --auth-check`
vypíše účet a pri oboch súboroch to, či ich ten účet **vlastní**.
Vlastníctvo je tu podstatné: na cudzí súbor, ktorý je len nasdieľaný odkazom,
platí ten istý denný strop ako na verejný prístup, takže prihlásenie by
nezískalo nič – a nemá sa tváriť, že áno (hlási to ako `::warning::`).

Čo sa mení v shime: `Pool` drží spojenia **po hostoch** (prihlásená cesta vie
odpovedať presmerovaním na podpísanú adresu, tá sa zapamätá a pri prvom
zlyhaní zahodí), `Authorization` chodí len na kanonický API host – rovnako
ako to robí `curl -L`, ktorý hlavičku pri zmene hosta zahodí – a access token
sa sám obnovuje 5 minút pred vypršaním, lebo platí hodinu a čítanie blokov
trvá aj dve. Chyby z API sú konečne rozdelené na tie, ktoré má zmysel
opakovať (`rateLimitExceeded`, 5xx), a tie, kde je čakanie stratený čas
(`downloadQuotaExceeded`, `notFound`, odmietnutý token aj po obnove) – tie
zastavia celý `Pool` hneď, ako ich uvidí prvý blok.

**Job je rozdelený na čo najviac krokov a každý hovorí, čo robí a ako ďaleko
je.** Kým to boli tri kroky, bola z hodinového behu v Actions jedna nemá
položka a z padnutého behu sa nedalo povedať, čo presne zlyhalo:

| krok | čo robí | čo vypíše |
|---|---|---|
| Nastavenia | z formulára konkrétne čísla a mená | tabuľku „čo tento beh spraví" do súhrnu |
| Sonda | otvorí zdroj (8 s, 9 požiadaviek, 0,3 MB) | rozmer, mriežku, úrovne pyramíd |
| Plán čítania | okno, bloky, odhad | `okno 5,2 × 5,6 km · 12 blokov · ~1 min · ~0,11 GB` |
| Čítanie z Drive | jediná fáza na sieti | `[7/12] blok-0006 prečítaný, 9,1 MB – 0,6 min za sebou, zostáva ~0,5 min` |
| Zloženie | mozaika → COG / 1° dlaždice | prevod výšok, veľkosť výstupu |
| Kontrola veľkosti | strop assetu 2 GB | veľkosť každého súboru |
| Metadáta, Nahranie | `meta-*.json`, upload | čo išlo do ktorého releasu |
| Súhrn | tabuľka + rozbaliteľný log | namerané vs. odhad |

Odhad v pláne je z merania (29 mil. buniek = 1,2 min a 0,11 GB pri
`--jobs=12`), nie od stola, a má povedať „minúty alebo hodiny" – nad dve
hodiny sa ozve varovanie. Kroky *Plán / Čítanie / Zloženie* sú fázy jedného
scriptu (`--stage=plan|read|finish`), ktoré si stav podávajú cez
`drive-work/`. Vedľajší zisk: prečítané bloky sa zapisujú cez `.part`
a premenovanie, takže **opakovaný beh dopočíta len zvyšok** – rovnako ako
sklad častí sklonu a z rovnakého dôvodu.

### Build map si to dopĺňa sám

Táto pipeline sa **nespúšťa ručne**. Je volateľná (`workflow_call`) a `Build
map` si ju zavolá, keď mu v release chýba to, čo si vypýtal:

```
check-dem  ──►  mirror-dmr5-area   area:  <bbox výrezu>     ──► dem-ugkk
                                   asset: ugkk-<kľúč>.tif
           ──►  mirror-dmr5-tiles  area:  <bbox stupňov>    ──► dem-dmr5
                                   tiles: true, grid_m: 5
```

**Dva joby nad jedným workflowom, lebo `dmr5` má dve podoby a chýbať môžu
naraz.** Vrstevnice a skaly čítajú výrez v plnom rozlíšení (`ugkk-<pohorie>.tif`
z `dem-ugkk`), tieňovanie 1° dlaždice na 5 m (`N49E020.tif` z `dem-dmr5`) – to
sa robí na celý región, kde 1 m verzia neexistuje. Jeden výber vo formulári,
dva rôzne assety. Nie sú to dve pipeline, len dve volania tej istej.

**Výrez sa podáva BBOXOM, nie menom pohoria.** Čo sa má z Drive naozaj
prečítať, vie len `Build map`: výrez UŽ PRETNUTÝ S REGIÓNOM, a pri rýchlom
teste štvorec na pár km². Kým sa podával kľúč, `dmr5-drive.yml` si ho vyriešil
z `areas.json` **druhýkrát** a prečítal celý obdĺžnik pohoria – rýchly test na
2 km² tak čítal z Drive 541 km² Vysokých Tatier, čiže hodiny namiesto minút.
Je to tá istá trieda chyby ako beh 31307163093 (dve odpovede na jednu otázku),
len nezhodila beh, iba ho predražila. Meno assetu preto chodí zvlášť
(`asset:`): z bboxu sa odvodiť nedá, lebo build si súbor hľadá podľa kľúča
výrezu. Stráži to `Lint workflows`.

**S tým súvisí kľúč výrezu v rýchlom teste.** `plan` ho rieši raz – keď počíta
`dem_bbox`, teda ten štvorec pre terénne vrstvy – a krok „Vyrieš testovací
výrez" si odpoveď preberá. Keď sa počítal druhýkrát, dostal `dem_bbox` (už ten
štvorec) a `--test-km2` sa mu nepodávalo, tak vyšiel ten istý bbox, ale kľúč
**bez** prípony `_test2`: výrez na 2 km² sa volal `vysoke_tatry` presne ako celé
pohorie. Meno assetu je z kľúča, takže by testovací DEM sadol v release pod
`ugkk-vysoke_tatry.tif` – meno, ktoré sľubuje celý obdĺžnik – a ďalší ostrý beh
by z dvoch kilometrov štvorcových počítal vrstevnice celých Tatier. Je to
presne ten istý druh sľubu ako pri dlaždiciach nižšie. (`cely` príponu
zámerne nedostáva: je to sentinel „žiadny výrez", nie meno územia, a prípona
by prepla podobu modelu z dlaždíc na výrez.)

**Dlaždice sa dopĺňajú po celých stupňoch, nie po bboxe.** Meno `N49E020.tif`
je sľub o celom stupni a build si dlaždicu podľa mena hľadá – keby v release
ležal pod tým menom len prienik s bboxom, ďalší beh by kontrolou prešiel
(„dlaždica tam je") a tieňovanie by ticho skončilo v polovici mapy. Preto
`--tiles` okno pred čítaním rozšíri na celé stupne. Cena: rádovo pol hodiny
a ~2 GB z Drive **na stupeň** – ale raz, a potom to v release ostane.

> **Prečo to nejde cez `update-dem.yml`.** Tá pipeline archív stiahne na runner
> a rozreže ho; DMR 5.0 má 145 GB a runner má voľných ~60 GB. Bol tam
> rozcestník, ktorý na `dmr5` vypísal, kam ísť ručne, a **skončil úspechom** –
> takže `check-dem` poslal „treba doplniť" do jobu, ktorý nedoplnil nič, job
> zazelenal a build spadol o desať jobov neskôr na tom, že v release nie je ani
> jedna dlaždica ([beh 31307163093](https://github.com/skifahrer/fricomaps/actions/runs/31307163093)).
> Dnes je `what: dmr5` v `update-dem.yml` chyba.

### Jedna odpoveď na „ktorý release a ktoré assety"

Tú istú otázku si kladú dve miesta: `workers/check-dem.sh` (čo hľadať a či to
treba doplniť) a `workers/fetch-dem.sh` (čo naozaj stiahnuť). Kým bola napísaná
dvakrát, rozišla sa – kontrola hľadala výrez v `dem-ugkk`, kým tieňovanie
sťahovalo dlaždice z `dem-dmr5`. Odpoveď preto dáva jediný
[`workers/dem-target.py`](../workers/dem-target.py):

```console
$ python3 workers/dem-target.py --source=dmr5 --area-key=vysoke_tatry --bbox=20.1,49.16,20.12,49.18
form=area
release=dem-ugkk
assets=ugkk-vysoke_tatry.tif
mirror=dmr5:area:vysoke_tatry

$ python3 workers/dem-target.py --source=dmr5 --area-key=cely --bbox=20.1,49.16,20.12,49.18
form=tiles
release=dem-dmr5
assets=N49E020.tif
mirror=dmr5:tiles:20,49,21,50
degrees=20,49,21,50
```

**Rozhoduje kľúč výrezu, nič iné.** Vrstva sa nepýta – pýta sa len ten, kto
volá: vrstevnice a skaly kľúč podávajú (`contours-build.sh`), tieňovanie nie
(krok „Tieňovanie reliéfu" v `build-map.yml`). Že to tak naozaj je, stráži
`Lint workflows` krokom *„Vrstvy podávajú kľúč výrezu tak, ako to čaká
kontrola"* – porovnáva tabuľku `layer_area_key` v `check-dem.sh` s počtom
argumentov v každom volaní `fetch-dem.sh`.

Z toho aj plynie, že sa **dedupuje podľa podoby, nie podľa zdroja**: pri
jedinom `dmr5` vo formulári môžu chýbať oba tvary a „ten model už dopĺňa iná
vrstva" by druhý ticho zahodilo.

## Piaty workflow: „Skaly z tieňovaných dlaždíc"

Pokusná druhá cesta k skalám. Všetko ostatné v pipeline počíta skaly zo
**sklonu DEM**; tento workflow sa výšok nedotkne a hľadá **tmavé plochy**
v hotovom hillshade z freemap.sk.

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
   ▼
otvorenie (`open`, 3 m)       preč všetko užšie než stena – vlásočnicové
   │                          ryhy a mikrotiene, z ktorých je pri z14
   │                          sivá deka (erózia + dilatácia)
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

Build map to **nepočíta** – ale zavolá si na to túto pipeline. Pri
`rock_source: tienovanie` sa v behu objaví job *Skaly z tieňovania*, ktorý je
`workflow_call` na `shading-rocks.yml`: stiahne dlaždice, nájde plochy,
nahrá ich do releasu, a job s vrstevnicami si ich potom už len vytiahne.
Platí teda to isté, čo pri výškových modeloch – **`Build map` je jediné, čo
spúšťaš**.

```
rock_source: tienovanie                    stiahne dlaždice a spočíta skaly
  + options: rock_img_zoom=18              iný zoom dlaždíc
  + options: rock_img_options="fill=40"    prepínače pre výpočet
  + options: rock_img_asset=rockimg-…      presne tento hotový asset
                                           (vtedy sa nepočíta nič)
```

Stiahnuté JPG dlaždice vypadnú ako artefakt `dlazdice-tienovania-{výrez}-z{zoom}`.
Sú to tie isté obrázky, z ktorých sa skaly hľadali – dovtedy sa dali vidieť
len v cache behu. Sú nekomprimované v ZIPe (JPG sa balí zbytočne) a pri z18
na veľké pohorie to je aj vyše gigabajtu, preto sa držia 14 dní a nie 90 ako
polygóny.

Keby v release aj tak nič nebolo, build to povie (`::error::`) a nespadne
späť na skaly z DEM – to by bola tichá zámena jedného zdroja za druhý. Zdroj skál je aj v kľúči cache vrstevníc, takže sa
`dem` a `shading` nemôžu navzájom vrátiť z cache.

Kód: [`workers/shading-rocks.py`](../workers/shading-rocks.py). Formát
výstupu je zhodný so skalami z DEM (vrstva `rock`, EPSG:4326, `class`
= `steep`/`cliff`, `area` v m²); jediný rozdiel je, že skaly z DEM majú
atribút `slope` a skaly z obrázka `dark`.

### Najtenšie vlákna siete skala nie sú

Prah nad hillshade nenájde len steny. Nájde aj **vlásočnicové ryhy
a mikrotiene** cez celý rozčlenený svah – a tie sú v mape to, čo škodí.
Vektorizáciou sa z nich stane **jeden prepojený polygón** cez celý výrez
a pri z14 a nižšie z neho nie je sieť, ale **rovnomerná sivá deka**.

Namerané na výreze pri Gerlachu (2 km², z17, `dark 125`):

| `open` | pokrytie | pri z14 zaliatych pixelov | ako to vyzerá |
|---|--:|--:|---|
| `0` (dovtedy) | 21,6 % | 20,7 % | súvislý sivý záves cez celý výrez |
| `2` (1,6 m) | 15,4 % | 15,2 % | – |
| **`3` (default)** | **9,5 %** | **9,2 %** | **čitateľné samostatné telesá** |
| `6` | 5,5 % | 5,3 % | len výrazné steny |

Zahadzuje sa podľa **ŠÍRKY**, nie podľa plochy: celá sieť je jeden veľký
útvar, takže `min_area` na ňu vôbec nesiaha. Robí to morfologické
**otvorenie** – erózia zmaže všetko užšie než `2 × open`, dilatácia vráti
prežitým jadrám ich pôvodný rozsah. Stena teda ostane stenou, vlásočnica
zmizne. Presne tým sa stena od ryhy líši.

Počíta sa to na hotovej maske tmavosti, ešte pred vektorizáciou: pred prahom
by sa mazalo z plynulej tmavosti a `dark_always` by sa nemal ako uplatniť,
po vektorizácii je už celá sieť jeden polygón a šírka sa z neho nedá
vytiahnuť. Polomer je v **metroch na zemi**, takže to isté nastavenie platí
na každom zoome rovnako.

Implementácia je separovateľné bežiace min/max (dva prechody po `2r+1`
posunoch namiesto `(2r+1)²`) – namerané 140 mil. px/s, čiže na z17 nad
Vysokými Tatrami okolo 7 sekúnd. Obrysy sa tým naopak **zrýchlia**: menej
vlákien = rádovo menej segmentov na poskladanie, a to je najdrahšia fáza
celého behu.

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

Hillshade je obraz sklonu, ale **osvetleného z jednej strany**. Severozápadné
steny sú na ňom najtmavšie, juhovýchodné najsvetlejšie – tie druhé teda táto
cesta systematicky prehliadne. Zato má rozlíšenie, na aké si sami sklon
nespočítame. Je to pokus vedľa hlavnej cesty, nie jej náhrada.

# fricomaps

All-in-one mapová aplikácia. Vektorové mapy Slovenska z OSM dát – jedna
pipeline, jeden formát (PMTiles), spoločné štýly pre web aj mobil.

## Štruktúra monorepa

```
app/ios/       iOS aplikácia (SwiftUI + MapLibre Native)
backend/       NestJS backend (API – regióny, budúce užívateľské veci)
poc/web/       proof-of-concept web viewer (MapLibre GL JS + PMTiles)
               + developer mode na ladenie štýlu priamo v prehliadači
workers/       pipeline: regióny, výškové dlaždice, značené trasy, generátor
               štýlov, SDF sprite, vzory do spritu, zápis úprav štýlu,
               skaly zo sklonu DEM aj z tmavých plôch v tieňovaní
docs/          návrhy (iOS / multiplatform), podrobný popis pipeline
.github/workflows/  CI pipeline (výškový model + build mapy + deploy Pages
                    + pokusné skaly z tieňovaných dlaždíc)
```

> Podrobne – čo robí každý krok, aké formáty medzi sebou putujú a prečo –
> je v [docs/pipeline.md](docs/pipeline.md).

## Ako funguje pipeline

```
Build map                    deväť jobov, tie dlhé bežia súbežne:
(manuálne, výber regiónu)      plan     región + PBF z osm.fr exportov
                               tiles    Planetiler ─► {región}.pmtiles
                               contours vrstevnice + skaly z DEM
                               terrain  tieňovanie a 3D ako PNG dlaždice
                               trails   značené trasy z OSM relácií
                               assets   SDF sprity a glyfy
                               deploy   zloží _site ─► GitHub Pages

Stiahnuť výškové dáta        Sonny 20m / ÚGKK DMR 3.5 ─► rezanie
(sám, keď terén chýba)       na 1° dlaždice ─► release `dem-sonny`:
                             N49E019.tif + meta.json
                             ▲ Build map ho zavolá automaticky, keď v release
                               nie je pre jeho územie ani jedna dlaždica

Pripraviť DMR 5.0            198 GB ZIP z opendata.skgeodesy.sk, čítaný
(vedome, trvá dlho)          priamo cez /vsizip//vsicurl/ – nič sa nesťahuje:
                               plan   centrálny adresár ─► inventár archívu
                               model  jeden prechod ─► hotový model do releasu
                             celé Slovensko (5 m) ─► `dem-dmr5`  ─► zdroj: dmr5
                             pohorie (1 m)        ─► `dem-ugkk`  ─► zdroj: ugkk
                             ▲ výstup je vstup pre Build map: vrstevnice,
                               skaly aj tieňovanie sa počítajú z neho

Skaly z tieňovaných dlaždíc  POKUS: hillshade JPG z freemap.sk ─► tmavé
(pokus, na jedno pohorie)    plochy ─► polygóny ─► release `dem-rocks-img`
                             ▲ Build map si ich vypýta výberom
                               rock_source: tienovanie

Uložiť úpravy štýlu          style-overrides.json z developer módu
(po doladení mapy)           ─► kontrola + prečistenie
                             ─► poc/web/style-overrides.json v repozitári
```

- **Výber regiónu:** celé Slovensko alebo ktorýkoľvek z 8 krajov – PBF sa
  sťahuje **iba pre daný región** z regionálnych exportov
  [osm.fr](https://download.openstreetmap.fr/extracts/europe/slovakia/)
  (rezané po skutočných administratívnych hraniciach, denne aktualizované):
  `europe/slovakia/{kraj}-latest.osm.pbf` (36–63 MB na kraj), celé Slovensko
  `europe/slovakia-latest.osm.pbf` (~380 MB). Mapovanie a presné bboxy z
  osm.fr rezacích polygónov sú vo [workers/regions.json](workers/regions.json).
- **Ľubovoľný región Európy/sveta:** pri spúšťaní workflowu vyplň
  `custom_pbf_url` (URL na `.osm.pbf` z osm.fr extracts stromu, napr.
  `https://download.openstreetmap.fr/extracts/europe/austria.osm.pbf`)
  a `custom_name`. Bbox sa prečíta z PBF hlavičky (alebo zadaj `custom_bbox`).
- **Témy a štýlovanie:** [poc/web/themes.js](poc/web/themes.js) – 4 farebné
  témy (Svetlá, Tmavá, Outdoor, Retro/Pastel), ~135 vrstiev pokrývajúcich celú
  OpenMapTiles schému: krajinná pokrývka, využitie územia, voda a vodné toky,
  budovy (od z16 v 3D), cesty vrátane chodníkov/cyklotrás/schodov, mosty a
  tunely, železnice, lanovky, hranice až po obce, súpisné čísla, vrcholy hôr,
  letiská a POI s ikonkami zo spritu osm-liberty (maki). Všetky nápisy majú
  jemný obrys (`textHalo`), aby zostali čitateľné nad ľubovoľným podkladom;
  pohoria, hrebene a geografické oblasti sa od nízkych zoomov kreslia
  kurzívou a verzálkami, aby sa nepliedli so sídlami.
  Ten istý generátor vyrába statické `styles/{region}-{tema}.json` pre iOS.
- **Značené trasy:** turistické chodníky, cyklotrasy, bežky a jazdecké trasy
  z OSM relácií – ako farebné pásiky **vedľa** cesty, s názvom pozdĺž trasy.
  Viď [Značené trasy](#značené-trasy-turistika-cyklo-bežky).
- **Ikonky bez podkladov, s farbou:** hotové sprity kreslia symboly na
  podklade (osm-liberty v bielom koliesku, osm-bright so svetlým halom) a
  farbu im meniť nejde. Pipeline z každého zdroja vyrobí vlastný **SDF sprite**
  ([workers/build-sdf-sprite.mjs](workers/build-sdf-sprite.mjs)), kde je len
  samotný symbol a dá sa mu nastaviť `icon-color` aj `icon-halo-color`.
- **Tri sady ikoniek** ([poc/web/icon-sources.js](poc/web/icon-sources.js)) sa
  nasadzujú všetky naraz, takže sa dajú v developer móde prepínať naživo.
- **Developer mode:** ladenie mapy priamo v prehliadači – viď nižšie.

## Nadmorská výška, vrstevnice a skaly

**OpenStreetMap výškové dáta neobsahuje.** Má len bodový tag
[`ele`](https://wiki.openstreetmap.org/wiki/Key:ele) na vrcholoch, sedlách,
prameňoch či staniciach — žiadny terénny model, a vrstevnice sa doň zámerne
nenahrávajú. Každá OSM mapa s reliéfom (OpenTopoMap, OpenAndroMaps, Waymarked
Trails) preto kombinuje OSM s externým DEM. Robíme to rovnako:

| čo | zdroj | kde sa berie |
|---|---|---|
| výšky vrcholov | OSM tag `ele` | už v dlaždiciach, vrstva `mountain_peak` |
| **vrstevnice a skaly** | **Sonny's LiDAR DTM, model 20m** | náš release `dem-sonny` (napĺňa ho workflow *Stiahnuť výškové dáta*) |
| **tieňovanie reliéfu, 3D terén** | **ten istý Sonny DEM** | vlastné PNG dlaždice `terrain/{z}/{x}/{y}.png`, uložené v release `dem-terrain` |
| tieňovanie a 3D – záloha | AWS Terrain Tiles (Terrarium) | [registry.opendata.aws](https://registry.opendata.aws/terrain-tiles/), keď sa vlastné nevyrobia |

Tieňovanie reliéfu je **predvolene vypnuté** – na farebnej mape prekrýva
odtiene plôch a pri malých mierkach z nej robí hnedý šum. Zapína sa
prepínačom v paneli ⚙ (a takto zapnuté sa aj zapečie do štýlu pre iOS).

### Zdroj výšok: Sonny's LiDAR DTM, model **20m**

[Sonny's LiDAR DTM](https://sonny.4lima.de/) je **model terénu z LiDARu** –
na rozdiel od Copernicus GLO-30, ktorý je *DSM*, teda model povrchu vrátane
stromov a striech. V lese preto vrstevnice sedia na zemi, nie na korunách, a
skalné steny nie sú rozmazané vegetáciou. Práve preto je predvoleným zdrojom.

Sonny ponúka pre Slovensko dva použiteľné modely a **berieme 20m**:

| model | formát | vodorovne | **zvisle** |
|---|---|---|---|
| **20m** | GeoTIFF | 20 × 20 m | **0,1 m** |
| 1″ | `.hgt` | 20 × 30 m | 1 m |

Rozhoduje ten zvislý krok. Z metrových schodov vychádza schodíkovitý sklon,
ktorý súvislú skalnú stenu roztrhá na kopu falošných úlomkov – namerané na
tom istom území Vysokých Tatier:

| zvislé rozlíšenie | skalných plôch | plocha skál | bodov na obrys |
|---|---|---|---|
| 0,1 m (20m model) | 2 138 | 4 218 ha | 195 |
| 1 m (1″ `.hgt`) | 5 293 | 4 223 ha | 101 |

Rovnaká celková plocha skál, ale z metrových dát je z nej **2,5× viac
polámaných kúskov s hrubším obrysom**. Viac polygónov tu teda neznamená viac
detailu, ale viac šumu.

Sonny distribuuje dáta cez **Google Drive**, z ktorého sa v každom builde
sťahovať nedá (nemá stabilné priame URL a pri väčšom počte stiahnutí vracia
limit). Preto je medzi tým **zrkadlo v releasi**:

```
Google Drive (priečinok krajiny)
  → gdown              stiahne celý priečinok
  → 7z / unzip         rozbalí .zip
  → workers/dem-tiles.py   GeoTIFF (aj celá krajina v metrickej projekcii)
                           → dlaždice 1°×1° N49E019.tif vo WGS84
     (.hgt sa prevádza priamo – je to už 1° dlaždica, len bez hlavičky)
  → release `dem-sonny` + meta.json
```

Rezanie na dlaždice je potrebné preto, že 20m model môže byť **jeden GeoTIFF
na celú krajinu a v metrickej projekcii**, kým build mapy chce sťahovať len
dlaždice pre svoj bbox a lepiť ich `gdalbuildvrt`-om (ten rôzne projekcie
v jednom VRT neunesie). Jeden release = jeden model; miešať 20m a 1″ pod
rovnakým `release_tag` nemá zmysel, dlaždice sa volajú rovnako.

Build mapy si potom vypýta **len tie dlaždice, ktoré pokrývajú jeho bbox**.
Bbox je obdĺžnik, ale produkt pokrýva krajinu – rohové bunky za hranicou
(u Slovenska napr. `N47E016` v Maďarsku) v ňom nikdy nebudú. Chýbajúce
dlaždice sú preto **varovanie so zoznamom**, nie chyba: tam jednoducho nebude
terén. Build zlyhá až vtedy, keď pre dané územie nie je **ani jedna**.

> **Copernicus GLO-30 ako záloha je zámerne vypnutý.** Je to model *povrchu*:
> vrstevnice by v lese viedli po korunách stromov a skaly by vychádzali
> z vegetácie. Keby sa ním chýbajúce dlaždice ticho dopĺňali, časť mapy by
> klamala a nikde by nebolo vidieť, ktorá. Radšej nech build povie, že terén
> chýba. Zapnúť sa dá vrátením sťahovania z `copernicus-dem-30m.s3.amazonaws.com`
> do kroku *Vrstevnice a skaly z DEM*.

Licencia Sonny's DTM je CC BY 4.0, zdroj sa uvádza v atribúcii mapy.

### Vrstevnice

Počítajú sa v pipeline a končia vo **vlastnom `.pmtiles`**, takže fungujú na
webe aj na iOS cez ten istý `style.json`:

```
DEM (1°×1° dlaždice pre bbox: dem-sonny, doplnené Copernicusom)
  → gdalwarp   orez na bbox (zjemnenie DEM je predvolene vypnuté – vrstevnice
               sa trasujú z plného rozlíšenia; `contour_smoothing` v oblúkových
               sekundách ho vie zapnúť, 2 = pôvodné hladenie)
  → gdal_contour -i 10
  → ogr2ogr    dopočíta `level`: major (100 m) / mid (50 m) / minor (10 m)
  → planetiler generate-custom --schema=workers/contours.yml
  → {región}-contours.pmtiles
```

Vrstevnice sa trasujú z **plného rozlíšenia DEM** a do dlaždíc idú na
najvyššom zoome bez zjednodušovania geometrie
(`--simplify_tolerance_at_max_zoom=0`) a bez zahadzovania drobných prvkov
(`--min_feature_size_at_max_zoom=0`) – malé uzavreté krúžky na kopčekoch a
v jamách teda ostávajú. Nižšie zoomy si Planetiler zjednodušuje sám, inak by
z vrstevníc bola čierna plocha.

`level` riadi, čo je vidieť kedy: hlavné vrstevnice od z10, polovičné od z12,
základné od z13, popisky výšky pozdĺž hlavných od z13. Výsledok je
nacacheovaný podľa bboxu, zdroja výšok a intervalu — vrstevnice závisia len od
územia, takže sa pri ďalšom builde mapy nepočítajú znova.

Ovládanie vo workflowe: `contours` (zap/vyp), `contour_interval` (default
10 m; zvýrazňuje sa každá 10. čiara ako hlavná a každá 5. ako polovičná, čiže
pri 10 m sú to doterajších 100 a 50 m), `contour_maxzoom` (default 14) a
`contour_smoothing` (default 0 = bez zjemnenia). Bez zjemnenia je terén detailnejší, ale vrstevníc je viac – a keď
prekročia 40 % rozpočtu stránky, pipeline im sama zníži maxzoom.

### Tieňovanie reliéfu a 3D terén

MapLibre nevie čítať výšky z GeoTIFFu – potrebuje pyramídu PNG dlaždíc, kde je
výška zakódovaná do farby (*terrarium*). Robia sa z toho istého Sonny DEM ako
vrstevnice ([workers/build-terrain.py](workers/build-terrain.py)), takže 3D
reliéf nedvíha koruny stromov, kým vrstevnice vedú po zemi.

- **Každý zoom sa prevzorkuje z DEM nanovo** (`-r average`), nezmenšujú sa
  hotové dlaždice: priemerovať sa musí *výška*, nie zakódovaná farba.
- **Zoom končí na 13** (`terrain_maxzoom`) – jemnejšie 20 m DEM neunesie.
- **Ukladajú sa do releasu `dem-terrain`** ako jeden `.tar.zst` na región
  a maxzoom. Ďalší build ich už len stiahne; `terrain_rebuild: áno` ich
  vynúti prepočítať nanovo.
- Keď sa nevyrobia, štýl padá späť na AWS Terrain Tiles.

### Skaly (najstrmšie úseky terénu)

Kde sú vrstevnice husté, je stena. Hustota čiar je ale len **obraz sklonu** –
a závisí od intervalu vrstevníc aj od zoomu. Skaly sa preto nepočítajú z
hotových vrstevníc, ale rovno **zo sklonu terénu**, z toho istého DEM:

```
DEM
  → gdalwarp -t_srs EPSG:3035     do metrickej projekcie (v stupňoch by sklon
                                  vyšiel skreslený – 1° po dĺžke je u nás
                                  o tretinu kratší než 1° po šírke),
                                  mriežka `rock_res` (auto = najjemnejšia,
                                  ktorá sa zmestí do času a má pri danom
                                  DEM ešte zmysel)
  → gdaldem slope                 sklon v stupňoch
  → gdal_translate -ot Int16      sklon v stotinách ° na disk (mozaika celého
                                  územia sa vo Float32 nezmestí)
  → gdalbuildvrt                  mozaika sklonu, a až nad ňou NARAZ:
  → gdal_contour -p -fl …         izolínie sklonu ako PLOCHY, aj s dierami
                                  (hladší okraj než polygonizácia po pixeloch)
  → -explodecollections           samostatné skaly
  → filter najmenšej plochy       + `class`: steep (≥50°) / cliff (≥65°)
  → -simplify                     preč so schodíkmi po hranách buniek
  → smooth-polygons.py            zaoblenie rohov, ktoré po zjednodušení
                                  ostali ostré (Chaikin, 2 prechody)
  → vrstva `rock` v {región}-contours.pmtiles  – vektor, ako všetko ostatné
```

**Tvar plôch je tvar terénu.** Obrys je izolínia sklonu, teda presne tá čiara,
kde svah prekročí prah – členitý pás pod hrebeňom, oblúk okolo žľabu, ostrov
brala v suti. Žiadna mriežka štvorčekov (tá tu bola do augusta 2026 a je
preč).

**Obrys je zaoblený, nie zubatý.** Samotná izolínia zubatá nie je (priemerný
lom medzi segmentmi 4,6°) – zubatou ju robilo až zjednodušenie, ktoré tie
státisíce bodov zredukuje (28,5°). Preto sa po zjednodušení rohy ešte zaoblia
Chaikinovým orezávaním: dva prechody dajú 7,7°, čo je hladšie než pôvodný
raster, a stále o 43 % menej bodov než nezjednodušený originál. Čísla a
neúspešné pokusy (vyhladzovanie rastra sklonu plochy rozbíja: 326 → 1668) sú
v `workers/smooth-polygons.py`.

**Čo nie je nad prahom, sa nezafarbí.** Keď je vnútri steny miesto s menším
sklonom – polica, terasa, zarastený stupeň – vypadne z plochy **diera**, aj
keď je dookola všade sklon nad prahom. Diery sa nezapĺňajú ani nefiltrujú a
obrys ich obkreslí rovnako ako vonkajšiu hranicu, takže je polica v mape
vidieť.

**Vektorizuje sa naraz nad celým územím – a je to nutné.** Sklon sa pre kraj
nedá spočítať jedným rasterom (pri 2 m je to vyše 3 miliárd buniek), takže sa
počíta po častiach. Vektorizovať po častiach sa ale nedá: diera prerezaná
hranicou časti sa zmení na zárez v okraji a späť sa nezlepí ani cez
`ST_Union`. Namerané na syntetickom teréne (prstencová terasa v kuželi):

| postup | plôch | dier |
|---|--:|--:|
| celý raster naraz (referencia) | 2 | 2 |
| po častiach + `ST_Union` | 4 | **0** |
| **sklon po častiach, vektorizácia naraz** | **2** | **2** |

Preto sa po častiach počíta **len raster sklonu**, uloží sa na disk ako `Byte`
s krokom 0,5° (vo `Float32` by mala mozaika kraja ~13 GB) a `gdal_contour` ide
jedným priechodom nad celou mozaikou. Výsledok potom nezávisí od toho, na
koľko častí sa počítalo – overené pri 1, 12 aj 60 častiach je zhodný do
posledného m².

**Skaly sú vidieť všade, kde sú** – vrstva ide do dlaždíc od **z9** a štýl ich
kreslí od z9 (obrys od z11). Drobné plochy pritom nižšie zoomy nezaťažia:
Planetiler sám zahodí všetko menšie než pixel, takže z prehľadu ostanú len
veľké steny a s približovaním pribúdajú detaily.

#### Aký je to detail

| vec | hodnota |
|---|---|
| mriežka, na ktorej sa obrys počíta | **auto** (`rock_res`) – najjemnejšia, ktorá sa zmestí do času a má pri danom DEM zmysel |
| krok sklonu v mozaike | **0,01°** (Int16) – hrubší krok robil obrys zubatý |
| zjednodušenie obrysu | štvrtina mriežky (`ROCK_SIMPLIFY: -1`) – zmaže schodíky |
| zaoblenie rohov | **2× Chaikin** (`ROCK_SMOOTH: 2`) – priemerný lom 28,5° → 7,7° |
| bunka zdrojového DEM (Sonny 20 m) | ~20 m → **strop skutočného detailu** |
| najmenšia ponechaná plocha | jedna bunka mriežky: **4 m²** pri 2 m, **1 m²** pri 1 m |
| filter drobných prvkov v dlaždiciach | vypnutý na najvyššom zoome |

Presné čísla za konkrétny beh (počet plôch, najmenšia/priemerná/najväčšia
plocha, koľko km² skál, koľko plôch má dieru a koľko km² diery vykrojili) píše
build do **Summary** – viď [Súhrn buildu](#súhrn-buildu).

**Mriežku vyberá `auto` a vypíše prečo.** Prejde rebríček 0,5 / 1 / 1,5 / 2 /
3 / 4 / 5 / 8 / 10 / 15 / 20 m a zoberie najjemnejšiu, ktorá sa zmestí do
rozpočtu času (`ROCK_BUDGET_MIN`) a nie je jemnejšia než desatina bunky
zdrojového DEM. Pri Sonnym (20 m) z toho vždy vyjde **2 m** – jemnejšia
mriežka by len interpolovala medzi tými istými výškami, stála 4× viac času a
nepridala ani jeden nový tvar terénu. Skutočný skok v detaile prinesie až
`rock_source: ugkk` (1 m LiDAR), kde auto ide na 0,5 m. Zadať sa dá aj číslo
natvrdo (`rock_res: 1`).

> **Mriežka nie je to isté ako detail.** Mriežka 2 m hovorí, ako jemne je
> obrys odkrokovaný. Skutočný detail je ale stropený zdrojom: Sonny má pre
> Slovensko bunku ~20 m, takže tvary pod 20 m sú **dopočítané, nie merané** –
> interpolácia dá hladší a presnejšie umiestnený obrys, novú informáciu však
> nepridá. Jemnejšie by vedel len 1 m LiDAR
> ([ÚGKK DMR 5.0](https://www.geoportal.sk/)); ten sa z geoportálu sťahuje cez
> interaktívny export, takže by sa musel najprv nazrkadliť do releasu rovnako
> ako Sonnyho DTM.

#### Druhá cesta k skalám: tmavé plochy v tieňovaní (pokus)

Všetko vyššie počíta skaly **zo sklonu DEM**. Existuje aj druhá, pokusná
cesta, ktorá sa výšok vôbec nedotkne: vezme hotový **hillshade** z freemap.sk
a hľadá v ňom **tmavé plochy**. Robí to workflow **Skaly z tieňovaných
dlaždíc** ([`workers/shading-rocks.py`](workers/shading-rocks.py)):

```
XYZ dlaždice sk-hires-shading.tiles.freemap.sk/{z}/{x}/{y}.jpg
  → mozaika odtieňov šedej v EPSG:3857   dlaždice sú v ňom natívne, takže
                                         sa nič neprevzorkúva: 1 px = 1 px
  → raster „tmavosti"                    o koľko je pixel pod referenciou
  → gdal_contour -p -fl 0,5 -fl …        izolínia tmavosti ako PLOCHY,
                                         s dierami – ten istý nástroj aj tá
                                         istá sémantika ako u skál z DEM
  → -explodecollections + filter plôch a dier
  → -simplify + smooth-polygons.py       rovnaké zaoblenie ako pri DEM
  → rock.gpkg  ─► release `dem-rocks-img`  +  artefakt behu (iba polygóny)
```

**Prečo to môže fungovať:** tieňovanie je obraz sklonu a hires vrstva
freemap.sk je robená z 1 m LiDARu – pri z18 vyjde jeden pixel na **~0,4 m**
terénu. To je jemnejšie, než na čo si sklon vieme rozumne spočítať sami.

**Prečo to klame:** hillshade je osvetlený z jednej strany. Rovnako strmá
stena otočená k slnku je na ňom **najsvetlejšia zo všetkého**. Táto cesta
teda systematicky nájde severozápadné steny a systematicky prehliadne
juhovýchodné. Preto je to jedna z možností vo výbere `rock_source`
(`tienovanie`) a nie náhrada skál počítaných zo sklonu.

**Prah nie je jedno číslo.** Celý zatienený svah je tmavý bez toho, aby bol
skala; stena v presvetlenej doline býva svetlejšia než tráva vedľa. Prah sa
preto skladá z troch:

| input | čo znamená |
|---|---|
| `dark` (125) | nad touto šedou nie je skala **nikdy** |
| `dark_always` (70) | pod touto šedou je skala **vždy**, nech je okolo čokoľvek |
| `rel` (18) | medzi tým: koľko musí byť pixel pod **miestnym pozadím** |

Miestne pozadie nie je obyčajný priemer, ale priemer **svetlejších** pixelov
v okne (`local`, default 1500 m na zemi) – odpoveď na „ako svetlý je tu
osvetlený terén" sa nesmie dať stiahnuť dole tým, čo práve hľadáme. Dolný
strop `dark_always` tam nie je pre ozdobu: bez neho sa veľká súvislá stena
nenájde, lebo sa okno pozadia zmestí celé dovnútra nej a ostane z nej len
prstenec (namerané na skúšobných dátach).

**Svetlé miesto vnútri tmavej plochy ostane dierou** – polica, sneh,
kosodrevina. Presne ako pri skalách z DEM, a z toho istého dôvodu: pásmo
`gdal_contour -p` má vnútorné prstence tam, kde hodnota klesla pod prah.
Zahadzujú sa len dierky menšie než `min_hole` (default 10 m²), čo je zrno
JPEGu, nie polica.

##### Čo ukázala skutočná dlaždica

Predvolené hodnoty nie sú odhad – sú namerané na výreze z tej vrstvy
(1260×1933 px, Vysoké Tatry):

- **Je to farebný hillshade, nie šedý.** Žltozelený nádych, tiene ťahajú do
  modra (sýtosť ~34, `B−R` od −95 do +50). Čítame ho ako jas (luma 601), kde
  modrý kanál váži najmenej – modré tiene sa tým ešte prehĺbia, čo nám
  vyhovuje. Farba ako druhý, nezávislý signál zatiaľ použitá **nie je**.
- **Rozloženie jasu:** medián 176, 20. percentil 135, 10. percentil 107.
  Prah `dark = 125` z toho odkrojí ~16 % plochy a sedí na skalnatý terén.
- **Tmavé nie je plocha, ale sieť.** Tmavé miesta nie sú súvislé steny, ale
  hustá sieť žliabkov, ryhiek a mikrotieňov v rozčlenenom teréne. Táto jemná
  štruktúra je to, čo chceme – nie vyplnená klaksa. Kto chce súvislé plochy,
  zapne `options: fill=40` (spriemeruje tmavosť v okne 40 m); štandardne je
  to **vypnuté**.
- **Sieť je pospájaná**, takže počet útvarov neexploduje – 16 útvarov pokrylo
  15 % výrezu. Explodujú **body**: pri z18 to vyšlo na ~2 MB GeoPackage na km²
  skalnatého terénu. Toto číslo píše beh do súhrnu (`MB na km² skál`), lebo
  práve ono rozhoduje, či sa vrstva zmestí do rozpočtu mapy.
- **Odtiaľ sú predvolené filtre.** Merané na tom istom výreze:

  | nastavenie | plôch | dier | dáta |
  |---|--:|--:|--:|
  | `min_area 200`, `min_hole 50`, simplify ½ px, Chaikin 2× | 16 | 89 | 3,95 MB/km² |
  | **`min_area 50`, `min_hole 10`, simplify 1 px, Chaikin 1×** | **78** | **392** | **1,97 MB/km²** |

  Jemnejšie filtre a hrubšie zjednodušenie dali **súčasne viac štruktúry aj
  polovičné dáta**: pol pixela a druhý prechod Chaikinom leštili obrys, ktorý
  aj tak nikto nerozozná, zatiaľ čo `min_area 200` zmazal práve tie drobné
  útvary, o ktoré ide.

**Prvý beh je ladiaci.** Predvolené prahy sú kvalifikovaný odhad, nie
nameraná hodnota – tá dlaždicová vrstva sa nedá ochutnať dopredu. Beh preto
odloží artefakt `nahlad-…` s PNG **mozaika vedľa nájdených plôch** (vľavo
tieňovanie, vpravo to isté s červenou maskou) a histogramom odtieňov. Podľa
nich sa `dark` / `dark_always` / `rel` doladia za jeden pohľad.

**Každý request vyzerá ako iný prehliadač.** Hlavičky sa berú z deviatich
profilov skutočných prehliadačov (Chrome, Firefox, Safari, Edge; Windows,
macOS, Linux, iOS, Android) a vyberajú sa náhodne na každý request. Profil je
celý – `User-Agent`, `Sec-CH-UA`, platforma aj `Accept-Language` sedia
dokopy, lebo Chrome, ktorý o sebe v `Sec-CH-UA` tvrdí, že je Firefox, nie je
maskovanie, ale rozbitá hlavička.

> Stojí za to vedieť, čo to robí: berie to freemap.sk možnosť rozoznať dávku
> od človeka, a je to dobrovoľnícky server. Slušnosť preto musí zabezpečiť
> objem – `jobs` ostáva na 12 a dlaždice sa cachujú, takže druhý beh nestiahne
> ani jednu. `options: ua=project` vráti pôvodnú hlavičku, ktorá sa priznáva
> menom projektu; `ua=…` pošle čokoľvek vlastné.

**Efektivita.** Dlaždice sa sťahujú paralelne (`jobs`, default 12 – je to
dobrovoľnícka služba) s trvalým spojením, ukladajú sa do cache behu a pri
opakovanom ladení prahov sa už neťahajú. `zoom: auto` skúsi najvyšší zoom,
ktorý server dá a ktorý sa zmestí do stropu 60 000 dlaždíc. Vektorizuje sa
**naraz nad celou mozaikou** (po častiach len raster tmavosti) – z rovnakého
dôvodu ako pri skalách z DEM: diera prerezaná hranicou časti sa späť nezlepí.

Vysoké Tatry na z17 sú ~12 000 dlaždíc (~300 MB) a jednotky minút; z18 je
štvornásobok všetkého.

**Ako to dostať do mapy:** stačí **Build map** s `area: vysoke_tatry`
a `rock_source: tienovanie`. Nič sa dopredu púšťať nemusí – build si tú
pipeline zavolá sám, rovnako ako si sám dopĺňa chýbajúce výškové modely.
V behu z toho vypadnú dva artefakty: `dlazdice-tienovania-…` so stiahnutými
JPG dlaždicami (to sú tie obrázky, z ktorých sa skaly hľadali)
a `nahlad-…` s mozaikou, maskou a histogramom na doladenie prahov.

| chcem | ako |
|---|---|
| skaly z tieňovania, nech to trvá koľko chce | `rock_source: tienovanie` |
| iný zoom dlaždíc | `options: rock_img_zoom=18` |
| iné prahy / vyplnenie | `options: rock_img_options="fill=40 min_hole=5"` |
| presne ten asset, čo som si doladil ručne | `options: rock_img_asset=rockimg-…gpkg.zst` (vtedy sa nič nepočíta nanovo) |

Dlaždice majú vlastnú cache podľa výrezu a zoomu, takže druhý build z
dobrovoľníckeho servera freemap.sk neťahá nič. Tieňovanie na **celý región**
build odmietne hneď v príprave – dlaždice sú cudzie a na kraj by ich boli
státisíce.

Keby v release aj tak nič nebolo (napr. keď ten job spadol), build to povie
a **nespadne späť na skaly z DEM** – tichá zámena jedného zdroja za druhý by
bola horšia než zastavenie.

Vrstva je tá istá `rock` v tých istých dlaždiciach, s triedami `steep`
a `cliff`, takže štýl netreba meniť. Líši sa len atribút: skaly z DEM majú
`slope` (stupne sklonu), skaly z obrázka `dark` (o koľko stupňov šedej pod
referenciou). V manifeste je `rock_source`, takže je v mape vidieť, odkiaľ
tie plochy sú.

#### Zdroj výšok sa vyberá zvlášť pre každú vrstvu

Tri výbery vo formulári – `contour_source` (vrstevnice), `rock_source`
(skaly) a `shading_source` (tieňovanie a 3D terén). Ponúkajú tie isté modely:

| hodnota | model | mriežka | pokrytie | stav |
|---|---|--:|---|---|
| **`sonny`** (default) | Sonny's LiDAR DTM | 20 m | celý región | overené |
| **`dmr35`** | ÚGKK DMR 3.5 (otvorené dáta) | **10 m** | celý región | **overené** ✓ |
| **`dmr5`** | ÚGKK DMR 5.0 (LLS, otvorené dáta) | **5 m** | celý región | naplniť *Pripraviť DMR 5.0* |
| `ugkk` | ÚGKK DMR 5.0 (1 m LiDAR) | **1 m** | **len s výrezom** (`area`) | naplniť *Pripraviť DMR 5.0* |
| `ziadne` | – | – | – | vrstva sa negeneruje |

Navyše: `rock_source: tienovanie` neberie výšky vôbec – vezme hotové polygóny
z workflowu *Skaly z tieňovaných dlaždíc*. A `shading_source` nemá `ugkk`:
1 m LiDAR máme len na výrez, kým tieňovanie sa robí na celý región.

Vrstvy môžu mať **rôzny model naraz** – napríklad vrstevnice zo `sonny`
a skaly z `dmr5`. Build vtedy stiahne oba (každý do `dem/<zdroj>/`) a v mape
je pri každej vrstve atribúcia toho modelu, z ktorého naozaj je.

`dmr5` a `ugkk` sú **ten istý zdroj**, len inak nakrájaný: `dmr5` je celá
krajina na hrubšej mriežke v dlaždiciach `N49E019.tif`, `ugkk` je jedno
pohorie v plnom metrovom rozlíšení ako `ugkk-<vyrez>.tif`. Oba release plní
workflow [*Pripraviť DMR 5.0*](#pripraviť-dmr-50-198-gb-cez-http-range) a ani
jeden sa **nedopĺňa sám** – 198 GB archív nie je vedľajší účinok buildu mapy.

**`dmr35` funguje a je to najlepší model, ktorý vieme vziať priamo
v pipeline.** Overené behom
[31125042584](https://github.com/skifahrer/fricomaps/actions/runs/31125042584):
2319 MB ZIP z `opendata.skgeodesy.sk` stiahnutý, v archíve jeden raster
42 692×20 429, mriežka **presne 10,0×10,0 m**, CRS S-JTSK / Krovak East
North. Rozrezané na 15 dlaždíc (1315 MB) a nahraté do releasu `dem-dmr35`.

Ten hostiteľ je iný stroj než ten, na ktorom je DMR 5.0 — statické úložisko,
nie ArcGIS za mapovým klientom — a odpovedal na prvý pokus, kým `zbgis.` aj
`zbgisws.` timeoutujú aj pri 30 s.

Model je starší a redší než 1 m LiDAR, ale **dvakrát jemnejší než Sonny**, a
mriežka zdroja je jediné, čo stropuje skutočný detail skál. `rock_res: auto`
si to zoberie sám: dolný strop je desatina bunky DEM, takže z 2 m spadne na
1 m — v `rock-areas.py` netreba meniť nič.

Dlaždice majú tú istú pomenúvaciu schému ako Sonny (`N49E019.tif`), takže sa
sťahujú tou istou cestou — `sonny` a `dmr35` sa líšia len menom releasu
(`dem-sonny` vs. `dem-dmr35`).

Platí pre **vrstevnice, skaly aj tieňovanie** – všetko sa počíta z toho istého
modelu, nech obrys skaly, priebeh vrstevnice a tieň pod nimi sedia na tom istom
teréne. (Výnimka je `ugkk`: 1 m LiDAR máme len na výrez, ale tieňovanie
potrebuje celý región, takže tam ostáva Sonny.)

> **Dlaždice sú vo WGS84, nie v S-JTSK.** Zdrojový ZIP je v *S-JTSK / Krovak
> East North* — to hlási súhrn ako „CRS zdroja“ — ale `fetch-dem-open.py` ho
> pri krájaní prepočíta (`gdalwarp -t_srs EPSG:4326`). Overené na hotovej
> dlaždici z releasu: `N49E017.tif`, `GEOGCRS["WGS 84"]`, roh presne
> 17°E/50°N, 8826×8826 px, Float32, výšky 383–782 m.

### Pripraviť DMR 5.0: 198 GB cez HTTP Range

ÚGKK dal DMR 5.0 (1 m LiDAR) na to isté statické úložisko, z ktorého ide
DMR 3.5 — ako **jeden ZIP na celé Slovensko**:

```
https://opendata.skgeodesy.sk/static/LLS/DMR5/DMR5_0_sjtsk03_bpv.zip
197 707 257 567 B = 197,7 GB          (zmerané behom 31182614668)
```

Je to tá istá dátová sada, na ktorú sme sa mesiace márne pokúšali dostať cez
`zbgis.skgeodesy.sk` — len leží na stroji, ktorý odpovedá. Čo je v ňom,
prečítal beh [31184095104](https://github.com/skifahrer/fricomaps/actions/runs/31184095104)
(`mode: len plán`, nestiahol ani bajt dát):

```
dmr5_0/dmr5_jtsk03.tif        151,43 GB   celé Slovensko, 1 m, JEDEN raster
dmr5_0/dmr5_jtsk03.tif.ovr     46,28 GB   prehľadové úrovne (pyramídy)
dmr5_0/dmr5_jtsk03.tfw                    world file
dmr5_0/dmr5_jtsk03.tif.aux.xml, .xml      metadáta
INFO_slk.txt, INFO_eng.txt, 4× PDF        licencie a popis
prehlad_lokalit_lls_1_cyklus/*.shp …      prehľad lokalít zberu
```

**Nie sú to výškové body po blokoch — je to jeden súvislý GeoTIFF.** To má
jeden zásadný dôsledok: po položkách archívu sa deliť nedá. Zato sa dá čítať
priamo.

**Stiahnuť sa celý nedá a nie je to otázka trpezlivosti:**

| strop | koľko |
|---|--:|
| voľné miesto na runneri | ~60 GB (po vyčistení ~85 GB) |
| artefakt / asset releasu | **2 GB na súbor** |
| archív | **198 GB** |
| samotný raster v ňom | **151 GB** |

Klasické „stiahni a rozbaľ“ tu teda neexistuje. Ale nič sťahovať netreba:
**GDAL vie čítať GeoTIFF priamo zo vzdialeného ZIPu.**

```
/vsizip//vsicurl/https://opendata.skgeodesy.sk/.../DMR5_0_sjtsk03_bpv.zip/dmr5_0/dmr5_jtsk03.tif
```

ZIP sa číta cez HTTP Range, GeoTIFF je dlaždicovaný (512×512, DEFLATE), takže
si GDAL vypýta len tie dlaždice, ktoré potrebuje. Na disku nepristane nič.

To je workflow **Pripraviť DMR 5.0** ([`dmr5.yml`](.github/workflows/dmr5.yml)),
dva joby a **tri polia vo formulári**:

```
plan     prečíta centrálny adresár (ZIP64 – pri 198 GB sú offsety nad 4 GB)
         ─► inventár celého archívu do súhrnu behu
         ─► nájde v ňom hlavný raster

model    jeden job, jeden prechod, hotový model rovno do releasu
         ─► a do súhrnu napíše, s čím spustiť Build map
```

| input | čo to je |
|---|---|
| **`area`** | **dropdown**: `cele_slovensko` alebo pohorie z [`areas.json`](workers/areas.json) |
| `grid_m` | dropdown: `auto` / 1 / 2 / 5 / 10 / 20 m |
| `mode` | `model do releasu` / `len sonda` / `model + podrobný log` |

To je celý formulár. URL archívu je v `env` (mení sa raz za roky), release
aj to, ako sa výsledok volá v Build map, sa odvodia z územia — nemá zmysel,
aby si ich vypĺňal, keď z výberu pohoria jednoznačne vyplývajú.

#### Čo stojí čítanie

Položka v ZIPe je zabalená deflate-om a **v deflate prúde sa nedá skočiť
dopredu** — dá sa doň len rozbaliť od začiatku. Cena je preto úmerná tomu,
ako ďaleko v súbore dáta ležia. Zmerané na napodobenine (44 MB ZIP,
dlaždicovaný DEFLATE GeoTIFF, vlastný HTTP server s Range):

| čítanie | prenesené | čas |
|---|--:|--:|
| hlavička (`gdalinfo`) | 0,2 MB | 0,4 s |
| výrez na **začiatku** rastra | 0,5 MB (1 %) | 0,4 s |
| výrez na **konci** rastra | 44,1 MB (100 %) | 0,8 s |
| celý raster 1 m → 5 m, **s `.ovr`** | 37,8 MB | 1,1 s |
| to isté **bez `.ovr`** | 46,1 MB | 2,7 s |

Z toho plynú pravidlá, podľa ktorých je pipeline napísaná:

1. **Jeden prechod, nie viac.** Celá krajina sa prevzorkúva jedným
   `gdal_translate -tr`, a na 1° dlaždice sa krája až hotový malý raster.
   Krájať dlaždice priamo zo zdroja by stálo N× cestu od začiatku súboru.
2. **Čítať sa musí dopredu.** Výrez ide v dvoch krokoch: najprv
   `gdal_translate -projwin` sekvenčne na disk, až potom `gdalwarp` z disku
   do WGS84. Warp priamo nad vzdialeným zdrojom si dlaždice pýta v poradí
   *cieľovej* mriežky — a každý skok späť v deflate prúde znamená
   rozbaľovanie od začiatku, čiže môže stáť desiatky GB.
3. **Pyramídy miesto rastra, keď to ide.** Pri cieli aspoň 2× hrubšom než
   zdroj sa číta z `.ovr` (46 GB) a nie z hlavného rastra (151 GB) — a
   vyberáme ho výslovne, nie s dôverou, že si ho GDAL nájde sám.
4. **Sidecary sa nesmú schovať.** `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`
   síce šetrí požiadavky, ale skryje `.ovr` aj `.tfw`.

**Pri výreze v plnom rozlíšení je sever lacný a juh drahý** — raster sa číta
po riadkoch od severu, takže Vysoké Tatry stoja ~30 % archívu a Slovenský
kras skoro celý. Pyramídy tu nepomôžu: pri 1 m chceš práve tie plné dáta.
S tým sa nedá nič robiť, deflate sa preskakovať nedá.

Preto beh vypisuje **každých 30 sekúnd**, koľko už stiahol, akou rýchlosťou
a koľko odhadom ostáva — hodinový prechod cez 151 GB je inak v logu úplne
ticho a nedá sa odlíšiť od zaseknutého behu.

#### Najprv 16 bajtov, potom GDAL

Je jedna vec, ktorá rozhodne o všetkom ešte pred prvým pixelom: **kde v TIFFe
leží adresár dlaždíc (IFD)**. Hlavička TIFFu nesie jeho offset.

| IFD | otvorenie súboru |
|---|---|
| na začiatku | prečíta pár stoviek kB, hotovo za sekundu |
| na konci | GDAL sa k nemu dostane **len rozbalením celého člena** |

Nad obyčajným súborom je to jedno — skok na koniec je zadarmo. Ale člen ZIPu
zabalený deflate-om sa preskakovať nedá, dá sa doň len rozbaliť od začiatku.
IFD na konci 151 GB člena teda znamená, že **samotné otvorenie prečíta
151 GB**. A zapisovatelia ho tam bežne dávajú — počas zápisu ešte nevedia,
kde dlaždice skončia.

Behy [31191478190](https://github.com/skifahrer/fricomaps/actions/runs/31191478190)
a [31197330753](https://github.com/skifahrer/fricomaps/actions/runs/31197330753)
sa zasekli presne tu. Ten druhý to dokázal čierne na bielom — po 87 minútach
ticha ho zrušil až používateľ a v logu ostalo:

```
16:25:13  Cesta pre GDAL: /vsizip//vsicurl/…/dmr5_jtsk03.tif
          (87 minút ticha)
17:52:25  Terminate orphan process: pid (2977) (gdalinfo)   ← stále bežal
```

**Nezaseklo sa to na sťahovaní dát — nedostalo sa ani k prvému pixelu.**
Preto sa teraz **najprv prečíta 16 bajtov** z hlavného rastra aj z `.ovr`,
offset IFD sa vypíše, `gdalinfo` má strop (`--probe-timeout`, predvolene
15 min) a beží pod ním heartbeat.

#### Keď sa hlavný raster neotvorí, ide sa cez pyramídy

Model s hrubšou mriežkou je viac než dokonalý model, ktorý sa nikdy
nedopočíta. Keď `gdalinfo` nad hlavným rastrom neprejde, pipeline skúsi
**`.ovr` samotné** — má 46 GB namiesto 151 GB, teda trikrát väčšiu šancu.

Georeferencia nemôže prísť z rodiča (ten sa neotvára), tak sa poskladá
z **`.tfw`**: veľkosť pixela × pomer zmenšenia a ten istý ľavý horný roh.
Overené, že to dá presne tú istú mriežku ako cesta cez rodiča — rovnaký
`geoTransform` aj rovnaké výšky.

Cena je rozlíšenie: najjemnejšie, čo z pyramíd vypadne, sú **2 m**. Na
`dem-dmr5` (5 m na celé Slovensko) to nevadí vôbec; na `ugkk-<vyrez>.tif`
v plnom metrovom rozlíšení áno, a workflow to napíše ako varovanie.

**Rozlíšenie vs. územie.** Pri 1 m má jedna 1°×1° dlaždica ~48 GB, takže:

| `area` | mriežka | výsledok | release | zdroj v Build map |
|---|--:|---|---|---|
| `cele` | 5 m | dlaždice `N49E019.tif` | `dem-dmr5` | `dmr5` |
| `vysoke_tatry`, … | **1 m** | jeden COG `ugkk-<vyrez>.tif` | `dem-ugkk` | `ugkk` |

Workflow to ustráži sám: celé Slovensko pod 3 m odmietne **v prvej minúte**,
v nastaveniach, nie po ôsmich hodinách sťahovania. Aj tak je 5 m štyrikrát
jemnejšie než Sonny (20 m) a dvakrát než DMR 3.5 (10 m) — a mriežka zdroja je
jediné, čo stropuje skutočný detail skál.

#### Výstup je vstup pre Build map

Toto je celý zmysel workflowu — čo z neho vypadne, z toho vie `Build map`
počítať **vrstevnice, skaly aj tieňovanie**:

| `area` | mriežka | výsledok | release | v Build map |
|---|--:|---|---|---|
| `cele_slovensko` | 5 m | dlaždice `N49E019.tif` | `dem-dmr5` | `dmr5` vo výbere vrstevníc/skál/tieňovania |
| pohorie | **1 m** | `ugkk-<pohorie>.tif` | `dem-ugkk` | `ugkk` vo výbere vrstevníc/skál + rovnaké `area` |

Pomenúvacia schéma aj formát sú tie isté ako u Sonnyho a DMR 3.5, takže
[`workers/fetch-dem.sh`](workers/fetch-dem.sh) sa pri čítaní vôbec nevetví —
rozhoduje len meno releasu. Build mapy sa nemusí učiť nič nové. Presné
nastavenia vypíše workflow do súhrnu behu, aby sa nemuseli hádať.

**Prvý beh na neznámom archíve nech je `mode: len sonda`.** Prečíta centrálny
adresár, vypíše do súhrnu **všetky mená súborov, veľkosti a či sú uložené
alebo deflate** — a nestiahne pritom nič. Presne takto sme zistili, že DMR 5.0
je jeden GeoTIFF a nie body.

Kód, každý sa dá spustiť aj samostatne:

| súbor | čo robí |
|---|---|
| [`workers/zip-remote.py`](workers/zip-remote.py) | čítanie vzdialeného ZIP64 cez Range: centrálny adresár, streamované rozbaľovanie, obnova pretrhnutého prenosu |
| [`workers/dmr5-plan.py`](workers/dmr5-plan.py) | inventár archívu z jeho centrálneho adresára a nájdenie hlavného rastra |
| **[`workers/dmr5-raster.py`](workers/dmr5-raster.py)** | **raster cez `/vsizip//vsicurl/` → dlaždice alebo COG, aj so záchranou cez pyramídy** |

```bash
URL=https://opendata.skgeodesy.sk/static/LLS/DMR5/DMR5_0_sjtsk03_bpv.zip

# čo je v archíve, bez sťahovania obsahu
python3 workers/zip-remote.py list "$URL" --limit=50

# hlavička rastra (rozmery, mriežka, CRS, pyramídy) – pár stoviek kB
gdalinfo "/vsizip//vsicurl/$URL/dmr5_0/dmr5_jtsk03.tif"
```

**Licencia ÚGKK:** voľné použitie vrátane komerčného pri uvedení zdroja
(ÚGKK SR) — atribúcia je v [`poc/web/themes.js`](poc/web/themes.js).

**Spúšťaš len jednu pipeline.** `Build map` sa sám pozrie, či je výrez v
release `dem-ugkk`, a keď nie je, spustí si zrkadlo ako svoju úlohu – to isté,
čo už robí `mirror-dem` pre Sonnyho. Ručne netreba spúšťať nič. **Výnimka je
`dmr5`:** 198 GB archív sa vedome nespúšťa ako vedľajší účinok buildu mapy,
takže `Pripraviť DMR 5.0` treba pustiť raz ručne. Build to povie – aj s tým,
čo presne spustiť.

```
Build map
  └─ check-dem        je výrez v release dem-ugkk?
       └─ (nie) → Doplniť ÚGKK 1 m LiDAR      ← spustí sa sám
                    1. priame URL (ak si ich dal)
                    2. ArcGIS ImageServer  (+ objaví služby v ich adresári)
                    3. WCS GetCoverage
                    → jeden COG do releasu dem-ugkk
       └─ contours    stiahne COG z releasu a počíta
```

> **Zrkadlo skúša štyri cesty a v každej sa tvári ako prehliadač.**
> Geoportály za WAF-om bežne zahadzujú požiadavky, ktoré nevyzerajú ako
> prehliadač – a nezahadzujú ich chybou, ale **tichom**, čo v logu vyzerá
> presne ako výpadok siete. V behu
> [30997189220](https://github.com/skifahrer/fricomaps/actions/runs/30997189220)
> to bol práve timeout, takže to stálo za skúšku.
>
> | # | cesta | poznámka |
> |--:|---|---|
> | 1 | priame URL (`ugkk_urls`) | čo si zadal ručne |
> | 2 | **metadátový katalóg RPI** | dá *skutočné* URL služieb namiesto uhádnutých názvov – a je to iný hostiteľ |
> | 3 | ArcGIS `exportImage` | kandidáti + čo sa nájde v adresári služieb |
> | 4 | WCS `GetCoverage` | |
>
> Každá požiadavka ide postupne ako **Safari 17 → Chrome 124 → ArcGIS Pro →
> fricomaps**, a keď neprejde ani jeden, ešte raz cez **`curl` s HTTP/2** –
> lebo časť WAF-ov blokuje podľa TLS odtlačku spojenia, nie podľa hlavičiek,
> a curl má iný TLS stack než Python.
>
> **Prvý krok zrkadla je diagnostika**, ktorá to celé zmeria a napíše do
> Summary maticu hostiteľ × profil:
>
> ```
>    zbgis.skgeodesy.sk                     URL!      URL!      URL!      URL!       000
>    rpi.gov.sk                             URL!      URL!      URL!      URL!       000
>    pypi.org                                200       200       200       200       200
>                                         Safari    Chrome    ArcGIS  fricomaps      curl
> ```
>
> Riadok `pypi.org` je kontrolný: keď je 200 a ÚGKK riadky nie, problém je na
> ich strane. Keď nie je 200 ani pypi, je rozbitá sieť runnera. Bez tohto sa
> „nefunguje to" nedá odlíšiť od „nefunguje to takto".

#### Ako to dopadlo: z GitHub runnera sa k ÚGKK dostať nedá

Zmerané, nie odhadnuté. Diagnostický workflow prehľadal širokú sadu vstupných
bodov, čo našiel to stiahol a všetko vyhodil ako artefakt. Tri behy
([31072215798](https://github.com/skifahrer/fricomaps/actions/runs/31072215798),
[31075806874](https://github.com/skifahrer/fricomaps/actions/runs/31075806874),
[31096745697](https://github.com/skifahrer/fricomaps/actions/runs/31096745697))
dali zakaždým to isté:

| hostiteľ | výsledok |
|---|---|
| `zbgis.skgeodesy.sk` | `Connection timed out` — aj pri 30 s |
| `zbgisws.skgeodesy.sk` | `Connection timed out` — aj pri 30 s |
| `geoportal.sk` aj `www.geoportal.sk` | chyba certifikátu, ich cert nesedí ani na jedno meno |
| `mapy.geoportal.sk` | neexistuje, DNS ho nepozná (bol to náš tip) |
| `data.slovensko.sk`, `data.gov.sk`, `rpi.gov.sk`, `inspire.gov.sk`, `www.skgeodesy.sk` | **HTTP 200** |

Posledný riadok je dôležitý: **nie je to geoblok na Slovensko.** Mŕtve sú
presne tie dva stroje, na ktorých sú dáta.

Vyčerpali sme adresár služieb, správne meno služby, dvoch rôznych hostiteľov,
WMS, WCS, národný katalóg otvorených dát aj INSPIRE. Že mechanika je v poriadku,
dokázalo české ČÚZK — tá istá cesta (`exportImage`) odtiaľ vrátila skutočné
GeoTIFFy.

Vedľajší nález: **správne meno služby je `LLS_DMR5`**, nie žiadne zo šiestich,
ktoré sme hádali. Vrátilo ho vyhľadávanie ArcGIS Online ako „DMR 5.0 (Web
Mercator)“, vlastník `UGKK_SR`. Nepomohlo to — na mŕtvy hostiteľ sa nedostaneš
ani so správnym menom — ale keby sa cesta niekedy otvorila, toto je meno,
ktorým začať.

**Prakticky:** `ugkk` (vo výbere vrstevníc aj skál) funguje len vtedy, keď
je výrez už v releasi `dem-ugkk`. Dostane sa tam **workflowom [*Pripraviť DMR
5.0*](#pripraviť-dmr-50-198-gb-cez-http-range)** (`area: <pohorie>`), alebo
jednorazovým exportom zo ZBGIS Mapového klienta (Terén → Export údajov →
DMR 5.0, do 400 km²) a nahratím ako `ugkk-<vyrez>.tif`. Inak build spadne späť
na Sonnyho a napíše to.

> **Dodatok (august 2026): tá cesta sa našla, len vedie inde.** Všetko nižšie
> o mŕtvom `zbgis.skgeodesy.sk` platí – ale to isté DMR 5.0 leží aj na
> `opendata.skgeodesy.sk` ako jeden 198 GB ZIP a odtiaľ sa vziať dá. Viď
> [Pripraviť DMR 5.0](#pripraviť-dmr-50-198-gb-cez-http-range).

Build to preto **neskúša naslepo**: `fetch-dem-ugkk.py` sa najprv spýta na
dostupnosť hostiteľa a keď neodpovedá, ImageServer ani WCS už nerozbieha —
všetky sú na tej istej doméne a každý z nich stojí štyri profily prehliadača
plus curl.

**1 m sa dá len na výrez.** Celý kraj má pri 1 m 16 miliárd buniek, čo je 64 GB
vo Float32 – to sa nezmestí ani do release assetu (strop 2 GB), ani do runnera.
Build to preto odmietne **v prvej minúte**, v prípravnom jobe, nie po hodine
sťahovania. Preto ide `ugkk` ruka v ruke s inputom `area`:

| výrez | plocha | 1 m raster (Float32) |
|---|--:|--:|
| Belianske Tatry | 177 km² | ~0,7 GB |
| Vysoké Tatry | 541 km² | ~2,2 GB (COG ~0,6 GB) |
| celý kraj | 16 103 km² | ~64 GB → **odmietne** |

#### Testovací výrez – vrstevnice aj skaly len na pohorí

Terén je najdrahšia časť buildu. Pri ladení prahu, mriežky, zdroja alebo
farieb nemá zmysel čakať polhodinu na celý kraj, keď ťa zaujíma jedno pohorie
– na to je input **`area`**. Platí na **vrstevnice aj skaly**:

| `area` | územie | plocha | terén trvá |
|---|---|--:|--:|
| *(prázdne)* | celý región | 16 103 km² | ~30 min |
| `tatry` | Západné + Vysoké + Belianske | 1 032 km² | ~2 min |
| `vysoke_tatry` | Vysoké Tatry | 541 km² | ~1 min |
| `belianske_tatry` | Belianske Tatry | 177 km² | <1 min |
| `slovensky_raj` | Slovenský raj | 424 km² | ~1 min |
| `20.0,49.1,20.2,49.2` | vlastný bbox | 161 km² | <1 min |

*(plochy sú po orezaní na Prešovský kraj)*

Pomenované výrezy sú vo [`workers/areas.json`](workers/areas.json) – zatiaľ
Tatry (celé aj po častiach), Nízke Tatry, Slovenský raj, Pieniny, Malá aj
Veľká Fatra, Súľovské skaly, Slovenský kras, Muránska planina, Vihorlat,
Strážovské vrchy a Malé Karpaty. Namiesto názvu sa dá zadať aj bbox
`west,south,east,north`.

Výrez sa vždy **pretne s bboxom regiónu** – čo je mimo, sa nepočíta (nie je
tam ani DEM, ani mapa). Keď sa neprekrývajú vôbec (napr. `mala_fatra`
s Prešovským krajom), build to povie rovno a zastaví sa, namiesto aby
polhodinu počítal prázdno.

> **Vo zvyšku regiónu potom nie sú ani vrstevnice, ani skaly.** Mapa a
> tieňovanie sú za celý región – toto je beh na testovanie, nie na nasadenie. Build to hlási ako
> `::warning::` aj v súhrne, aby sa taký beh omylom nenasadil ako finálny.
> Výrez je aj v mene uloženého assetu (`rock-{región}-{výrez}-…`) a v kľúči
> cache, takže sa skaly z Tatier nikdy nevydávajú za skaly celého kraja.

#### Koľko to bude trvať sa povie dopredu

Skaly sa **nezačnú počítať**, kým sa nevypíše plán a neoverí, že sa zmestí do
rozpočtu (`ROCK_BUDGET_MIN`, default 100 min):

```
── Plán výpočtu skál ────────────────────────────────
  územie          208×111 km (obdĺžnik v EPSG:3035)
  mriežka         1 m
  buniek          19.60 mld.
  častí           144 z 170 (26 mimo územia sa preskočí), po 12.2×11.1 km
  odhad sklon     1:04:02
  odhad obrysy    1:33:19
  odhad SPOLU     2:37:21  (rozpočet 1:40:00)
  mozaika na disk ~1.0 GB
  špička pamäte   ~13.4 GB
─────────────────────────────────────────────────────
::error::Skaly by trvali 2:37:21 …
::error::Zmestí sa: (1) rock_res aspoň 1.3 m na tomto území, alebo
(2) rock_area na výrez s ~64 % plochy – napr. vysoke_tatry, tatry, …
```

Presne z tohto odhadu vyberá aj `rock_res: auto` – len ho použije **dopredu**
a zoberie najjemnejšiu mriežku so ✓ (zdola stropenú desatinou bunky DEM),
namiesto toho, aby beh po hodine odmietol.

| územie | `rock_res` | buniek | odhad | |
|---|--:|--:|--:|---|
| Prešovský kraj | 1 m | 19,60 mld. | 2:37:21 | ✗ odmietne |
| Prešovský kraj | **2 m (auto pri Sonnym)** | 5,27 mld. | 0:42:18 | ✓ |
| Prešovský kraj | 3 m | 2,57 mld. | 0:20:38 | ✓ |
| Tatry | 1 m | 1,34 mld. | 0:10:46 | ✓ |
| Vysoké Tatry | 1 m | 0,71 mld. | 0:05:44 | ✓ |
| Belianske Tatry | 1 m | 0,23 mld. | 0:01:49 | ✓ |

Konštanty odhadu sú **namerané na runneri**, nie odhadnuté: sklon
5,1 mil. buniek/s, obrysy 3,5 mil./s.

Trojhodinový beh, ktorý spadne na timeout jobu, minie celý rozpočet
a nevyrobí nič. Toto to zastaví za pár sekúnd a povie, čo zmenšiť.

#### Počas výpočtu je vidieť, čo sa deje

```
  [12/144] sklon – 0:07:41 za sebou, zostáva ~0:84:26, mozaika 96 MB
  … sklon: beží 0:07:52, na disku 0.1 GB
Vektorizujem sklon jedným priechodom nad celým územím (5.27 mld. buniek, odhad 0:25:05)…
  … gdal_contour: 30 % (beží 0:07:14)
  … gdal_contour: beží 0:07:30, pamäť 2.4 GB, na disku 1.1 GB
```

Pri sklone ide riadok po každej časti s odpracovaným časom a odhadom zvyšku,
`gdal_contour` hlási percentá a nezávisle od oboch beží **tep** každých 30 s
(`ROCK_HEARTBEAT_S`) s časom, pamäťou procesu a miestom na disku. Keď pamäť
prekročí `ROCK_MAX_RSS_GB` (12 GB), tep výpočet zastaví s hláškou – to je
lepšie než tiché zabitie runnera na OOM, po ktorom v logu nie je nič.

**Časti mimo územia sa preskočia.** EPSG:3035 je pootočená voči poludníkom,
takže obdĺžnik opísaný bboxu je v metroch väčší než región – pri Prešovskom
kraji 208×111 km namiesto 200×82 km. Čo do bboxu nezasahuje, sa nepočíta
(26 zo 170 častí pri 1 m).

#### Veľkosť plôch určuje prah sklonu, nie mriežka

Súvislá stena nad prahom je jedna plocha, nech ju počítaš na akejkoľvek
mriežke – jemnejšia mriežka dá presnejší *obrys*, nie menšie plochy. Namerané
na výreze Vysokých Tatier (mriežka 2 m):

| prah | plôch | plocha spolu | priemerná | najväčšia |
|---|---|---|---|---|
| 40° | 1 299 | 2 931 ha | 22 567 m² | **428 ha** |
| 45° | 1 019 | 1 710 ha | 16 788 m² | 82 ha |
| **50° (default)** | **719** | **884 ha** | **12 295 m²** | **38 ha** |
| 55° | 402 | 389 ha | 9 698 m² | 30 ha |
| 60° | 208 | 131 ha | 6 301 m² | 18 ha |

Pri 40° je najväčšia súvislá plocha 428 ha – to už nie je skala, ale celý
strmý svah. Preto je predvolený prah **50°**; kto chce drobnejšie a ostrejšie
vymedzené skaly, dá 55° alebo 60°.

**Počíta sa po častiach.** Bbox kraja má pri 2 m vyše 3 miliardy buniek, čo je
~13 GB na jeden raster – viac, než má runner miesta aj pamäte. Územie sa preto
krája (`ROCK_CHUNK_CELLS`, default 150 mil. buniek na kus), každá časť sa
spracuje a hneď upratá; sklon sa počíta s presahom a plochy sa orežú presne na
hranicu časti, takže susedné kusy na seba nadväzujú bez medzery ani prekryvu.
Čas rastie lineárne – merané ~2,5 mil. buniek/s, teda kraj pri 2 m okolo
30 minút. **Mriežka 1 m sa oplatí len na `crop_bbox`; pre kraj by to boli
~2 hodiny.**

Ovládanie vo workflowe: `rock_source` (z ktorého modelu – alebo `ziadne`,
čím sa skaly vypnú), `rock_slope` (od akého sklonu je terén skala, default
50°) a `rock_res` (mriežka obrysu v metroch alebo `auto`, default `auto`).
Ostatné ladenie je v `env:` na začiatku
[build-map.yml](.github/workflows/build-map.yml): `ROCK_SIMPLIFY` (0 = presný
obrys), `ROCK_SMOOTH` (koľkokrát zaobliť rohy, 0 = vypnúť),
`ROCK_CLIFF_PLUS` (o koľko ° nad prahom začína trieda `cliff`),
`ROCK_CHUNK_CELLS` (koľko buniek naraz pri počítaní sklonu), `ROCK_ALGO`
(verzia algoritmu v mene uloženého assetu).

V mape z toho sú **tmavosivé plochy** s tenkým obrysom, kreslené *pod*
vrstevnicami, takže ohraničujú strmé úseky a vrstevnice nad nimi zostávajú
čitateľné. Strmý svah je svetlejší, výrazná stena tmavšia – farby `Skalná
plocha` a `Skalná stena` sú v palete v skupine **Vrstevnice a skaly**, takže
sa dajú v developer móde doladiť ako čokoľvek iné.

**Hotové skaly sa neprepočítavajú.** Uložia sa do releasu `dem-rocks` pod
menom, ktoré nesie región aj nastavenia
(`rock-{región}-s{prah}-g{mriežka}-{algo}.gpkg.zst`), takže ďalší build s tými istými
nastaveniami ich len stiahne – sekundy namiesto desiatok minút. Iné nastavenia
dajú iné meno assetu, takže sa nikdy nepomiešajú. Ako to prepočítať nanovo,
hovorí [Pregenerovanie](#pregenerovanie).

Hotové skaly a vrstevnice si každý build odloží aj ako **artefakt behu**
(`teren-{región}-s{prah}-g{mriežka}`) s 90-dňovou lehotou – to je maximum,
ktoré GitHub dovolí. Dajú sa teda stiahnuť a pozrieť v QGISe bez ďalšieho
buildu.

Podiel plochy nad prahom (merané pri 40°, teda hornom odhade):

| územie | podiel plochy nad 40° |
|---|---|
| Vysoké Tatry (hrebeň, doliny) | 8,0 % |
| Malá Fatra (lesnaté hory) | 0,7 % |
| Považie pri Trenčíne (kopce) | 0,7 % |

### Pregenerovanie

Nič sa nepočíta dvakrát: vrstevnice, skaly aj tieňovanie sa berú z cache
(a skaly navyše z releasu `dem-rocks`, tieňovanie z `dem-terrain`). Keď sa
zmenia nastavenia, zmení sa aj kľúč a prepočíta sa to samo. Keď chceš to isté
prepočítať **nanovo aj pri rovnakých nastaveniach**, spusť *Build map*
so zaškrtnutým inputom:

| input | čo pregeneruje |
|---|---|
| `contours_rebuild` | vrstevnice **aj skaly** – zmaže cache `contours-…` a trasuje z DEM odznova |
| `rocks_rebuild` | skaly – zmaže cache aj asset v release `dem-rocks` (vrstevnice sa prepočítajú s nimi, sú lacné) |
| `terrain_rebuild` | tieňovanie a 3D terén – zmaže cache aj asset v release `dem-terrain` |

Prečo to musí najprv mazať: **cache sa v GitHube nedá prepísať.** Kľúč, ktorý
raz existuje, si drží starý obsah, takže bez zmazania by sa prepočítaná verzia
zahodila a ďalší build by dostal späť tú starú. Preto má build právo
`actions: write` a každý `*_rebuild` začne tým, že príslušný záznam zmaže.

Ostatné cache (PBF, Planetiler, DEM dlaždice, glyfy a sprity) sa
nepregenerúvajú vôbec – sú to stiahnuté dáta, nie výpočet, a majú v kľúči buď
dátum, alebo otlačok zdroja.

### Súhrn buildu

Každý beh napíše do záložky **Summary** prehľad: čo sa robilo, ako dlho to
trvalo a s akým výsledkom.

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

*(Ukážka – čísla sa líšia podľa regiónu a nastavení.)*

Pod tabuľkou je **detail skál** za tento beh (počet plôch, mriežka, bunka DEM,
najmenšia/priemerná/najväčšia plocha, koľko km² skalného terénu spolu) a
prehľad, **čo prišlo z cache a čo sa naozaj počítalo** – takže sa hneď vidí,
či mal beh trvať hodinu, alebo minútu.


## Značené trasy (turistika, cyklo, bežky)

**Trasa nie je cesta.** V OpenStreetMape je značená trasa `type=route`
**relácia**: zoznam cudzích ciest plus samotné značenie – farba pásika
([`osmc:symbol`](https://wiki.openstreetmap.org/wiki/Key:osmc:symbol),
`colour`), sieť (`network`), názov, `ref`, dĺžka. Schéma OpenMapTiles relácie
trás **nepozná**: v dlaždiciach ostane len cesta (`class=path`) a z nej sa
nedá zistiť, či po nej vedie červená turistická, dve cyklotrasy, alebo nič.

Preto majú trasy vlastný krok pipeline a vlastný `.pmtiles`:

```
data/region.osm.pbf
  → osmium tags-filter r/route=hiking,foot,…   len relácie trás a ich členovia
  → workers/trail-routes.py (pyosmium)         relácie → línie s pruhmi
  → data/trails.geojson
  → planetiler generate-custom --schema=workers/trails.yml
  → {región}-trails.pmtiles
```

### Pásiky vedľa cesty, nie namiesto nej

Trasa sa kreslí ako farebný pásik **vedľa** cesty (`line-offset`), takže pod
ním zostane vidieť, aká je to vlastne cesta – chodník, lesná cesta, asfaltka:

```
── cesta ────────────────    zostane vidieť, aká to je cesta
━━ červená (off 0,5) ━━━━
━━ modrá   (off 1,5) ━━━━    druhá trasa po tej istej ceste
━╍ cyklotrasa (off 2,5) ╍    a tretia, prerušovane
```

Po jednej ceste vedie bežne viac trás naraz, takže sa každá zapíše do dlaždíc
zvlášť a dostane vlastný **pruh**. Detaily, ktoré na tom závisia:

| vec | ako to je | prečo |
|---|---|---|
| číslovanie pruhov | od cesty von: 0,5 · 1,5 · 2,5 … | keby boli vycentrované, koniec jednej trasy by posunul všetky ostatné |
| poradie | sieť → druh → farba → id relácie | závisí len od trasy, takže si dve trasy na susedných úsekoch pruhy neprehodia; dôležitejšia je bližšie k ceste |
| smer čiary | vždy od západnejšieho konca | `line-offset` posúva podľa smeru geometrie – inak by pásik preskakoval z jednej strany cesty na druhú podľa toho, ako kto cestu nakreslil |
| duplikáty | nadradená trasa a jej časť sa zlúčia | superroute a jej člen sú dve relácie na tých istých cestách; dva rovnaké pásiky vedľa seba nie sú informácia, ale chyba |
| krok pruhu | 1,6 px (z9) až 20 px (z20) | musí byť aspoň polovica šírky cesty pod ním, a tá s približovaním rastie |

### Farba ide z OSM, odtieň z palety

Farba sa berie z `osmc:symbol` (prvé pole je farba pásika na strome), inak
z `colour`/`color`:

| v OSM | v dlaždiciach | v mape |
|---|---|---|
| `osmc:symbol=red:white:red_bar` | `colour=red` | farba `Značka červená` z palety |
| `colour=blue` | `colour=blue` | farba `Značka modrá` z palety |
| `colour=#0000ee` | `colour=blue` | zaokrúhlené na modrú (je dosť blízko) |
| `colour=#ff69b4` | `hex=#ff69b4` | presne tento hex – žiadnej značke sa nepodobá |
| *(nič)* | – | farba podľa druhu trasy |

**Prečo cez paletu a nie priamo hex z OSM.** „Červená" značka má v každej téme
vyzerať ako červená značka, nie ako presne to `#ff0000`, ktoré do OSM napísal
ten, kto trasu zadával. V tmavej téme je navyše čierna značka svetlosivá –
inak by na tmavom podklade zmizla. Všetkých desať farieb značiek je v palete
v skupine **Značené trasy**, takže sa dajú v developer móde doladiť ako
čokoľvek iné.

### Druhy trás

| druh | `route` v OSM | predvolená ikona | čiara |
|---|---|---|---|
| turistická | `hiking`, `foot`, `walking` | vrch | plná |
| cyklotrasa | `bicycle` | bicykel | čiarkovaná |
| horská cyklotrasa | `mtb` | bicykel | krátke čiarky |
| lyžiarska / bežkárska | `ski`, `nordic`, `skitour` | lyžiar | dlhé čiarky |
| jazdecká | `horse` | koliesko | bodkovaná |

Každý druh má vlastnú vrstvu pre čiaru, ikonu aj názov – v developer móde sa
im dá zvlášť meniť farba, ikona, hrúbka, prerušovanie aj rozsah zoomu.

### Názov pozdĺž trasy

Trasy s názvom alebo `ref` majú od z12 popisok **pozdĺž čiary a vo farbe
trasy** (`0801 Chodník hrdinov SNP`). Aby sa názov nekreslil po 200-metrových
kúskoch, Planetiler v dlaždici **zlepí úseky s rovnakými atribútmi** –
teda tej istej trasy v tom istom pruhu (`merge_line_strings`).

Klik na pásik ukáže popup s názvom, druhom, farbou značky, sieťou a odkazom
na reláciu v OSM.

### Od akého zoomu je čo vidieť

Riadi to `network` (`iwn`/`nwn`/`rwn`/`lwn` a cyklo obdoby), lebo diaľkovú
trasu má zmysel vidieť aj z prehľadu, kým miestny okruh až vtedy, keď je
vidieť aj cesta pod ním:

| sieť | v dlaždiciach od | typicky |
|---|--:|---|
| medzinárodná (`iwn`, `icn`) | z8 | E-cesty, Eurovelo |
| národná (`nwn`, `ncn`) | z8 | magistrály |
| regionálna (`rwn`, `rcn`) | z10 | väčšina našich značených trás |
| miestna (`lwn`, `lcn`) | z12 | okruhy, náučné chodníky |

Keď trasa sieť nemá, rozhodne `distance` (nad 150 km = národná, nad 50 km =
regionálna, inak miestna).

### Ovládanie

Vo workflowe: `trails` (zap/vyp) a `trails_maxzoom` (default 14). V mape sa
trasy vypínajú prepínačom **Značené trasy** v paneli ⚙. Job sa **necachuje** –
celé sú to pár minút a závisí to od PBF, ktoré sa mení denne.

Súhrn buildu píše, koľko trás sa v území našlo, koľko z nich má názov, po
koľkých cestách vedú a koľko z tých ciest nesie viac trás naraz.

## Developer mode – ladenie mapy v prehliadači

Mapa sa dá doladiť priamo vo viewri, bez čakania na pipeline. Zapína sa
prepínačom **🛠 Developer mode** v paneli ⚙ (alebo cez `?dev=1` v URL).

| záložka | čo sa v nej dá |
|---|---|
| **Vrstvy** | všetkých ~135 vrstiev po skupinách, s druhom (plocha / línia / bod / popisok / 3D / reliéf). Filtre podľa druhu a hľadanie, zapnutie a vypnutie vrstvy aj celej skupiny, rozsah zoomu (`od z` / `do z`), farby všetkých `*-color` vlastností, **ikona** pri symbolových vrstvách, **vzor**, **okraj** a prerušovanie čiary. Riadok sa rozklikne kliknutím na názov |
| **Prvky** | inšpektor: klik do mapy vypíše **všetko, čo je pod kurzorom** – naraz zo všetkých vrstiev, s celým obsahom dlaždice. Viď nižšie |
| **Paleta** | ~85 farieb aktuálnej témy po skupinách. Zmena farby prefarbí naraz všetky vrstvy, ktoré ju používajú |
| **Ikony** | sada ikoniek pre POI, vrcholy a letiská – s náhľadom, počtom obrázkov a licenciou |
| **POI** | ktoré triedy bodov sa zobrazujú (zoznam sa načíta z dlaždíc v aktuálnom výreze) |
| **Súbor** | stiahnutie, nahratie a vymazanie úprav |

**Prehliadanie po zoomoch.** Nad zoznamom je posuvník zoomu: nastavíš zoom
(mapa tam skočí) a zoznam ukáže, čo je na ňom naozaj povolené – hlavička
skupiny má počítadlo `aktívne/všetky`, každý riadok svoj rozsah (`z13–16`,
`z9+`, `vždy`) a vrstvy, ktoré sa na danom zoome neorežú, ostávajú výrazné.
Prepínač **len aktívne** schová zvyšok. Posuvník sleduje aj bežné zoomovanie
myšou, takže sa dá ísť zoom po zoome a hneď vidieť, čo pribudlo.

**Inšpektor prvkov (záložka Prvky).** Mapa je poskladaná z desiatok vrstiev
nad sebou: na jednom mieste býva plocha, cesta, jej obrys, vrstevnica, pásik
trasy aj popisok. Klik do mapy preto nevyberie „ten jeden prvok", ale vypíše
**všetko, čo je pod kurzorom** – pri každom prvku vrstvu, z ktorej pochádza,
zdrojovú vrstvu dlaždice a po rozkliknutí **všetky jeho atribúty** tak, ako sú
v dlaždici. Vybrané prvky sa v mape zvýraznia oranžovo (aj po zmene farieb či
témy) a každý sa dá skopírovať ako JSON alebo jedným tlačidlom nájsť
v záložke *Vrstvy* a hneď preštýlovať.

Nad zoznamom je zvlášť sekcia **Značené trasy tadiaľto**: pásiky trás sú
posunuté vedľa cesty, takže klik do chodníka by ich netrafil – hľadajú sa
preto v širšom okolí a vypíšu sa všetky relácie, ktoré tadiaľ vedú, s farbou
značky, sieťou, pruhom a odkazom do OSM. Polomer výberu (predvolene 6 px) sa
dá zmeniť; k dispozícii sú aj súradnice kliknutého miesta a odkaz naň
v OpenStreetMape.

**Vzory, okraje a prerušovanie.** Ploche aj čiare sa dá dať opakujúci sa vzor
(18 predvolieb – šrafovanie, mriežka, bodky, vlnky, stromčeky, šupiny, tehly,
krížiky, priečky, šípky…) s vlastnou farbou, veľkosťou dlaždice, hrúbkou ťahu
a krytím. Okraj je pri ploche obrysová čiara, pri čiare širší obrys pod ňou
(casing) – oboje s farbou, šírkou, prerušovaním a krytím. Samotná čiara má
navyše 9 predvolieb prerušovania (čiarkovaná, bodkovaná, šrafovanie
železnice, priečky, rebrík lanovky…).

Vzory nie sú hotové obrázky: **názov obrázka je jeho predpis**
(`pat:trees:2f5a28:22:12`), takže si ho prehliadač dokreslí sám cez
`styleimagemissing`, a pipeline tie isté názvy nájde v hotovom štýle a
dopečie ich do spritu ([workers/add-sprite-patterns.mjs](workers/add-sprite-patterns.mjs)),
aby fungovali aj v statickom `style.json` pre iOS.

**Ikona a farby z palety priamo v riadku vrstvy.** Symbolová vrstva s pevne
zadanou ikonou (ikony trás, vrcholy, letiská) má v detaile výber **Ikona** so
všetkými obrázkami z nasadenej sady. Vrstvy, ktoré si farbu vyberajú
**výrazom** – pásik trasy podľa značky z OSM – nemajú v `paint` hex, ktorý by
sa dal prepísať; namiesto toho je v riadku sekcia *farby z palety*, kde sa
dajú doladiť rovno tam, kde je vidieť, čo menia. Taká zmena platí pre celú
tému (je to paleta, nie vrstva).

**Sady ikoniek.** Schéma OpenMapTiles pomenúva POI cez `class`/`subclass`
(`restaurant`, `cafe`, `fuel`, …) a štýl z toho skladá meno ikony – zdroj je
teda použiteľný len vtedy, keď jeho ikony nesú rovnaké mená. Nasadené sú tri:

| sada | obrázkov | pokrytie bežných tried | poznámka |
|---|---|---|---|
| **OSM Liberty (maki)** – predvolená | 244 | 44/50 | jediná so šípkou jednosmeriek; symboly sú v bielom koliesku |
| **OSM Liberty Topo** | 242 | 42/50 | turistická odvodenina s outdoorovými symbolmi |
| **OSM Bright (OpenMapTiles)** | 101 | 42/50 | bez koliesok, len svetlé halo; menej tried, čistejšia kresba |

Preverené a zamietnuté: sprity ostatných štýlov OpenMapTiles (positron,
dark-matter, klokantech, maptiler-basic, fiord) obsahujú 1–4 obrázky, teda
žiadne POI ikony; sprite Protomaps v4 má vlastné pomenovanie a z bežných tried
OSM pokryje asi tretinu, navyše s rámčekom okolo symbolu.

**Hromadné úpravy a kopírovanie.** V oboch zoznamoch sa dajú položky
zaškrtnúť (aj celá skupina naraz alebo „Vybrať zobrazené" podľa filtra)
a potom ich naraz zobraziť, skryť, zafarbiť jednou farbou, skopírovať ako
JSON alebo resetovať. Každá farba má vedľa seba hex pole aj tlačidlo na
skopírovanie; v palete sa dá aj vložiť JSON s farbami.

Zmeny sa priebežne ukladajú **do prehliadača** (`localStorage`) a hneď sa
prejavia v mape.

### Cesta úprav do zdrojáku

```
mapa na Pages ─► 🛠 developer mode ─► „Stiahnuť style-overrides.json"
                                       │
                                       ▼
              Actions ─► „Uložiť úpravy štýlu do zdrojáku" (vlož obsah súboru)
                                       │
                       workers/apply-overrides.mjs – kontrola a prečistenie
                                       │
                       poc/web/style-overrides.json v repozitári
                                       │
                       ďalší „Build map" ─► mapa pre web aj iOS s úpravami
```

`pattern` a `outline` sa nezapisujú do pôvodnej vrstvy – pipeline z nich
vyrobí odvodené vrstvy `<id>__pattern` a `<id>__outline` (okraj plochy nad
ňou, obrys čiary pod ňou), takže sa dajú kedykoľvek odobrať bez stopy.

Workflow **Uložiť úpravy štýlu do zdrojáku** berie obsah súboru ako vstup
(prípadne `overrides_url` pri väčšom súbore), overí ho tou istou funkciou ako
prehliadač – neznáma farba, neplatný hex, neprepísateľná vlastnosť či
prehodený rozsah zoomu skončia varovaním a vyhodia sa – a až potom ho
commitne (voliteľne cez pull request). `reset` vráti pôvodný štýl.

Formát súboru:

```json
{
  "version": 1,
  "icons": "osm-bright",
  "palette": { "outdoor": { "forest": "#a8cc8e", "trailRed": "#cc2222" } },
  "layers": {
    "landcover-wood": {
      "paint":   { "fill-color": "#a8cc8e" },
      "pattern": { "id": "trees", "color": "#2f5a28", "size": 22, "weight": 1.2, "opacity": 0.7 },
      "outline": { "color": "#2f5a28", "width": 1, "dash": "dashed", "opacity": 1 }
    },
    "rail-bg":          { "dash": "ties", "outline": { "color": "#5a5a5a", "width": 1 } },
    "trail-hiking-icon": { "icon": "triangle_11" },
    "housenumber":      { "visible": false },
    "road-motorway":    { "minzoom": 6, "maxzoom": 20 }
  },
  "poi": { "hidden": ["fast_food"] }
}
```

Prehliadač uprednostní to, čo má uložené v `localStorage`; ak tam nič nie je,
použije `style-overrides.json` zo stránky. Tlačidlo **Vymazať všetky zmeny**
vráti mapu na to, čo je v zdrojáku.

## Zoom a detail

Planetiler má tvrdý limit `maxzoom <= 16`
(`PlanetilerConfig.MAX_MAXZOOM`) – vyššia hodnota zhodí build hláškou
`Max zoom must be <= 16`. Pipeline preto zoom nad 16 automaticky oreže na 16
a upozorní v logu.

Priblíženie až na **z20** to nijako neblokuje: dlaždice z16 sa dopočítavajú
**overzoomom** v MapLibre (web aj iOS majú `maxZoom = 20`). Aby overzoom
vyzeral ostro, najvyšší zoom sa generuje bez zjednodušovania geometrie:

```
--maxzoom=16 --render_maxzoom=16
--min_feature_size_at_max_zoom=0     # nezahadzuj malé prvky
--simplify_tolerance_at_max_zoom=0   # presná geometria
--transportation_z13_paths=true      # všetky chodníky/cestičky
--building_merge_z13=false           # samostatné budovy, nie zlepence
```

Čo je vidieť na akom zoome (`DETAIL_Z = 14` v `themes.js`):

| zoom | správanie |
|---|---|
| < 14 | mapa sa orezáva – vrstvy sa zapínajú postupne podľa `minzoom`. Cesty sa kreslia už od z4 vlasovými čiarami (obrysy až od z10), aby bola sieť čitateľná aj na malých mierkach |
| 14–15 | plný detail, POI filtrované na `rank <= 24`, aby mapa nebola zahltená |
| 16+ | **všetko bez filtra** – všetky body, línie aj plochy, 3D budovy |
| 17+ | navyše súpisné čísla domov |

**Veľkosť vs. zoom.** GitHub Pages zvládne stránku do ~1 GB a do toho sa musia
zmestiť dlaždice **aj vrstevnice, fonty a sprity** – nie každé zvlášť. Celé
Slovensko má pri z14 ~800 MB, vrstevnice po 10 m do z14 ďalších niekoľko sto,
takže spolu by limit prekročili. Pipeline preto hospodári s jedným rozpočtom:

- `size_limit_mb` (default 900) – rozpočet na **celú stránku**,
- vrstevnice sa robia **pred** dlaždicami a majú strop 40 % rozpočtu; keď sú
  nad ním, prepočítajú sa o zoom nižšie (z hotového GPKG, teda v sekundách –
  DEM sa znovu nesťahuje),
- dlaždice potom dostanú presne to, čo zvýšilo, a `auto_shrink` (default áno)
  ich zmenší na zoom, ktorý sa doň vojde. Keďže nižší zoom zmenší dlaždice
  zhruba 3,5×, skáče sa rovno o toľko zoomov, koľko treba (najviac o dva
  naraz), aby sa nerobili zbytočné hodinové behy Planetileru,
- `crop_bbox` – oreže PBF na menšie územie (`west,south,east,north`), čím sa
  maxzoom 16 pohodlne zmestí.

Vďaka tomu build na veľkosti nepadne až na konci po hodinách tilovania, ale
sám sa zmestí a do logu napíše, čím ubral. Ak chceš väčší detail, ubrať treba
územiu (`crop_bbox`, kraj) alebo vrstevniciam (`contour_interval` 20 m,
`contour_maxzoom` 12, prípadne `contours: nie`).

Pre maximálny detail na z20 teda voľ **kraj alebo `crop_bbox` + maxzoom 16**;
pre celé Slovensko nechaj pipeline zvoliť najvyšší zoom, ktorý sa zmestí.
- **iOS / multiplatform:** appka v [app/ios](app/ios), návrh v
  [docs/ios-multiplatform.md](docs/ios-multiplatform.md).
- **Backend:** [backend](backend) – NestJS API (`/api/health`, `/api/regions`).

## Prvé spustenie

1. **Zapni GitHub Pages:** Settings → Pages → Source: **GitHub Actions**.
2. Actions → **Build map (PBF → PMTiles) & deploy Pages** → *Run workflow*.
   Formulár má **desať polí** – viac `workflow_dispatch` inputov GitHub
   neprijme (pri 26 sa workflow prestal načítať a beh skončil ako „failure"
   s nula jobmi). Vo formulári sú preto veci, ktoré sa naozaj menia:

   | input | typ | čo robí |
   |---|---|---|
   | `region` | výber | `slovensko` alebo kraj |
   | `area` | **výber** | pohorie, na ktorom sa počíta terén – `cely_region`, `vysoke_tatry`, `tatry`, `slovensky_raj`, `mala_fatra`… |
   | `contour_source` | **výber** | odkiaľ **vrstevnice**: `sonny` (20 m), `dmr35` (10 m), `dmr5` (5 m), `ugkk` (1 m LiDAR, len s výrezom), `ziadne` |
   | `rock_source` | **výber** | odkiaľ **skaly**: ten istý zoznam modelov (počíta sa sklon), alebo `tienovanie` (hotové polygóny z tieňovaných dlaždíc), alebo `ziadne` |
   | `shading_source` | **výber** | odkiaľ **tieňovanie a 3D terén**: `sonny`, `dmr35`, `dmr5`, `ziadne` |
   | `contour_interval` | text | interval vrstevníc v metroch (každá 10. je hlavná, každá 5. polovičná) |
   | `rock_slope` | text | od akého sklonu (°) je terén skala |
   | `rock_res` | text | mriežka na obrys skál – `auto` (odporúčané) alebo číslo v metroch |
   | `rebuild` | výber | `nic` / `vrstevnice` / `skaly` / `teren` / `vsetko` |
   | `options` | text | zriedka menené nastavenia ako `kľúč=hodnota` |

   **Tri výbery zdroja, jeden na vrstvu.** Kým to bol jeden `dem_source` pre
   všetko, nedalo sa povedať to, čo dáva zmysel najčastejšie: skaly
   z najjemnejšieho modelu (aj keď ho máme len na výrez) a tieňovanie
   z hrubšieho, ktorý pokrýva celý región. `ziadne` vrstvu vypne – zapínač je
   tým pádom v tom istom poli ako zdroj, takže sa nedá zadať „generuj
   vrstevnice, zdroj žiadny". Nahradilo to aj pole `layers`; značené trasy
   (jediná vrstva bez výberu zdroja, ide z toho istého PBF ako mapa) sa
   vypínajú cez `options: trails=false`.

   `ugkk` v tieňovaní zámerne nie je: 1 m LiDAR máme len na výrez, kým
   tieňovanie sa robí vždy na celý región. Zoznamy vo formulári stráži
   `Lint workflows` proti [workers/dem-sources.json](workers/dem-sources.json)
   – zdroj sa nedá pridať do jedného a zabudnúť v druhom.

   Zoznam pohorí v `area` sa berie z
   [workers/areas.json](workers/areas.json) – keď tam pribudne pohorie, treba
   ho dopísať aj do výberu vo workflowe. Vlastný bbox ide cez
   `options: area_bbox=W,S,E,N`.

   Do `options` idú veci, ktoré sa menia zriedka – napíšu sa za sebou,
   oddelené medzerou:

   ```
   crop_bbox=18.9,49.1,19.2,49.3 size_limit_mb=1200 contour_maxzoom=15
   ```

   Známe kľúče s predvolenými hodnotami sú vo
   [workers/parse-options.py](workers/parse-options.py): `crop_bbox`,
   `area_bbox`, `size_limit_mb`, `auto_shrink`, `ugkk_fallback`, `ugkk_urls`,
   `contour_maxzoom`, `contour_smoothing`, `trails`, `trails_maxzoom`,
   `terrain_maxzoom`, `maxzoom`, `rock_img_asset`, `rock_img_zoom`,
   `rock_img_options`, `custom_pbf_url`, `custom_name`, `custom_bbox`.

   Zdroj skál sa vyberá **inputom `rock_source`**, nie tu – prepína celý
   pôvod vrstvy, takže patrí do formulára. Cez `options` sa dá nanajvýš
   vynútiť konkrétny asset (`rock_img_asset=rockimg-…gpkg.zst`); viď
   [Druhá cesta k skalám](#druhá-cesta-k-skalám-tmavé-plochy-v-tieňovaní-pokus).

   **Preklep je chyba, nie ticho ignorovaná hodnota.** `size_limit=1200` build
   zastaví so zoznamom známych kľúčov – inak by bežal hodinu s iným
   nastavením, než si myslíš. Na začiatku behu sa vypíše tabuľka všetkých
   nastavení s vyznačením toho, čo si zmenil.

3. Mapa je na `https://<user>.github.io/fricomaps/` – ovládanie je zbalené pod
   tlačidlom ⚙ vľavo hore, aby bolo vidieť hlavne mapu. V paneli je prepínač
   témy, regiónu, vrstevníc a skál, 3D terénu a developer módu.

Pipeline si po nasadení sama overí, že mapa naozaj funguje (**smoke test**):
`manifest.json`, `style.json`, sprite, glyfy a `Range` request na `.pmtiles`
(musí vrátiť `206`). Ak niečo z toho chýba, workflow zlyhá s konkrétnou URL –
namiesto ticha a bielej mapy v prehliadači. Viewer navyše chyby načítania
vypisuje priamo do panela.

**Ikonky a nápisy** nevisia na cudzích službách: sprite aj glyfy (Noto Sans)
sa kopírujú na naše Pages a pred nahratím sa kontroluje, že štýl odkazuje len
na fontstacky a ikony, ktoré tam naozaj sú.

> Pozn.: ak deploy zlyhá na ochrane prostredia `github-pages`, povoľ v
> Settings → Environments → github-pages nasadzovanie aj z tejto vetvy
> (alebo zmerguj do default vetvy a spusti workflow tam).

## Lokálny vývoj

```bash
npx serve poc/web        # viewer (dlaždice vznikajú až v CI)
cd backend && npm install && npm run start:dev   # API na :3000
```

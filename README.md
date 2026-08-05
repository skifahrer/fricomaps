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
               štýlov, SDF sprite, vzory do spritu, zápis úprav štýlu
docs/          návrhy (iOS / multiplatform), podrobný popis pipeline
.github/workflows/  CI pipeline (výškový model + build mapy + deploy Pages)
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

Update DEM                   Sonny's LiDAR DTM 20m (Google Drive) ─► rezanie
(sám, keď terén chýba)       na 1° dlaždice ─► release `dem-sonny`:
                             N49E019.tif + meta.json
                             ▲ Build map ho zavolá automaticky, keď v release
                               nie je pre jeho územie ani jedna dlaždica

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
| **vrstevnice a skaly** | **Sonny's LiDAR DTM, model 20m** | náš release `dem-sonny` (napĺňa ho workflow *Update DEM*) |
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

Ovládanie vo workflowe: `contours` (zap/vyp), `contour_interval` (default 10 m),
`contour_maxzoom` (default 14) a `contour_smoothing` (default 0 = bez
zjemnenia). Bez zjemnenia je terén detailnejší, ale vrstevníc je viac – a keď
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
                                  mriežka 2 m (`rock_res`)
  → gdaldem slope                 sklon v stupňoch
  → gdal_translate -ot Byte       sklon s krokom 0,5° na disk (mozaika celého
                                  územia sa vo Float32 nezmestí)
  → gdalbuildvrt                  mozaika sklonu, a až nad ňou NARAZ:
  → gdal_contour -p -fl 100 130   izolínie sklonu ako PLOCHY, aj s dierami
                                  (hladší okraj než polygonizácia po pixeloch)
  → -explodecollections           samostatné skaly
  → filter najmenšej plochy       + `class`: steep (≥50°) / cliff (≥65°)
  → vrstva `rock` v {región}-contours.pmtiles  – vektor, ako všetko ostatné
```

**Tvar plôch je tvar terénu.** Obrys je izolínia sklonu, teda presne tá čiara,
kde svah prekročí prah – zubatý pás pod hrebeňom, oblúk okolo žľabu, ostrov
brala v suti. Žiadna mriežka štvorčekov (tá tu bola do augusta 2026 a je
preč).

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
| mriežka, na ktorej sa obrys počíta | **2 m** (`rock_res`; `1` dá 1 m²) |
| krok sklonu v mozaike | **0,01°** (Int16) – hrubší krok robil obrys zubatý |
| zjednodušenie obrysu | štvrtina mriežky (`ROCK_SIMPLIFY: -1`) – zmaže schodíky |
| bunka zdrojového DEM (Sonny 20 m) | ~20 m → **strop skutočného detailu** |
| najmenšia ponechaná plocha | jedna bunka mriežky: **4 m²** pri 2 m, **1 m²** pri 1 m |
| zjednodušovanie obrysu | žiadne (`ROCK_SIMPLIFY: 0`) |
| filter drobných prvkov v dlaždiciach | vypnutý na najvyššom zoome |

Presné čísla za konkrétny beh (počet plôch, najmenšia/priemerná/najväčšia
plocha, koľko km² skál, koľko plôch má dieru a koľko km² diery vykrojili) píše
build do **Summary** – viď [Súhrn buildu](#súhrn-buildu).

**Detail na 1 m² sa dá zapnúť** – `rock_res: 1`. Najmenšia ponechaná plocha je
potom naozaj 1 m². Cena: 4× viac buniek, teda pre celý kraj okolo dvoch hodín,
takže to má zmysel len s `crop_bbox`. A platí to isté ako vyššie: zdrojový DEM
má 20 m, takže sú to jemnejšie *obrysy a diery*, nie nové merania terénu.

> **Mriežka nie je to isté ako detail.** Mriežka 2 m hovorí, ako jemne je
> obrys odkrokovaný. Skutočný detail je ale stropený zdrojom: Sonny má pre
> Slovensko bunku ~20 m, takže tvary pod 20 m sú **dopočítané, nie merané** –
> interpolácia dá hladší a presnejšie umiestnený obrys, novú informáciu však
> nepridá. Jemnejšie by vedel len 1 m LiDAR
> ([ÚGKK DMR 5.0](https://www.geoportal.sk/)); ten sa z geoportálu sťahuje cez
> interaktívny export, takže by sa musel najprv nazrkadliť do releasu rovnako
> ako Sonnyho DTM.

#### Zdroj výšok sa dá prepnúť

Input **`dem_source`**:

| hodnota | model | mriežka | pokrytie | stav |
|---|---|--:|---|---|
| **`sonny`** (default) | Sonny's LiDAR DTM | 20 m | celý región | overené |
| `ugkk` | ÚGKK DMR 5.0 (1 m LiDAR) | **1 m** | **len s výrezom** (`area`) | **neoverené** |

Platí pre **vrstevnice aj skaly** – oboje sa počíta z toho istého modelu, nech
obrys skaly a priebeh vrstevnice sedia na tom istom teréne.

**Spúšťaš len jednu pipeline.** `Build map` sa sám pozrie, či je výrez v
release `dem-ugkk`, a keď nie je, spustí si zrkadlo ako svoju úlohu – to isté,
čo už robí `mirror-dem` pre Sonnyho. Ručne netreba spúšťať nič.

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

> **Testované som to však nemal kde.** Sieť prostredia, v ktorom sa to písalo,
> blokuje `*.skgeodesy.sk`, `geoportal.sk` aj `rpi.gov.sk`. Overená je
> mechanika (rotácia profilov prešla proti dostupnému hostiteľovi), nie to,
> či ÚGKK pustí Safari. To povie prvý beh – v matici vyššie.

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

| územie | `rock_res` | buniek | odhad | |
|---|--:|--:|--:|---|
| Prešovský kraj | 1 m | 19,60 mld. | 2:37:21 | ✗ odmietne |
| Prešovský kraj | **2 m (default)** | 5,27 mld. | 0:42:18 | ✓ |
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

Ovládanie vo workflowe: `rocks` (zap/vyp), `rock_slope` (od akého sklonu je
terén skala, default 50°) a `rock_res` (mriežka obrysu v metroch, default 2).
Ostatné ladenie je v `env:` na začiatku
[build-map.yml](.github/workflows/build-map.yml): `ROCK_SIMPLIFY` (0 = presný
obrys), `ROCK_CLIFF_PLUS` (o koľko ° nad prahom začína trieda `cliff`),
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
   | `dem_source` | výber | `sonny` (20 m) alebo `ugkk` (1 m LiDAR, len s výrezom) |
   | `layers` | text | čo generovať: `contours,terrain,trails` |
   | `contour_interval` | text | interval vrstevníc v metroch |
   | `rock_slope` | text | od akého sklonu (°) je terén skala |
   | `rock_res` | text | mriežka na obrys skál (2 m; `1` dá detail na 1 m²) |
   | `maxzoom` | text | max zoom mapových dlaždíc |
   | `rebuild` | výber | `nic` / `vrstevnice` / `skaly` / `teren` / `vsetko` |
   | `options` | text | zriedka menené nastavenia ako `kľúč=hodnota` |

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
   `contour_maxzoom`, `contour_smoothing`, `trails_maxzoom`,
   `terrain_maxzoom`, `rocks`, `custom_pbf_url`, `custom_name`, `custom_bbox`.

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

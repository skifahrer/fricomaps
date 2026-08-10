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
(sám, keď terén chýba)       na 1° dlaždice ─► sklad `dem-sonny`:
                             N49E019.tif + meta.json
                             ▲ Build map ho zavolá automaticky, keď v sklade
                               nie je pre jeho územie ani jedna dlaždica

DMR 5.0 z Drive (ETRS89)     145 GB BigTIFF + 43 GB pyramíd na Google Drive,
(toto si volá Build map)     čítané cez HTTP Range – berie sa len to, čo
                             výrez pretína:
                               výrez (1 m)          ─► `dem-ugkk` ┐ jeden zdroj
                               1° dlaždice (5 m)    ─► `dem-dmr5` ┘ `dmr5`
                             ▲ výšky sú elipsoidické, prevádzajú sa cez EGM2008
                             ▲ toto je zdroj pre skaly v plnom rozlíšení
                             ▲ Build map ju volá DVOMA jobmi (výrez + dlaždice),
                               lebo model má dve podoby a chýbať môžu naraz


Skaly z tieňovaných dlaždíc  POKUS: hillshade JPG z freemap.sk ─► tmavé
(pokus, na jedno pohorie)    plochy ─► polygóny ─► sklad `dem-rocks-img`
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
- **Typy máp:** [poc/web/map-types.js](poc/web/map-types.js) – **turistická,
  lyžiarska, cestná, historická** a základná („všetko"). Typ mapy hovorí, *čo*
  mapa ukazuje; téma len to, *ako* to vyzerá. Viď
  [Typy máp](#typy-máp--čo-ktorá-mapa-ukazuje).
- **Témy a štýlovanie:** [poc/web/themes.js](poc/web/themes.js) – 4 farebné
  témy (Svetlá, Tmavá, Outdoor, Retro/Pastel), ~140 vrstiev pokrývajúcich celú
  OpenMapTiles schému: krajinná pokrývka, využitie územia, voda a vodné toky,
  budovy (od z16 v 3D), cesty vrátane chodníkov/cyklotrás/schodov, mosty a
  tunely, železnice, lanovky, hranice až po obce, súpisné čísla, vrcholy hôr,
  letiská a POI s ikonkami zo spritu osm-liberty (maki). Všetky nápisy majú
  jemný obrys (`textHalo`), aby zostali čitateľné nad ľubovoľným podkladom;
  pohoria, hrebene a geografické oblasti sa od nízkych zoomov kreslia
  kurzívou a verzálkami, aby sa nepliedli so sídlami.
  Ten istý generátor vyrába statické `styles/{region}-{typ mapy}-{tema}.json`
  pre iOS (predvolený typ aj pod starým menom `{region}-{tema}.json`).
- **Značené trasy:** turistické chodníky, cyklotrasy, bežky, ferraty
  a jazdecké trasy z OSM relácií – ako farebné pásiky **vedľa** cesty,
  s názvom pozdĺž trasy.
  Viď [Značené trasy](#značené-trasy-turistika-cyklo-bežky).
- **Krajinné prvky mimo schémy:** násypy, zárezy, múry, ploty, elektrické
  vedenia, prieseky, pramene, jaskyne, rozhľadne, parkoviská a zjazdovky.
  Schéma OpenMapTiles ich nemá vôbec, takže majú vlastné dlaždice.
  Viď [Krajinné prvky](#krajinné-prvky-čo-openmaptiles-nemá).
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
| **vrstevnice a skaly** | **Sonny's LiDAR DTM, model 20m** | náš sklad `dem-sonny` na Drive (napĺňa ho workflow *Stiahnuť výškové dáta*) |
| **tieňovanie reliéfu, 3D terén** | **ten istý Sonny DEM** | vlastné PNG dlaždice `terrain/{z}/{x}/{y}.png`, uložené v sklade `dem-terrain` |
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
  → workers/drive-folder.py   stiahne celý priečinok, prihlásene cez Drive API
                              (bez tokenu gdown a s varovaním o limite)
  → 7z / unzip         rozbalí .zip
  → workers/dem-tiles.py   GeoTIFF (aj celá krajina v metrickej projekcii)
                           → dlaždice 1°×1° N49E019.tif vo WGS84
     (.hgt sa prevádza priamo – je to už 1° dlaždica, len bez hlavičky)
  → sklad `dem-sonny` + meta.json
```

Rezanie na dlaždice je potrebné preto, že 20m model môže byť **jeden GeoTIFF
na celú krajinu a v metrickej projekcii**, kým build mapy chce sťahovať len
dlaždice pre svoj bbox a lepiť ich `gdalbuildvrt`-om (ten rôzne projekcie
v jednom VRT neunesie). Jeden sklad = jeden model; miešať 20m a 1″ v jednom
sklade nemá zmysel, dlaždice sa volajú rovnako.

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
  → gdalwarp   vyhladí DEM: priemer v okne 2 m (`-r average` na hrubšiu
               mriežku a `-r cubicspline` späť na pôvodnú). Pri hrubom modeli
               vyjde okno na jednu bunku a nerobí sa nič
  → gdal_contour -i 10
  → ogr2ogr    dopočíta `level`: major (100 m) / mid (50 m) / minor (10 m)
               a `-simplify` zmaže schodíky po hranách buniek DEM
               (tolerancia: štvrtina bunky)
  → smooth-shapes.py  zaoblí rohy, čo po zjednodušení ostali ostré
                      (Chaikin, 2 prechody)
  → planetiler generate-custom --schema=workers/contours.yml
  → {región}-contours.pmtiles
```

**Zubatosť robí mikroreliéf v modeli, nie mriežka – a preto sa hladí DEM, nie
len čiara.** `gdal_contour` interpoluje priesečník na hrane bunky, takže
z hladkého poľa výšok vyjde hladká čiara aj bez akýchkoľvek úprav. Čo ju krčí,
je to, čo je v LiDARovom DTM naozaj: kry, balvany, šum merania na úrovni
decimetrov. Zaoblenie čiary to vlnenie len **zaokrúhli**, neodstráni.

**Lenže v tom okne nie je len šum – a práve tým sa vrstevnice zaoblili
priveľmi.** Rebro, žľab či terasa široká pár metrov sú tvary, ktoré v teréne
naozaj sú, a priemer v okne 5×5 ich zmazal spolu s krami: čiara potom nebola
zubatá, ale ani sa nedržala terénu. Merané na simulovanom teréne, ktorý má
okrem šumu (σ = 0,15 m na 1 m mriežke) aj **reálne tvary** s vlnovou dĺžkou
60, 25 a 12 m; „odchýlka" je vzdialenosť od izolínie toho istého terénu **bez**
šumu, posledné dva stĺpce hovoria, koľko z tvaru na čiare ostalo:

| postup | bodov | priemerný lom | lomov > 30° | odchýlka | tvar 25 m | tvar 12 m |
|---|--:|--:|--:|--:|--:|--:|
| izolínia terénu bez šumu (referencia) | 1420 | 5,6° | 4,1 % | 0,04 m | 99 % | 98 % |
| bez vyhladenia, 1/4 bunky, 1× Chaikin (do augusta) | 1908 | 31,3° | 43,4 % | 0,86 m | 100 % | 93 % |
| okno 5×5, 1/2 bunky, 2× Chaikin (august) | 436 | 10,6° | 4,1 % | 1,52 m | 75 % | **27 %** |
| okno 3×3, 1/2 bunky, 2× Chaikin | 604 | 10,7° | 4,2 % | 0,95 m | 90 % | 52 % |
| **okno 3×3, 1/4 bunky, 2× Chaikin (teraz)** | **860** | **8,2°** | **1,6 %** | **0,70 m** | **93 %** | **63 %** |
| okno 7×7, 1/2 bunky, 2× Chaikin | 336 | 10,8° | 3,0 % | 2,23 m | 58 % | **5 %** |

Meria to [`workers/measure-smoothing.py`](workers/measure-smoothing.py) – nie
je to časť pipeline, nevolá to žiadny workflow a stačí naň numpy, takže sa
tabuľka dá kedykoľvek zopakovať (`python3 workers/measure-smoothing.py`).

Kľúčové je porovnanie tretieho a piateho riadku: **menšie okno nie je ústupok
zubatosti**. Priemerný lom je menší (8,2° oproti 10,6°), ostrých lomov je
1,6 % namiesto 4,1 % a odchýlka od skutočnej izolínie klesla z 1,52 na 0,70 m –
čiara je zároveň hladšia **aj** vernejšia. Platí sa **bodmi**: 860 namiesto
436, stále však o 55 % menej než pred augustom.

Okno sa zadáva **v metroch** (`CONTOUR_DEM_LOWPASS`, default 2 m), nie
v bunkách – a to je celé, prečo sa smie zapnúť predvolene. Okno je vždy
nepárny násobok bunky, takže dva metre sú na 1 m LiDARe okno 3×3, kým na 5 m
dlaždiciach DMR 5.0, na DMR 3.5 (10 m) aj na Sonnyho 20 m vyjde jedna bunka
a nevyhladzuje sa nič. Hrubý model mikroreliéf neobsahuje – je v ňom
spriemerovaný už zo zdroja – a okno „3×3 buniek" by v ňom zmazalo desiatky
metrov terénu. Priemer robia dva `gdalwarp`y (zmenšenie s `-r average`,
zväčšenie späť s `-r cubicspline`), takže sa gigabajtový raster nemusí ťahať
cez pamäť.

**Až potom sa upratuje čiara.** `-simplify` zmaže schodíky po hranách buniek,
po ňom ostanú **ostré rohy** – a tie zaobli Chaikinovo orezávanie rohov.
Zjednodušenie je na **štvrtine** bunky, nie na polovici, a je to tá istá otázka
ako veľkosť okna: samo čiaru oblou nerobí, ale predlžuje segmenty, a Chaikin
potom reže rohy dlhé štvrtinu segmentu – čím dlhší segment, tým väčší kus
tvaru sa odreže (pri 1/2 bunky prežije z 12 m tvaru 52 %, pri 1/4 bunky 63 %).

Koľko prechodov stačí, merané na tom istom teréne (okno 3×3, 1/4 bunky):

| nastavenie | bodov | priemerný lom | lomov > 30° | odchýlka | tvar 12 m |
|---|--:|--:|--:|--:|--:|
| bez zaoblenia | 215 | 33,1° | 44,1 % | 0,61 m | 71 % |
| 1× Chaikin | 430 | 16,5° | 16,8 % | 0,68 m | 65 % |
| **2× Chaikin (default)** | **860** | **8,2°** | **1,6 %** | **0,70 m** | **63 %** |
| 3× Chaikin | 1720 | 4,1° | 0,1 % | 0,70 m | 63 % |

**Zaoblenie rohov nie je to, čo vrstevnice zaobľovalo priveľmi** – to bolo okno
na DEM a tolerancia zjednodušenia. Chaikin nad krátkymi segmentmi ubral z tvaru
terénu 2 percentuálne body (65 → 63 %), kým vyhladenie DEM ich brávalo desiatky
(63 → 27 %). Dva prechody preto ostávajú: jeden nechá každý šiesty lom nad 30°,
čo je presne tá zubatosť, ktorú na čiare pri max zoome vidno, a tretí je
dvojnásobok bodov za rohy, ktoré už ostré nie sú.

Ovláda to `CONTOUR_DEM_LOWPASS`, `CONTOUR_SIMPLIFY` a `CONTOUR_SMOOTH` v `env:`
build-map.yml: okno v metroch (`0` = nehladiť DEM), záporné číslo = koľko
**štvrtín** bunky DEM (`-1` = štvrtina), `0` = presná čiara, kladné číslo =
tolerancia v metroch; `CONTOUR_SMOOTH: 0` zaoblenie vypne. Všetky tri sú aj
v kľúči cache, takže po ich zmene sa vrstevnice naozaj prepočítajú.

Vrstevnice sa trasujú z **plného rozlíšenia DEM** a do dlaždíc idú na
najvyššom zoome bez zjednodušovania geometrie
(`--simplify_tolerance_at_max_zoom=0`) a bez zahadzovania drobných prvkov
(`--min_feature_size_at_max_zoom=0`) – malé uzavreté krúžky na kopčekoch a
v jamách teda ostávajú. Nižšie zoomy si Planetiler zjednodušuje sám, inak by
z vrstevníc bola čierna plocha.

`level` riadi, čo je vidieť kedy: hlavné vrstevnice od **z1**, polovičné od
z12, základné od z13, popisky výšky pozdĺž hlavných od z13. To je celé to
„zjednodušene na malých mierkach": pod z12 je v mape len hlavná vrstevnica,
a Planetiler ju má na každom zoome zjednodušenú podľa veľkosti pixela, takže
z1–z9 je v súbore stotinou toho, čo zaberá jeden detailný zoom. Výsledok je
nacacheovaný podľa bboxu, zdroja výšok a intervalu — vrstevnice závisia len od
územia, takže sa pri ďalšom builde mapy nepočítajú znova.

Ovládanie vo workflowe: `contours` (zap/vyp), `contour_interval` (default
10 m; zvýrazňuje sa každá 10. čiara ako hlavná a každá 5. ako polovičná, čiže
pri 10 m sú to doterajších 100 a 50 m), `contour_maxzoom` (default 14) a
`contour_smoothing` (default 0 = bez zjemnenia). Bez zjemnenia je terén detailnejší, ale vrstevníc je viac – a keď
prekročia 40 % rozpočtu stránky, pipeline im sama zníži maxzoom.

### Tieňovanie reliéfu a 3D terén

MapLibre nevie čítať výšky z GeoTIFFu – potrebuje pyramídu PNG dlaždíc, kde je
výška zakódovaná do farby (*terrarium*). Robia sa z výškového modelu, ktorý
vyberá **`shading_source`** ([workers/build-terrain.py](workers/build-terrain.py)),
takže 3D reliéf nedvíha koruny stromov, kým vrstevnice vedú po zemi.

- **Áno, dá sa aj z DMR 5.0** – `shading_source: dmr5`. Tieňovanie sa robí
  vždy na celý región, takže `dmr5` tu vyjde na svoju **5 m** dlaždicovú
  podobu (metrová existuje len na výrez, viď „jeden zdroj, dve podoby").
- **Každý zoom sa prevzorkuje z DEM nanovo** (`-r average`), nezmenšujú sa
  hotové dlaždice: priemerovať sa musí *výška*, nie zakódovaná farba.
- **Zoom je `auto`** (`terrain_maxzoom`): najnižší, na ktorom je pixel
  dlaždice jemnejší než bunka modelu – Sonny (20 m) → **z13**, DMR 3.5 (10 m)
  → **z14**, DMR 5.0 (5 m) → **z15**. Pevná trinástka tu bola dovtedy, kým bol
  Sonny jediný zdroj, a znamenala, že si síce vyberieš DMR 5.0, ale reliéf
  vyzerá ako zo Sonnyho: pixel z13 má 12,5 m, takže sa 5 m model nemá ako
  prejaviť.
- **Každý zoom navyše je štvornásobok dlaždíc**, takže z13 → z15 je
  šestnásťnásobok. Preto má tieňovanie svoj podiel rozpočtu stránky
  (`BUDGET_TERRAIN_PCT`, 12 %) a `build-terrain.py` sa do neho zmestí sám:
  vypíše plán, počíta zoomy odspodu a ten, ktorý by rozpočet prekročil, ani
  nezačne – povie to warningom a čo s tým (menšie územie, vyšší
  `size_limit_mb`). Jemný reliéf celého kraja sa teda nedá dostať zadarmo,
  ale výrez alebo rýchly test ho majú.
- **Ukladajú sa do skladu `dem-terrain`** ako jeden `.tar.zst` na región,
  model a maxzoom. Meno nesie **skutočne vyrobený** maxzoom, nie želaný –
  a ďalší build si zo skladu vezme najvyšší uložený zoom, ktorý nie je vyšší
  než ten želaný, takže sa to isté nepočíta druhýkrát. `terrain_rebuild: áno`
  ich vynúti prepočítať nanovo.
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
  → gdal_contour -p -fl …         izolínia sklonu ako PLOCHY
                                  (hladší okraj než polygonizácia po pixeloch)
  → -explodecollections           samostatné skaly
  → ST_BuildArea(ST_ExteriorRing) PLNÉ plochy – von ide len vonkajší prstenec
  → filter najmenšej plochy
  → -simplify                     preč so schodíkmi po hranách buniek
  → smooth-shapes.py              zaoblenie rohov, ktoré po zjednodušení
                                  ostali ostré (Chaikin, 2 prechody); ten istý
                                  skript zaobľuje aj vrstevnice
  → vrstva `rock` v {región}-rocks.pmtiles  – VLASTNÉ dlaždice, vlastný maxzoom
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
v `workers/smooth-shapes.py`.

**Jedna trieda, jedna sivá.** Skala je v mape jedna plocha v jednej sivej bez
priehľadnosti — žiadna plocha vnútri inej. Priehľadnosť by totiž znamenala,
že každý prekryv je vidieť — dve plochy cez seba vyjdú tmavšie než jedna,
a stačí na to plocha rozseknutá hranicou bloku alebo `cliff` ležiaci v diere
`steep`u. Plná farba to rieši na úrovni kreslenia a plochy sa nemusia ani
zlepovať, ani strážiť proti sebe.

Predtým to boli dve polopriehľadné triedy (`steep` ≥ 50°, `cliff` ≥ 65°).
Vrátiť sa to dá: `options: rock_plne=0`, prípadne `rock_img_options=plne=0`
pre skaly z tieňovania.

**Diery v plochách ostávajú** — tam, kde je vnútri steny miesto pod prahom
(polica, terasa, zarastený stupeň). Krátko sa zapĺňali spolu s tým prechodom
na jednu triedu a bola to chyba: zo skál boli súvislé klaksy, v ktorých nebolo
vidieť žiaden tvar. `options: rock_zapln_diery=1` to vráti, ak by to niekto
naozaj chcel.

**Skaly majú vlastné dlaždice.** `{región}-rocks.pmtiles`, oddelene od
vrstevníc — a to kvôli maxzoomu: každý `.pmtiles` má len jeden a tie dve
vrstvy ho chcú úplne iný. Vrstevnice sú čiary cez celý kraj a rozpočet
stránky minú okolo z14; skaly sú plochy len tam, kde je terén strmý, takže sa
do z16 (tvrdý strop Planetilera) zmestia. Kým boli v jednom súbore, museli sa
obe uskromniť na to nižšie — a na skalách to bolo vidieť, lebo práve pri
priblížení sa pozerá, či obrys sedí na terén. Nad maxzoomom sa dlaždice
naťahujú overzoomom, takže sú skaly vidieť **až do maximálneho zoomu mapy**.
Vo viewri majú vlastný prepínač, takže sa dajú zapnúť aj bez vrstevníc.

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

**Skaly sú vidieť všade, kde sú** – vrstva ide do dlaždíc od **z1** a štýl ich
odtiaľ aj kreslí. Nízke zoomy pritom nič nestoja: Planetiler na každom zoome
zjednoduší obrys podľa veľkosti pixela a zahodí všetko menšie než pixel, takže
z prehľadu ostane len tvar veľkých stien – a dlaždíc je tam rádovo menej (z1
je jedna na celý región, z10 ich je tisíc). S približovaním pribúdajú detaily.

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
`rock_source: dmr5` s výrezom (1 m LiDAR), kde auto ide na 0,5 m. Zadať sa dá aj číslo
natvrdo (`options: rock_res=1`).

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
  → -simplify + smooth-shapes.py       rovnaké zaoblenie ako pri DEM
  → rock.gpkg  ─► sklad `dem-rocks-img`  +  sklad `vysledky` (na pozretie)
```

**Prečo to môže fungovať:** tieňovanie je obraz sklonu a hires vrstva
freemap.sk je robená z 1 m LiDARu – pri z18 vyjde jeden pixel na **~0,4 m**
terénu. To je jemnejšie, než na čo si sklon vieme rozumne spočítať sami.

**Prečo to klame:** hillshade je osvetlený z jednej strany. Rovnako strmá
stena otočená k slnku je na ňom **najsvetlejšia zo všetkého**. Táto cesta
teda systematicky nájde severozápadné steny a systematicky prehliadne
juhovýchodné. Preto je to jedna z možností vo výbere `rock_source`
(`tienovanie`) a nie náhrada skál počítaných zo sklonu.

**Najtenšie vlákna siete skala nie sú.** Prah nájde aj vlásočnicové ryhy
a mikrotiene cez celý svah. Vektorizáciou sa z nich stane jeden prepojený
polygón cez celý výrez a v mape z neho pri z14 a nižšie nie je sieť, ale
**rovnomerná sivá deka**. Zahadzuje ich `open` (default 3 m) – podľa ŠÍRKY,
nie podľa plochy, lebo celá sieť je jeden veľký útvar a `min_area` na ňu
nesiaha. Namerané pri Gerlachu: 21,6 % plochy bez neho, **9,5 %** s ním.

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
  útvary, o ktoré ide. Predvolené `min_area` je preto dnes **7 m²** – ~11
  pixelov na z17, teda blízko hranice, pod ktorou je už len zrno JPEGu.
  Tabuľka ostáva pri nameraných 200 a 50. `min_hole` sa neuplatňuje, kým sú
  plochy plné (diery sa nekreslia vôbec).

**Prvý beh je ladiaci.** Predvolené prahy sú kvalifikovaný odhad, nie
nameraná hodnota – tá dlaždicová vrstva sa nedá ochutnať dopredu. Beh preto
odloží do skladu `vysledky` na Drive súbor `nahlad-…` s PNG **mozaika vedľa
nájdených plôch** (vľavo tieňovanie, vpravo to isté s červenou maskou)
a histogramom odtieňov. Podľa nich sa `dark` / `dark_always` / `rel` doladia
za jeden pohľad.

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
ktorý server dá a ktorý sa zmestí do stropu 60 000 dlaždíc. Vektorizuje sa **po blokoch**
(`options: block_tiles=8`, teda 2048 px; menší blok = menej pamäte a jemnejšie
pokračovanie). Nad celou mozaikou to totiž nedobehlo: `gdal_contour -p`
skladá uzavreté prstence a v zrnitom JPEGu ich je toľko, že to rastie
rýchlejšie než lineárne — 3,62 mld. pixelov bežalo 2 h 41 min a nedopočítalo
sa, pričom pamäť ostala na 0,7 GB.

Plocha cez hranicu bloku vypadne ako dva kusy; tie sa na konci zlepia cez
`ST_Union` (spatialite), a to len tie, ktoré sa hranice naozaj dotýkajú.
Keď spatialite chýba, beh pokračuje a povie to — v skalách budú vidieť
rovné rezy.

**Zoom vyberá `auto` a nie je to štvornásobok na zoom.** Sťahovanie áno, ale
obrysy nie — a tie sú to drahé. Namerané na Vysokých Tatrách:

| zoom | dlaždice | mozaika | obrysy |
|---|--:|--:|---|
| z17 | 13 815 | 0,91 mld. px | ~50 min (odhad) |
| z18 | 55 260 | 3,62 mld. px | **2 h 41 min a nedopočítalo sa** (sťahovanie pritom 12 min) |

Preto má `auto` okrem stropu na dlaždice aj rozpočet času (`options:
budget_min=…`, default 100) a zíde pod neho sám — na Vysokých Tatrách teda
zvolí z17. Nad rozpočtom sa výpočet zastaví s hláškou namiesto toho, aby
bežal do timeoutu celého jobu.

**Jedna trieda, jedna sivá.** Výstupom je jedno pásmo, teda žiadna plocha
vnútri inej (`options: plne=0` vráti pôvodné dve pásma). Diery **ostávajú** —
sú to medzery medzi vláknami siete žliabkov a práve ony sú tá štruktúra;
`options: zapln_diery=1` ich zaplní a detail tým zmizne.
V mape sa kreslí plnou farbou bez priehľadnosti, takže sa prekryv nikde
neprejaví a plochy sa nemusia ani zlepovať. Vedľajší efekt, ktorý sa počíta:
jedno pásmo namiesto dvoch je polovica prstencov na obtiahnutie, a to je tá
najdrahšia fáza celého behu.

**Zoom dlaždíc končí na 17** (~0,8 m na pixel). Na z18 sú to štvornásobne
dlaždice a obrysy rastú ešte rýchlejšie — 3,62 mld. pixelov bežalo 2 h 41 min
a nedopočítalo sa. Mapa z toho nemá nič: skaly sa zobrazujú do maximálneho
zoomu tak či tak, z vyššieho zdroja by bol ostrejší tvar, nie väčší rozsah.

**Sú z toho tri joby**, nie jeden — strop času totiž platí na job:

| job | strop | čo robí | čo po ňom ostane |
|---|--:|---|---|
| Stiahnuť dlaždice | 2 h | JPG z freemap.sk | cache + `dlazdice-tienovania-…` v sklade |
| Obrysy po blokoch | 3 h | raster tmavosti, `gdal_contour` po blokoch | cache s rozrobeným + `nahlad-…` v sklade |
| Skaly z tieňovania | 1 h | zlepenie blokov, švy, filter, vyhladenie | polygóny do skladu, čísla |

Sťahovanie býva desiatky minút a obrysy ďalšiu hodinu — dokopy sa to do
jedného rozpočtu zmestiť nemusí, a keď čas dôjde, padne aj to, čo už bolo
hotové. Rozdelené má každá časť celý svoj rozpočet a v Actions je vidieť,
na ktorej beh práve je. Vedľajší efekt, ktorý stojí za to: **každý job odloží
svoj výsledok hneď**, takže obrázky aj náhľad sú po ruke aj vtedy, keď to za
nimi ešte nedobehlo. A zmena `min_area` je odteraz posledný job (minúty), nie
celý výpočet odznova.

Dáta si joby podávajú cache: dlaždice pod vlastným kľúčom, rozrobené pod
druhým (takže sa gigabajty JPEGov neukladajú dvakrát). Zvolený zoom ide
z prvého jobu ďalej ako výstup, takže sa pri `auto` nehádá trikrát.

**Testovací režim** (switch `test`) vyreže zo stredu výrezu štvorec s 2 km²
a počíta na ňom terén — vrstevnice, skaly a tieňovanie; mapa okolo ostáva
celá podľa nastavení regiónu. Ladenie prahov je potom minúty namiesto hodín
— a beh do súhrnu vypíše obrázok s okolím (červený štvorec = testované
územie), súradnice a odkaz, ktorý otvorí hotovú mapu presne tam.

**Čo je hotové, sa nepočíta znova.** Rozrobené leží v cache dlaždíc, ktorá
sa ukladá aj po páde a po timeoute:

| checkpoint | čo ušetrí |
|---|---|
| stiahnuté dlaždice | celé sťahovanie |
| pásy rastra tmavosti | pás po páse |
| `bloky/b00000…` | **obrysy, blok po bloku** |
| `bands.geojsonl`, `rock.geojsonl` | zlepenie a filter |

Takže aj beh, ktorý sa nezmestí do troch hodín, sa dá dotiahnuť opakovaným
spustením — každé ďalšie nadviaže tam, kde predošlé skončilo. Po úspechu sa
rozrobené maže; `options: fresh=1` ho zahodí dopredu.

**Ako to dostať do mapy:** stačí **Build map** s `area: vysoke_tatry`
a `rock_source: tienovanie`. Nič sa dopredu púšťať nemusí – build si tú
pipeline zavolá sám, rovnako ako si sám dopĺňa chýbajúce výškové modely.
V behu z toho pribudnú do skladu `vysledky` na Drive dva balíky:
`dlazdice-tienovania-…` so stiahnutými JPG dlaždicami (to sú tie obrázky,
z ktorých sa skaly hľadali) a `nahlad-…` s mozaikou, maskou a histogramom na
doladenie prahov.

| chcem | ako |
|---|---|
| skaly z tieňovania, nech to trvá koľko chce | `rock_source: tienovanie` |
| iný zoom dlaždíc | `options: rock_img_zoom=18` |
| iné prahy / vyplnenie | `options: rock_img_options="fill=40 min_hole=5"` |
| aj najtenšie ryhy ako skalu (sivá deka pri z14) | `options: rock_img_options="open=0"` |
| len výrazné steny | `options: rock_img_options="open=6"` |
| presne ten asset, čo som si doladil ručne | `options: rock_img_asset=rockimg-…gpkg.zst` (vtedy sa nič nepočíta nanovo) |
| len rýchlo overiť, či to vôbec niečo nájde | switch `test` (predvolene zapnutý, viď nižšie) |

### Rýchly test: pár km² namiesto celého pohoria

Switch **`test`** vyreže **zo stredu zvoleného výrezu štvorec s 2 km²**
a na ňom spočíta to drahé — vrstevnice, skaly a tieňovanie. Z desiatok minút
sú minúty, čiže sa dá prah alebo interval overiť za jeden beh a nie za jeden
obed.

**Mapa pritom ostáva celá podľa nastavení regiónu.** Zvolený kraj je zvolený
kraj: cesty, vodstvo, značené trasy aj krajinné prvky vyjdú na celom
prešovskom (alebo hocijakom inom zvolenom) území a orezáva sa len to, čo sa
počíta z výškového modelu. Kedysi sa testom orezával celý región vrátane PBF
a bolo to lacnejšie o pár minút Planetilera — ale výsledok sa nedal poriadne
pozerať: dva kilometre štvorcové skál viseli nad prázdnom, bez ciest a bez
okolia, na ktorom by bolo vidno, či sedia. Kto chce orezať aj mapu, má na to
`options: crop_bbox=W,S,E,N` (dá sa aj spolu s testom).

**Predvolene je zapnutý.** Ostrý build na celý výrez ho chce odškrtnúť.
Opačné poradie znamenalo, že sa každé ladenie prahu platilo desiatkami minút,
kým si niekto spomenul dopísať voľbu do textového poľa — a to je práve tá
vec, ktorá sa preklikáva pri každom behu. Veľkosť štvorca sa naopak mení
zriedka, tak ostala voľbou (`options: test_km2=5`). Za miesto vo formulári
zaplatila mriežka `rock_res`, ktorá sa prestavuje len s iným zdrojom výšok;
je z nej tiež voľba (`options: rock_res=1`).

**Mapa sa otvorí rovno na tom štvorci.** Manifest nesie pri regióne okrem
`bbox` (celý kraj) aj `test_bbox` (štvorec) a viewer sa pri štarte nastaví na
ten druhý — inak by sa 2 km² skál hľadali očami v štyroch tisícoch km².
Posúvať sa dá kamkoľvek, mapa je celá. Polohu z adresy (`#map=…`) viewer
zahodí, len keď mieri mimo nasadeného regiónu, aby `F5` ani starý odkaz
neotvorili mapu nad cudzím krajom. V paneli je napísané, že vrstevnice, skaly
a tieňovanie sú len na tých 2 km² — nech kraj bez skál nevyzerá ako pokazený
build. Tieňovanie má navyše v štýle `bounds` toho štvorca, takže sa jeho
dlaždice mimo neho ani nepýtajú.

Kľúč dostane príponu `_test2`, takže si testovací beh **nesadne do tej istej
cache ani na tie isté uložené výsledky** ako ostrý.

**Testovací beh pregenerúva vždy všetko**, aj keď je `rebuild: nic`. Ladíš
ním prah, interval alebo kód – a keby sa výsledok vrátil z cache, videl by si
to, čo vyšlo naposledy, a ladil by si ducha. Kľúč cache síce nesie nastavenia
aj otlačok skriptov, ale nie všetko, a pár km² prepočítať stojí minúty, kým
jedno takto stratené kolo ladenia stojí viac. Cache ostrého behu je pritom
v bezpečí: v kľúči terénových vrstiev je bbox výpočtu a ten je pri teste
bboxom testovacieho štvorca.
Platí to aj pre skaly z tieňovania – tá podpipeline dostane `fresh=1`, takže
nenadviaže na rozrobené obrysy z minulého behu. Zo stiahnutých **vstupov**
(PBF, DEM dlaždice, JPG dlaždice tieňovania, Planetiler, glyfy) sa nezahadzuje
nič: nie sú to výsledky a v kľúči majú dátum alebo otlačok zdroja.

Beh do súhrnu vypíše, kde ten štvorec je:

- **obrázok** s okolím (podklad je tieňovanie, červený štvorec = testované
  územie, modrý = celý výrez) — nasadí sa spolu so stránkou, takže ho súhrn
  ukáže priamo;
- **súradnice** stredu aj bbox;
- **odkaz do hotovej mapy** na tie súradnice, plus OSM a Freemap na porovnanie.

Bez toho je totiž „nenašlo ani jednu skalu" nečitateľné: nevie sa, či sú
prísne prahy, alebo len štvorec padol na lúku pod lesom.

| chcem | ako |
|---|---|
| iná veľkosť | `options: test_km2=5` |
| ostrý beh na celom výreze | odškrtnúť switch `test` |
| iné miesto než stred výrezu | `options: test_at=20.30,49.24` (`lon,lat`) |
| to isté v samostatnom workflowe so skalami z tieňovania | výber `test: 2` v „Skaly z tieňovaných dlaždíc“ (tam je to počet km², nie switch) |

Dlaždice majú vlastnú cache podľa výrezu a zoomu, takže druhý build z
dobrovoľníckeho servera freemap.sk neťahá nič. Tieňovanie na **celý región**
build odmietne hneď v príprave – dlaždice sú cudzie a na kraj by ich boli
státisíce.

Keby v sklade aj tak nič nebolo (napr. keď ten job spadol), build to povie
a **nespadne späť na skaly z DEM** – tichá zámena jedného zdroja za druhý by
bola horšia než zastavenie.

Vrstva je tá istá `rock` v tých istých dlaždiciach (`{región}-rocks.pmtiles`),
takže štýl netreba meniť. Líši sa len atribút: skaly z DEM majú `slope`
(stupne sklonu), skaly z obrázka `dark` (o koľko stupňov šedej pod
referenciou). V manifeste je `rock_source`, takže je v mape vidieť, odkiaľ
tie plochy sú.

#### Zdroj výšok sa vyberá zvlášť pre každú vrstvu

Tri výbery vo formulári – `contour_source` (vrstevnice), `rock_source`
(skaly) a `shading_source` (tieňovanie a 3D terén). Ponúkajú tie isté modely:

| hodnota | model | mriežka | pokrytie | stav |
|---|---|--:|---|---|
| **`sonny`** (default) | Sonny's LiDAR DTM | 20 m | celý región | overené |
| **`dmr35`** | ÚGKK DMR 3.5 (otvorené dáta) | **10 m** | celý región | **overené** ✓ |
| **`dmr5`** | ÚGKK DMR 5.0 (LiDAR) | **1 m** s výrezom, **5 m** na celý región | oboje | naplniť *DMR 5.0 z Drive* ✓ |
| `ziadne` | – | – | – | vrstva sa negeneruje |

Navyše: `rock_source: tienovanie` neberie výšky vôbec – vezme hotové polygóny
z workflowu *Skaly z tieňovaných dlaždíc*.

Vrstvy môžu mať **rôzny model naraz** – napríklad vrstevnice zo `sonny`
a skaly z `dmr5`. Build vtedy stiahne oba (každý do `dem/<zdroj>/`) a v mape
je pri každej vrstve atribúcia toho modelu, z ktorého naozaj je.

**`dmr5` má dve podoby a rozhoduje rozsah, nie ďalší výber.** S vyplneným
výrezom (`area`) si vezme `ugkk-<vyrez>.tif` z releasu `dem-ugkk` v **plnom
metrovom rozlíšení**; bez neho dlaždice `N49E019.tif` z `dem-dmr5` na **5 m**.
Je to ten istý LiDAR – pri 1 m má jedna 1°×1° dlaždica ~48 GB a strop assetu
je 2 GB, takže celý región v metri sa nemá kam uložiť. To je fyzika, nie
voľba, tak sa na ňu formulár nepýta.

> Boli to dva zdroje, `dmr5` a `ugkk`. Praktický rozdiel bol len ten, že sa
> dalo zadať `ugkk` bez výrezu a beh spadol na strážcovi – alebo `dmr5` na
> pohorie a build ticho vzal 5 m tam, kde bol k dispozícii meter.

Oba sklady plní jediný workflow – [*DMR 5.0 z
Drive*](#dmr-50-z-drive-145-gb-cez-http-range) (ETRS89 verzia, **toto si
`Build map` volá sám**, a to dvoma jobmi, lebo model má dve podoby). Záloha
z archívu ÚGKK (198 GB ZIP so sekvenčným čítaním) bola zrušená – Drive púšťa
spoľahlivo a Range na ľubovoľnom offsete je rádovo lacnejší.

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
teréne. (Pri `dmr5` s vyplneným výrezom to platí tiež, len tieňovanie sa robí
na celý región, takže tam vyjde jeho 5 m podoba.)

> **Dlaždice sú vo WGS84, nie v S-JTSK.** Zdrojový ZIP je v *S-JTSK / Krovak
> East North* — to hlási súhrn ako „CRS zdroja“ — ale `fetch-dem-open.py` ho
> pri krájaní prepočíta (`gdalwarp -t_srs EPSG:4326`). Overené na hotovej
> dlaždici z releasu: `N49E017.tif`, `GEOGCRS["WGS 84"]`, roh presne
> 17°E/50°N, 8826×8826 px, Float32, výšky 383–782 m.

### DMR 5.0 z Drive: 145 GB cez HTTP Range

Tá istá dátová sada, ale **ETRS89 verzia a bez ZIPu** — dva holé BigTIFFy na
Google Drive:

```
dmr5_etrs89.tif      156 108 150 990 B = 145,39 GiB   423 518 × 207 589 px, 1 m
dmr5_etrs89.tif.ovr   46 550 149 948 B =  43,35 GiB   pyramídy 2 … 256 m
```

**To, že nie sú v ZIPe, mení všetko.** V archíve ÚGKK je raster jedným
deflate prúdom a v deflate sa nedá skočiť dopredu — dá sa doň len rozbaliť od
začiatku, takže výrez na juhu Slovenska stojí prechod celým súborom. Tu má
každá dlaždica (128×128) vlastnú kompresiu a HTTP Range funguje na
ľubovoľnom offsete (overené na 20 GB aj 145 GB). **Číta sa len to, čo výrez
pretína** — Vysoké Tatry stoja rovnako ako Slovenský kras.

Georeferencia je priamo v GeoTIFF tagoch, nič sa nedopočítava:

| | |
|---|---|
| CRS | **EPSG:3046** — ETRS89 / TM zone N34 (cm 21° E, k₀ 0,9996, FE 500 000) |
| origin | X **191 148,0**, Y **5 497 220,0** (ľavý horný roh) |
| bunka | 1,0 × 1,0 m, Float32, nodata 3,4e38, LZW |

**Dve veci, ktoré to komplikujú, a ako sú vyriešené:**

1. **Drive klame o veľkosti.** Na `HEAD` vracia `content-length: 0`, takže
   GDAL súbor odmietne (`GetFileSize()=0` → „not recognized as a supported
   file format"). Na `Range` GET pritom odpovedá správne. Rieši to
   [`workers/drive-serve.py`](workers/drive-serve.py) — malý HTTP server na
   localhoste, ktorý tú jednu hlavičku opraví a Range requesty prepája ďalej.
   Podáva **oba** súbory pod jedným menom, takže si GDAL nájde `.ovr` ako
   sidecar sám: `gdalinfo` potom vypíše všetkých 8 úrovní, otvorenie 145 GiB
   trvá **8 s** a stojí 9 požiadaviek / 0,3 MB.

2. **Limituje latencia, nie šírka pásma.** Jeden Range request trvá rádovo
   0,1–1 s bez ohľadu na veľkosť. Zmerané na 48 náhodných výrezoch po 400 kB:
   1 vlákno 1 143 ms/req, 8 vlákien 147 ms/req, 24 vlákien 68 ms/req. Preto
   sa okno **krája na bloky prichytené na cieľovú mriežku** a číta sa
   súbežne (`jobs`, default 12). Výrez 5,2 × 5,6 km pri 1 m: **1,2 min,
   0,11 GB, 697 požiadaviek.**

**Číta sa prihlásený ako vlastník dát, a inak sa nečíta vôbec.** Model leží
v **priečinku** na Drive (`FOLDER_ID` vo [`workers/dmr5-drive.py`](workers/dmr5-drive.py))
a súbory sa v ňom hľadajú podľa mena — presun modelu inam je tak zmena jedného
čísla. Čo v priečinku je, ale povie len Drive API prihlásenému účtu, takže
verejný odkaz (s denným limitom sťahovania, ktorý zdieľajú všetci, kto naň
siahnu) tu už nie je náhradná cesta. Token vlastníka v repository secrete
**`GDRIVE_CREDENTIALS`** má navyše ten limit rádovo vyšší; číta sa cez Drive
API s `Authorization: Bearer`.

Vyrobí sa raz, na vlastnom počítači:

```bash
python3 workers/drive-auth.py --login --client-id=… --client-secret=…
# vypísaný JSON → Settings → Secrets and variables → Actions → GDRIVE_CREDENTIALS
python3 workers/dmr5-drive.py --auth-check      # ktorým účtom sa číta a či naň vidí
```

Klient je typu *Desktop app* z Google Cloud Console, rozsah práv `drive` —
pipeline z Drive nielen číta, ale aj ukladá cache buildu (viď nižšie), a na to
`drive.readonly` nestačí. **Publishing status appky musí
byť „In production"** — v „Testing" platí refresh token 7 dní a pipeline by
raz do týždňa spadla; pri type *Internal* (Workspace) to neplatí.

**Bez počítača** to spraví workflow *Prihlásenie na Drive (jednorazové)*
([`drive-login.yml`](.github/workflows/drive-login.yml)): prehliadač je
telefón, shell je runner. Token sa v ňom **nikde nevypíše** — log public
repozitára vidí ktokoľvek — ide zo súboru rovno do secretu `DRIVE_REFRESH`.
Prihlásenie sa dá podať aj po kusoch — `client_id` ako repository **variable**
`DRIVE_CLIENT` (nie je to tajné) a secrety `DRIVE_SECRET`, `DRIVE_REFRESH`,
lebo `client_secret` Google druhýkrát neukáže; nekompletná dvojica secretov
je chyba a `Lint workflows` ju zachytí.

Bez secretu beh spadne hneď a s návodom — nie po pol dni na vyčerpanom
limite. Podrobne (aj kam všade sa ten secret musí dostať, aj postup z telefónu)
v [`docs/pipeline.md`](docs/pipeline.md#prihlásenie-ako-vlastník-dát-secret-gdrive_credentials).

**Výšky sú elipsoidické, nie Bpv.** Maximum v súbore je 2 697,03 m, kým
Gerlachovský štít má 2 654,4 m n. m. — tých **+42,6 m je geoidová undulácia**.
Workflow ich preto predvolene prevádza cez EGM2008; kontrola na Gerlachu dá
po prevode 2 653,92 m, čiže rozdiel 0,5 m na 1 m mriežke. Na skaly a
tieňovanie by to bolo jedno (sklon sa geoidom nemení), na vrstevnice nie.

To je workflow **DMR 5.0 z Drive**
([`dmr5-drive.yml`](.github/workflows/dmr5-drive.yml)), jeden job:

```
area: <pohorie>       ─► out/ugkk-<pohorie>.tif  ─► sklad dem-ugkk
                          ▲ Build map: vrstevnice a skaly s rovnakým `area`
area: <bbox stupňov>  ─► out/N49E019.tif …       ─► sklad dem-dmr5
  + tiles: true           ▲ Build map: tieňovanie a 3D terén
area: cele_slovensko  ─► out/N49E019.tif …       ─► sklad dem-dmr5
                          ▲ to isté, ale rovno na celú krajinu
```

Výstup je **presne ten istý formát**, aký `workers/fetch-dem.sh` čaká už
dávno, takže Build map sa nemení ani o riadok.

**Nespúšťaš to ručne.** Workflow je volateľný a `Build map` si ho zavolá sám
(joby `Doplniť DMR 5.0 (výrez…)` a `Doplniť DMR 5.0 (dlaždice)`), keď mu
v sklade chýba to, čo si vypýtal. Dva joby preto, že `dmr5` má dve podoby
a chýbať môžu naraz: vrstevnice a skaly čítajú výrez v plnom rozlíšení,
tieňovanie 1° dlaždice na 5 m – to sa robí na celý región, kde 1 m verzia
neexistuje.

Dlaždice sa dopĺňajú **po celých stupňoch**: meno `N49E020.tif` je sľub o celom
stupni a build si ju podľa mena hľadá, takže polovičná dlaždica by v ďalšom
behu prešla kontrolou a tieňovanie by ticho skončilo v polovici mapy. Stojí to
rádovo pol hodiny a ~2 GB z Drive na stupeň – ale raz.

#### Výstup je vstup pre Build map

Toto je celý zmysel workflowu — čo z neho vypadne, z toho vie `Build map`
počítať **vrstevnice, skaly aj tieňovanie**:

| `area` | mriežka | výsledok | sklad | v Build map |
|---|--:|---|---|---|
| `cele_slovensko` | 5 m | dlaždice `N49E019.tif` | `dem-dmr5` | `dmr5` vo výbere vrstevníc/skál/tieňovania |
| pohorie | **1 m** | `ugkk-<pohorie>.tif` | `dem-ugkk` | `dmr5` vo výbere vrstevníc/skál + rovnaké `area` |

Pomenúvacia schéma aj formát sú tie isté ako u Sonnyho a DMR 3.5, takže
[`workers/fetch-dem.sh`](workers/fetch-dem.sh) sa pri čítaní vôbec nevetví —
rozhoduje len meno releasu. Build mapy sa nemusí učiť nič nové. Presné
nastavenia vypíše workflow do súhrnu behu, aby sa nemuseli hádať.

**Licencia ÚGKK:** voľné použitie vrátane komerčného pri uvedení zdroja
(ÚGKK SR) — atribúcia je v [`poc/web/themes.js`](poc/web/themes.js).

**Spúšťaš len jednu pipeline.** `Build map` sa sám pozrie, čo mu v sklade
chýba, a doplnenie si spustí ako svoju úlohu. Ručne netreba spúšťať nič –
vrátane `dmr5`, ktorý sa dopĺňa cez `DMR 5.0 z Drive` (číta cez HTTP Range len
to, čo územie pretína, takže to nie je „prekvapenie na osem hodín", ako keby sa
mal sťahovať celý model).

```
Build map
  └─ check-dem        čo chýba pre vrstevnice / skaly / tieňovanie?
       ├─ (výrez chýba)    → Doplniť DMR 5.0 (výrez)    ← spustí sa sám
       │                       DMR 5.0 z Drive, area: <pohorie>
       │                       → ugkk-<pohorie>.tif do dem-ugkk
       ├─ (dlaždice chýbajú) → Doplniť DMR 5.0 (dlaždice)  ← spustí sa sám
       │                       DMR 5.0 z Drive, area: <bbox stupňov>,
       │                       tiles: true → N49E020.tif do dem-dmr5
       ├─ contours    stiahne výrez z releasu a počíta
       └─ terrain     stiahne dlaždice z releasu a tieňuje
```

Ktorý sklad a ktoré súbory ktorá vrstva potrebuje, hovorí jediné miesto –
[`workers/dem-target.py`](workers/dem-target.py). Pýta sa doň aj kontrola
(`workers/check-dem.sh`), aj sťahovanie (`workers/fetch-dem.sh`); kým to bolo
napísané dvakrát, rozišlo sa to a build kontroloval jeden sklad, kým sťahoval
z druhého ([beh 31307163093](https://github.com/skifahrer/fricomaps/actions/runs/31307163093)).

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

**Prakticky:** `dmr5` **s výrezom** (teda plné metrové rozlíšenie) funguje len
vtedy, keď je ten výrez už v releasi `dem-ugkk`. Dostane sa tam **workflowom
[*DMR 5.0 z Drive*](#dmr-50-z-drive-145-gb-cez-http-range)** (`area:
<pohorie>`) – to si build spúšťa sám – alebo jednorazovým exportom zo
ZBGIS Mapového klienta (Terén → Export údajov → DMR 5.0, do 400 km²)
a nahratím ako `ugkk-<vyrez>.tif`. Inak build spadne späť na Sonnyho a napíše
to.

> **Dodatok (august 2026): tá cesta sa našla, dokonca dvakrát.** Všetko nižšie
> o mŕtvom `zbgis.skgeodesy.sk` platí – ale to isté DMR 5.0 leží aj na
> `opendata.skgeodesy.sk` ako jeden 198 GB ZIP, a jeho ETRS89 verzia na Google
> Drive ako dva holé BigTIFFy. Odtiaľ sa vziať dá. Viď [DMR 5.0 z
> Drive](#dmr-50-z-drive-145-gb-cez-http-range).

Build to preto **neskúša naslepo**: `fetch-dem-ugkk.py` sa najprv spýta na
dostupnosť hostiteľa a keď neodpovedá, ImageServer ani WCS už nerozbieha —
všetky sú na tej istej doméne a každý z nich stojí štyri profily prehliadača
plus curl.

**1 m sa dá len na výrez.** Celý kraj má pri 1 m 16 miliárd buniek, čo je 64 GB
vo Float32 – a to sa nezmestí na runner (voľných má ~60 GB).
Preto si `dmr5` podobu vyberá podľa rozsahu: s vyplneným `area` ide plné 1 m,
bez neho dlaždice na 5 m. Nie je to teda čo zakázať, ale čo dopočítať:

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

Skaly sa **nezačnú počítať, kým sa nevypíše plán**: čo sa ide robiť, nad čím,
za koľko a s akými stropmi (`ROCK_BUDGET_MIN`, default 30 min):

```
── Plán výpočtu skál ────────────────────────────────
  územie          208×111 km (obdĺžnik v EPSG:3035)
  mriežka         1 m
  buniek          19.60 mld.
  častí           144 z 170 (26 mimo územia sa preskočí), po 12.2×11.1 km
  odhad sklon     1:04:02
  odhad obrysy    1:33:19
  odhad SPOLU     2:37:21  (rozpočet 0:30:00)
  mozaika na disk ~1.0 GB
  špička pamäte   ~13.4 GB
─────────────────────────────────────────────────────
::warning::Vektorizácia … potrvá odhadom ~1:33:19, čo je nad rozpočet
30 min – NEZASTAVUJEM ju, nechávam dobehnúť. Keď to má byť rýchlejšie:
hrubší sklad (rock_res) alebo menší výrez (area).
```

**Rozpočet je odhad, nie vypínač.** Nad ním sa to povie a počíta sa ďalej –
zastaviť vektorizáciu nemá čo zachrániť: je to jeden nedeliteľný priechod nad
celou mozaikou (kvôli dieram), takže zabitý `gdal_contour` nenechá ani
neúplný výstup. Zastavenie znamenalo to isté ako timeout jobu, len skôr, a
k tomu bez šance, že by beh dobehol. Stropy, ktoré platia, sú timeout jobu
`rocks` (3 h) a pamäť.

Presne z toho istého odhadu ale vyberá `rock_res: auto` mriežku – zoberie
najjemnejšiu so ✓ (zdola stropenú desatinou bunky DEM), takže cesta, ako sa
do rozpočtu zmestiť, je tá automatická.

| územie | `rock_res` | buniek | odhad | |
|---|--:|--:|--:|---|
| Prešovský kraj | 1 m | 19,60 mld. | 2:37:21 | ✗ auto ho nezvolí |
| Prešovský kraj | **2 m (auto pri Sonnym)** | 5,27 mld. | 0:42:18 | ✓ |
| Prešovský kraj | 3 m | 2,57 mld. | 0:20:38 | ✓ |
| Tatry | 1 m | 1,34 mld. | 0:10:46 | ✓ |
| Vysoké Tatry | 1 m | 0,71 mld. | 0:05:44 | ✓ |
| Belianske Tatry | 1 m | 0,23 mld. | 0:01:49 | ✓ |

Konštanty odhadu sú **namerané na runneri**, nie odhadnuté: sklon
5,1 mil. buniek/s, obrysy 121 tis. zdrojových buniek/s. Keď sa beh s tou
druhou rozíde viac než 3×, povie to na konci sám – vtedy sa má prepísať.

#### Počas výpočtu je vidieť, čo sa deje

```
  [12/144] sklon – 0:07:41 za sebou, zostáva ~0:24:26, mozaika 96 MB
── Vektorizácia sklonu (gdal_contour -p) ────────────
  vstup           slope-chunks/slope-r2.vrt
  číta sa         5.27 mld. buniek skladu (2 m) – toto rozhoduje o čase
  trasuje sa      3.37 mld. buniek na 2 m
  prahy           sklon ≥ 50° (raster je v stotinách stupňa)
  odhad           ~0:25:05 pri 121 tis. buniek/s (rozpočet 30 min)
  stropy          pamäť 12 GB; čas NEOBMEDZENÝ – jeden priechod sa nedá
                  prerušiť a nadviazať, tak beží, kým nie je hotový
  postup          percentá po 2,5 % + tep každých 30 s
─────────────────────────────────────────────────────
▶ gdal_contour: gdal_contour -p -fl 5000.0 -amin smin -amax smax -f GPKG …
… gdal_contour: 30 % (beží 0:07:14, tempo 4.1 %/min, zostáva ~0:16:53 (koniec ~14:23))
… gdal_contour: beží 0:07:30, 32.5 %, zostáva ~0:15:35 (koniec ~14:23),
  pamäť 2.4 GB (špička 2.4 GB, strop 12.0 GB), CPU 99 % (priemer 97 %),
  disk 4210/640 MB (+8.1/+1.2 MB/s), výstup 1129 MB (+2.1 MB/s)
✔ gdal_contour: hotovo za 0:24:29, výstup 612 MB, špička pamäte 2.4 GB,
  CPU 0:23:51 (97 %), disk 4210 MB čítania / 640 MB zápisu
```

Pri sklone ide riadok po každej časti s odpracovaným časom a odhadom zvyšku.
`gdal_contour` hlási percentá **po 2,5 %** (bodky medzi desiatkami sú tri,
takže pri hodinovom behu je to správa každé tri minúty namiesto každých
dvanástich) a nezávisle od oboch beží **tep** každých 30 s
(`ROCK_HEARTBEAT_S`): kde to je, kedy skončí, pamäť aj jej špička, CPU teraz
aj v priemere, koľko číta a zapisuje a ako rastie výstup. `CPU 99 %` znamená
„počíta, pomôže len menej práce", `CPU 0 %` znamená, že problém je inde.
Riadok `✔` na konci nesie namerané čísla – z nich, a nie z odhadu, sa opravujú
konštanty.

Keď pamäť prekročí `ROCK_MAX_RSS_GB` (12 GB), tep výpočet zastaví s hláškou –
to je lepšie než tiché zabitie runnera na OOM, po ktorom v logu nie je nič.
**Čas takú poistku nemá** a je to zámer: strop času by zahodil hodiny práce
a nenechal ani neúplný výsledok.

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
čím sa skaly vypnú) a `rock_slope` (od akého sklonu je terén skala, default
50°); mriežka obrysu je voľba `options: rock_res=…` (číslo v metroch alebo
`auto`, default `auto`).
Ostatné ladenie je v `env:` na začiatku
[build-map.yml](.github/workflows/build-map.yml): `ROCK_SIMPLIFY` (0 = presný
obrys), `ROCK_SMOOTH` (koľkokrát zaobliť rohy, 0 = vypnúť),
`ROCK_CLIFF_PLUS` (o koľko ° nad prahom začína trieda `cliff`),
`ROCK_CHUNK_CELLS` (koľko buniek naraz pri počítaní sklonu), `ROCK_ALGO`
(verzia algoritmu v mene uloženého assetu).

V mape z toho sú **tmavšie sivohnedé plochy** (#8a8578, farba papierovej
horskej mapy) kreslené *pod* tieňovaním aj *pod*
vrstevnicami. Poradie je zámerné a v tomto poradí: skala je tvar terénu, takže
cez ňu musí prejsť tieňovanie (inak je práve stena v mape plochá škvrna bez
reliéfu), a vrstevnica musí prejsť cez oboje (inak nie sú výšky tam, kde je
terén najstrmší). Farba `Skalné plochy (plná výplň)` je v palete v skupine
**Vrstevnice a skaly**, takže sa dá v developer móde doladiť ako čokoľvek iné.

**Hotové skaly sa neprepočítavajú.** Uložia sa do releasu `dem-rocks` pod
menom, ktoré nesie región aj nastavenia
(`rock-{región}-s{prah}-g{mriežka}-{algo}.gpkg.zst`), takže ďalší build s tými istými
nastaveniami ich len stiahne – sekundy namiesto desiatok minút. Iné nastavenia
dajú iné meno súboru, takže sa nikdy nepomiešajú. Ako to prepočítať nanovo,
hovorí [Pregenerovanie](#pregenerovanie).

Hotové skaly a vrstevnice si každý build odloží aj do **skladu `vysledky`**
(`teren-{región}-s{prah}-g{mriežka}-{dátum}-r{beh}.tar.zst`) – aj s GPKG
geometriou, takže sa dajú stiahnuť a pozrieť v QGISe bez ďalšieho buildu.
Kedysi to bol artefakt behu s 90-dňovou lehotou; do GitHubu sa už nepublikuje
nič a sklad na Drive prerieďuje na tých istých 90 dní workflow *Upratať cache*.

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
| `rocks_rebuild` | skaly – zmaže cache aj súbor v sklade `dem-rocks` (vrstevnice sa prepočítajú s nimi, sú lacné) |
| `terrain_rebuild` | tieňovanie a 3D terén – zmaže cache aj súbor v sklade `dem-terrain` |

Prečo to musí najprv mazať: **existujúci záznam cache sa nedá prepísať.**
Kľúč, ktorý raz existuje, si drží starý obsah, takže bez zmazania by sa
prepočítaná verzia zahodila a ďalší build by dostal späť tú starú. Preto každý
`*_rebuild` začne tým, že príslušný záznam zmaže (aj jeho variant `-rocks`,
lebo skaly majú vlastný job a tým aj vlastný záznam).

Ostatné cache (PBF, Planetiler, DEM dlaždice, glyfy a sprity) sa
nepregenerúvajú vôbec – sú to stiahnuté dáta, nie výpočet, a majú v kľúči buď
dátum, alebo otlačok zdroja.

### Hotové dáta ležia v sklade na Google Drive, nie v releasoch

Do GitHubu nejde nič, čo má prežiť beh — **ani release, ani artefakt**. Osem
druhov drahých medzivýsledkov kedysi ležalo v releasoch (`dem-sonny`,
`dem-dmr35`, `dem-dmr5`, `dem-ugkk`, `dem-terrain`, `dem-rocks`,
`dem-rocks-img`, `dem-slope`) a medzivýsledky na pozretie v artefaktoch
s 30- až 90-dňovou retenciou. Oboje je teraz v **sklade na Google Drive** —
na tom istom účte, ktorý drží DMR 5.0, cache buildu aj hotové mapy.

```
<koreň>/dem-dmr5/N49E020.tif             <koreň> = `fricomaps-sklad`
<koreň>/dem-ugkk/ugkk-vysoke_tatry.tif   v My Drive vlastníka tokenu
<koreň>/vysledky/teren-…-r73.tar.zst
         sklad     meno — to isté, aké mal asset releasu
```

Prečo: release má na jeden asset **strop 2 GB**, ktorý pipeline tvaroval
zvonku, a hotové dáta v releasoch verejného repozitára vyzerajú ako vydanie
softvéru, ktorým nikdy neboli. Ten strop teda odpadol — **dve podoby DMR 5.0
však ostávajú**, tie nedržal release, ale runner: jedna 1°×1° dlaždica má
v metri ~48 GB a voľných je ~60 GB.

Čo sa tým **nezmenilo**: mená súborov (`N49E020.tif` ďalej hovorí „tento celý
stupeň je tu"), ani to, ktorý sklad ktorá vrstva hľadá. Celý rozpis je vo
[`workers/drive-store.py`](workers/drive-store.py).

Krátkodobé artefakty (`site-*`, `steps-*` s `retention-days: 1`) ostávajú a nie
sú publikovanie — sú to prepravky, ktorými si joby jedného behu podávajú kusy
`_site`. Čokoľvek s dlhšou retenciou ide do skladu `vysledky` cez
[`workers/publish-results.sh`](workers/publish-results.sh) a *Lint workflows* to
kontroluje. Staré releasy, ich tagy aj artefakty zmaže *Upratať GitHub*
([`cleanup-actions.yml`](.github/workflows/cleanup-actions.yml)) v režime
`releasy_a_artefakty`.

### Cache leží na Google Drive

GitHubová cache má na repozitár **10 GB** a keď sa naplní, nič nepovie — ticho
vyhodí najstaršie záznamy. Jeden výrez do nej pritom ukladá desiatky GB (DEM
dlaždice, sklad častí sklonu, vrstevnice, tieňovanie, dlaždice tieňovania),
takže si záznamy vyhadzovali navzájom a hodinové výpočty sa rátali odznova bez
toho, aby bolo na čom to vidieť — build je zelený, len trvá hodinu namiesto
minút.

Preto záznamy ležia na Google Drive, na tom istom účte, ktorý drží DMR 5.0.
Kroky vo workflowoch vyzerajú rovnako ako predtým (`.github/actions/cache-restore`
a `cache-save` namiesto `actions/cache/*`) a **sémantika je tá istá**:
`cache-hit` len pri presnej zhode kľúča, `restore-keys` ako predpony, existujúci
kľúč sa neprepisuje. Celý rozpis je vo
[`workers/drive-cache.py`](workers/drive-cache.py).

Dve veci, ktoré z toho plynú:

- **Token na Drive musí vedieť zapisovať** (rozsah `drive`, nie
  `drive.readonly`). `python3 workers/drive-cache.py --check` povie, či vie —
  aj koľko miesta na účte ešte je.
- **Nič sa nemaže samo.** GitHub staré záznamy vyhadzoval sám, Drive nie.
  Preriedi ich workflow *Upratať cache*
  ([`cleanup-cache.yml`](.github/workflows/cleanup-cache.yml)) — raz za týždeň,
  alebo ručne. Ten istý workflow vyprázdni aj GitHub cache, ktorú už nikto
  nehľadá.

### Hotová mapa ide aj na Drive ako ZIP

Okrem GitHub Pages sa každý build publikuje do priečinka na Google Drive: celý
web (dlaždice, štýly, vrstevnice, skaly, tieňovanie, fonty, sprity) ako **jeden
ZIP**. Priečinok hovorí, čoho sa mapa týka, a čo chýba, sa vyrobí:

```
<koreň>/slovensko/presovsky/vysoke_tatry/…zip
         krajina  kraj      výsek   (úrovne, čo nedávajú zmysel, sa vynechajú)
```

**Meno nesie, čo v tej mape je** — do jedného priečinka padajú desiatky behov
s rôznymi nastaveniami:

```
presovsky-vysoke_tatry-test2km2-z16-vrstevnice_dmr5_10m-skaly_dmr5-tienovanie_sonny-trasy-prvky-20260810-0748-r73.zip
```

Teda výrez, rýchly test a jeho veľkosť, zoom dlaždíc, ktoré vrstvy sú vnútri
a **z ktorého modelu sú spočítané** — a to podľa toho, čo build naozaj použil,
nie čo bolo vo formulári. Vrstva, ktorá v mape nie je, sa píše tiež
(`bez_skal`). Dátum, čas a číslo behu na konci robia meno jedinečným, takže sa
dva behy nikdy neprepíšu.

Robí to [`workers/publish-map.py`](workers/publish-map.py) a vypnúť sa to dá
voľbou `publish=false` v poli `options`.

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
| Tieňovanie a 3D terén | 0:00:31 | 24 118 PNG dlaždíc do z13, 96 MB (sklad dem-terrain) |
| Mapové dlaždice (Planetiler) | 0:18:20 | maxzoom 16, 421 MB |
| Ikonky (SDF sprity) | 0:00:09 | sady: maki temaki osm-bright, štýl používa temaki (z cache) |

*(Ukážka – čísla sa líšia podľa regiónu a nastavení.)*

Pod tabuľkou je **detail skál** za tento beh (počet plôch, mriežka, bunka DEM,
najmenšia/priemerná/najväčšia plocha, koľko km² skalného terénu spolu) a
prehľad, **čo prišlo z cache a čo sa naozaj počítalo** – takže sa hneď vidí,
či mal beh trvať hodinu, alebo minútu.

#### Nastavenia tohto behu

Súhrn vypíše aj celý formulár, s akým bol beh spustený, a označí, čo bolo iné
než predvolené:

| pole | hodnota | |
|---|---|---|
| `region` | `presovsky` | default |
| `area` | `mala_fatra` | **iné než default** |
| `test` | `true` | default |
| `rock_slope` | `45` | **iné než default** |

Je to preto, že formulár *Run workflow* sa vždy otvorí s predvolenými
hodnotami – GitHub si nepamätá, s čím si beh pustil naposledy, a v API to
nikde nie je. Keď teda chceš beh zopakovať a zmeniť jedinú vec (typicky
`rebuild`), z tohto bloku vidíš, čo treba nastaviť späť. Predvolené hodnoty
si blok číta priamo z workflowu ([workers/summary-inputs.py](workers/summary-inputs.py)),
takže sa s formulárom nemôžu rozísť.


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

Vo workflowe: `trails` (zap/vyp) a `trails_maxzoom` (default 14). Okrem
turistiky, cyklo, bežiek a jazdeckých trás sa berú aj **ferraty**
(`route=via_ferrata`) – vlastný druh, lebo po ferrate sa nedá ísť bez výstroja
a od turistickej značky sa má odlíšiť na prvý pohľad. V mape sa
trasy vypínajú prepínačom **Značené trasy** v paneli ⚙. Job sa **necachuje** –
celé sú to pár minút a závisí to od PBF, ktoré sa mení denne.

Súhrn buildu píše, koľko trás sa v území našlo, koľko z nich má názov, po
koľkých cestách vedú a koľko z tých ciest nesie viac trás naraz.

## Krajinné prvky (čo OpenMapTiles nemá)

**Schéma sa pozerá len na tridsať kľúčov.** V celom
`openmaptiles/planetiler-openmaptiles` sa slovo `embankment` nevyskytuje ani
raz. To isté platí pre `barrier` ako líniu (múr, plot, živý plot), `power`
(elektrické vedenie), `man_made=cutline`, `piste:type` (zjazdovky),
`natural=cave_entrance` aj `man_made=tower` (rozhľadňa sa do dlaždíc dostane
jedine vtedy, keď má navyše `tourism=viewpoint`). Nedá sa to zapnúť – tie
prvky v základných dlaždiciach jednoducho **nie sú**.

Preto sa z toho istého PBF ťahajú druhýkrát, vlastnou schémou a do vlastného
`.pmtiles` – rovnaký vzor ako značené trasy a skaly:

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
| `feature_line` | **násyp**, zárez, múr, hradby, plot, živý plot, elektrické vedenie, priesek, nadzemné potrubie, stromoradie, priehradný múr, hať, výmoľ | 11–15 |
| `feature_area` | parkovisko, skládka, halda, hospodársky dvor, skleníky, opustený priemysel, kamenné pole | 11–14 |
| `feature_point` | prameň, vodopád, jaskyňa, závrt, rozhľadňa, stožiar, vodojem, kríž pri ceste, pomník, archeologické nálezisko, štôlňa, útulňa, horský priechod, núdzový bod, geodetický bod | 11–15 |
| `piste` | zjazdovka, bežkárska trať, skialp, sánkarská dráha – čiara aj plocha, s obťažnosťou | 11 |

**Zoomy sú tu hlavné rozhodnutie, nie estetika.** Plotov je v OSM viac než
všetkých ciest dokopy, takže idú až od z15; vedenie vysokého napätia je
v otvorenej krajine orientačný bod na kilometre, takže od z11. Nie je to vkus,
je to priamo veľkosť súboru.

**Násyp a bralo sa kreslia zúbkami.** Kolmé čiarky MapLibre nevie, takže sa
robia druhou čiarou: širokou, prerušovanou a odsunutou nabok (`line-offset`),
z čoho pri hrane ostanú krátke hrubé kúsky. Kladný offset je vpravo v smere
čiary a presne tam je podľa konvencie OSM dolná strana.

**Zjazdovka je raz čiara a raz plocha.** Uzavretá cesta s `piste:type` vyjde
ako plocha aj ako čiara, takže dostane výplň s obrysom; otvorená len čiaru
(os zjazdovky). Farba je podľa `piste:difficulty` – modrá, červená, čierna,
tie isté odtiene ako pri značkách trás, aby sa mapa nerozpadla na dve sady
farieb.

### Čo sem NEPATRÍ, hoci to tak vyzerá

`natural=cliff`, `ridge` a `arete` v základných dlaždiciach **sú** – Planetiler
ich dáva ako línie do vrstvy `mountain_peak` (od z13). Chýbala len kresba
v štýle; horšie, symbolová vrstva „Vrcholy hôr" im dávala doprostred
trojuholníček vrcholu aj s popiskom, lebo `cliff` nebol medzi vylúčenými
triedami. Teraz sú z nich bralné hrany so zúbkami a čiarkované hrebene.

Podobne sa v štýle opravilo aj to, čo v dlaždiciach bolo od začiatku a nekreslilo
sa: cesty vo výstavbe (`*_construction`), plochy vo vrstve `transportation`
(námestia, pešie zóny, telesá mostov, plošné móla), brody (`brunnel=ford`),
nástupištia (`subclass=platform`), plocha priehrady (`landuse class=dam`)
a kosodrevina odlíšená od lúky (`landcover subclass=scrub/heath/fell`).

### Ovládanie

Vo workflowe: `options: features=false` (vypnutie) a `features_maxzoom`
(default 15). Nižšia hodnota nie je zakázaná, ale **ticho zahodí** triedy
s vyšším `min_zoom` – Planetiler o tom nepovie nič, preto na to job
upozorní varovaním. Pri z14 takto chýbali ploty, živé ploty, geodetické body
a hraničné kamene; z15 stojí 1,6× väčší súbor (nameraná Andorra: 248 kB →
394 kB), čo sú pri jednotkách MB drobné. V mape sa prvky vypínajú prepínačom **Krajinné prvky**
v paneli ⚙. Vrstvy sú v developer móde v skupine **Krajinné prvky (mimo
schémy)**, farby v rovnomennej skupine palety. Job sa **necachuje** a beží
súbežne so všetkým ostatným; podiel na rozpočte stránky je
`BUDGET_FEATURES_PCT` (4 %).

## Typy máp – čo ktorá mapa ukazuje

Jedna mapa nemôže byť dobrá turistická aj dobrá cestná naraz. Turista chce
skaly, chodníky a čo najviac detailu; vodič chce cesty, pumpy a odpočívadlá
a vrstevnice mu majú len naznačiť kopec. Preto sa z jedného zoznamu vrstiev
generuje **päť máp**, každá s vlastným profilom
([poc/web/map-types.js](poc/web/map-types.js)):

| typ mapy | čo ukazuje | čo zámerne nie |
|---|---|---|
| **Turistická** (predvolená) | skaly od z8, turistické chodníky od z10, značené trasy od z8, vrcholy od z8, plný detail | lyžiarske trasy a strediská |
| **Lyžiarska** | lyžiarske a bežkárske trasy od z8, vleky a lanovky od z9, strediská a ich body, skaly | ostatné značené trasy až od z14 a stlmené |
| **Cestná** | cesty, pumpy, nabíjačky, odpočívadlá, servis a parkoviská od z10; vrstevnice **len po 50 m** a stlmené | vrstevnice po 10 m, skaly, chodníky, značené trasy, tieňovanie; krajina je stlmená, bežné POI až od z15 |
| **Historická** | hrady, zámky, pamiatky, bane, štôlne, haldy a lomy od z9, POI od z12, terén ako pri turistickej | turistické chodníky, schody a značené trasy |
| **Základná (všetko)** | všetky vrstvy tak, ako ich generuje štýl – na ladenie | – |

Profil je len **predvolený stav**: v developer móde sa dá každej mape
nastaviť po svojom (viď nižšie), takže „na cestnej mape toto nechcem"
neznamená „nikde to nechcem".

Typ mapy sa vyberá v paneli ⚙ (výber **Typ mapy**) a pamätá si ho prehliadač.
Pipeline generuje `styles/{región}-{typ mapy}-{téma}.json` pre každú
kombináciu – teda 5 × 4 = 20 štýlov, plus predvolený typ aj pod pôvodným
menom `{región}-{téma}.json`, aby fungovali staršie odkazy.

### Terénna trojica

Tri farby, ktoré robia z mapy horskú mapu, sú v každej téme z tej istej rodiny –
prevzatej z papierovej horskej mapy:

| čo | farba | kde je v palete |
|---|---|---|
| podklad mapy (základná farba horského terénu) | **#dedcd1** svetlo béžovosivá s jemným zeleným nádychom | `Pozadie mapy` |
| skalnaté partie a sutiny | **#9c9286** teplá stredná sivohnedá | `Skaly / suť` (OSM) a `Skalné plochy (plná výplň)` (počítané z DEM) |
| vrstevnice | **#8b8676** tenké olivovosivé línie s popiskom výšky | `Vrstevnica`, `Hlavná vrstevnica`, `Popisok výšky` |

Nie sú to neutrálne sivé: celá trojica má ten istý teplý zemitý nádych (odtieň
okolo 45°, sýtosť do 10 %). Neutrálna sivá vedľa béžového podkladu vyzerá
domodra a mapa z toho vyjde studená.

Každá téma má **veľmi jemne iný** odtieň tej istej trojice, nie kópiu jednej
hodnoty: *Svetlá* je neutrálna, *Outdoor* o odtieň teplejšia a tmavšia (je to
turistická mapa), *Retro* o odtieň svetlejšia a *Tmavá* má tú istú rodinu
preloženú do tmy – teda neutrálne teplú, nie domodra ako predtým. Rozdiel je
pár krokov: dosť na to, aby sa témy dali rozoznať, málo na to, aby niektorá
vyzerala ako iná mapa.

Suť z OSM (`Skaly / suť`) sa kreslí s krytím 0,8, takže sa s podkladom mieša –
hodnota v palete je preto tmavšia než to, čo je v mape vidieť, a výsledok je
o odtieň svetlejší než počítané skalné plochy. To je zámer: suť je sypká
a svetlejšia než stena.

**Tematické body.** Každý typ mapy má skupinu bodov, ktorá je preň tá hlavná –
`poi-historic` (hrady, zrúcaniny, pamätníky, archeológia), `poi-mining`
(bane, štôlne, haldy, lomy), `poi-ski` (vleky, lanovky, požičovne, školy)
a `poi-road` (pumpy, nabíjačky, odpočívadlá, servis). Sú to samostatné vrstvy:
väčšie, s vlastnou farbou v palete a s prednosťou pri umiestňovaní popiskov,
takže sa zapnú skôr než ostatné POI a v developer móde sa ladia zvlášť.
Filtrujú sa podľa `class`/`subclass` z OpenMapTiles – zoznamy tried sú štedré,
trieda, ktorú dlaždice neobsahujú, sa jednoducho nikdy netrafí.

## Developer mode – ladenie mapy v prehliadači

Mapa sa dá doladiť priamo vo viewri, bez čakania na pipeline. Zapína sa
prepínačom **🛠 Developer mode** v paneli ⚙ (alebo cez `?dev=1` v URL).

| záložka | čo sa v nej dá |
|---|---|
| **Vrstvy** | všetkých ~140 vrstiev po skupinách, s druhom (plocha / línia / bod / popisok / 3D / reliéf). Filtre podľa druhu a hľadanie, zapnutie a vypnutie vrstvy aj celej skupiny, rozsah zoomu (pásik z0–z20 aj `od z` / `do z`), farby všetkých `*-color` vlastností, **ikona** pri symbolových vrstvách, **druh čiary**, hrúbka a krytie, **vzor** a **okraj**. Riadok sa rozklikne kliknutím na názov |
| **Prvky** | inšpektor: klik do mapy vypíše **všetko, čo je pod kurzorom** – naraz zo všetkých vrstiev, s celým obsahom dlaždice. Viď nižšie |
| **Paleta** | ~90 farieb aktuálnej témy po skupinách. Zmena farby prefarbí naraz všetky vrstvy, ktoré ju používajú |
| **Ikony** | sada ikoniek pre POI, vrcholy a letiská – s náhľadom, počtom obrázkov a licenciou |
| **POI** | ktoré triedy bodov sa zobrazujú (zoznam sa načíta z dlaždíc v aktuálnom výreze) |
| **Súbor** | stiahnutie, nahratie a vymazanie úprav |

### Každá mapa zvlášť

Developer mode ladí vždy **tú mapu, ktorá je práve na obrazovke** (výber *Typ
mapy* v paneli ⚙). Nad zoznamom vrstiev aj v záložke POI je preto prepínač
rozsahu:

| rozsah | kam sa úprava zapíše |
|---|---|
| **len táto mapa** | `maps.<typ mapy>` – platí len pre ňu (napr. „na cestnej mape nechcem vrstevnice po 10 m") |
| **všetky mapy** | `layers` / `poi` – platí pre všetky typy máp naraz |

Pri každom je v zátvorke počet úprav, ktoré ten priečinok drží. Vrstva
upravená v práve zvolenom rozsahu má v riadku modrú bodku. Zapnutie alebo
vypnutie vrstvy v rozsahu *všetky mapy* zároveň zruší výnimky nastavené
v jednotlivých mapách – inak by tlačidlo tvrdilo, že vrstvu zapína, a nič by
sa nestalo.

Keď vrstvu vypína profil typu mapy (lyžiarske trasy na turistickej mape),
zapnutie sa uloží ako výslovné `visible: true` – iba „prestať ju vypínať"
by tam nestačilo.

### Zoom: čo sa na ňom zobrazí a čo nie

Zoom nie je len informácia, ale hlavný nástroj: **nastav zoom a povedz, čo na
ňom má a nemá byť.**

- **Posuvník zoomu** (mapa tam skočí) + skratky `z4 z8 z10 z12 z14 z16 z18 z20`
  na zoomy, kde sa mapa láme. Posuvník sleduje aj bežné zoomovanie myšou.
- **Štítok s rozsahom v riadku** (`z13–16`, `z9+`, `vždy`) je **prepínač**:
  klik povie, či sa vrstva na aktuálnom zoome kresliť má, alebo nie. Rozsah
  ostáva jeden súvislý interval – zapnutie natiahne bližší koniec, vypnutie
  ustúpi tým, ktorý je bližšie. Keď by z rozsahu neostalo nič, vrstva sa rovno
  vypne.
- **Pásik zoomov z0–z20** v detaile vrstvy: jedna bunka = jeden zoom,
  zvýraznené sú tie, na ktorých sa vrstva kreslí, oranžový rámček je aktuálny
  zoom. Klik do bunky ju zapne alebo vypne – z pásika je hneď vidieť, čo
  vrstva robí.
- **Tlačidlá `od z…` / `do z…`** v detaile nastavia hranicu na aktuálny zoom,
  `⟲` vráti pôvodný rozsah.
- **Hromadne:** zaškrtni vrstvy a použi `Zobraziť od z…` alebo `Skryť na z…`.
- Hlavička skupiny má počítadlo `aktívne/všetky`, vrstvy orezané zoomom sú
  bledé a prepínač **len aktívne** schová zvyšok.

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

### Druh čiary a výplň plochy

Detail vrstvy je rozdelený na sekcie **Zoom → Farby → Ikona → Štýl čiary /
Štýl plochy → Okraj**, takže je vidieť, čo sa kde nastavuje.

**Štýl čiary** (línie): výber druhu čiary s **náhľadom** vedľa rozbaľovačky –
12 predvolieb: plná, čiarkovaná, dlhé čiarky, krátke čiarky, bodkovaná,
bodkovaná hustá, bodkovaná riedka, čiarka-bodka, **čiarka-bodka-bodka
(náučný chodník)**, šrafovanie železnice, priečky, rebrík lanovky. K tomu
hrúbka a krytie čiary. Malý chodník sa teda spraví bodkovaný a náučný
chodník čiarka-bodka-bodka jedným výberom – a keďže úprava vie ísť len do
jednej mapy, môže to platiť napríklad iba na turistickej.

**Štýl plochy** (plochy a 3D): krytie výplne + opakujúci sa **vzor** (18
predvolieb – šrafovanie, mriežka, bodky, vlnky, stromčeky, šupiny, tehly,
krížiky, priečky, šípky…) s vlastnou farbou, veľkosťou dlaždice, hrúbkou
ťahu a krytím.

**Okraj** je pri ploche obrysová čiara, pri čiare širší obrys pod ňou
(casing) – oboje s farbou, šírkou, druhom čiary (tá istá ponuka s náhľadom)
a krytím.

Číselné polia (hrúbka, krytie) sú prázdne s nápisom `auto`, keď je hodnota
v štýle zadaná interpoláciou podľa zoomu; vyplnením sa nahradí pevnou
hodnotou, vymazaním sa vráti pôvodná interpolácia.

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
  "version": 2,
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
  "poi": { "hidden": ["fast_food"] },

  "maps": {
    "turisticka": {
      "layers": {
        "road-path":  { "dash": "dotted", "paint": { "line-width": 2 } },
        "trail-ski":  { "visible": true }
      },
      "poi": { "hidden": [] }
    },
    "cestna": {
      "layers": { "poi-road": { "minzoom": 8 } },
      "poi": { "hidden": ["picnic_site"] }
    }
  }
}
```

`layers` a `poi` platia pre **všetky** typy máp, `maps.<typ>` len pre jeden a
prebíja spoločné nastavenie (`paint` sa mieša po jednotlivých vlastnostiach).
Súbory z verzie 1 (bez `maps`) sa načítajú bez zmeny – všetko z nich sa berie
ako spoločné.

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

Toto je základ; **typ mapy tieto hranice posúva** – turistická púšťa skaly,
chodníky a trasy skôr (z8–z10), cestná naopak bežné POI až od z15 a chodníky
vôbec. Konkrétne posuny sú v [poc/web/map-types.js](poc/web/map-types.js)
a dajú sa prekliknúť v developer móde.

**Výplň nad zmiešanou geometriou (a čudné polygóny od z13).** `--transportation_z13_paths=true`
vyššie má jeden dôsledok, ktorý stál opravu v štýle: od z13 sú v dlaždiciach
všetky chodníky, a to sú **čiary**. MapLibre `fill` vrstve čiary nepreskočí –
otvorenú lomenú čiaru pošle earcutu, ako keby to bol uzavretý prstenec, a
vyrobí z nej sebaprekrývajúci sa mnohouholník. Vrstva `pedestrian-area` (`fill`
nad `transportation`, `minzoom: 13`) tak od z13 kreslila útvary „prerezané" cez
krajinu, a keďže farba `pedestrian` je od podkladu na nerozoznanie, vyzeralo to
ako diera do podkladu. Každá výplň nad vrstvou, ktorá nesie viac typov
geometrie (`transportation`, `piste`, `aeroway`, `park`), preto ide cez
`polygonOnly(…)` a stráži to kontrola
[`workers/lint-style.mjs`](workers/lint-style.mjs). Rozpis:
[docs/pipeline.md](docs/pipeline.md#výplň-nad-zmiešanou-geometriou-prečo-boli-od-z13-čudné-polygóny).

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

1. **Pages si beh zapne sám.** Prvý krok skontroluje nastavenie repozitára
   a keď zdroj Pages nie je *GitHub Actions*, prepne ho (a keď Pages nie sú
   zapnuté vôbec, zapne ich). Je to preto, že na stránke má byť **mapa a nie
   README**: pri zdroji „vetva" beží popri nás zabudovaný Jekyll builder,
   ktorý po každom pushi nasadí koreň repozitára a mapu prepíše. Keby na to
   token nemal práva, beh sa zastaví v tretej sekunde s návodom –
   Settings → Pages → Build and deployment → Source: **GitHub Actions**.
2. Actions → **Build map (PBF → PMTiles) & deploy Pages** → *Run workflow*.
   Formulár má **desať polí** – viac `workflow_dispatch` inputov GitHub
   neprijme (pri 26 sa workflow prestal načítať a beh skončil ako „failure"
   s nula jobmi). Vo formulári sú preto veci, ktoré sa naozaj menia:

   | input | typ | čo robí |
   |---|---|---|
   | `region` | výber | `slovensko` alebo kraj (default **`presovsky`**) |
   | `area` | **výber** | pohorie, na ktorom sa počíta terén – `cely_region`, `tatry`, `slovensky_raj`, `mala_fatra`… (default **`vysoke_tatry`**) |
   | `test` | **switch** | **rýchly test**: spraviť všetko len na štvorci 2 km² zo stredu výrezu a mapu otvoriť rovno tam (predvolene zapnutý; ostrý beh = odškrtnúť) |
   | `contour_source` | **výber** | odkiaľ **vrstevnice**: `sonny` (20 m), `dmr35` (10 m), `dmr5` (LiDAR – s výrezom 1 m, inak 5 m), `ziadne` |
   | `rock_source` | **výber** | odkiaľ **skaly**: ten istý zoznam modelov (počíta sa sklon), alebo `tienovanie` (hotové polygóny z tieňovaných dlaždíc), alebo `ziadne` |
   | `shading_source` | **výber** | odkiaľ **tieňovanie a 3D terén**: `sonny`, `dmr35`, `dmr5`, `ziadne` |
   | `contour_interval` | text | interval vrstevníc v metroch (každá 10. je hlavná, každá 5. polovičná) |
   | `rock_slope` | text | od akého sklonu (°) je terén skala |
   | `rebuild` | výber | `nic` / `vrstevnice` / `skaly` / `teren` / `vsetko` |
   | `options` | text | zriedka menené nastavenia ako `kľúč=hodnota` (napr. veľkosť testu `test_km2=5`, mriežka na obrys skál `rock_res=1`) |

   **Defaulty sú to, na čom sa reálne pracuje** – Prešovský kraj, Vysoké
   Tatry, rýchly test na 2 km². Formulár *Run workflow* sa totiž po každom
   otvorení vracia na predvolené hodnoty: GitHub si nepamätá, s čím si beh
   pustil naposledy, a z API sa to ani nedá zistiť. Čím menej treba
   prekliknúť, tým menej sa toho zabudne. Čo bolo v konkrétnom behu iné než
   default, vypíše súhrn v bloku **Nastavenia tohto behu** – z neho sa dá
   beh zopakovať bez hádania.

   **Prečo je vo formulári `test` a nie `rock_res`.** Polí je desať a je to
   strop, takže sa dá pridať len to, za čo niečo vypadne. Rýchly test sa
   zapína a vypína pri každom behu — to je switch. Jeho veľkosť aj mriežka na
   obrys skál sa menia zriedka, takže sú z nich voľby (`test_km2=5`,
   `rock_res=1`); mriežku navyše `auto` vyberie z bunky DEM a rozpočtu času
   lepšie, než sa háda ručne.

   **Tri výbery zdroja, jeden na vrstvu.** Kým to bol jeden `dem_source` pre
   všetko, nedalo sa povedať to, čo dáva zmysel najčastejšie: skaly
   z najjemnejšieho modelu (aj keď ho máme len na výrez) a tieňovanie
   z hrubšieho, ktorý pokrýva celý región. `ziadne` vrstvu vypne – zapínač je
   tým pádom v tom istom poli ako zdroj, takže sa nedá zadať „generuj
   vrstevnice, zdroj žiadny". Nahradilo to aj pole `layers`; značené trasy
   (jediná vrstva bez výberu zdroja, ide z toho istého PBF ako mapa) sa
   vypínajú cez `options: trails=false`.

   `dmr5` má dve podoby a rozhoduje rozsah, nie ďalší výber: s vyplneným
   `area` plné 1 m, bez neho dlaždice na 5 m. Zoznamy vo formulári stráži
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

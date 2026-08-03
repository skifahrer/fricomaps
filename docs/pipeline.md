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

## Workflow „Build map" krok po kroku

### 1. Kontrola, či je GitHub Pages zapnuté

`GITHUB_TOKEN` nemá práva Pages zapnúť, takže sa to musí raz spraviť ručne.
Kontrola je **hneď na začiatku** zámerne: keby bola na konci, zistili by sme
to až po hodinách tilovania.

### 2. Stiahnutie PBF iba daného regiónu

Zdrojom sú regionálne exporty [osm.fr](https://download.openstreetmap.fr/extracts/),
rezané po **skutočných administratívnych hraniciach** a aktualizované denne.
Sťahuje sa len zvolený región – celá planéta má ~80 GB, kraj 36–63 MB.

Ak osm.fr vypadne, použije sa fallback z releasu `osm-extracts`, ktorý raz
týždenne vyrába druhý workflow (Geofabrik Slovensko → `osmium extract -c` →
všetky kraje naraz).

Voliteľný `crop_bbox` oreže PBF ešte viac (`osmium extract --bbox`). Menšie
územie = výrazne menší výsledok, takže sa doň zmestí vyšší zoom.

### 3. Vrstevnice, skaly a výškové dlaždice z DEM

**OpenStreetMap výškové dáta neobsahuje** – má len bodový tag `ele` na
vrcholoch a sedlách. Terén preto musí prísť odinakiaľ:

| zdroj | čo to je | odkiaľ |
|---|---|---|
| **Sonny's LiDAR DTM** (default) | *model terénu* z LiDARu – bez stromov a striech | náš release `dem-sonny` (zrkadlo, viď [Update DEM](#tretí-workflow-update-dem)) |
| Copernicus GLO-30 | *model povrchu* vrátane vegetácie, 1″ (~30 m) | AWS Open Data, bez autentifikácie |

Berie sa to, čo je pre danú dlaždicu k dispozícii: chýbajúce Sonny dlaždice
doplní Copernicus, takže build nezostane bez terénu (a v logu je varovanie,
ktoré dlaždice chýbali). Prepínač je input `dem_source`.

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
  │    gdalwarp -t_srs EPSG:3035 … do metrickej projekcie, mriežka 20 m
  │    gdaldem slope             … sklon v stupňoch
  │    gdalwarp -r average       … priemer na 40 m (súvislé úseky, nie šum)
  │    gdal_contour -p -fl 40 55 … izolínie sklonu ako plochy
  │    ogr2ogr                   … filter plochy, zjednodušenie, `class`
  │        │
  │        │  planetiler generate-custom --schema=workers/contours.yml
  │        ▼
  │  {región}-contours.pmtiles   (vrstvy `contour` a `rock`)
  │
  └─ výškové dlaždice ───────────────────────────────────────
       gdalwarp -t_srs EPSG:3857 … pre každý zoom zvlášť, priemerom
       build-terrain.py          … terrarium PNG 256×256
                                 → terrain/{z}/{x}/{y}.png
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
- **Prečo priemer na hrubšej mriežke.** Bez neho vzniknú z jednotlivých
  strmých pixelov roztrúsené bodky („soľ a korenie"). Skala je súvislý strmý
  úsek, takže sa sklon najprv spriemeruje na 40 m a až potom prahuje.
- **Prečo `gdal_contour -p`, a nie polygonizácia rastra.** Polygonizácia by
  obkreslila pixely, teda schodíky; izolínia sklonu má body interpolované
  medzi bunkami, takže je okraj hladký a bodov výrazne menej.
- **`class`** rozlišuje `steep` (nad prahom `rock_slope`, default 40°) a
  `cliff` (o 15° viac) – štýl z toho kreslí svetlejšiu a tmavšiu sivú.
- **Cache.** Vrstevnice aj skaly závisia len od územia, zdroja výšok,
  intervalu, maxzoomu, zjemnenia a prahu sklonu – nie od toho, čo sa zmenilo
  v OSM. Sú preto nacacheované podľa týchto parametrov a pri ďalšom builde
  mapy sa nepočítajú znova.
- **Vlastný `.pmtiles`** (nie súčasť mapových dlaždíc) práve preto, aby sa
  dali cacheovať zvlášť a aby ich štýl vedel vypnúť bez prebuildu mapy.

#### Výškové dlaždice (3D terén a tieňovanie)

MapLibre nevie čítať výšky z GeoTIFFu – potrebuje pyramídu PNG dlaždíc, kde je
nadmorská výška zakódovaná do farby (*terrarium*: `výška = R·256 + G + B/256 −
32768`). Doteraz sa brali z verejných AWS Terrain Tiles; tie sú ale povrchový
model, takže by 3D reliéf zdvíhal koruny stromov, kým vrstevnice by viedli po
zemi. [`workers/build-terrain.py`](../workers/build-terrain.py) ich preto
vyrobí z toho istého DEM:

- **Každý zoom sa prevzorkuje z DEM nanovo** (`gdalwarp -r average`), nezmenšujú
  sa hotové dlaždice. Priemerovať sa totiž musí *výška*; priemer bajtov R a G
  zakódovanej farby by dal nezmysel (R je horný bajt, jeho priemer nie je
  priemer výšky).
- **Obyčajné `{z}/{x}/{y}.png`**, nie `.pmtiles`: raster-dem cez vlastný
  protokol by musel fungovať aj v MapLibre Native na iOS. Takto je to formát,
  ktorému rozumie každý klient bez ďalších závislostí.
- **Zoom končí na 12.** Dlaždica z12 má u nás ~25 m na pixel, čo je práve
  rozlíšenie 30 m DEM – vyšší zoom by pridal len bajty. Vyššie priblíženie si
  MapLibre dopočíta sám.
- **Výška po celých metroch** (posledný bajt kódovania = 0). Porovnané na
  Vysokých Tatrách proti plnej presnosti: dlaždice **2,9× menšie**, rozdiel vo
  vykreslenom tieňovaní priemerne 0,5 z 255 odtieňov (max 8) – okom
  nerozoznateľné. Ten bajt je totiž z väčšiny šum, ktorý sa nedá skomprimovať.
- **PNG si skript zapisuje sám** (zlib + hlavičky, filter sa pre každý riadok
  vyberá heuristikou najmenšieho súčtu odchýlok). Ušetrí to závislosť na
  Pillow a od `optimize=True` v Pillow je výsledok väčší len o ~4 %.
- Keď dlaždice nevzniknú alebo sú vypnuté (`terrain: nie`), štýl padá späť na
  AWS Terrain Tiles – 3D terén teda funguje ako predtým.

Vrstevnice sa robia **pred** mapovými dlaždicami zámerne – viď [rozpočet
veľkosti](#rozpočet-veľkosti).

### 4. PBF → PMTiles (Planetiler)

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

### 5. Sady ikoniek → SDF sprity

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

### 6. Glyfy (fonty)

Balík predpočítaných glyfov Noto Sans sa kopíruje **na naše Pages**. Verejná
služba `fonts.openmaptiles.org` je jediný bod zlyhania, pri ktorom by sa mapa
vykreslila úplne bez nápisov – to sa nechce.

### 7. Generovanie `style.json`

[`workers/build-styles.mjs`](../workers/build-styles.mjs) zavolá ten istý
generátor ([`poc/web/themes.js`](../poc/web/themes.js)), aký používa web, a
vyrobí statické štýly pre všetky témy. Naviaže ich na **reálne dostupné
assety**: zoznam ikon berie zo sprite indexu a fontstacky z adresára s
glyfmi, takže štýl nikdy neodkazuje na niečo, čo na Pages nie je.

Sem sa zároveň zapečú [úpravy z developer
módu](../README.md#cesta-úprav-do-zdrojáku) (`poc/web/style-overrides.json`)
– farby, viditeľnosť vrstiev, rozsahy zoomu, vzory, okraje, sada ikoniek a
tieňovanie reliéfu.

### 8. Vzory do spritu

Vzory plôch a čiar sú **generované z predpisu, ktorý je zároveň názvom
obrázka** (`pat:trees:2f5a28:22:12`). Web si ich dokreslí sám cez
`styleimagemissing`, statický štýl pre iOS ich ale potrebuje v sprite –
[`workers/add-sprite-patterns.mjs`](../workers/add-sprite-patterns.mjs) preto
prejde hotové štýly, pozbiera použité názvy a dopečie ich do atlasu.

### 9. Manifest a viewer

`tiles/manifest.json` je to jediné, čo si web načíta na začiatku: kde sú
dlaždice, aký majú maxzoom, bbox regiónu, aké sady ikoniek sú nasadené a kedy
sa mapa vygenerovala.

### 10. Kontrola pred nasadením

Prejde sa hotový `style.json` a overí sa, že **všetko, na čo odkazuje, naozaj
existuje**: sprite, fontstacky, pevne zadané mená ikon, vzory, `.pmtiles`
a vrstevnice. Bez toho by sa chyba prejavila až ako biela mapa v prehliadači.

### 11. Deploy a smoke test

Po nasadení si pipeline **sama overí, že mapa funguje**: `manifest.json`,
`style.json`, sprite, glyfy a – najdôležitejšie – `Range` request na
`.pmtiles`, ktorý musí vrátiť **HTTP 206**. Keby hosting Range requesty
nepodporoval, `.pmtiles` sa nedá čítať a mapa zostane prázdna.

---

## Rozpočet veľkosti

GitHub Pages zvládne stránku do ~1 GB a do toho sa musia zmestiť dlaždice
**aj vrstevnice, fonty a sprity**. Preto je rozpočet jeden a spoločný
(`size_limit_mb`, default 900):

1. **Vrstevnice a výšky sa robia prvé** – vrstevnice majú strop 40 %
   rozpočtu, výškové dlaždice 20 % (nad stropom sa im odoberie najvyšší zoom,
   každý je totiž generovaný samostatne). Keď vrstevnice strop prekročia,
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

## Druhý workflow: „Update OSM extracts"

Beží raz týždenne a slúži len ako **poistka**, keby osm.fr nebolo dostupné.
Stiahne Geofabrik export Slovenska a `osmium extract -c` z neho jedným
priechodom vyreže všetky kraje po administratívnych hraniciach; výsledok
uloží do releasu `osm-extracts`.

## Tretí workflow: „Update DEM"

Zrkadlí výškový model **Sonny's LiDAR DTM** do releasu `dem-sonny`. Sonny ho
distribuuje cez Google Drive – ten nemá stabilné priame URL na súbory v
zdieľanom priečinku a pri väčšom počte stiahnutí odpovedá limitom, takže sa
z neho nedá sťahovať v každom builde mapy.

```
Google Drive priečinok (napr. Slovensko)
  │  gdown --folder     … stiahne celý priečinok naraz
  │  7z                 … rozbalí .zip / .7z
  │  gdal_translate     … .hgt → N49E019.tif (GeoTIFF, DEFLATE)
  ▼
release `dem-sonny`: N49E019.tif … + meta.json
```

- **Meno dlaždice** sa berie z názvu súboru (konvencia SRTM: juhozápadný roh),
  takže je jedno, ako sú súbory v priečinku pomenované navyše.
- **`.hgt` je surové pole int16 bez hlavičky.** GDAL ho pozná pri štandardných
  veľkostiach (1201², 3601²); pri neštandardnej mriežke (0,5″ = 7201²) si
  workflow georeferenciu poskladá sám cez VRT – krok mriežky je `1/(n−1)`,
  lebo vzorky sú v uzloch, nie v stredoch buniek.
- **Prevod na GeoTIFF** je zámerný: je menší (bezstratová kompresia) a
  `gdalbuildvrt` v builde mapy ho zlepí bez ďalších pomôcok.
- Ak by Drive limitoval, dá sa namiesto neho vyplniť `direct_urls` (priame
  odkazy na mirror).

## Štvrtý workflow: „Uložiť úpravy štýlu do zdrojáku"

Protikus developer módu – vezme stiahnutý `style-overrides.json`, prežene ho
**tou istou validáciou ako prehliadač** (`normalizeOverrides`) a commitne do
repozitára. Neznáma farba, neplatný hex, neprepísateľná vlastnosť či
prehodený rozsah zoomu skončia varovaním a vyhodia sa, takže do zdrojáku sa
nedostane nič, čo by štýl rozbilo.

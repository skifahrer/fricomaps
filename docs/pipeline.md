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

Voliteľný `crop_bbox` oreže PBF ešte viac (`osmium extract --bbox`). Menšie
územie = výrazne menší výsledok, takže sa doň zmestí vyšší zoom.

### 3. Vrstevnice, skaly a tieňovanie z DEM

**OpenStreetMap výškové dáta neobsahuje** – má len bodový tag `ele` na
vrcholoch a sedlách. Terén preto musí prísť odinakiaľ:

| zdroj | čo to je | odkiaľ |
|---|---|---|
| **Sonny's LiDAR DTM, model 20m** | *model terénu* z LiDARu – bez stromov a striech, mriežka 20×20 m, výška po 0,1 m | náš release `dem-sonny` (zrkadlo, viď [Update DEM](#druhý-workflow-update-dem)) |

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
  │    workers/rock-areas.py – po častiach, aby sa 2 m mriežka zmestila:
  │      gdalwarp -t_srs EPSG:3035 … do metrickej projekcie, mriežka 2 m
  │      gdaldem slope             … sklon v stupňoch
  │      gdal_contour -p -fl 50 65 … izolínie sklonu ako plochy
  │      ogr2ogr                   … rozbitie na kusy, orez, `class`
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
- **Skaly sa delia na malé kúsky** (`rock_piece`, default 10 m). Sklon sa
  spriemeruje na mriežku kúskov a každá bunka nad prahom sa vypíše ako
  samostatný štvorček vyplňujúci 80 % bunky (`ROCK_PIECE_FILL`). Susedné sa
  teda nedotýkajú a v mape z toho je šrafovanie namiesto jednej plochy.
  Namerané na výreze Vysokých Tatier (884 ha skál pri 50°): 10 m → 87 839
  kúskov (35 MB), 20 m → 21 491 (8,4 MB), 30 m → 9 261 (3,7 MB). Kúsky po
  1–2 m² možné nie sú: strana 1,4 m dá na kraj ~23 miliónov polygónov, teda
  rádovo 9 GB GeoPackage. Praktické minimum je 5 m.
- **Veľkosť plôch pri `rock_piece: 0` neurčuje mriežka, ale prah sklonu.**
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
- **Mriežka 2 m** (`rock_res`) riadi jemnosť *obrysu*, nie veľkosť plôch:
  na tom istom území dal 5 m 366 plôch a 2 m 387. Tvary pod 20 m sú
  dopočítané, nie merané – bunka DEM má 20×20 m.
- **Počíta sa po častiach** ([`workers/rock-areas.py`](../workers/rock-areas.py)).
  Bbox kraja má pri 2 m vyše 3 miliardy buniek, čo je ~13 GB na jeden raster –
  viac, než má runner miesta aj pamäte. Územie sa preto krája na kusy
  (`ROCK_CHUNK_CELLS`, default 150 mil. buniek), každý sa spracuje a hneď
  upratá. Sklon sa počíta s presahom niekoľkých pixelov a plochy sa orežú
  presne na hranicu kusa (`-clipsrc`), takže susedné na seba nadväzujú bez
  medzery aj bez prekryvu. Merané ~1,5 mil. buniek/s → kraj pri 2 m ~35 minút;
  mriežka 1 m sa oplatí len na `crop_bbox`.
- **Bez zjednodušovania** (`ROCK_SIMPLIFY=0`) – zjednodušenie obrysu by tie
  najmenšie plochy zmazalo úplne.
- **Najmenšia plocha 1 m²** (`ROCK_MIN_AREA`), teda bez filtra – a aby ich
  nezahodil Planetiler, dostane `--min_feature_size_at_max_zoom=0`. Overené
  na hotových dlaždiciach: najmenšie skalné polygóny v nich majú 1,2 m².
  Pri nižších zoomoch drobnosti odpadnú samé – tam Planetiler prvky menšie
  než pixel zahadzuje, čo je správne, lebo by z nich boli nečitateľné bodky.
- **Skutočná rozlišovacia schopnosť dát.** Bunka 1″ DEM má u nás ~20×30 m,
  takže najmenší *meraný* útvar má rádovo stovky m². Naozajstné 1 m² skaly by
  potreboval 1 m LiDAR (ÚGKK DMR 5.0), ktorý sa ale z geoportálu sťahuje cez
  interaktívny export – musel by sa najprv nazrkadliť do releasu ako Sonnyho
  DTM.
- **Prečo `gdal_contour -p`, a nie polygonizácia rastra.** Polygonizácia by
  obkreslila pixely, teda schodíky; izolínia sklonu má body interpolované
  medzi bunkami, takže je okraj hladký a bodov výrazne menej.
- **`class`** rozlišuje `steep` (nad prahom `rock_slope`, default 40°) a
  `cliff` (o 15° viac) – štýl z toho kreslí svetlejšiu a tmavšiu sivú.
- **Prečo `-explodecollections`.** `gdal_contour -p` zlepí každé pásmo sklonu
  do jedného multipolygónu; bez rozbitia na kusy by sa nedala merať plocha
  jednotlivej skaly ani filtrovať tá najmenšia.
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

## Druhý workflow: „Update DEM"

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

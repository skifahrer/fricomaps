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

Najprv prehľad – **každý krok behu v poradí**, aj tie, ktoré nič nepočítajú.
Kroky s cache sú rozdelené na *restore* (hore, hneď ako je známy kľúč)
a *save* (hneď ako dáta vzniknú, s `if: always()`), aby pád o hodinu neskôr
nezahodil to, čo je už hotové.

| # | krok | čo robí | čo z toho vypadne |
|--:|---|---|---|
| — | *Kontrola výškového modelu* (vlastný job) | pozrie sa, či sú v release `dem-sonny` dlaždice pre bbox; spočíta otlačok obsahu releasu | `needed`, `demkey` (ide do kľúčov cache) |
| — | *Doplniť výškový model* (vlastný job) | keď dlaždice chýbajú, spustí workflow **Update DEM** | naplnený release `dem-sonny` |
| 1 | Over, že GitHub Pages je zapnuté | `gh api repos/…/pages` | rýchly pád namiesto pádu po hodinách |
| 2 | Dnešný dátum do kľúča cache | dátum + štart merania času | `steps.day.outputs.d`, `BUILD_T0` |
| 3 | Cache PBF (restore) | skúsi vytiahnuť už stiahnutý `.osm.pbf` | `data/region.osm.pbf` |
| 4 | Stiahni PBF iba daného regiónu | osm.fr export, voliteľný `osmium extract` orez | PBF + `key`, `name`, `bbox`, `bboxkey` |
| 5 | **Kľúče cache** | poskladá kľúče pre vrstevnice, DEM dlaždice a terén na jednom mieste | `steps.keys.outputs.*` |
| 6 | **Pregenerovanie – zmaž staré cache** | pri `*_rebuild` zmaže príslušný záznam (`gh cache delete`) | prázdny kľúč, pod ktorý sa dá uložiť nová verzia |
| 7 | Setup Java 21 | JDK pre Planetiler | — |
| 8 | Cache zdrojov Planetileru | water polygons, Natural Earth (1,4 GB, pevný kľúč) | `data/sources` |
| 9 | Cache Planetileru (restore) + stiahnutie | `planetiler.jar` (89 MB, kľúč = dátum) | `planetiler.jar` |
| 10 | Cache PBF a Planetileru (save) | uloží oboje **ešte pred** dlhými výpočtami | — |
| 11 | Cache vrstevníc a DEM dlaždíc (restore) | pri `*_rebuild` sa preskočí | `contours-out`, `dem/tiles` |
| 12 | **Vrstevnice a skaly z DEM** | stiahne DEM dlaždice, `gdal_contour`, `rock-areas.py` (voliteľne len na výreze `rock_area`), Planetiler `generate-custom` | `data/contours.gpkg`, `data/rock.gpkg`, `contours-out/contours.pmtiles` |
| 13 | Cache vrstevníc a DEM dlaždíc (save) | `if: always()` – uloží aj keď build ďalej spadne | — |
| 14 | Zaraď vrstevnice do webu | kópia do `_site/tiles`, prečíta skutočný maxzoom | `steps.contours.outputs.*` |
| 15 | Cache terénu (restore) + **Tieňovanie reliéfu** | `build-terrain.py` alebo stiahnutie z release `dem-terrain` | `_site/terrain/{z}/{x}/{y}.png` |
| 16 | Cache terénu (save) | `if: always()` | — |
| 17 | Odlož skaly a vrstevnice ako artefakt | GPKG + PMTiles + `rock-stats.txt`, 90 dní | artefakt behu |
| 18 | **PBF → PMTiles (Planetiler)** | mapové dlaždice, s `auto_shrink` do rozpočtu | `_site/tiles/{región}.pmtiles` |
| 19 | Cache glyfov a spritov (restore) | kľúč = hash zoznamu zdrojov | `_site/fonts`, `_site/sprites` |
| 20 | Stiahni a priprav sady ikoniek | SDF sprity z maki/temaki/… | `_site/sprites/*.json`, `*.png` |
| 21 | Stiahni glyfy (fonty) | Noto Sans z balíka openmaptiles | `_site/fonts/{fontstack}/…` |
| 22 | Cache glyfov a spritov (save) | **pred** zapečením vzorov do atlasu | — |
| 23 | Setup Pages | zistí `base_url` | `steps.pages.outputs.base_url` |
| 24 | Vygeneruj `style.json` | `build-styles.mjs` pre web aj iOS | `_site/styles/*.json` |
| 25 | Dopeč vzory plôch a čiar do spritu | vzory z developer módu do atlasu | upravený sprite |
| 26 | Poskladaj web | viewer + `manifest.json` | `_site/*` |
| 27 | Kontrola pred nasadením | overí, že štýl odkazuje len na existujúce súbory a že sa všetko zmestí do rozpočtu | pád s konkrétnou hláškou |
| 28 | Deploy na GitHub Pages | `upload-pages-artifact` + `deploy-pages` | živá mapa |
| 29 | Smoke test | HTTP kontrola manifestu, štýlu, spritu, glyfov a `Range` na `.pmtiles` | istota, že mapa naozaj beží |
| 30 | **Súhrn buildu** | tabuľka „čo sa robilo, ako dlho, s akým výsledkom" | záložka Summary |

Ďalej detailne, čo z toho je zaujímavé a **prečo** je to tak.

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
  │    workers/rock-areas.py
  │    a) sklon PO ČASTIACH (pamäťovo drahé), na disk:
  │      gdalwarp -t_srs EPSG:3035 … do metrickej projekcie, mriežka 2 m
  │      gdaldem slope             … sklon v stupňoch
  │      gdal_translate -ot Byte   … krok 0,5°, aby sa mozaika zmestila
  │      gdalbuildvrt              … mozaika sklonu celého územia
  │    b) vektorizácia NARAZ nad mozaikou:
  │      gdal_contour -p -fl 100 130 … izolínie sklonu ako plochy (s dierami)
  │      -explodecollections       … samostatné skaly
  │      filter najmenšej plochy   … + `class`, `slope`, `area`
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
- **Diery: čo nie je nad prahom, sa nezafarbí.** Keď je vnútri steny miesto
  s menším sklonom – polica, terasa, zarastený stupeň – vypadne z plochy
  **diera**, aj keď je dookola všade sklon nad prahom. Robí to priamo
  `gdal_contour -p`: pásmo `[prah, ∞)` je polygón s vnútornými prstencami
  tam, kde hodnota pod prah klesla. Diery sa nezapĺňajú ani nefiltrujú a
  vrstva `rock-outline` ich obkreslí rovnako ako vonkajší obrys, takže je
  polica v mape vidieť.
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
- **Mozaika sklonu je Byte s krokom 0,5°.** Vo `Float32` by mala pre kraj pri
  2 m ~13 GB, čo sa na disk runnera nezmestí; ako `Byte` (hodnota = 2×
  stupne, 0–180) je 4× menšia a ešte sa komprimuje `DEFLATE`+`PREDICTOR`.
  Presnosť 0,5° je na prahovanie viac než dosť – prahy sú aj tak celé stupne.
  Prahy sa preto do `gdal_contour` dávajú vynásobené dvomi (`-fl 100 130`).
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
- **Skaly len na výreze** (`rock_area`). Skaly sú najdrahšia časť buildu, tak
  sa dajú počítať len na kuse regiónu – pri ladení prahu alebo mriežky netreba
  čakať polhodinu na celý kraj. Input berie buď názov pohoria zo
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
- **Aký je to detail.** Obrys sa počíta na mriežke `rock_res` (default 2 m),
  najmenšia ponechaná plocha je **jedna bunka tejto mriežky** (pri 2 m teda
  4 m², pri 1 m rovno 1 m²) – menší útvar už nie je tvar terénu, ale zubaté
  rohy jedinej bunky. Skutočný detail je ale stropený zdrojom: **Sonny má pre
  Slovensko bunku ~20 m**, takže tvary pod 20 m sú dopočítané, nie merané.
  Jemnejšia mriežka dá hladší a presnejšie umiestnený obrys a viac dier, novú
  informáciu o teréne však nepridá. Cena za `rock_res: 1` je 4× viac buniek
  (kraj ~2 hodiny), takže má zmysel len s `crop_bbox`. Presné čísla za
  konkrétny beh píše `rock-areas.py` do `contours-out/rock-stats.txt` a build
  ich vypíše v [súhrne](#12-súhrn-buildu).
- **Bez zjednodušovania** (`ROCK_SIMPLIFY=0`) – zjednodušenie obrysu by tie
  najmenšie plochy zmazalo úplne.
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

| input | čo pregeneruje |
|---|---|
| `contours_rebuild` | vrstevnice **aj skaly** – zmaže cache `contours-…` a trasuje z DEM odznova |
| `rocks_rebuild` | skaly – zmaže cache aj asset v release `dem-rocks` (vrstevnice sa prepočítajú s nimi, sú lacné) |
| `terrain_rebuild` | tieňovanie a 3D terén – zmaže cache aj asset v release `dem-terrain` |

Mechanika je dôležitá, lebo nie je zrejmá: **cache sa v GitHube nedá
prepísať.** Kľúč, ktorý raz existuje, si drží starý obsah a `cache/save` naň
len upozorní, že už tam je. Keby sa teda `*_rebuild` len „prepočítal a uložil",
uloženie by nič nespravilo a ďalší build by dostal späť starú verziu. Preto:

1. build má právo `actions: write`,
2. krok *Pregenerovanie – zmaž staré cache* zmaže príslušný záznam
   (`gh cache delete`) hneď na začiatku,
3. restore sa pri `*_rebuild` **preskočí**, takže výpočet beží,
4. save uloží novú verziu pod ten istý kľúč.

Kľúče sa počítajú na jednom mieste (krok *Kľúče cache*) a používa ich restore,
save aj mazanie – keby boli napísané trikrát, stačí ich raz zabudnúť opraviť
a cache sa ticho rozsype: ukladala by sa pod iným kľúčom, než sa hľadá.

Ostatné cache (PBF, Planetiler, DEM dlaždice, glyfy, sprity) sa
nepregenerúvajú vôbec – sú to stiahnuté dáta, nie výpočet, a majú v kľúči buď
dátum, alebo otlačok zdroja.

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

### 12. Súhrn buildu

Posledný krok napíše do záložky **Summary** prehľad celého behu. Beží
s `if: always()`, takže je aj (hlavne) vtedy, keď build spadol – z padnutého
behu je tak vidieť, kam sa dostal a čo stihol.

Meranie funguje tak, že si každý dlhý krok na konci pripíše riadok „názov,
sekundy, čo spravil" do `/tmp/build-steps.tsv`; súhrn z toho poskladá tabuľku:

| krok | trvanie | výsledok |
|---|--:|---|
| PBF regiónu | 0:00:12 | Prešovský kraj, 63M (z cache) |
| DEM dlaždice (Sonny) | 0:01:44 | 9 z 21 dlaždíc, 412M |
| Vrstevnice (gdal_contour) | 0:04:31 | interval 10 m, 218M |
| Skalné plochy | 0:36:07 | 41 802 plôch, sklon ≥ 50°, mriežka 2 m (výpočet) |
| Vrstevnice a skaly → PMTiles | 0:06:12 | maxzoom 14, 187M |
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

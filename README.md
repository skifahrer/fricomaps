# fricomaps

All-in-one mapová aplikácia. Vektorové mapy Slovenska z OSM dát – jedna
pipeline, jeden formát (PMTiles), spoločné štýly pre web aj mobil.

## Štruktúra monorepa

```
app/ios/       iOS aplikácia (SwiftUI + MapLibre Native)
backend/       NestJS backend (API – regióny, budúce užívateľské veci)
poc/web/       proof-of-concept web viewer (MapLibre GL JS + PMTiles)
               + developer mode na ladenie štýlu priamo v prehliadači
workers/       pipeline: regióny, príprava PBF exportov, generátor štýlov,
               SDF sprite, vzory do spritu, zápis úprav štýlu do zdrojáku
docs/          návrhy (iOS / multiplatform)
.github/workflows/  CI pipeline (extrakty + build mapy + deploy Pages)
```

## Ako funguje pipeline

```
Build map                    stiahne IBA {región}.osm.pbf:
(manuálne, výber regiónu)      1. osm.fr exporty (download.openstreetmap.fr/extracts –
                                  Európa aj svet, rezané po admin. hraniciach, denné)
                               2. fallback: release `osm-extracts` (vlastné exporty)
                             ─► Planetiler ─► {región}.pmtiles
                             ─► GitHub Pages (viewer + dlaždice + style.json)

Update OSM extracts          Geofabrik slovakia.pbf ─► osmium extract -c
(fallback, raz týždenne)     (všetky kraje po OSM admin. hraniciach naraz)
                             ─► release `osm-extracts`: {kraj}.osm.pbf + meta.json

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
  osm.fr rezacích polygónov sú vo [workers/regions.json](workers/regions.json);
  pri výpadku osm.fr sa použije fallback release `osm-extracts`.
- **Ľubovoľný región Európy/sveta:** pri spúšťaní workflowu vyplň
  `custom_pbf_url` (URL na `.osm.pbf` z osm.fr extracts stromu, napr.
  `https://download.openstreetmap.fr/extracts/europe/austria.osm.pbf`)
  a `custom_name`. Bbox sa prečíta z PBF hlavičky (alebo zadaj `custom_bbox`).
- **Témy a štýlovanie:** [poc/web/themes.js](poc/web/themes.js) – 4 farebné
  témy (Svetlá, Tmavá, Outdoor, Retro/Pastel), ~110 vrstiev pokrývajúcich celú
  OpenMapTiles schému: krajinná pokrývka, využitie územia, voda a vodné toky,
  budovy (od z16 v 3D), cesty vrátane chodníkov/cyklotrás/schodov, mosty a
  tunely, železnice, lanovky, hranice až po obce, súpisné čísla, vrcholy hôr,
  letiská a POI s ikonkami zo spritu osm-liberty (maki).
  Ten istý generátor vyrába statické `styles/{region}-{tema}.json` pre iOS.
- **Ikonky bez podkladov, s farbou:** hotové sprity kreslia symboly na
  podklade (osm-liberty v bielom koliesku, osm-bright so svetlým halom) a
  farbu im meniť nejde. Pipeline z každého zdroja vyrobí vlastný **SDF sprite**
  ([workers/build-sdf-sprite.mjs](workers/build-sdf-sprite.mjs)), kde je len
  samotný symbol a dá sa mu nastaviť `icon-color` aj `icon-halo-color`.
- **Tri sady ikoniek** ([poc/web/icon-sources.js](poc/web/icon-sources.js)) sa
  nasadzujú všetky naraz, takže sa dajú v developer móde prepínať naživo.
- **Developer mode:** ladenie mapy priamo v prehliadači – viď nižšie.

## Nadmorská výška a vrstevnice

**OpenStreetMap výškové dáta neobsahuje.** Má len bodový tag
[`ele`](https://wiki.openstreetmap.org/wiki/Key:ele) na vrcholoch, sedlách,
prameňoch či staniciach — žiadny terénny model, a vrstevnice sa doň zámerne
nenahrávajú. Každá OSM mapa s reliéfom (OpenTopoMap, OpenAndroMaps, Waymarked
Trails) preto kombinuje OSM s externým DEM. Robíme to rovnako:

| čo | zdroj | kde sa berie |
|---|---|---|
| výšky vrcholov | OSM tag `ele` | už v dlaždiciach, vrstva `mountain_peak` |
| vrstevnice | Copernicus GLO-30 | [AWS Open Data](https://registry.opendata.aws/copernicus-dem/), bez autentifikácie |
| tieňovanie reliéfu, 3D terén | AWS Terrain Tiles (Terrarium) | [registry.opendata.aws](https://registry.opendata.aws/terrain-tiles/) |

Vrstevnice sa počítajú v pipeline a končia vo **vlastnom `.pmtiles`**, takže
fungujú na webe aj na iOS cez ten istý `style.json`:

```
Copernicus GLO-30 (1°×1° COG dlaždice pre bbox)
  → gdalwarp   orez na bbox + zjemnenie na ~2″ (≈60 m), inak sú vrstevnice zubaté
  → gdal_contour -i 10
  → ogr2ogr    dopočíta `level`: major (100 m) / mid (50 m) / minor (10 m)
  → planetiler generate-custom --schema=workers/contours.yml
  → {región}-contours.pmtiles
```

`level` riadi, čo je vidieť kedy: hlavné vrstevnice od z10, polovičné od z12,
základné od z13, popisky výšky pozdĺž hlavných od z13. Výsledok je
nacacheovaný podľa bboxu a intervalu — vrstevnice závisia len od územia, takže
sa pri ďalšom builde mapy nepočítajú znova.

Ovládanie vo workflowe: `contours` (zap/vyp), `contour_interval` (default 10 m),
`contour_maxzoom` (default 14 — sú to hladké krivky, vyššie netreba).

> **Kvalita dát:** Copernicus GLO-30 je *DSM*, teda povrchový model vrátane
> stromov a budov. V lese sú vrstevnice preto mierne posunuté. Lepšie by boli
> [Sonny's LiDAR DTM](https://sonny.4lima.de/) alebo slovenský
> [ÚGKK DMR 5.0](https://www.geoportal.sk/) (1 m LiDAR, voľný aj komerčne pri
> uvedení zdroja) — ani jeden sa však nedá sťahovať priamo v CI, museli by sa
> nacacheovať do releasu.

## Developer mode – ladenie mapy v prehliadači

Mapa sa dá doladiť priamo vo viewri, bez čakania na pipeline. Zapína sa
prepínačom **🛠 Developer mode** v paneli ⚙ (alebo cez `?dev=1` v URL).

| záložka | čo sa v nej dá |
|---|---|
| **Vrstvy** | všetkých ~115 vrstiev po skupinách, s druhom (plocha / línia / bod / popisok / 3D / reliéf). Filtre podľa druhu a hľadanie, zapnutie a vypnutie vrstvy aj celej skupiny, rozsah zoomu (`od z` / `do z`), farby všetkých `*-color` vlastností, **vzor**, **okraj** a prerušovanie čiary. Riadok sa rozklikne kliknutím na názov |
| **Paleta** | ~67 farieb aktuálnej témy po skupinách. Zmena farby prefarbí naraz všetky vrstvy, ktoré ju používajú |
| **Ikony** | sada ikoniek pre POI, vrcholy a letiská – s náhľadom, počtom obrázkov a licenciou |
| **POI** | ktoré triedy bodov sa zobrazujú (zoznam sa načíta z dlaždíc v aktuálnom výreze) |
| **Súbor** | stiahnutie, nahratie a vymazanie úprav |

**Prehliadanie po zoomoch.** Nad zoznamom je posuvník zoomu: nastavíš zoom
(mapa tam skočí) a zoznam ukáže, čo je na ňom naozaj povolené – hlavička
skupiny má počítadlo `aktívne/všetky`, každý riadok svoj rozsah (`z13–16`,
`z9+`, `vždy`) a vrstvy, ktoré sa na danom zoome neorežú, ostávajú výrazné.
Prepínač **len aktívne** schová zvyšok. Posuvník sleduje aj bežné zoomovanie
myšou, takže sa dá ísť zoom po zoome a hneď vidieť, čo pribudlo.

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
  "palette": { "outdoor": { "forest": "#a8cc8e" } },
  "layers": {
    "landcover-wood": {
      "paint":   { "fill-color": "#a8cc8e" },
      "pattern": { "id": "trees", "color": "#2f5a28", "size": 22, "weight": 1.2, "opacity": 0.7 },
      "outline": { "color": "#2f5a28", "width": 1, "dash": "dashed", "opacity": 1 }
    },
    "rail-bg":        { "dash": "ties", "outline": { "color": "#5a5a5a", "width": 1 } },
    "housenumber":    { "visible": false },
    "road-motorway":  { "minzoom": 6, "maxzoom": 20 }
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
| < 14 | mapa sa orezáva – vrstvy sa zapínajú postupne podľa `minzoom` |
| 14–15 | plný detail, POI filtrované na `rank <= 24`, aby mapa nebola zahltená |
| 16+ | **všetko bez filtra** – všetky body, línie aj plochy, 3D budovy |
| 17+ | navyše súpisné čísla domov |

**Veľkosť vs. zoom.** GitHub Pages zvládne stránku do ~1 GB. Celé Slovensko má
pri z14 ~800 MB, pri z16 by limit prekročilo. Preto má workflow:

- `size_limit_mb` (default 900) – strop pre `.pmtiles`,
- `auto_shrink` (default áno) – ak je výsledok väčší, automaticky skúsi
  o zoom nižšie (a povie to vo warningu),
- `crop_bbox` – oreže PBF na menšie územie (`west,south,east,north`), čím sa
  maxzoom 16 pohodlne zmestí.

Pre maximálny detail na z20 teda voľ **kraj alebo `crop_bbox` + maxzoom 16**;
pre celé Slovensko nechaj pipeline zvoliť najvyšší zoom, ktorý sa zmestí.
- **iOS / multiplatform:** appka v [app/ios](app/ios), návrh v
  [docs/ios-multiplatform.md](docs/ios-multiplatform.md).
- **Backend:** [backend](backend) – NestJS API (`/api/health`, `/api/regions`).

## Prvé spustenie

1. **Zapni GitHub Pages:** Settings → Pages → Source: **GitHub Actions**.
2. Actions → **Update OSM extracts (PBF exporty regiónov)** → *Run workflow*
   (vyrobí PBF exporty krajov do releasu `osm-extracts`; potom beží sám raz
   týždenne).
3. Actions → **Build map (PBF → PMTiles) & deploy Pages** → *Run workflow*:
   - `region`: `slovensko` alebo kraj (`bratislavsky`, `zilinsky`, …)
   - `maxzoom`: `16` (max, aký Planetiler vie; `12` pre rýchly testovací build)
   - `crop_bbox`: voliteľné orezanie, napr. `18.98,49.18,19.20,49.28` (Žilina)
   - `contours`: vrstevnice z DEM (zapnuté; pre celé Slovensko pozor na veľkosť)
4. Mapa je na `https://<user>.github.io/fricomaps/` – ovládanie je zbalené pod
   tlačidlom ⚙ vľavo hore, aby bolo vidieť hlavne mapu. V paneli je prepínač
   témy, regiónu, vrstevníc, 3D terénu a developer módu.

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

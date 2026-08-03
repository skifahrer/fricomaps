# fricomaps

All-in-one mapová aplikácia. Vektorové mapy Slovenska z OSM dát – jedna
pipeline, jeden formát (PMTiles), spoločné štýly pre web aj mobil.

## Štruktúra monorepa

```
app/ios/       iOS aplikácia (SwiftUI + MapLibre Native)
backend/       NestJS backend (API – regióny, budúce užívateľské veci)
poc/web/       proof-of-concept web viewer (MapLibre GL JS + PMTiles)
workers/       pipeline: regióny, príprava PBF exportov, generátor štýlov
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
   témy, regiónu, vrstevníc a 3D terénu.

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

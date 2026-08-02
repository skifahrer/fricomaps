# fricomaps

All-in-one mapová aplikácia. Vektorové mapy Slovenska z OSM dát – jedna
pipeline, jeden formát (PMTiles), spoločné štýly pre web aj mobil.

## Ako to funguje

```
Geofabrik PBF ─► osmium extract (kraj) ─► Planetiler ─► PMTiles ─► GitHub Pages
                                                            │
                                              web (MapLibre GL JS) + iOS (MapLibre Native)
```

- **Pipeline:** [.github/workflows/build-map.yml](.github/workflows/build-map.yml)
  – GitHub Actions workflow konvertuje OSM PBF na PMTiles (OpenMapTiles schéma,
  Planetiler) a nasadí web na GitHub Pages.
- **Výber regiónu:** pri spustení workflowu si vyberieš celé Slovensko alebo
  konkrétny kraj. Výrez sa robí rovnako, ako sa robia oficiálne OSM PBF
  exporty (Geofabrik) – podľa **skutočnej administratívnej hranice** regiónu:
  pipeline si z OSM dát vytiahne polygón relácie kraja
  (`boundary=administrative`, `admin_level=4`) a `osmium extract --polygon`
  vyreže presne územie kraja, nie obdĺžnik. Zoznam regiónov a ich OSM mená sú
  v [pipeline/regions.json](pipeline/regions.json).
- **Web viewer:** [web/](web/) – MapLibre GL JS + pmtiles protokol, beží čisto
  staticky (GitHub Pages podporuje HTTP range requesty, netreba tile server).
- **Témy a štýlovanie:** [web/themes.js](web/themes.js) – 4 farebné témy
  (Svetlá, Tmavá, Outdoor, Retro/Pastel), farbenie ciest/vôd/lesov/budov podľa
  OpenMapTiles schémy + POI ikonky zo spritu osm-liberty (maki). Ten istý
  generátor vyrába statické `styles/{region}-{tema}.json` pre iOS.
- **iOS / multiplatform:** návrh implementácie v
  [docs/ios-multiplatform.md](docs/ios-multiplatform.md).

## Spustenie pipeline (build mapy)

1. **Zapni GitHub Pages:** Settings → Pages → *Build and deployment* →
   Source: **GitHub Actions** (workflow to skúsi zapnúť aj sám cez
   `configure-pages`, ale manuálne nastavenie je spoľahlivejšie).
2. Actions → **Build map (PBF → PMTiles) & deploy Pages** → *Run workflow*:
   - `region`: `slovensko` alebo kraj (`bratislavsky`, `zilinsky`, …)
   - `maxzoom`: `14` (štandard; `12` pre rýchly/malý testovací build)
3. Po dobehnutí (Slovensko ~10–15 min) je mapa na
   `https://<user>.github.io/fricomaps/`.

> Pozn.: ak deploy zlyhá na ochrane prostredia `github-pages`, povoľ v
> Settings → Environments → github-pages nasadzovanie aj z tejto vetvy
> (alebo zmerguj do default vetvy a spusti workflow tam).

## Overenie v prehliadači

Otvor URL Pages – viewer načíta `tiles/manifest.json`, ponúkne výber témy a
regiónu, POI majú ikonky a klik zobrazí popup. Lokálne testovanie webu:

```bash
npx serve web   # ale tiles/ vzniknú až v CI, lokálne uvidíš chybovú obrazovku
```

## Štruktúra

```
.github/workflows/build-map.yml   pipeline PBF → PMTiles → Pages
pipeline/regions.json             regióny (kraje SR) + bboxy
tools/build-styles.mjs            generátor statických style.json (pre iOS)
web/                              viewer (index.html, app.js, themes.js)
docs/ios-multiplatform.md         návrh pre iOS / multiplatform
```

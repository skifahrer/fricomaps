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

## Ako funguje pipeline (2 workflowy)

```
1) Update OSM extracts        Geofabrik slovakia.pbf ─► osmium extract -c
   (raz týždenne / manuálne)  (všetky kraje po OSM admin. hraniciach naraz)
                              ─► release `osm-extracts`: {kraj}.osm.pbf + meta.json

2) Build map                  stiahne IBA {región}.osm.pbf z releasu
   (manuálne, výber regiónu)  ─► Planetiler ─► {región}.pmtiles
                              ─► GitHub Pages (viewer + dlaždice + style.json)
```

- **Výber regiónu:** celé Slovensko alebo ktorýkoľvek z 8 krajov. Exporty
  krajov sa robia rovnako ako oficiálne OSM PBF exporty – po **skutočnej
  administratívnej hranici** (polygón relácie `boundary=administrative`,
  `admin_level=4` z OSM dát), nie po obdĺžniku. Build mapy potom sťahuje
  **len PBF daného regiónu**, nie celé Slovensko.
- **Témy a štýlovanie:** [poc/web/themes.js](poc/web/themes.js) – 4 farebné
  témy (Svetlá, Tmavá, Outdoor, Retro/Pastel), farbenie ciest/vôd/lesov/budov
  podľa OpenMapTiles schémy + POI ikonky zo spritu osm-liberty (maki).
  Ten istý generátor vyrába statické `styles/{region}-{tema}.json` pre iOS.
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
   - `maxzoom`: `14` (štandard; `12` pre rýchly testovací build)
4. Mapa je na `https://<user>.github.io/fricomaps/` – viewer s prepínačom
   témy a regiónu, POI ikonkami a popupmi.

> Pozn.: ak deploy zlyhá na ochrane prostredia `github-pages`, povoľ v
> Settings → Environments → github-pages nasadzovanie aj z tejto vetvy
> (alebo zmerguj do default vetvy a spusti workflow tam).

## Lokálny vývoj

```bash
npx serve poc/web        # viewer (dlaždice vznikajú až v CI)
cd backend && npm install && npm run start:dev   # API na :3000
```

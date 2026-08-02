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
  [osm.fr](https://download.openstreetmap.fr/extracts/europe/) (rezané po
  skutočných administratívnych hraniciach). Kandidátske názvy súborov sú vo
  [workers/regions.json](workers/regions.json) (`osmfr.slugs`); ak žiadny
  nesedí, build vypíše do logu reálny obsah osm.fr adresára a použije
  fallback release `osm-extracts`.
- **Ľubovoľný región Európy/sveta:** pri spúšťaní workflowu vyplň
  `custom_pbf_url` (URL na `.osm.pbf` z osm.fr extracts stromu, napr.
  `https://download.openstreetmap.fr/extracts/europe/austria.osm.pbf`)
  a `custom_name`. Bbox sa prečíta z PBF hlavičky (alebo zadaj `custom_bbox`).
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

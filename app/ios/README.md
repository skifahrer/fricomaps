# FricoMaps iOS

Natívna iOS aplikácia (SwiftUI + MapLibre Native). Návrh architektúry a
multiplatformné alternatívy: [docs/ios-multiplatform.md](../../docs/ios-multiplatform.md).

## Setup (Xcode)

1. Xcode → *File → New → Project* → iOS App (SwiftUI), názov **FricoMaps**,
   umiestni do tohto adresára (`app/ios`).
2. *File → Add Package Dependencies* →
   `https://github.com/maplibre/maplibre-gl-native-distribution` (MapLibre ≥ 6.5).
3. Pridaj do projektu zdrojáky z [`FricoMaps/`](FricoMaps/) (sú pripravené,
   Xcode projekt sa negeneruje v repo – vytvor si ho krokom 1).
4. V `Config.swift` uprav `pagesBaseURL` na svoju GitHub Pages URL.

## Ako to funguje

- Appka načítava **hotové style.json z GitHub Pages**
  (`styles/{region}-{typ mapy}-{tema}.json`), ktoré generuje pipeline –
  rovnaké mapy, témy a dlaždice ako web, žiadna duplicitná logika štýlovania.
- **Typ mapy** (turistická / lyžiarska / cestná / historická / všetko) hovorí,
  čo mapa ukazuje; **téma** len to, ako to vyzerá. Oboje je len iná styleURL,
  takže prepnutie nič nesťahuje navyše. Zoznam typov drží `MapKind`
  v `Config.swift` a musí sedieť s `poc/web/map-types.js`.
- Dlaždice sa čítajú cez `pmtiles://` priamo z Pages (HTTP range requesty);
  MapLibre Native podporuje PMTiles natívne.
- Zoznam regiónov vie appka ťahať z NestJS backendu (`/api/regions`) alebo
  offline z pribaleného `regions.json`.
- Offline mapy: stiahnutie `tiles/{region}.pmtiles` do zariadenia a prepnutie
  zdroja na lokálny súbor (pozri `OfflineManager` TODO v docs).

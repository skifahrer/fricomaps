# FricoMaps na iOS (a multiplatformne) – od PBF po PMTiles

Tento dokument popisuje, ako dostať mapu Slovenska (alebo jednotlivého kraja)
z OSM dát až do iOS aplikácie – a čo z toho už rieši naša pipeline.

## 1. Celková architektúra

```
OSM (Geofabrik)          GitHub Actions pipeline                 Klienti
─────────────────        ─────────────────────────────────       ─────────────────
osm.fr exporty   ──►  build: stiahne iba {región}.osm.pbf
(kraje po admin.      │
 hraniciach)          │
                      │
                      ▼
                 Planetiler (OpenMapTiles schéma)
                      │
                      ▼
                 {region}.pmtiles  ─────────►  GitHub Pages ──►  Web (MapLibre GL JS)
                      +                        (HTTP range   ──►  iOS (MapLibre Native)
                 styles/{region}-{tema}.json    requests)    ──►  Android (MapLibre Native)
                      + sprity (ikonky) + glyfy (fonty)
```

Kľúčová vlastnosť **PMTiles**: je to jediný súbor, z ktorého sa dlaždice čítajú
cez HTTP *range requests* – netreba žiadny tile server. GitHub Pages range
requesty podporuje, takže tá istá URL slúži webu aj mobilom. Ten istý súbor sa
dá stiahnuť do zariadenia a čítať lokálne = plnohodnotný **offline režim**.

## 2. Prečo PMTiles a nie MBTiles/raster

| | PMTiles | MBTiles | Raster (PNG) |
|---|---|---|---|
| Server | žiadny (statický hosting) | potrebný tile server | potrebný/CDN |
| Offline v appke | 1 súbor, priame čítanie | 1 súbor (SQLite) | tisíce súborov |
| Preštýlovanie (témy) | ✅ vektor, štýl na klientovi | ✅ | ❌ nutný rebuild |
| Veľkosť SR (z14) | ~200–400 MB | podobná | rádovo viac |

## 3. Možnosti implementácie na iOS

### A) Natívne: SwiftUI + MapLibre Native (odporúčané pre iOS-first)

[MapLibre Native](https://github.com/maplibre/maplibre-native) sa pridáva cez
Swift Package Manager (`https://github.com/maplibre/maplibre-gl-native-distribution`).
Novšie verzie (2024+) majú **natívnu podporu `pmtiles://` URL schémy** – stačí
v style.json použiť zdroj `pmtiles://https://…/tiles/slovensko.pmtiles`,
presne tak, ako ho generuje naša pipeline do `styles/`.

```swift
import MapLibre
import SwiftUI

struct MapView: UIViewRepresentable {
    let theme: String   // "svetla" | "tmava" | "outdoor" | "retro"
    let region: String  // "slovensko" | "zilinsky" | …

    func makeUIView(context: Context) -> MLNMapView {
        let styleURL = URL(string:
            "https://<user>.github.io/fricomaps/styles/\(region)-\(theme).json")!
        let map = MLNMapView(frame: .zero, styleURL: styleURL)
        map.setCenter(CLLocationCoordinate2D(latitude: 48.7, longitude: 19.5),
                      zoomLevel: 7, animated: false)
        return map
    }
    func updateUIView(_ uiView: MLNMapView, context: Context) {
        uiView.styleURL = URL(string:
            "https://<user>.github.io/fricomaps/styles/\(region)-\(theme).json")!
    }
}
```

**Offline:** stiahni `{region}.pmtiles` (URLSession, background download) do
`Application Support`, uprav v štýle zdroj na
`pmtiles://file:///…/slovensko.pmtiles` (style.json si appka môže upraviť v
pamäti – zmení sa iba `sources.omt.url`). Pri starších verziách MapLibre bez
pmtiles podpory je fallback vstavaný lokálny HTTP server (GCDWebServer) alebo
konverzia `pmtiles → mbtiles` v pipeline.

**Prepínanie tém** = iba zmena `styleURL` (dáta sa nesťahujú znova, dlaždice
zostávajú v cache). Presne tie isté 4 témy ako na webe, lebo JSON generuje
jeden zdroj pravdy: `poc/web/themes.js` → `workers/build-styles.mjs`.
Hotové SwiftUI zdrojáky sú v [`app/ios`](../app/ios).

### B) Multiplatform (iOS + Android z jednej codebase)

| Framework | Balík | Poznámka |
|---|---|---|
| **React Native** | `@maplibre/maplibre-react-native` | najzrelší wrapper, Expo config plugin, štýl = tá istá URL |
| **Flutter** | `maplibre_gl` | aktívne udržiavaný, podpora style URL aj lokálnych súborov |
| **Kotlin Multiplatform** | MapLibre Native (iOS aj Android) + expect/actual wrapper | zdieľaná logika, natívne mapové view |

Vo všetkých troch prípadoch je integrácia rovnaká myšlienka: **appka dostane
len URL na style.json z GitHub Pages** – celé štýlovanie, farby, ikonky aj
zdroj dlaždíc sú v ňom. Mobilná appka tak automaticky dostane nové témy alebo
nové dlaždice bez releasu do App Store.

Odporúčanie: ak je cieľ iOS + Android + už existujúci web, najrýchlejšia cesta
je **React Native (Expo) + maplibre-react-native**; ak sa má znovupoužiť
`web/app.js` takmer 1:1, dá sa web zabaliť aj do **Capacitor**u (WKWebView) –
MapLibre GL JS + pmtiles fungujú vo WKWebView vrátane range requestov.

### C) Ikonky a fonty offline

Štýl odkazuje na sprity (`sprites/osm-liberty.*` na našich Pages) a glyfy
(`fonts.openmaptiles.org`). Pre plný offline režim treba do app bundlu pribaliť:

- 4 sprite súbory (json + png, 1x a 2x) – pipeline ich už kopíruje do `_site/sprites/`,
- pregenerované glyfy pre `Noto Sans Regular/Bold/Italic` (napr. z
  [openmaptiles/fonts](https://github.com/openmaptiles/fonts)),

a v lokálnej kópii style.json prepísať `sprite`/`glyphs` na `file://` cesty
(resp. `asset://` pri React Native).

## 4. Kontrolný zoznam pre iOS MVP

1. ✅ Pipeline generuje `{region}.pmtiles` + `styles/{region}-{tema}.json` (hotové v tomto repe)
2. ✅ Overenie v prehliadači na GitHub Pages (hotové – `web/`)
3. ☐ Xcode projekt + SPM závislosť MapLibre Native
4. ☐ `MapView` (kód vyššie) + picker témy/regiónu
5. ☐ Download manažér pre offline `.pmtiles` + prepnutie zdroja na lokálny súbor
6. ☐ Pribalené sprity a glyfy pre offline
7. ☐ (voliteľné) GPS poloha, vyhľadávanie (offline geocoding – napr. vygenerovať
   z PBF zoznam sídiel/ulíc do SQLite v tej istej pipeline)

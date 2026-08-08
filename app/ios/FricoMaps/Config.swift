import Foundation

enum Config {
    /// GitHub Pages, kam pipeline nasadzuje dlaždice a štýly.
    static let pagesBaseURL = URL(string: "https://skifahrer.github.io/fricomaps")!

    /// NestJS backend (voliteľné – zoznam regiónov, budúce API).
    static let apiBaseURL = URL(string: "http://localhost:3000/api")!

    /// Najvyšší zoom, ktorý vie vygenerovať Planetiler (MAX_MAXZOOM = 16).
    static let maxTileZoom: Double = 16

    /// Najvyšší zoom v aplikácii – nad maxTileZoom ide overzoom.
    /// Musí sedieť s MAX_DISPLAY_Z v poc/web/themes.js.
    static let maxDisplayZoom: Double = 20

    /// Pipeline generuje štýl pre každú kombináciu typ mapy × téma.
    static func styleURL(region: String, kind: MapKind, theme: MapTheme) -> URL {
        pagesBaseURL.appendingPathComponent(
            "styles/\(region)-\(kind.rawValue)-\(theme.rawValue).json")
    }
}

/// Témy musia sedieť s kľúčmi v poc/web/themes.js (zdroj pravdy štýlov).
enum MapTheme: String, CaseIterable, Identifiable {
    case svetla, tmava, outdoor, retro

    var id: String { rawValue }

    var label: String {
        switch self {
        case .svetla: return "Svetlá"
        case .tmava: return "Tmavá"
        case .outdoor: return "Outdoor"
        case .retro: return "Retro"
        }
    }
}

/// Typ mapy – **čo** mapa ukazuje (téma rieši, ako to vyzerá).
/// Musí sedieť s id v poc/web/map-types.js.
enum MapKind: String, CaseIterable, Identifiable {
    case turisticka, lyziarska, cestna, historicka, zakladna

    var id: String { rawValue }

    var label: String {
        switch self {
        case .turisticka: return "Turistická"
        case .lyziarska: return "Lyžiarska"
        case .cestna: return "Cestná"
        case .historicka: return "Historická"
        case .zakladna: return "Všetko"
        }
    }
}

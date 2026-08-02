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

    static func styleURL(region: String, theme: MapTheme) -> URL {
        pagesBaseURL.appendingPathComponent("styles/\(region)-\(theme.rawValue).json")
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

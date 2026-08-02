import Foundation

enum Config {
    /// GitHub Pages, kam pipeline nasadzuje dlaždice a štýly.
    static let pagesBaseURL = URL(string: "https://skifahrer.github.io/fricomaps")!

    /// NestJS backend (voliteľné – zoznam regiónov, budúce API).
    static let apiBaseURL = URL(string: "http://localhost:3000/api")!

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

import SwiftUI

struct ContentView: View {
    @State private var kind: MapKind = .turisticka
    @State private var theme: MapTheme = .svetla
    @State private var region = "slovensko"

    var body: some View {
        ZStack(alignment: .topLeading) {
            MapView(region: region, kind: kind, theme: theme)
                .ignoresSafeArea()

            VStack(spacing: 8) {
                // Typ mapy hovorí, čo mapa ukazuje (turistika / lyžovanie /
                // cesty / história), téma len to, ako to vyzerá.
                Picker("Typ mapy", selection: $kind) {
                    ForEach(MapKind.allCases) { kind in
                        Text(kind.label).tag(kind)
                    }
                }
                .pickerStyle(.segmented)

                Picker("Téma", selection: $theme) {
                    ForEach(MapTheme.allCases) { theme in
                        Text(theme.label).tag(theme)
                    }
                }
                .pickerStyle(.segmented)
            }
            .padding(12)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
            .padding()
        }
    }
}

#Preview {
    ContentView()
}

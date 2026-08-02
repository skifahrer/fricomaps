import SwiftUI

struct ContentView: View {
    @State private var theme: MapTheme = .svetla
    @State private var region = "slovensko"

    var body: some View {
        ZStack(alignment: .topLeading) {
            MapView(region: region, theme: theme)
                .ignoresSafeArea()

            Picker("Téma", selection: $theme) {
                ForEach(MapTheme.allCases) { theme in
                    Text(theme.label).tag(theme)
                }
            }
            .pickerStyle(.segmented)
            .padding(12)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
            .padding()
        }
    }
}

#Preview {
    ContentView()
}

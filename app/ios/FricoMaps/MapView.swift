import MapLibre
import SwiftUI

/// SwiftUI wrapper nad MLNMapView (MapLibre Native).
/// Štýl (farby, ikonky, zdroj PMTiles dlaždíc) prichádza celý z Pages –
/// prepnutie témy alebo regiónu je len zmena styleURL.
struct MapView: UIViewRepresentable {
    let region: String
    let kind: MapKind
    let theme: MapTheme

    func makeUIView(context: Context) -> MLNMapView {
        let mapView = MLNMapView(
            frame: .zero,
            styleURL: Config.styleURL(region: region, kind: kind, theme: theme))
        mapView.setCenter(
            CLLocationCoordinate2D(latitude: 48.7, longitude: 19.5),
            zoomLevel: 7,
            animated: false
        )
        // Dlaždice končia na zoome Config.maxTileZoom (limit Planetileru);
        // vyššie priblíženie dopočíta MapLibre overzoomom.
        mapView.maximumZoomLevel = Config.maxDisplayZoom
        mapView.showsUserLocation = true
        mapView.logoView.isHidden = true
        return mapView
    }

    func updateUIView(_ mapView: MLNMapView, context: Context) {
        let url = Config.styleURL(region: region, kind: kind, theme: theme)
        if mapView.styleURL != url {
            mapView.styleURL = url
        }
    }
}

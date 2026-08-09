#!/usr/bin/env bash
# Planetiler do `planetiler.jar` v pracovnom priečinku, ak tam ešte nie je.
#
# Sťahujú si ho ŠTYRI joby (tiles, contours, trails, features) – každý má
# vlastný runner a vlastnú cache, takže sa to spraviť raz a podať ďalej nedá.
# Kým to boli štyri kópie toho istého `run:` bloku, bola to štvornásobná
# príležitosť, aby sa rozišli: verzia sa zmení na jednom mieste a tri joby
# ticho stavajú z inej. Jedna otázka, jedna odpoveď, jedno miesto.
#
# Použitie:  workers/fetch-planetiler.sh
set -euo pipefail

JAR="${JAR:-planetiler.jar}"
URL="${PLANETILER_URL:-https://github.com/onthegomap/planetiler/releases/latest/download/planetiler.jar}"

[ -s "$JAR" ] && { echo "Planetiler z cache ✓"; exit 0; }
curl -fL --retry 4 --retry-delay 5 -o "$JAR" "$URL"

#!/usr/bin/env bash
# Čím sa Planetileru povie „drž sa regiónu": `--polygon`, ALEBO `--bounds`.
#
# PREČO SPOLOČNÝ SÚBOR: dlaždice z regionálneho PBF robia TRI joby – `tiles`
# (mapa), `trails` (značené trasy) a `features` (krajinné prvky). Orez musí byť
# vo všetkých rovnaký; vrstva, ktorá siaha ďalej než ostatné, je presne ten
# tichý rozdiel, na ktorý je pravidlo 1. Tri kópie dvoch riadkov by sa raz
# rozišli.
#
# ČO TO RIEŠI. Planetiler si rozsah berie z hlavičky PBF, čiže z OBDĹŽNIKA
# bboxu – a do tých dlaždíc kreslí okrem OSM dát aj vodstvo, pobrežia a
# Natural Earth, ktoré sú celosvetové. Bbox Prešovského kraja je 199 × 82 km,
# takmer dvojnásobok jeho plochy, takže mapa pokračovala do Poľska aj na
# Ukrajinu – s podfarbeným prázdnom bez ciest a sídel. Po stiahnutí regiónu do
# telefónu to vyzerá ako mapa, ktorá sa nedonačítala. `--polygon` je
# v Planetileri „emit any tile that intersects the shape", takže sa tie
# dlaždice prestanú vyrábať.
#
# ═══ `--bounds` A `--polygon` NARAZ NEDÁVAJ. TICHO SI VYPNEŠ POLYGÓN. ═══
#
# `Bounds` si `tileExtents` (to, čo o dlaždici rozhoduje) spočíta UŽ
# V KONŠTRUKTORE, teda z `--bounds` a s prázdnym tvarom; `setShape()` potom
# tvar priradí, ale keď `--bounds` prišlo, prepočet nespustí – a `tileExtents()`
# si vezme, čo je odložené. Polygón je teda v logu vidieť („argument:
# polygon=…"), Planetiler nepovie ani slovo a orezané NIE JE nič.
#
# Namerané na Monaku (planetiler.jar z releases, maxzoom 15, `generate-custom`):
#
#     bez orezu                      27 dlaždíc
#     --polygon (polovica územia)    17 dlaždíc   ← funguje
#     --polygon + --bounds           27 dlaždíc   ← polygón ignorovaný
#
# Preto sa `--bounds` dáva LEN vtedy, keď polygón nie je. Bez oboch si
# Planetiler vezme bbox z hlavičky PBF, čo je to isté územie – `--bounds` je
# v tej vetve poistka pre PBF s nepresnou hlavičkou (napr. po `osmium extract`).
#
# JE TO HRUBÝ OREZ – po celé dlaždice (na z14 ~1,5 km). Presnú hranicu dokreslí
# až štýl z `_site/region.geojson` (`workers/deploy/region-mask.py`); toto je tá
# polovica, vďaka ktorej sa dlaždice mimo regiónu ani nevyrobia, ani nestiahnu.
# A to je zároveň dôvod, prečo `crop_bbox` neprekáža: mapa je vtedy menšia než
# polygón, ale dlaždice sa berú z FEATUR, takže mimo orezaného PBF nemá čo
# vzniknúť, a maska v štýle je počítaná ako prienik regiónu s bboxom behu.
#
# Použitie:
#   mapfile -t CLIP < <(workers/lib/region-clip.sh "$REGION_BBOX")
#   java -jar planetiler.jar … "${CLIP[@]}"
set -euo pipefail

BBOX="${1:-}"
POLY="${2:-data/region.poly}"

# Argumenty idú na stdout (volajúci si ich načíta), vysvetlenie do logu.
if [ -s "$POLY" ]; then
  echo "--polygon=$POLY"
  echo "Orez na región: $POLY – dlaždice mimo regiónu sa nevyrobia. (\`--bounds\` sa zámerne NEPRIDÁVA, tichý vypínač polygónu – viď hlavičku skriptu.)" >&2
else
  # (`set -e`: `[ … ] && echo` by pri prázdnom bboxe zhodilo skript.)
  if [ -n "$BBOX" ]; then echo "--bounds=$BBOX"; fi
  echo "::warning::Polygón regiónu ($POLY) nie je, takže sa dlaždice vyrobia na CELOM obdĺžniku bboxu – mapa bude siahať aj za región (vodstvo a Natural Earth kreslí Planetiler všade). Zvyčajne to znamená, že sa v jobe \`plan\` nestiahol \`.poly\`; skús beh zopakovať." >&2
fi

#!/usr/bin/env bash
# Balíky mapy ešte raz ako Apple Archive (`.aar`) a hore na Drive.
#
# PREČO SAMOSTATNÝ SKRIPT: `build-map.yml` má strop 128 KiB a nad ním ho GitHub
# ticho NEPRIJME – po pushi vznikne beh bez jobov s prázdnym logom. Job, ktorý
# toto volá, pridal do súboru dva kilobajty a bol už na 125; rozpis teda patrí
# sem (stráži to `Lint workflows`).
#
# ČO TO ROBÍ. To isté, čo `deploy` spravil so ZIPmi, len s druhou príponou:
# `publish-map.py --format=aar`. Ten istý obsah, tie isté stále mená, ten istý
# priečinok na Drive. iOS a macOS `.aar` rozbalia SYSTÉMOVO (framework
# AppleArchive), bez tretej knižnice v aplikácii, a LZFSE je na Apple hardvéri
# rýchlejšie než deflate. ZIP tým nezaniká – otvorí ho čokoľvek.
#
# NÁSTROJ `aa` JE LEN NA macOS, a preto to nie je krok v `deploy`, ale vlastný
# job na `macos-latest`. Keby tu `aa` nebol, `publish-map.py` spadne sám a
# povie prečo; kontrola nižšie je len o to skôr a s menej mätúcou hláškou.
#
# Hodnoty z prostredia (viď job „Balíky ako Apple Archive" v build-map.yml) –
# je to ten istý zoznam, aký dostáva krok „Publikuj mapu na Drive", lebo sa
# z neho skladá `obsah.json` v balíku:
#   REGION_KEY AREA_KEY AREA_BBOX TEST_KM2 TILES_MAXZOOM
#   CONTOURS_ENABLED CONTOURS_SOURCE CONTOUR_INTERVAL
#   ROCKS_ENABLED ROCKS_SOURCE TERRAIN_ENABLED TERRAIN_SOURCE
#   TRAILS_ENABLED FEATURES_ENABLED CUSTOM_NAME CUSTOM_PBF_URL
# a k tomu prihlásenie na Drive z `env:` celého workflowu.
set -euo pipefail

if ! command -v aa >/dev/null 2>&1; then
  echo "::error::Nástroj \`aa\` tu nie je. Apple Archive je súčasť macOS 11+, takže tento job musí bežať na \`macos-latest\`; na Linuxe sa \`.aar\` vyrobiť nedá."
  exit 1
fi
echo "Apple Archive: $(command -v aa)"

# Články z Wikipédie sú štvrtý balík a majú vlastný artefakt – keď ich build
# nerobil, priečinok tu jednoducho nie je a `--wiki` ostane prázdne.
WIKI=""
[ -f _wiki/index.json ] && WIKI=_wiki

python3 workers/deploy/publish-map.py --site=_site --format=aar \
  --wiki="$WIKI" \
  --maps=maps.json \
  --summary="${GITHUB_STEP_SUMMARY:-/dev/null}"

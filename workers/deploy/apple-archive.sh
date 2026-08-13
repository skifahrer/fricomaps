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
# a k tomu WIKI_ENABLED – „mali prísť články?", aby sa dalo odlíšiť „wiki
# v tomto builde nie je" od „artefakt sa nestiahol" (viď nižšie)
# a k tomu prihlásenie na Drive z `env:` celého workflowu.
set -euo pipefail

if ! command -v aa >/dev/null 2>&1; then
  echo "::error::Nástroj \`aa\` tu nie je. Apple Archive je súčasť macOS 11+, takže tento job musí bežať na \`macos-latest\`; na Linuxe sa \`.aar\` vyrobiť nedá."
  exit 1
fi
echo "Apple Archive: $(command -v aa)"

# ---------- články z Wikipédie ----------
# Štvrtý balík, s vlastným artefaktom – a rovnako ako ostatné tri ide aj ako
# `.aar`. Keď ho build nerobil, priečinok tu jednoducho nie je a `--wiki`
# ostane prázdne.
#
# ROZLÍŠIŤ „NEBOLI" OD „NEPRIŠLI" JE TU NUTNÉ. Keď `_wiki` chýba, považuje
# `publish-map.py` ten balík za „v tomto builde nie je" a starý `.aar` na
# Drive ZMAŽE – aby vedľa novej mapy neostal balík z iného behu. To je
# správne, kým články naozaj neboli; keby sa len nestiahol artefakt, zmazal
# by sa dobrý balík kvôli výpadku prenosu. `WIKI_ENABLED` hovorí, či mali
# prísť, takže sa tie dva prípady dajú odlíšiť a druhý zhodí job.
WIKI=""
if [ -f _wiki/index.json ]; then
  WIKI=_wiki
  echo "Články z Wikipédie: $(du -sh _wiki | cut -f1) – pribalia sa ako .aar"
elif [ "${WIKI_ENABLED:-false}" = 'true' ]; then
  echo "::error::Job wiki články vyrobil, ale artefakt wiki-articles sa sem nestiahol (_wiki/index.json tu nie je). Nepokračujem: bez neho by publish-map.py považoval balík za nevyrobený a starý -wikipedia.aar na Drive by zmazal. Pozri krok „Stiahni články z Wikipédie“ v tomto jobe."
  exit 1
else
  echo "Články z Wikipédie: v tomto builde nie sú."
fi

python3 workers/deploy/publish-map.py --site=_site --format=aar \
  --wiki="$WIKI" \
  --maps=maps.json \
  --summary="${GITHUB_STEP_SUMMARY:-/dev/null}"

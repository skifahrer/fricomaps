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
# a k tomu ONLY / WIKI – ktorý balík sa ide robiť (prázdne = balíky mapy
# z `_site`, `wikipedia` = jediný balík s článkami z `WIKI`) – a BRANCH,
# z ktorej si vypýtať čerstvý `maps.json` (viď nižšie)
# a prihlásenie na Drive z `env:` celého workflowu.
set -euo pipefail

if ! command -v aa >/dev/null 2>&1; then
  echo "::error::Nástroj aa tu nie je. Apple Archive je súčasť macOS 11+, takže tento job musí bežať na macos-latest; na Linuxe sa .aar vyrobiť nedá."
  exit 1
fi
echo "Apple Archive: $(command -v aa)"

# ---------- čo sa ide baliť ----------
# `ONLY` prázdne = balíky mapy z `_site` (Build map). `ONLY=wikipedia` = jediný
# balík s článkami z `WIKI` (workflow „Wikipédia k mape"). Je to ten istý
# skript pre obe pipeline zámerne: „ako sa vyrobí .aar a nahrá na Drive" je
# jedna otázka a dve kópie by sa raz rozišli – jedna by mala strážcu, druhá nie.
ONLY="${ONLY:-}"
WIKI="${WIKI:-}"
ARGS=(--format=aar --maps=maps.json --summary="${GITHUB_STEP_SUMMARY:-/dev/null}")

if [ -n "$ONLY" ]; then
  ARGS+=(--only="$ONLY")
  if [ -z "$WIKI" ] || [ ! -f "$WIKI/index.json" ]; then
    echo "::error::ONLY=$ONLY, ale články nie sú (WIKI=${WIKI:-prázdne}). Nepokračujem: publish-map.py by spadol na prázdnom balíku."
    exit 1
  fi
  ARGS+=(--wiki="$WIKI" --site="${SITE:-_site}")
  echo "Balí sa jediný balík: $ONLY ($(du -sh "$WIKI" | cut -f1))"
else
  # ---------- je `_site` naozaj zložené? ----------
  # Job si `_site` skladá z artefaktov `site-*`, teda z KUSOV od jednotlivých
  # jobov – to je stav PRED zložením: dlaždice, fonty a sprite, ale bez štýlov,
  # bez viewera a bez `manifest.json`. Tie vyrába až `deploy` a posiela ich sem
  # artefaktom `deploy-site`.
  #
  # Prvý ostrý beh (31741329496) to ukázal presne: `.aar` mal 787 súborov proti
  # 828 v ZIPe a v logu bolo len varovanie „manifest.json sa nedá prečítať".
  # Boli to dve chyby naraz a ani jedna nebola na súbore vidieť:
  #   1. `.aar` „celá mapa" sa dal rozbaliť, ale ako mapa by sa NEOTVORIL,
  #   2. bez manifestu skladá `publish-map.py` položku katalógu bez bboxu,
  #      zoomov a zdroja výšok – a keďže sa položka prepisuje celá, ostrý beh
  #      by tie polia z `maps.json` ODSTRÁNIL.
  # Preto je to tu tvrdá chyba, nie varovanie.
  if [ ! -f _site/tiles/manifest.json ]; then
    echo "::error::_site nie je zložené – chýba tiles/manifest.json (a s ním štýly aj viewer). Sem chodia kusy site-* a navrch artefakt deploy-site z jobu deploy; pozri krok „Pozbieraj zloženú časť webu“. Nepokračujem: .aar by nebol mapa a maps.json by prišiel o bbox a zoomy."
    exit 1
  fi
  if [ ! -d _site/styles ]; then
    echo "::error::_site nemá priečinok styles – bez štýlov nie je .aar mapa, len dlaždice. Pozri krok „Pozbieraj zloženú časť webu“."
    exit 1
  fi
  ARGS+=(--site=_site)
  echo "Zložené _site ✓ ($(find _site -type f | wc -l | tr -d ' ') súborov, $(du -sh _site | cut -f1))"
  # Články z Wikipédie tu NIE SÚ: majú vlastnú pipeline, ktorá si `.aar` robí
  # sama. Bez `--wiki` ich `publish-map.py` do zoznamu nezaradí, takže ich
  # tento beh ani nezmaže.
fi

# ---------- katalóg čítaj z VETVY, nie z checkoutu ----------
# Tento job beží AŽ PO tom, čo ten predošlý nahral ZIP a `maps.json` do vetvy
# commitol. Checkout má ale SHA, s ktorým beh začal – čiže `maps.json` BEZ
# toho zápisu. `publish-map.py` by teda skladal položku zo stavu spred pár
# desiatok sekúnd a `catalog.sh` by ju potom rebasoval na commit, ktorý mení
# ten istý riadok. Rebase neprejde a zápis sa ZAHODÍ – s varovaním, ale job
# ostane zelený.
#
# Nie je to teoretické: beh 31746901920 skončil presne takto. Oba joby zelené,
# na Drive `.aar` aj `.zip`, a v `maps.json` len `formats.zip`. Konflikt je
# pritom SYSTEMATICKÝ, nie smola – ten predošlý job commituje vždy.
#
# Stačí si pred zápisom vypýtať najnovšiu podobu súboru: potom je to, čo
# `publish-map.py` napíše, prírastok nad aktuálnym stavom a rebase je
# triviálny. `|| true`: keď `maps.json` vo vetve ešte nie je (prvá mapa
# vôbec), zostane ten z checkoutu a `publish-map.py` ho založí.
BRANCH="${BRANCH:-master}"
if git fetch --depth=1 origin "$BRANCH" >/dev/null 2>&1 \
   && git checkout FETCH_HEAD -- maps.json 2>/dev/null; then
  echo "maps.json: čerstvý z vetvy $BRANCH (ten predošlý job doň práve zapísal)"
else
  echo "maps.json: vo vetve $BRANCH ho nemám – beriem ten z checkoutu."
fi

python3 workers/deploy/publish-map.py "${ARGS[@]}"

#!/usr/bin/env bash
# Kľúče cache pre celý build – na jednom mieste.
#
# PREČO SAMOSTATNÝ SKRIPT: `build-map.yml` má strop 128 kB a nad ním ho GitHub
# ticho neprijme (stráži to Lint workflows).
#
# PREČO JEDEN ZDROJ. Kľúč potrebuje restore, save AJ mazanie pri pregenerovaní.
# Keby bol napísaný trikrát, stačí ho raz zabudnúť opraviť a cache sa ticho
# rozsype: ukladá sa pod iný kľúč, než sa hľadá, takže sa všetko počíta odznova
# a nikto nevie prečo.
#
# ČO PATRÍ DO KĽÚČA je vždy „to, čo mení obsah" – a nič viac. Prah sklonu
# napríklad NIE JE v kľúči skladu častí sklonu: uplatňuje sa až pri
# vektorizácii, takže po jeho zmene sa sklad použije a preráta sa len tá lacná
# časť (minúty namiesto hodiny čítania z Drive).
#
# `hashFiles` je funkcia GitHubu, nie shellu – jej výsledok chodí ako
# SCHEMA_HASH.

set -euo pipefail
# Územie, na ktorom sa počíta z DEM – pri rýchlom teste testovací
# štvorec, inak celý región. Všetky kľúče nižšie sú o vrstvách z DEM.
B="$DEM_BBOXKEY"
# Otlačok releasu na vrstvu – každá si vyberá zdroj sama, takže
# jeden spoločný by po doplnení ktoréhokoľvek releasu zahodil cache
# všetkých troch.
DC="$DEMKEY_CONTOURS"
DR="$DEMKEY_ROCKS"
DT="$DEMKEY_TERRAIN"
CS="$OPT_CONTOUR_SOURCE"
RS="$OPT_ROCK_SOURCE"
TS="$OPT_SHADING_SOURCE"
{
  # Výrez skál je v kľúči tiež – inak by sa skaly len z Tatier
  # vrátili z cache ako keby to boli skaly celého kraja.
  RA=$(printf '%s' "$AREA_IN" | tr -c 'a-zA-Z0-9' '_')
  # v8: vrstevnice a skaly majú vlastný zdroj, takže sú v kľúči oba
  # (a oba otlačky). Zdroj skál je v ňom aj preto, že `sonny`
  # a `tienovanie` dávajú úplne iné plochy – jedny sa nesmú vrátiť
  # z cache namiesto druhých.
  echo "contours=contours-v9-c$CS$DC-r$RS$DR-$B-i${CONTOUR_INTERVAL}-z${OPT_CONTOUR_MAXZOOM}-rz${OPT_ROCK_MAXZOOM}p${OPT_ROCK_PLNE}d${OPT_ROCK_ZAPLN_DIERY}-s${OPT_CONTOUR_SMOOTHING}-${ROCK_SLOPE}g${OPT_ROCK_RES}a$RA-${OPT_ROCK_IMG_ASSET}-${SCHEMA_HASH}"
  # Stiahnuté DEM dlaždice. Sú v podpriečinku podľa zdroja, takže
  # jeden job môže mať naraz dva modely (vrstevnice z jedného,
  # skaly z druhého) – kľúč preto nesie obe mená.
  echo "demtiles_contours=demtiles-v2-c$CS$DC-r$RS$DR-$B"
  echo "demtiles_terrain=demtiles-v2-t$TS$DT-$B"
  echo "terrain=terrain-v2-$TS-$DT-$B-z${OPT_TERRAIN_MAXZOOM}"
  # Sklad častí sklonu. V kľúči je VÝREZ, MODEL a MRIEŽKA – teda
  # presne to, čo mení obsah častí. Prah sklonu v ňom zámerne NIE
  # JE: uplatňuje sa až pri vektorizácii, takže po jeho zmene sa
  # sklad použije a preráta sa len tá lacná časť (minúty namiesto
  # hodiny čítania z Drive). Prefix musí sedieť s `restore-keys`
  # v jobe s vrstevnicami.
  # Končí pomlčkou zámerne: je to PREFIX. Cache v GitHube sa nedá
  # prepísať, takže keby bol kľúč pevný, prvý beh by ho zabral a
  # časti dopočítané v ďalších behoch by sa už nikdy neuložili –
  # sklad by navždy ostal taký, aký bol po prvom behu. Ukladá sa
  # preto pod prefix + číslo behu a obnovuje sa cez `restore-keys`,
  # čiže vždy z najnovšieho záznamu, ktorý na prefix sedí.
  echo "slope=slope-v1-${AREA_KEY}-$RS-g${OPT_ROCK_RES}-"
} >> "$GITHUB_OUTPUT"
cat "$GITHUB_OUTPUT"

#!/usr/bin/env bash
# „Koľko zoomov sa zmestí do rozpočtu stránky" – jedna otázka, jeden súbor.
#
# Definuje funkciu `pmtiles_do_rozpoctu`, ktorú SOURCUJE `contours-build.sh`
# (vrstevnice aj skaly). Vlastný súbor preto, že je to iná otázka než „ako
# vzniká vrstevnica": tá hovorí o teréne, táto o veľkosti výsledku a o tom,
# čo znesie stránka. A tiež preto, že `contours-build.sh` narazil na strop
# 800 riadkov (pravidlo 5 v CLAUDE.md).
#
# Po návrate drží použitý zoom `PM_Z` a veľkosť `PM_MB` – návratová hodnota by
# sa miešala s výstupom Planetilera, ktorý ide na stdout.
#
# Používa sa takto (`planetiler.jar` musí byť v pracovnom priečinku):
#     . workers/lib/pmtiles-budget.sh
#     pmtiles_do_rozpoctu <schéma> <výstup> <maxzoom> <strop MB> <dno> \
#                         <popis> <rada pri prekročení> [<strop zoomu>]

# Rozpočet sa hľadá OBOMA SMERMI – dole, keď sa výsledok nezmestí, a hore, keď
# v ňom ostalo miesto. To druhé je kvôli zubatým vrstevniciam pri max zoome:
# dlaždica má súradnice na celočíselnej mriežke 4096 (extent), Planetiler ju
# meniť nevie, takže krok mriežky určuje zoom – z14 0,391 m, z16 0,098 m. Nad
# svojím maxzoomom sa vrstevnice už len naťahujú a práve ten krok vidno ako
# schodíky: lomov nad 30° je pri z14 11,5 % namiesto 1,6 %, pri z16 2,1 %.
# Dočistenie po Chaikinovi to ZHORŠUJE – jediná páka je maxzoom. Merané
# v `workers/contours-rocks/measure-smoothing.py --maxzoom`, rozpis v docs/pipeline.md.
#
# Zoom sa preto dvíha, kým sa vrstevnice do svojho podielu zmestia: úroveň
# navyše je zhruba dvojnásobok (kraj má pri z14 namerané 187 MB, teda sa
# nedvihne), výrez pohoria má do stropu ďaleko. Po jednej, vždy meraním.
pmtiles_do_rozpoctu() { # $1 schéma $2 výstup $3 maxzoom $4 strop MB $5 dno $6 popis $7 rada $8 strop zoomu
  PM_Z="$3"
  local strop="${8:-$3}" znizovane=""
  while : ; do
    java -Xmx5g -jar planetiler.jar generate-custom \
      --schema="$1" \
      --output="$2" \
      --maxzoom="$PM_Z" --render_maxzoom="$PM_Z" \
      --simplify_tolerance_at_max_zoom=0 \
      --min_feature_size_at_max_zoom=0 \
      --force

    PM_MB=$(( $(stat -c%s "$2") / 1048576 ))
    echo "$6 maxzoom $PM_Z → ${PM_MB} MB (strop ${4} MB)"

    if [ "$PM_MB" -gt "$4" ]; then
      if [ "$PM_Z" -le "$5" ]; then
        echo "::warning::$6 majú ${PM_MB} MB ani pri maxzoome ${5} – $7"
        break
      fi
      PM_Z=$(( PM_Z - 1 ))
      # Keď sa raz znižovalo, už sa nedvíha: inak by sa beh hojdal medzi
      # dvomi zoomami donekonečna (zmestí sa → skús vyššie → nezmestí sa →
      # späť → zmestí sa…), a každé hojdanie je celý beh Planetilera.
      znizovane=1
      echo "::warning::$6 sú nad stropom ${4} MB – skúšam maxzoom ${PM_Z}."
      continue
    fi

    # Zmestilo sa. Ostalo miesto na ďalšiu úroveň? Odhad je dvojnásobok (úroveň
    # navyše pridá asi toľko, koľko je všetko pod ňou); keby bol prihrubý,
    # vetva vyššie to vráti späť.
    if [ -z "$znizovane" ] && [ "$PM_Z" -lt "$strop" ] \
       && [ $(( PM_MB * 2 )) -le "$4" ]; then
      PM_Z=$(( PM_Z + 1 ))
      echo "$6: v rozpočte ostalo miesto (${PM_MB} MB z ${4} MB, ďalšia úroveň" \
           "vyjde asi na $(( PM_MB * 2 )) MB) – skúšam maxzoom ${PM_Z}," \
           "aby pri max zoome neboli schodíky z mriežky dlaždice."
      continue
    fi
    break
  done
}

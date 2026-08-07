#!/usr/bin/env bash
# Súhrn behu „Build map" do záložky Summary: čo sa robilo, ako dlho to trvalo
# a s akým detailom. Riadky si každý job odložil do svojho `steps-*` artefaktu;
# `deploy` ich zlepí a zoradí podľa poradového čísla, nie podľa času – joby
# bežia súbežne, takže čas by hovoril len o tom, ktorý runner bol rýchlejší.
#
# PREČO SAMOSTATNÝ SKRIPT A NIE `run:` V WORKFLOWE: workflow súbor má strop
# 128 KiB a `build-map.yml` bol tesne nad ním. GitHub taký súbor NEPRIJME –
# neohlási chybu, len po pushi vyrobí beh bez jobov, pomenovaný cestou
# k súboru. Deväť kilobajtov markdownu je preto tu.
#
# Hodnoty z workflowu chodia cez prostredie (viď krok „Súhrn buildu"):
#   REGION_NAME  R_PLAN R_CONTOURS R_SHADING_ROCKS R_TRAILS R_TERRAIN
#   R_TILES R_ASSETS  SRC_CONTOURS SRC_ROCKS SRC_SHADING
#   USED_CONTOURS USED_ROCKS USED_SHADING  SIZE_LIMIT_MB  PAGE_URL
# Očakáva aj `gh` a premenné GITHUB_* od runnera.

set -uo pipefail
S="$GITHUB_STEP_SUMMARY"
hms() { printf '%d:%02d:%02d' $(( $1 / 3600 )) $(( $1 % 3600 / 60 )) $(( $1 % 60 )); }

# Celkový čas behu je čas celého workflowu, nie tohto jobu – joby
# bežia súbežne, takže súčet ich časov by klamal.
STARTED=$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" \
  -q .run_started_at 2>/dev/null || echo '')
if [ -n "$STARTED" ]; then
  TOTAL=$(( $(date +%s) - $(date -d "$STARTED" +%s) ))
else
  TOTAL=0
fi

{
  echo "# ${REGION_NAME}"
  echo
  echo "Celý beh: **$(hms "$TOTAL")** (joby bežali súbežne, súčet nižšie je väčší)"
  echo
  echo "| job | výsledok |"
  echo "|---|---|"
  echo "| Príprava | ${R_PLAN} |"
  echo "| Vrstevnice a skaly | ${R_CONTOURS} |"
  echo "| Skaly z tieňovania | ${R_SHADING_ROCKS} |"
  echo "| Značené trasy | ${R_TRAILS} |"
  echo "| Tieňovanie a 3D terén | ${R_TERRAIN} |"
  echo "| Mapové dlaždice | ${R_TILES} |"
  echo "| Ikonky a fonty | ${R_ASSETS} |"
  echo
  echo "## Čo sa robilo"
  echo
  echo "| krok | trvanie | výsledok |"
  echo "|---|--:|---|"
} >> "$S"

if [ -d steps-out ] && [ -n "$(find steps-out -name '*.tsv' 2>/dev/null)" ]; then
  # Prvé pole je len na zoradenie (`sort -n`), ďalej sa nepoužíva.
  cat steps-out/*.tsv | sort -n | while IFS=$'\t' read -r _ord name secs detail; do
    [ -n "$name" ] || continue
    printf '| %s | %s | %s |\n' "$name" "$(hms "${secs:-0}")" "$detail" >> "$S"
  done
else
  echo "| — | — | žiadny job sa nedostal po prvý meraný krok |" >> "$S"
fi

# ---- detail skál ----
# Čísla píše workers/rock-areas.py; job s vrstevnicami ich pribalil
# k meraniu krokov, takže ich súhrn má aj pri behu bez výpočtu.
if [ -s steps-out/rock-stats.txt ]; then
  # shellcheck disable=SC1091
  . steps-out/rock-stats.txt
fi

# Skaly z tieňovaných dlaždíc majú vlastnú tabuľku: nemajú sklon,
# mriežku ani bunku DEM, takže tá dole by bola stĺpec otáznikov
# a pod ním text o izolínii sklonu, ktorá tu nikdy nevznikla.
if [ "${source:-dem}" = "tienovanie" ]; then
  {
    echo
    echo "## Skalné plochy – z tieňovaných dlaždíc"
    echo
    echo "| vlastnosť | hodnota |"
    echo "|---|---|"
    echo "| územie | ${area_name:-celý región}${area_bbox:+ (\`$area_bbox\`)} |"
    echo "| počet samostatných plôch | ${count:-?} |"
    echo "| zdroj | ${asset:-release dem-rocks-img} |"
    echo
    echo "Tieto skaly sa v tomto behu **nepočítali**. Našiel ich workflow"
    echo "*Skaly z tieňovaných dlaždíc* ako tmavé plochy v hillshade JPG"
    echo "z freemap.sk a build si ich len stiahol z releasu \`dem-rocks-img\`."
    echo "Podrobné čísla (prahy, zoom, koľko dlaždíc) sú v súhrne toho behu."
    echo
    echo "> ⚠️ Hillshade je osvetlený z jednej strany, takže sú v ňom tmavé"
    echo "> **severozápadné** steny a svetlé juhovýchodné. Táto vrstva teda"
    echo "> časť skál systematicky nemá. Skaly zo sklonu výškového modelu"
    echo "> (\`rock_source: sonny\` / \`dmr35\` / \`dmr5\` / \`ugkk\`) touto"
    echo "> vadou netrpia."
  } >> "$S"
elif [ -s steps-out/rock-stats.txt ]; then
  {
    echo
    echo "## Skalné plochy – aký to je detail"
    echo
    echo "| vlastnosť | hodnota |"
    echo "|---|---|"
    echo "| územie | ${area_name:-celý región}${area_bbox:+ (\`$area_bbox\`)} |"
    echo "| výškový model | ${rock_dem:-?} |"
    echo "| počet samostatných plôch | ${count:-?} |"
    echo "| obrys sa počíta na mriežke | ${grid_m:-?} m |"
    echo "| buniek sklonu / čas výpočtu | ${cells_g:-?} mld. / ${took:-?} |"
    echo "| bunka zdrojového DEM (${rock_dem:-?}) | ~${dem_cell_m:-?} m → **strop skutočného detailu** |"
    echo "| najmenšia ponechaná plocha | ${min_area_m2:-?} m² |"
    echo "| skutočne najmenšia plocha | ${min_m2:-?} m² |"
    echo "| priemerná plocha | ${avg_m2:-?} m² |"
    echo "| najväčšia plocha | ${max_ha:-?} ha |"
    echo "| skalného terénu spolu | ${total_km2:-?} km² |"
    echo "| prah sklonu | ≥ ${slope_deg:-?}° (steny od ${cliff_deg:-?}°, krok ${slope_step_deg:-?}°) |"
    echo "| plôch s dierou (miesto pod prahom vnútri skaly) | ${with_holes:-0} |"
    echo "| vykrojené dierami | ${holes_km2:-0} km² |"
    echo "| zjednodušenie obrysu | ${simplify_m:-?} m |"
    echo "| zaoblenie rohov (Chaikin) | ${smooth_passes:-0}× |"
    echo
    echo "Obrys je izolínia sklonu – plocha má tvar, aký terén naozaj má."
    echo "Kde je vnútri steny miesto s menším sklonom (polica, terasa),"
    echo "vypadne z plochy **diera** a nezafarbí sa – aj keď je dookola"
    echo "všade sklon nad prahom."
    if [ "${area_key:-cely}" != "cely" ]; then
      echo
      echo "> ⚠️ **Vrstevnice aj skaly sú len na výreze „${area_name}“.**"
      echo "> Vo zvyšku regiónu nebude v mape ani jedno – toto je beh"
      echo "> na testovanie, nie na nasadenie. Pre celý región nechaj"
      echo "> input \`area\` prázdny."
    fi
    echo
    echo "> Mriežka ${grid_m:-?} m hovorí, ako jemne je obrys odkrokovaný;"
    echo "> ale zdrojový DEM má bunku ~${dem_cell_m:-?} m, takže nové detaily"
    echo "> terénu jemnejšia mriežka nevymyslí – len obrys vyhladí a presnejšie"
    echo "> umiestni. Preto \`rock_res=auto\` nejde pod desatinu bunky DEM:"
    echo "> ďalšie zjemňovanie by stálo štvornásobok času za nulový detail."
    echo
    echo "> Zubatosť rieši zaoblenie rohov, nie hrubšia mriežka. Samotná"
    echo "> izolínia zubatá nie je (priemerný lom 4,6°), zubatou ju robí až"
    echo "> zjednodušenie obrysu (28,5°). Chaikin každý roh nahradí dvomi"
    echo "> polovičnými, takže dva prechody dajú 7,7° – hladší obrys, než má"
    echo "> nezjednodušený originál, a stále o 43 % menej bodov."
  } >> "$S"
fi

# ---- detail značených trás ----
# Čísla píše workers/trail-routes.py; job s trasami ich pribalil
# k meraniu krokov.
if [ -s steps-out/trail-stats.txt ]; then
  # shellcheck disable=SC1091
  . steps-out/trail-stats.txt
  {
    echo
    echo "## Značené trasy – čo sa našlo v OSM"
    echo
    echo "| vlastnosť | hodnota |"
    echo "|---|---|"
    echo "| relácií trás (\`type=route\`) | ${routes:-0} |"
    echo "| z toho pomenovaných | ${named:-0} |"
    echo "| ciest, po ktorých vedie trasa | ${ways:-0} |"
    echo "| úsekov v dlaždiciach (cesta × trasa) | ${features:-0} |"
    echo "| ciest s viac než jednou trasou | ${multi:-0} (najviac naraz ${max_lanes:-0}) |"
    echo "| turistické / cyklo / MTB | ${type_hiking:-0} / ${type_bicycle:-0} / ${type_mtb:-0} |"
    echo "| lyžiarske / jazdecké | ${type_ski:-0} / ${type_horse:-0} |"
    echo "| diaľkové (medzinárodné + národné) | $(( ${tier_international:-0} + ${tier_national:-0} )) |"
    echo "| farby značiek | ${colours:-–} |"
    echo
    echo "Trasa sa kreslí ako farebný pásik **vedľa** cesty, každá vo"
    echo "svojom pruhu – po jednej ceste ich vedie aj ${max_lanes:-1} naraz"
    echo "a cesta pod nimi zostane vidieť aj s tým, aká je."
  } >> "$S"
fi

{
  echo
  echo "## Rozpočet stránky"
  echo
  echo "| časť | veľkosť |"
  echo "|---|--:|"
  for d in tiles terrain sprites fonts; do
    [ -d "_site/$d" ] && echo "| $d | $(du -sm "_site/$d" | cut -f1) MB |"
  done
  echo "| **spolu** | **$(du -sm _site 2>/dev/null | cut -f1) MB** z ${SIZE_LIMIT_MB} MB |"
  echo
  echo "## Odkiaľ je terén"
  echo
  echo "| vrstva | vybraný zdroj | naozaj použitý |"
  echo "|---|---|---|"
  echo "| vrstevnice | \`${SRC_CONTOURS}\` | ${USED_CONTOURS} |"
  echo "| skaly | \`${SRC_ROCKS}\` | ${USED_ROCKS} |"
  echo "| tieňovanie a 3D | \`${SRC_SHADING}\` | ${USED_SHADING} |"
  echo
  echo "Vybraný a použitý sa líšia len vtedy, keď model nebol"
  echo "k dispozícii a zapol sa náhradný (napr. 1 m ÚGKK → Sonny)."
  if [ "$SRC_ROCKS" = 'tienovanie' ]; then
    echo
    echo "Tieňované dlaždice, z ktorých sú skaly, stiahol v tomto behu"
    echo "job *Skaly z tieňovania* – sú v artefakte"
    echo "\`dlazdice-tienovania-…\` a náhľad mozaiky v \`nahlad-…\`."
  fi
  echo
  echo "**Ako pregenerovať:** spusti workflow znova a vo výbere"
  echo "\`rebuild\` zvoľ \`vrstevnice\`, \`skaly\` (vrátane uloženej"
  echo "verzie v release \`dem-rocks\`), \`teren\` alebo \`vsetko\`."
  echo "Najprv sa zmaže príslušná cache – inak by sa stará verzia"
  echo "len vrátila späť."
  echo
  echo "**Rýchly testovací beh:** \`area\` (napr. \`vysoke_tatry\`) počíta"
  echo "vrstevnice aj skaly len na výreze – z ~40 minút sa stane ~2."
} >> "$S"

if [ "$PAGE_URL" != '' ]; then
  echo -e "\n[Otvoriť mapu](${PAGE_URL})" >> "$S"
fi

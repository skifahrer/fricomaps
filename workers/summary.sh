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
#   REGION_NAME  R_PLAN R_CONTOURS R_SHADING_ROCKS R_TRAILS R_FEATURES R_TERRAIN
#   R_TILES R_ASSETS  SRC_CONTOURS SRC_ROCKS SRC_SHADING
#   USED_CONTOURS USED_ROCKS USED_SHADING  SIZE_LIMIT_MB  PAGE_URL
#   PAGES_BUILD_TYPE  (`legacy` = mapu prepíše najbližší push)
#   REGION_KEY  TEST_KM2 TEST_BBOX TEST_FULL_BBOX  (testovací režim)
#   INPUTS_JSON  (celý formulár ako JSON – blok „Nastavenia tohto behu")
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
  echo "| Krajinné prvky | ${R_FEATURES:-–} |"
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
    if [ "${plne:-1}" = '1' ]; then
      echo "| prah sklonu | ≥ ${slope_deg:-?}° (krok ${slope_step_deg:-?}°), jedna trieda |"
    else
      echo "| prah sklonu | ≥ ${slope_deg:-?}° (steny od ${cliff_deg:-?}°, krok ${slope_step_deg:-?}°) |"
    fi
    if [ "${zapln_diery:-0}" = '1' ]; then
      echo "| diery | **zaplnené** (\`rock_zapln_diery=1\`) – detail tvaru je preč |"
    else
      echo "| plôch s dierou (miesto pod prahom vnútri skaly) | ${with_holes:-0} |"
      echo "| vykrojené dierami | ${holes_km2:-0} km² |"
    fi
    echo "| zjednodušenie obrysu | ${simplify_m:-?} m |"
    echo "| zaoblenie rohov (Chaikin) | ${smooth_passes:-0}× |"
    echo
    echo "Obrys je izolínia sklonu – plocha má tvar, aký terén naozaj má."
    if [ "${zapln_diery:-0}" = '1' ]; then
      echo "Diery sú **zaplnené** (\`options: rock_zapln_diery=1\`), takže"
      echo "z každej skaly je súvislá plocha bez vnútorného tvaru. Vypnutie"
      echo "toho prepínača vráti police a medzery tam, kam patria."
    else
      echo "Kde je vnútri steny miesto s menším sklonom (polica, terasa),"
      echo "vypadne z plochy **diera** a nezafarbí sa – aj keď je dookola"
      echo "všade sklon nad prahom. Práve tie diery robia tvar skaly"
      echo "čitateľným."
    fi
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
} >> "$S"

# ---- s čím bol beh spustený ----
# Formulár `Run workflow` sa vždy otvorí s predvolenými hodnotami – GitHub
# si nepamätá, s čím si beh pustil naposledy, a z API sa to ani nedá zistiť.
# Keď teda chceš zopakovať beh a zmeniť jedinú vec, ostatné polia musíš
# nastaviť znova; toto je zoznam, z ktorého sa dajú odpísať. `|| true`:
# súhrn je užitočný aj bez tohto bloku, nemá kvôli nemu spadnúť.
python3 workers/summary-inputs.py \
  --inputs="${INPUTS_JSON:-}" \
  --workflow=.github/workflows/build-map.yml >> "$S" || true

{
  echo "**Ako pregenerovať:** spusti workflow znova a vo výbere"
  echo "\`rebuild\` zvoľ \`vrstevnice\`, \`skaly\` (vrátane uloženej"
  echo "verzie v release \`dem-rocks\`), \`teren\` alebo \`vsetko\`."
  echo "Najprv sa zmaže príslušná cache – inak by sa stará verzia"
  echo "len vrátila späť."
  # Tabuľka „Nastavenia tohto behu" vyššie ukazuje `rebuild` tak, ako bol
  # vo formulári – pri zapnutom teste by teda tvrdila `nic`, hoci sa počítalo
  # všetko nanovo. Bez tejto vety by to vyzeralo ako chyba súhrnu.
  if [ "${TEST_KM2:-0}" != '0' ]; then
    echo
    echo "V tomto behu to však nebolo treba: **rýchly test pregenerúva vždy"
    echo "všetko**, aj pri \`rebuild: nic\` – inak by si ladil na výsledku,"
    echo "ktorý sa vrátil z cache. Cache ostrého behu to nemaže, testovací"
    echo "štvorec má vlastný kľúč."
  fi
  echo
  echo "**Rýchly testovací beh:** \`area\` (napr. \`vysoke_tatry\`) počíta"
  echo "vrstevnice aj skaly len na výreze – z ~40 minút sa stane ~2."
  echo "Ešte rýchlejší je switch \`test\` (predvolene zapnutý): vyreže zo"
  echo "stredu výrezu štvorec so 4 km², spraví na ňom VŠETKO vrátane"
  echo "tieňovania a mapu otvorí rovno tam. Ostrý beh na celom výreze ho"
  echo "chce odškrtnúť; iná veľkosť je \`options: test_km2=2\`."
} >> "$S"

if [ "$PAGE_URL" != '' ]; then
  echo -e "\n[Otvoriť mapu](${PAGE_URL})" >> "$S"
fi

# ---- Pages berie zdroj z vetvy ----
# Toto patrí hore a nahlas: mapa síce je nasadená a odkaz vyššie funguje,
# ale najbližší push do master ju prepíše obsahom repozitára. Beh o tom
# nemôže spraviť nič – je to nastavenie repozitára a `GITHUB_TOKEN` naň
# nemá práva (mení sa ním repozitár, nie obsah stránky).
if [ -n "${PAGES_BUILD_TYPE:-}" ] && [ "$PAGES_BUILD_TYPE" != 'workflow' ]; then
  {
    echo
    echo "> ### ⚠️ Mapu na Pages prepíše najbližší merge"
    echo ">"
    echo "> Zdroj GitHub Pages je nastavený na **vetvu**, nie na Actions"
    echo "> (\`build_type=$PAGES_BUILD_TYPE\`). Popri tomto workflowe preto beží"
    echo "> zabudovaný Jekyll builder (*pages build and deployment*), ktorý pri"
    echo "> každom pushi do \`master\` nasadí koreň repozitára – teda README –"
    echo "> a mapu z tohto behu prepíše."
    echo ">"
    echo "> Mapa je **teraz nasadená a funguje**; zmizne až pri ďalšom mergi."
    echo ">"
    echo "> **Oprava je jednorazová a musíš ju spraviť ty** (token na zmenu"
    echo "> nastavení repozitára práva nemá):"
    echo "> **Settings → Pages → Build and deployment → Source: \`GitHub Actions\`**"
  } >> "$S"
fi

# ---- kde je testovací výrez ----
# Obrázok sa nasadil spolu so stránkou, takže má verejnú adresu a súhrn ho
# vie priamo ukázať – z artefaktu by sa musel sťahovať. Odkaz do mapy mieri
# na stred testovaného štvorca; bez neho by sa výsledok hľadal ručne.
if [ "${TEST_KM2:-0}" != '0' ] && [ -n "${TEST_BBOX:-}" ]; then
  python3 workers/test-locator.py \
    --bbox="$TEST_BBOX" --full-bbox="${TEST_FULL_BBOX:-}" \
    --name="$REGION_NAME" \
    --layers="vrstevnice: ${SRC_CONTOURS}, skaly: ${SRC_ROCKS}, tieňovanie: ${SRC_SHADING}" \
    --png= --md=/tmp/kde-to-je.md \
    --img-url="${PAGE_URL}kde-to-je.png" \
    --pages-url="$PAGE_URL" --region="${REGION_KEY:-}" || true
  if [ -s /tmp/kde-to-je.md ]; then
    { echo; cat /tmp/kde-to-je.md; } >> "$S"
  else
    { echo; echo "### Testovací výrez"; echo;
      echo "bbox \`${TEST_BBOX}\` (${TEST_KM2} km²) – obrázok sa nepodarilo vyrobiť.";
    } >> "$S"
  fi
fi

# ---- čo spadlo ----
# Tabuľka jobov hore povie „failure" a tým to končí – ktorý krok, ako dlho
# bežal a čo vlastne vypísal, sa dá zistiť len prehrabaním sa logom. Tu je
# to rovno: krok, trvanie a posledné `::error::` z logu toho jobu.
#
# Zvlášť pri `cancelled`: to nie je pád, ale zrušenie – buď timeoutom jobu
# (potom trvanie sedí na jeho strop), alebo zvonku. Bez trvania sa to
# nerozlíši. Beh 31222472790 bol práve toto: tri hodiny a runner ho zabil.
#
# `|| true` všade: keď na to token nemá právo alebo je log ešte nedostupný,
# nemá to zhodiť súhrn – zvyšok tabuliek je aj tak užitočný.
SPADLO=$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID/jobs?per_page=100" \
  --jq '.jobs[]
        | select(.conclusion == "failure" or .conclusion == "cancelled")
        | [.id, .name, .conclusion, .started_at, .completed_at,
           ([.steps[]? | select(.conclusion == "failure" or .conclusion == "cancelled")
             | .name] | first // "—"),
           .html_url] | @tsv' 2>/dev/null || true)

if [ -n "$SPADLO" ]; then
  { echo; echo "## Čo spadlo"; echo; } >> "$S"
  while IFS=$'\t' read -r jid jname jconcl jstart jend jstep jurl; do
    [ -n "${jname:-}" ] || continue
    if [ -n "${jstart:-}" ] && [ -n "${jend:-}" ]; then
      TRVALO=$(( $(date -d "$jend" +%s) - $(date -d "$jstart" +%s) ))
    else
      TRVALO=0
    fi
    {
      echo "### [$jname]($jurl) – $jconcl po $(hms "$TRVALO")"
      echo
      echo "Zastavilo sa na kroku **$jstep**."
      if [ "$jconcl" = "cancelled" ] && [ "$TRVALO" -gt 3000 ]; then
        echo
        echo "> Zrušené po $(hms "$TRVALO") – to nie je pád, to je strop."
        echo "> Buď timeout jobu, alebo rozpočet výpočtu. Skús menší výrez,"
        echo "> nižší zoom alebo hrubšiu mriežku."
      fi
    } >> "$S"
    # Posledné chybové riadky z logu. Čas na začiatku riadku ide preč – je
    # to šum, ktorý v súhrne akurát zalomí tabuľku.
    CHYBY=$(gh api "repos/$GITHUB_REPOSITORY/actions/jobs/$jid/logs" 2>/dev/null \
      | grep -a "##\[error\]" | tail -3 | sed 's/^[0-9TZ:.-]* //' || true)
    if [ -n "$CHYBY" ]; then
      { echo; echo '```'; echo "$CHYBY"; echo '```'; echo; } >> "$S"
    fi
  done <<< "$SPADLO"
fi

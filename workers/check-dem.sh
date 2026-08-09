#!/usr/bin/env bash
# Je v release výškový model pre naše územie – a keď nie, čo treba doplniť?
#
# PREČO SAMOSTATNÝ SKRIPT A NIE `run:` V WORKFLOWE: build-map.yml má k stropu,
# nad ktorým ho GitHub NEPRIJME (128 kB), blízko – a nepovie to; po pushi len
# vyrobí beh bez jobov. Rovnako sú na tom `contours-build.sh` či `fetch-dem.sh`.
# Bokom od toho je to aj tak správnejšie: takto sa dá rozhodovanie spustiť
# lokálne a nie „pushni a pozri sa, čo z toho vyšlo".
#
# ČO ROBÍ. Pre každú z troch vrstiev (vrstevnice, skaly, tieňovanie) sa spýta
# `workers/dem-target.py`, ktorý release a ktoré assety jej zdroj potrebuje,
# a pozrie sa, či tam sú. Keď nie sú, zaradí ich na doplnenie.
#
# KĽÚČOVÁ VEC, NA KTOREJ SA TO UŽ RAZ ROZBILO. `dmr5` má dve podoby a prepína
# medzi nimi KĽÚČ VÝREZU, ktorý vrstva podá do `fetch-dem.sh`:
#
#   contours  workers/contours-build.sh … "$src" "$AREA_KEY_IN"   → výrez
#   rocks     to isté (jeden job, jeden skript)                   → výrez
#   terrain   build-map.yml, „Tieňovanie reliéfu" … "$TDEM"        → dlaždice
#
# Tieňovanie sa robí na CELÝ REGIÓN, kde 1 m verzia neexistuje, tak kľúč
# nepodáva. Kým tu bol pre všetky tri vrstvy ten istý `AREA_KEY`, kontrola
# hľadala výrez v `dem-ugkk`, kým tieňovanie sťahovalo dlaždice z `dem-dmr5` –
# a beh 31307163093 spadol na tom, že v `dem-dmr5` nie je ani jedna dlaždica.
# Tabuľka `layer_area_key` nižšie musí sedieť s tými troma volaniami; stráži
# to `Lint workflows`.
#
# Použitie (hodnoty chodia z prostredia, aby sa dal skript spustiť aj ručne):
#   BBOX=W,S,E,N AREA_KEY=vysoke_tatry AREA_BBOX=W,S,E,N \
#   SRC_CONTOURS=dmr5 SRC_ROCKS=dmr5 SRC_TERRAIN=dmr5 \
#   GH_TOKEN=… GITHUB_REPOSITORY=owner/repo workers/check-dem.sh
#
# Zapisuje do $GITHUB_OUTPUT (keď je nastavený):
#   demkey_<vrstva>       otlačok obsahu releasu, ide do kľúča cache
#   mirror_<vrstva>       zdroj pre update-dem.yml (sonny/dmr35), inak prázdne
#   mirror_dmr5_area      bbox výrezu `W,S,E,N` pre `DMR 5.0 z Drive`
#   mirror_dmr5_asset     meno, pod ktorým ten výrez build hľadá v release
#   mirror_dmr5_tiles     stupne `W,S,E,N` pre `DMR 5.0 z Drive`, inak prázdne
#
# PREČO SA VÝREZ PODÁVA BBOXOM A NIE KĽÚČOM POHORIA. `DMR 5.0 z Drive` dostane
# presne to územie, ktoré si beh naozaj vypýtal – teda výrez UŽ PRETNUTÝ
# S REGIÓNOM a pri rýchlom teste štvorec na pár km². Kým sa podával kľúč, tá
# pipeline si ho vyriešila z `areas.json` DRUHÝKRÁT a prečítala z Drive celý
# obdĺžnik pohoria: rýchly test na 2 km² tak čítal 541 km² Vysokých Tatier,
# čiže hodiny namiesto minút. Je to tá istá chyba ako beh 31307163093 – dve
# odpovede na jednu otázku – len tentoraz nezhodila beh, iba ho predražila.
#
# Meno assetu ide zvlášť: bbox sa doň dať nedá, build si súbor hľadá podľa
# kľúča výrezu (`ugkk-vysoke_tatry.tif`), a to meno vie povedať `dem-target.py`.
set -euo pipefail

HERE="$(dirname "$0")"
BBOX="${BBOX:-}"
AREA_KEY="${AREA_KEY:-cely}"
# Bbox výrezu, už pretnutý s regiónom (počíta ho `resolve-area.py` v príprave).
# Prázdny = beží sa bez výrezu alebo skript spustil niekto ručne; vtedy je
# výrezom celý región a jeho bbox je to isté.
AREA_BBOX="${AREA_BBOX:-$BBOX}"
OUT="${GITHUB_OUTPUT:-/dev/null}"

# Ktorá vrstva podáva kľúč výrezu – viď rozpis vyššie.
layer_area_key() {
  case "$1" in
    terrain) echo cely ;;
    *) echo "$AREA_KEY" ;;
  esac
}

MIRROR=""       # už zaradené na doplnenie (podľa PODOBY, nie podľa zdroja)
MIRROR_LIST=""  # na výpis
DMR5_AREA=""    # bbox, ktorý sa má prečítať ako výrez v plnom rozlíšení
DMR5_ASSET=""   # a meno, pod ktorým ho build hľadá
DMR5_TILES=""   # ktoré stupne doplniť ako 1° dlaždice

# Pozrie sa na jeden zdroj: či pre naše územie v jeho release niečo je a aký je
# otlačok obsahu. Výsledok ide do DEMKEY, prípadné doplnenie do NEED_SRC
# (resp. DMR5_AREA / DMR5_TILES).
check_source() { # $1 = vrstva (na výpis), $2 = zdroj
  local what="$1" src="$2" akey rel assets names need=false
  local form target want mirror degrees
  akey=$(layer_area_key "$what")
  target=$(python3 "$HERE/dem-target.py" --source="$src" \
    --area-key="$akey" --bbox="$BBOX")
  tget() { printf '%s\n' "$target" | sed -n "s/^$1=//p" | head -1; }
  form=$(tget form); rel=$(tget release); want=$(tget assets)
  mirror=$(tget mirror); degrees=$(tget degrees)

  # Meno aj veľkosť naraz: z mien sa hľadá, z celého riadku počíta otlačok.
  # Dva `gh` dopyty na to isté by sa len rozišli. `|| true` vnútri zátvoriek:
  # keď release ešte neexistuje, gh skončí nenulovo a `pipefail` by zhodil
  # celý krok.
  assets=$({ gh release view "$rel" --repo "$GITHUB_REPOSITORY" --json assets \
    -q '.assets[] | .name + ":" + (.size|tostring)' 2>/dev/null || true; } | sort)
  names=$(printf '%s\n' "$assets" | cut -d: -f1)

  if [ "$form" = 'area' ]; then
    # Plné rozlíšenie sa zrkadlí po výrezoch (pri 1 m má jedna 1° dlaždica
    # ~48 GB), takže sa hľadá jeden asset podľa kľúča výrezu.
    if printf '%s\n' "$names" | grep -qx "$want"; then
      echo "$what ($src): $want je v release $rel ✓"
    else
      echo "$what ($src): $want v release $rel nie je → doplní sa"
      need=true
    fi
  elif [ -z "$want" ]; then
    # Vlastný región bez bboxu – zoznam dlaždíc sa nedá zistiť, tak sa
    # pozeráme len na to, či v release vôbec niečo je.
    [ -z "$assets" ] && need=true || true
    echo "$what ($src): bbox nie je známy; release $rel má $(printf '%s' "$assets" | grep -c . || true) assetov → doplniť: $need"
  else
    local have=0 total=0 t
    for t in $want; do
      total=$(( total + 1 ))
      printf '%s\n' "$names" | grep -qx "$t" && have=$(( have + 1 )) || true
    done
    # Doplňujeme, len keď nie je ani jedna použiteľná dlaždica. Bbox je
    # obdĺžnik, ale produkt pokrýva krajinu – rohové bunky (u Slovenska napr.
    # N47E016 v Maďarsku) v ňom nikdy nebudú, takže „chýba jedna, tak sťahuj
    # znova" by mirrorovalo pri každom builde.
    [ "$have" -eq 0 ] && need=true || true
    echo "$what ($src): dlaždíc pre bbox $total, v release $rel $have → doplniť: $need"
  fi

  if [ "$need" = true ]; then
    # Deduplikuje sa PODĽA PODOBY, nie podľa zdroja. Pri jedinom `dmr5` vo
    # formulári môžu chýbať oba tvary naraz (vrstevnice chcú výrez z dem-ugkk,
    # tieňovanie dlaždice z dem-dmr5) – a kým sa dedupovalo podľa mena zdroja,
    # druhý sa ticho zahodil a build spadol až v jobe, ktorý si ho vypýtal.
    case " $MIRROR " in
      *" $mirror "*) echo "  ($mirror už dopĺňa iná vrstva)" ;;
      *)
        MIRROR="$MIRROR $mirror"
        MIRROR_LIST="$MIRROR_LIST $mirror"
        if [ "$src" = 'dmr5' ]; then
          # DMR 5.0 nedopĺňa update-dem.yml – ten ho stiahnuť nevie (145 GB na
          # Drive, runner má voľných ~60 GB). Robí to `DMR 5.0 z Drive`, ktorá
          # číta cez HTTP Range len to, čo územie pretína.
          if [ "$form" = 'area' ]; then
            # Bbox, nie kľúč: čítať sa má presne to, čo si beh vypýtal (viď
            # rozpis hore). Meno assetu je to isté `$want`, ktoré sa o pár
            # riadkov vyššie hľadalo v release – z jedného zdroja pravdy,
            # takže sa doplní práve to, čo chýbalo.
            DMR5_AREA="$AREA_BBOX"
            DMR5_ASSET="$want"
          else
            DMR5_TILES="$degrees"
          fi
        else
          NEED_SRC="$src"
        fi
        ;;
    esac
  fi
  DEMKEY=$(printf '%s' "$assets" | sha256sum | cut -c1-12)
}

for pair in \
  "contours:${SRC_CONTOURS:-}" \
  "rocks:${SRC_ROCKS:-}" \
  "terrain:${SRC_TERRAIN:-}"; do
  layer="${pair%%:*}"
  src="${pair#*:}"
  DEMKEY=""
  NEED_SRC=""
  # SKALY Z DMR 5.0 SI DEM NEPÝTAJÚ. Sklon si ich `slope-chunks.py` prečíta
  # z Drive po častiach a každú si odloží do vlastného skladu, takže celý
  # výrez ako jeden COG netreba – a to je práve tá hodina, ktorú predtým
  # zožral job `Doplniť DMR 5.0 (výrez)` a pri zrušení zahodil.
  if [ "$layer" = 'rocks' ] && [ "$src" = 'dmr5' ]; then
    echo "rocks ($src): DEM sa nedopĺňa – sklon sa číta z Drive po častiach"
  # Prázdny zdroj = vrstva je vypnutá (alebo skaly idú z tieňovania) a žiadny
  # výškový model sa pre ňu nečíta.
  elif [ -n "$src" ] && [ "$src" != 'ziadne' ]; then
    check_source "$layer" "$src"
  fi
  echo "demkey_$layer=$DEMKEY" >> "$OUT"
  echo "mirror_$layer=$NEED_SRC" >> "$OUT"
done

# Dlaždice DMR 5.0 sa dopĺňajú po celých stupňoch a to je drahé (jeden stupeň
# pri 5 m je rádovo desiatky minút čítania z Drive). Nech je z logu vidno,
# koľko ich je, a nie až z trvania jobu.
if [ -n "$DMR5_TILES" ]; then
  IFS=, read -r DW DS DE DN <<< "$DMR5_TILES"
  DEG=$(( (DE - DW) * (DN - DS) ))
  echo "DMR 5.0 dlaždice: doplní sa $DEG stupňov ($DMR5_TILES)"
  # Odhad, nie meranie: 29 km² v 1 m stálo 0,11 GB a 1,2 min, jeden stupeň
  # v 5 m sa číta z pyramídy 4 m, čo je asi sedemnásťnásobok pixelov – teda
  # rádovo dve gigabajty a pol hodiny NA STUPEŇ. Nech to je v logu vopred,
  # a nie až ako job, ktorý „z ničoho nič" beží tri hodiny.
  if [ "$DEG" -gt 2 ]; then
    echo "::warning::Doplnenie $DEG stupňov DMR 5.0 je rádovo $(( DEG / 2 ))–$DEG hodín. Kratšie to ide s menším územím (input \`area\`) alebo so switchom \`test\`; hrubší model (sonny, dmr35) je hotový hneď."
  fi
  # A NAOPAK: keď je územie oveľa menšie než stupeň, ktorý sa preň číta.
  # Dlaždica sa VŽDY musí prečítať celá – jej meno je sľub o celom stupni –
  # takže rýchly test na 2 km² zaplatí za tieňovanie pol hodinu, kým zvyšok
  # behu trvá minúty. Nie je to chyba, ale je to prekvapenie, a to má byť
  # v logu vopred. Raz doplnená dlaždica v release ostane, takže ďalší
  # testovací beh na tom istom stupni je zadarmo.
  python3 - "$BBOX" "$DEG" <<'PY' || true
import math, sys
try:
    w, s, e, n = (float(v) for v in sys.argv[1].split(","))
except ValueError:
    raise SystemExit(0)
deg = int(sys.argv[2])
km2 = ((e - w) * 111.32 * math.cos(math.radians((s + n) / 2))) * ((n - s) * 110.54)
tile_km2 = deg * 111.32 * 110.54 * math.cos(math.radians((s + n) / 2))
if km2 > 0 and tile_km2 / km2 > 50:
    print(f"::warning::Tieňovanie z DMR 5.0 potrebuje {deg}° dlaždice, čo je "
          f"~{tile_km2:.0f} km² čítania na územie s {km2:.0f} km² "
          f"({tile_km2 / km2:.0f}× viac). Dlaždica sa musí prečítať celá – jej "
          f"meno je sľub o celom stupni. Je to rádovo pol hodiny na stupeň; "
          f"na rýchly test je lacnejšie `shading_source: sonny`. Raz doplnená "
          f"dlaždica v release ostane, takže ďalší beh ju už neplatí.")
PY
fi
if [ -n "$DMR5_AREA" ]; then
  echo "DMR 5.0 výrez: prečíta sa $DMR5_AREA → $DMR5_ASSET"
fi
{
  echo "mirror_dmr5_area=$DMR5_AREA"
  echo "mirror_dmr5_asset=$DMR5_ASSET"
  echo "mirror_dmr5_tiles=$DMR5_TILES"
} >> "$OUT"

echo "Doplniť treba:${MIRROR_LIST:- nič}"

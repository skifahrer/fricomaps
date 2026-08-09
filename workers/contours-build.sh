#!/usr/bin/env bash
# Vrstevnice a skalné plochy z výškových modelov → contours-out/contours.pmtiles.
#
# PREČO SAMOSTATNÝ SKRIPT A NIE `run:` V WORKFLOWE: súbor s workflowom má
# strop, nad ktorým ho GitHub NEPRIJME – a nepovie to; po pushi len vyrobí
# beh bez jobov, pomenovaný cestou k súboru, ktorý vyzerá, že sa workflow
# spustil sám. Dvadsať kilobajtov bashu je preto tu. Bokom od toho je to aj
# tak správnejšie: rovnako sú na tom `fetch-dem.sh`, `rock-areas.py`
# či `build-terrain.py`.
#
# Hodnoty z formulára a z prípravy chodia cez prostredie (viď krok
# „Vrstevnice a skaly z DEM" v build-map.yml):
#   REGION_BBOX REGION_KEY AREA_KEY_IN AREA_NAME_IN AREA_BBOX_IN AREA_KM2
#   CONTOUR_INTERVAL OPT_CONTOUR_LINES OPT_CONTOUR_SOURCE
#   OPT_CONTOUR_SMOOTHING OPT_CONTOUR_MAXZOOM OPT_ROCK_MAXZOOM
#   ROCK_SLOPE_IN ROCK_RES_IN OPT_ROCKS OPT_ROCK_DEM OPT_ROCK_SOURCE
#   OPT_ROCK_PLNE OPT_ROCK_ZAPLN_DIERY
#   OPT_ROCK_IMG_ASSET OPT_ROCKS_REBUILD
#   OPT_SIZE_LIMIT_MB OPT_UGKK_FALLBACK
# a k tomu `env:` celého workflowu (ROCK_* , *_RELEASE,
# BUDGET_CONTOURS_PCT, BUDGET_ROCKS_PCT)
# plus GH_TOKEN.

set -euo pipefail
sudo apt-get update -qq
sudo apt-get install -y -qq gdal-bin libsqlite3-mod-spatialite zstd
python3 -m pip install --quiet numpy
# `work/` sú medzivýsledky (orez, surové izolínie) – zámerne mimo
# `dem/`, ktoré ide celé do cache. `clip.tif` má aj gigabajty.
mkdir -p dem data work contours-out

BBOX="$REGION_BBOX"
IFS=, read -r W S E N <<< "$BBOX"
INTERVAL="$CONTOUR_INTERVAL"
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=10 ;; esac

# ---------- výrez na testovanie ----------
# Vyriešila ho príprava (workers/resolve-area.py), tu sa už len
# preberá – aby check-dem, zrkadlo ÚGKK aj tento výpočet pracovali
# s tým istým územím a nie s tromi mierne odlišnými.
AREA_KEY="$AREA_KEY_IN"
AREA_NAME="$AREA_NAME_IN"
AREA_BBOX="$AREA_BBOX_IN"
if [ "$AREA_KEY" != "cely" ]; then
  echo "::warning::Vrstevnice AJ skaly sa počítajú LEN na výreze „$AREA_NAME“ ($AREA_BBOX, ${AREA_KM2} km²). Vo zvyšku regiónu nebude v mape ani jedno – toto je beh na testovanie, nie na nasadenie. Pre celý región nechaj input „area“ prázdny."
  # Vrstevnice sa ďalej trasujú z výrezu, nie z celého regiónu.
  IFS=, read -r W S E N <<< "$AREA_BBOX"
fi

# ---------- výškové modely ----------
# Sťahovanie DEM je v samostatnom skripte – potrebuje ho aj job
# s tieňovaním a dve kópie by časom zaostali jedna za druhou.
#
# Vrstevnice a skaly majú vlastný výber zdroja, takže tu môžu byť
# naraz DVA modely. Každý ide do `dem/<zdroj>/`; keď je zdroj ten
# istý, druhé volanie nájde hotovú mozaiku a nesťahuje nič.
#
# Dlaždicové modely sa sťahujú pre CELÝ región: sú v cache pod
# kľúčom regiónu, takže čiastočné stiahnutie by sa nabudúce vrátilo
# ako keby bolo úplné. ÚGKK sa naopak pýta po výrezoch a pri 1 m je
# celý kraj mimo možností – tam ide len výrez.

fetch_dem() { # $1 = zdroj → DEM_VRT, DEM_GOT (čo sa NAOZAJ použilo)
  local src="$1" fbbox rc
  if [ -s "dem/$src/all.vrt" ]; then
    DEM_VRT="dem/$src/all.vrt"; DEM_GOT="$src"
    echo "DEM $src: mozaika už je ✓"
    return 0
  fi
  # DMR 5.0 na výrez je jeden COG presne pre ten výrez, takže sa pýta jeho
  # bboxom; všetko ostatné sú dlaždice pre celý región.
  if [ "$src" = 'dmr5' ] && [ "$AREA_KEY_IN" != 'cely' ]; then
    fbbox="$AREA_BBOX"
  else
    fbbox="$BBOX"
  fi
  set +e
  workers/fetch-dem.sh "$fbbox" "dem/$src" steps-out/contours.tsv \
    "$src" "$AREA_KEY_IN"
  rc=$?
  set -e
  if [ "$rc" -eq 3 ]; then
    # Ten model pre toto územie nemáme. Buď to zhodíme, alebo dopočítame
    # zo Sonnyho – ale nikdy ticho: `dem-source.txt` nesie, čo sa NAOZAJ
    # použilo, takže atribúcia v mape nebude tvrdiť DMR 5.0 tam, kde je Sonny.
    local how
    if [ "$src" = 'dmr5' ] && [ "$AREA_KEY_IN" != 'cely' ]; then
      how="workflow 'DMR 5.0 z Drive (ETRS89)' s area: $AREA_KEY_IN"
    elif [ "$src" = 'dmr5' ]; then
      how="workflow 'DMR 5.0 z Drive (ETRS89)' s area: cele_slovensko"
    else
      how="workflow 'Stiahnuť výškové dáta' so zdrojom $src"
    fi
    if [ "$OPT_UGKK_FALLBACK" != 'true' ]; then
      echo "::error::Model $src pre toto územie nie je k dispozícii a ugkk_fallback je vypnutý. Naplň ho ($how), zapni fallback, alebo vyber iný zdroj."
      exit 1
    fi
    echo "::warning::Model $src pre toto územie nie je k dispozícii – počíta sa zo Sonnyho (20 m). Mapa bude, len s hrubším modelom. Doplní ho $how."
    fetch_dem sonny
    return 0
  elif [ "$rc" -ne 0 ]; then
    exit "$rc"
  fi
  DEM_VRT="dem/$src/all.vrt"; DEM_GOT="$src"
}

# ---------- čo sa vlastne ide počítať ----------
# Bez tohto je z logu vidieť len „Vrstevnice a skaly z DEM" a potom
# desiatky minút ticha. Rozmer rastra a počet buniek povedia, či
# ide o minúty alebo o hodinu, a to ešte pred prvým gdalwarpom.
dem_info() { # $1 = popis, $2 = mozaika
  echo "── Vstupný DEM: $1 ──────────────────────────────"
  gdalinfo "$2" 2>/dev/null \
    | grep -E "^Size is|^Pixel Size|^Upper Left|^Lower Right" || true
  python3 - "$W" "$S" "$E" "$N" "$2" <<'PY'
import json, math, subprocess, sys
w, s, e, n = map(float, sys.argv[1:5])
try:
    info = json.loads(subprocess.run(
        ["gdalinfo", "-json", sys.argv[5]],
        capture_output=True, text=True, check=True).stdout)
    gt = info["geoTransform"]
    dx, dy = abs(gt[1]), abs(gt[5])
    cells = ((e - w) / dx) * ((n - s) / dy)
    lat = (s + n) / 2
    print(f"  výrez        {e-w:.3f}° × {n-s:.3f}°  "
          f"(~{(e-w)*111.32*math.cos(math.radians(lat)):.0f} × "
          f"{(n-s)*110.54:.0f} km)")
    print(f"  bunka DEM    {dx*111320*math.cos(math.radians(lat)):.0f} × "
          f"{dy*110540:.0f} m")
    print(f"  buniek       {cells/1e6:.1f} mil.")
except Exception as exc:
    print(f"  (rozmer sa nedá zistiť: {exc})")
PY
  echo "─────────────────────────────────────────────────────"
}

# Zdroje z formulára. `opt_rock_dem` je prázdny, keď skaly idú
# z tieňovania alebo sú vypnuté – vtedy sa na DEM kvôli nim nesiaha.
CONTOUR_SRC="$OPT_CONTOUR_SOURCE"
ROCK_DEM="$OPT_ROCK_DEM"
CONTOUR_VRT=""; CONTOUR_DEM=""
ROCK_VRT=""; ROCK_DEM_USED=""

if [ "$OPT_CONTOUR_LINES" = 'true' ]; then
  fetch_dem "$CONTOUR_SRC"
  CONTOUR_VRT="$DEM_VRT"; CONTOUR_DEM="$DEM_GOT"
  dem_info "vrstevnice ($CONTOUR_DEM)" "$CONTOUR_VRT"
fi
if [ -n "$ROCK_DEM" ]; then
  fetch_dem "$ROCK_DEM"
  ROCK_VRT="$DEM_VRT"; ROCK_DEM_USED="$DEM_GOT"
  [ "$ROCK_VRT" = "$CONTOUR_VRT" ] \
    || dem_info "skaly ($ROCK_DEM_USED)" "$ROCK_VRT"
fi

# Zjemnenie DEM pred trasovaním vrstevníc. Priemerovanie na hrubšiu
# mriežku vyhladí šum, ale zároveň zje detail terénu. Default 0 =
# žiadne zjemnenie, len orez na bbox v plnom rozlíšení.
T_CONT=$(date +%s)

make_empty_gpkg() { # $1 = súbor, $2 = vrstva, $3 = typ geometrie
  # Schéma odkazuje na obe vrstvy vždy, takže súbor musí existovať
  # aj vtedy, keď je vrstva vypnutá alebo sa nič nenašlo.
  echo '{"type":"FeatureCollection","features":[]}' > work/empty.geojson
  ogr2ogr -f GPKG "$1" work/empty.geojson -nln "$2" -overwrite \
    -nlt "$3" -a_srs EPSG:4326 -lco GEOMETRY_NAME=geom
}

if [ "$OPT_CONTOUR_LINES" != 'true' ]; then
  echo "Vrstevnice: vypnuté (contour_source: ziadne) – prázdna vrstva."
  make_empty_gpkg data/contours.gpkg contours LINESTRING
else
  SMOOTH="$OPT_CONTOUR_SMOOTHING"
  case "$SMOOTH" in ''|*[!0-9.]*) SMOOTH=0 ;; esac
  if [ "${SMOOTH%%.*}" -gt 0 ] 2>/dev/null; then
    RES=$(python3 -c "print(f'{$SMOOTH / 3600:.8f}')")
    echo "Zjemnenie DEM: ${SMOOTH}″ (mriežka $RES°)"
    python3 workers/watch.py --label="orez DEM" --watch-file=work/clip.tif \
      -- gdalwarp -overwrite -te "$W" "$S" "$E" "$N" \
         -tr "$RES" "$RES" -r average "$CONTOUR_VRT" work/clip.tif
  else
    echo "Zjemnenie DEM: vypnuté – vrstevnice sa trasujú z plného rozlíšenia."
    python3 workers/watch.py --label="orez DEM" --watch-file=work/clip.tif \
      -- gdalwarp -overwrite -te "$W" "$S" "$E" "$N" "$CONTOUR_VRT" work/clip.tif
  fi

  # `-q` je preč a ide to cez watch.py: gdal_contour nad krajom beží
  # desiatky minút a doteraz pri tom nepovedal ani slovo.
  python3 workers/watch.py --label="vrstevnice" --watch-file=work/raw.gpkg \
    -- gdal_contour -a ele -i "$INTERVAL" -f GPKG -nln contours \
       work/clip.tif work/raw.gpkg

  # `level` rozdelí vrstevnice na hlavné/polovičné/základné, aby sa
  # dali zapínať podľa zoomu a kresliť rôzne hrubo. Hranice sa počítajú
  # z intervalu, nie natvrdo zo 100/50 – pri interval=5 by inak bola
  # zvýraznená každá dvadsiata čiara namiesto každej desiatej a pri
  # interval=25 by sa `mid` netrafilo nikdy. Zvýrazňuje sa každá
  # desiata (major) a každá piata (mid) vrstevnica, čo je pri
  # štandardných 10 m presne doterajších 100 a 50 m.
  MAJOR=$(( INTERVAL * 10 ))
  MID=$(( INTERVAL * 5 ))
  echo "Vrstevnice: interval ${INTERVAL} m z modelu $CONTOUR_DEM, zvýraznená každá ${MAJOR} m (major) a ${MID} m (mid)"
  python3 workers/watch.py --label="triedenie vrstevníc" \
    --watch-file=data/contours.gpkg \
    -- ogr2ogr -f GPKG data/contours.gpkg work/raw.gpkg -nln contours \
    -dialect SQLITE -sql "SELECT *, CASE
         WHEN CAST(ele AS INTEGER) % $MAJOR = 0 THEN 'major'
         WHEN CAST(ele AS INTEGER) % $MID  = 0 THEN 'mid'
         ELSE 'minor' END AS level
       FROM contours WHERE ele IS NOT NULL"
  ls -lh data/contours.gpkg
  printf '%s\t%s\t%s\t%s\n' "30" "Vrstevnice (gdal_contour)" "$(( $(date +%s) - T_CONT ))" \
    "interval ${INTERVAL} m z $CONTOUR_DEM, $(du -h data/contours.gpkg | cut -f1)" \
    >> steps-out/contours.tsv
fi

# ---------- skaly: najstrmšie úseky terénu ----------
# Celý výpočet je vo workers/rock-areas.py – po častiach, aby sa
# jemná mriežka zmestila do pamäte aj na disk. Bbox kraja má pri
# 2 m vyše 3 miliardy buniek (~13 GB na jeden raster), takže naraz
# sa to spočítať nedá.
T_ROCK=$(date +%s)
ROCK_SLOPE="$ROCK_SLOPE_IN"
case "$ROCK_SLOPE" in ''|*[!0-9]*) ROCK_SLOPE=50 ;; esac
ROCK_CLIFF=$(( ROCK_SLOPE + ROCK_CLIFF_PLUS ))
# `auto` = mriežku vyberie rock-areas.py: najjemnejšiu, ktorá sa
# zmestí do rozpočtu času a má pri danom DEM ešte zmysel. Nedá sa
# to spočítať tu, lebo to závisí od plochy výrezu aj od bunky DEM.
RR="$ROCK_RES_IN"
case "$RR" in
  auto|'') RR=auto ;;
  *[!0-9.]*) RR="$ROCK_RES" ;;
esac
# Najmenšiu skalu (= jedna bunka mriežky) dopočíta rock-areas.py,
# lebo pri `auto` sa mriežka vyberá až tam.

make_empty_rock() { make_empty_gpkg data/rock.gpkg rock POLYGON; }

if [ "$OPT_ROCKS" = 'true' ]; then
  ROCK_READY=""
  ROCK_SRC="výpočet"

  # ---------- skaly z tieňovaných dlaždíc (rock_source: tienovanie) ----------
  # Tie sa v TOMTO jobe nepočítajú – spravil to job `shading-rocks`
  # o kus vyššie v tom istom behu (stiahol tieňované dlaždice
  # z freemap.sk, odložil ich ako artefakt a hotové polygóny nahral
  # do releasu). Tu sa už len stiahne výsledok. Keď je vyplnený
  # `rock_img_asset`, ten job nebežal a berie sa presne ten asset.
  if [ "$OPT_ROCK_SOURCE" = 'tienovanie' ]; then
    IMG_ASSET="$OPT_ROCK_IMG_ASSET"
    echo "::group::Skaly z tieňovania – $AREA_NAME, release $ROCK_IMG_RELEASE"
    if [ -z "$IMG_ASSET" ]; then
      # Najnovší asset pre tento výrez. Zoradiť sa musí podľa času
      # nahratia, nie podľa mena: v mene sú prahy, takže abecedne
      # by vyhral ten s najväčším číslom, nie ten posledný.
      # `env.PREFIX`, lebo `gh --jq` nevie `--arg`.
      export PREFIX="rockimg-${AREA_KEY}-"
      IMG_ASSET=$(gh release view "$ROCK_IMG_RELEASE" \
        --repo "$GITHUB_REPOSITORY" --json assets \
        --jq '[.assets[] | select(.name | startswith(env.PREFIX))
               | select(.name | endswith(".gpkg.zst"))]
              | sort_by(.createdAt) | last | .name // ""' \
        2>/dev/null || true)
    fi
    if [ -z "$IMG_ASSET" ]; then
      echo "::endgroup::"
      echo "::error::V release $ROCK_IMG_RELEASE nie je pre výrez '$AREA_KEY' žiadny asset (rockimg-${AREA_KEY}-*.gpkg.zst). Pozri job „Skaly z tieňovania\" v tomto behu – ten ich mal vyrobiť; keď spadol, hovorí prečo. Alebo vo výbere rock_source zvoľ výškový model (sonny / dmr35 / dmr5 / ugkk)."
      exit 1
    fi
    rm -rf /tmp/rockimg && mkdir -p /tmp/rockimg
    if ! gh release download "$ROCK_IMG_RELEASE" --repo "$GITHUB_REPOSITORY" \
           --pattern "$IMG_ASSET" --dir /tmp/rockimg --clobber; then
      echo "::endgroup::"
      echo "::error::Asset $IMG_ASSET sa z releasu $ROCK_IMG_RELEASE nedal stiahnuť."
      exit 1
    fi
    echo "  beriem: $IMG_ASSET ($(du -h "/tmp/rockimg/$IMG_ASSET" | cut -f1))"
    unzstd -q -f -o data/rock.gpkg "/tmp/rockimg/$IMG_ASSET"
    ROCK_READY=1
    ROCK_SRC="release $ROCK_IMG_RELEASE ($IMG_ASSET)"
    # Výrez sa tu NEOREZÁVA na bbox regiónu zámerne: asset vznikol
    # presne pre tento výrez a orezanie by len prerezalo polygóny
    # na hranici. Keby výrez presahoval región, dlaždice mimo neho
    # aj tak nikto nevykreslí.
    echo "::endgroup::"
  fi

  # Hotové skaly pre tento región a tieto nastavenia sú v release –
  # nastavenia sú v mene assetu, takže sa nikdy nepomiešajú. Výpočet
  # je na desiatky minút, stiahnutie na sekundy. `rocks_rebuild` ten
  # asset zahodí a počíta odznova.
  # Výrez je v mene assetu: skaly len z Tatier sa nesmú nabudúce
  # vydávať za skaly celého kraja.
  ROCK_ASSET="rock-${REGION_KEY}-${AREA_KEY}-${ROCK_DEM_USED:-none}-s${ROCK_SLOPE}-g${RR}-${ROCK_ALGO}.gpkg.zst"
  if [ -n "$ROCK_READY" ]; then
    : # skaly už sú (z tieňovania) – DEM sa na ne vôbec nečíta
  elif [ "$OPT_ROCKS_REBUILD" = 'true' ]; then
    echo "rocks_rebuild=áno – zahadzujem uloženú verziu a počítam nanovo."
    gh release delete-asset "$ROCK_RELEASE" "$ROCK_ASSET" \
      --repo "$GITHUB_REPOSITORY" --yes >/dev/null 2>&1 \
      && echo "  zmazané z releasu: $ROCK_ASSET" || true
  elif gh release download "$ROCK_RELEASE" --repo "$GITHUB_REPOSITORY" \
         --pattern "$ROCK_ASSET" --dir /tmp --clobber >/dev/null 2>&1; then
    unzstd -q -f -o data/rock.gpkg "/tmp/$ROCK_ASSET" && ROCK_READY=1
    [ -n "$ROCK_READY" ] && ROCK_SRC="release $ROCK_RELEASE" \
      && echo "Skaly z releasu $ROCK_RELEASE ✓ ($ROCK_ASSET)"
  fi

  if [ -z "$ROCK_READY" ]; then
    echo "::group::Skaly z modelu $ROCK_DEM_USED – $AREA_NAME, sklon ≥ ${ROCK_SLOPE}° (steny od ${ROCK_CLIFF}°), mriežka ${RR}, zaoblenie ${ROCK_SMOOTH}×"
    # Skaly sú bonus nad vrstevnicami: keby ich výpočet zlyhal (alebo
    # v rovine nič nenašiel), nemá to zhodiť hodinový build.
    # Exit 2 = „toto sa nedá spočítať" (plán nad stropom alebo
    # pamäť). To nie je bonus, ktorý sa dá preskočiť – je to zlé
    # zadanie a build sa má zastaviť hneď, nie nasadiť mapu bez
    # skál po hodine práce. Iný nenulový kód je skutočné zlyhanie
    # výpočtu a tam prázdna vrstva stačí.
    set +e
    python3 workers/rock-areas.py --dem="$ROCK_VRT" --bbox="$AREA_BBOX" \
      --res="$RR" --slope="$ROCK_SLOPE" --cliff="$ROCK_CLIFF" \
      --min-area=-1 --simplify="$ROCK_SIMPLIFY" \
      --plne="${OPT_ROCK_PLNE:-1}" \
      --zapln-diery="${OPT_ROCK_ZAPLN_DIERY:-0}" \
      --smooth="$ROCK_SMOOTH" \
      --stats=contours-out/rock-stats.txt \
      --chunk-cells="$ROCK_CHUNK_CELLS" --budget-min="$ROCK_BUDGET_MIN" \
      --max-rss-gb="$ROCK_MAX_RSS_GB" --heartbeat="$ROCK_HEARTBEAT_S" \
      --out=data/rock.gpkg
    RC=$?
    set -e
    if [ "$RC" -eq 2 ]; then
      echo "::endgroup::"
      echo "::error::Výpočet skál sa nezačal – zadanie je nad možnosti runnera (viď hlášky vyššie). Uprav rock_res alebo area a spusti znova."
      exit 1
    fi
    if [ "$RC" -eq 0 ]; then
      ls -lh data/rock.gpkg
      # Ulož ich, nech ich nabudúce netreba počítať znova.
      zstd -q -19 -T0 -f -o "/tmp/$ROCK_ASSET" data/rock.gpkg
      gh release view "$ROCK_RELEASE" --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1 || \
        gh release create "$ROCK_RELEASE" --repo "$GITHUB_REPOSITORY" \
          --title "Skalné plochy z DEM" \
          --notes "Vektorové skaly počítané zo sklonu výškového modelu. Meno assetu nesie región, výrez, model a nastavenia (prah sklonu, mriežka obrysu)." || true
      gh release upload "$ROCK_RELEASE" "/tmp/$ROCK_ASSET" \
        --repo "$GITHUB_REPOSITORY" --clobber \
        && echo "Uložené do releasu $ROCK_RELEASE ako $ROCK_ASSET" \
        || echo "::warning::Skaly sa nepodarilo uložiť do releasu."
    else
      echo "::warning::Skalné plochy sa nevygenerovali – vrstva bude prázdna."
      make_empty_rock
    fi
    echo "::endgroup::"
  fi

  # Štatistika ide do contours-out, takže ju nesie aj cache – pri
  # cache hite sa tento krok vôbec nespustí, ale súhrn čísla má.
  ROCK_N=$(ogrinfo -so data/rock.gpkg rock 2>/dev/null \
    | awk -F': ' '/^Feature Count/ {print $2}')
  # Skaly z tieňovania nemajú ani sklon, ani mriežku – písať ich do
  # merania by bolo číslo, ktoré nikde nevzniklo.
  if [ "$OPT_ROCK_SOURCE" = 'tienovanie' ]; then
    ROCK_HOW="tmavé plochy v tieňovaní"
  else
    ROCK_HOW="$ROCK_DEM_USED, sklon ≥ ${ROCK_SLOPE}°, mriežka ${RR} m"
  fi
  printf '%s\t%s\t%s\t%s\n' "40" "Skalné plochy" "$(( $(date +%s) - T_ROCK ))" \
    "${ROCK_N:-0} plôch, $AREA_NAME, ${ROCK_HOW} ($ROCK_SRC)" \
    >> steps-out/contours.tsv
  # Keď skaly prišli z releasu, script nebežal a štatistiku nemá kto
  # napísať – aspoň to základné, nech súhrn nie je plný otáznikov.
  if [ ! -s contours-out/rock-stats.txt ]; then
    # `min_area_m2` sa tu nedopĺňa: dopočítava ho rock-areas.py
    # z vybranej mriežky a ten práve nebežal. Súhrn si s chýbajúcou
    # hodnotou poradí (`${min_area_m2:-?}`) – dosadiť sem premennú,
    # ktorá nikde nevzniká, by pri `set -u` zhodilo celý build.
    if [ "$OPT_ROCK_SOURCE" = 'tienovanie' ]; then
      # Skaly z tieňovania sú z iného sveta – ani sklon, ani mriežka
      # pre ne neexistujú. Súhrn podľa `source` vypíše inú tabuľku.
      { echo "source=tienovanie"; echo "count=${ROCK_N:-0}"
        printf "asset='%s'\n" "$ROCK_SRC"
      } > contours-out/rock-stats.txt
    else
      { echo "source=dem"; echo "count=${ROCK_N:-0}"; echo "grid_m=$RR"
        echo "slope_deg=$ROCK_SLOPE"; echo "cliff_deg=$ROCK_CLIFF"
      } > contours-out/rock-stats.txt
    fi
  fi
  # Z ktorého modelu sú skaly – do súhrnu. Pri `tienovanie` je
  # prázdny, lebo tam žiadny výškový model nefiguruje.
  printf "rock_dem='%s'\n" "$ROCK_DEM_USED" >> contours-out/rock-stats.txt
  # Výrez do štatistiky, nech je v súhrne vidieť, že skaly nie sú
  # všade – aj keď sa tento krok nabudúce vezme z cache.
  # Hodnoty v apostrofoch: súhrn si súbor načíta cez `.` a meno
  # výrezu má medzeru („celý región").
  { printf "area_key='%s'\n" "$AREA_KEY"
    printf "area_name='%s'\n" "$AREA_NAME"
    printf "area_bbox='%s'\n" "$AREA_BBOX"; } >> contours-out/rock-stats.txt
else
  make_empty_rock
  echo "Skaly: vypnuté (prázdna vrstva)."
fi

CZ="$OPT_CONTOUR_MAXZOOM"
case "$CZ" in ''|*[!0-9]*) CZ=14 ;; esac
if [ "$CZ" -gt 16 ]; then CZ=16; fi

# Skaly majú VLASTNÝ .pmtiles a vlastný maxzoom. Každý .pmtiles má totiž
# len jeden a tie dve vrstvy ho chcú úplne iný: vrstevnice sú čiary cez
# celý kraj a rozpočet minú okolo z14, skaly sú plochy len tam, kde je
# terén strmý, takže sa do z16 zmestia. Kým boli v jednom súbore, museli
# sa obe uskromniť na to nižšie – a na skalách to bolo vidieť, lebo
# práve pri priblížení sa pozerá, či obrys sedí na terén.
# 16 je tvrdý strop Planetilera; vyššie zoomy rieši overzoom, takže sa
# skaly zobrazujú až do maximálneho zoomu mapy tak či tak.
RZ="$OPT_ROCK_MAXZOOM"
case "$RZ" in ''|*[!0-9]*) RZ=16 ;; esac
if [ "$RZ" -gt 16 ]; then RZ=16; fi

# Vrstevnice, skaly a mapa idú na tú istú stránku, takže si rozpočet
# delia. Prepočet z hotového GPKG je lacný (sekundy), na rozdiel od
# sťahovania DEM.
LIMIT_MB="$OPT_SIZE_LIMIT_MB"
case "$LIMIT_MB" in ''|*[!0-9]*) LIMIT_MB=900 ;; esac
CBUDGET_MB=$(( LIMIT_MB * BUDGET_CONTOURS_PCT / 100 ))
RBUDGET_MB=$(( LIMIT_MB * BUDGET_ROCKS_PCT / 100 ))

# Planetiler s rozpočtom: keď je výsledok nad stropom, skúsi o zoom
# nižšie. Použitý maxzoom ostane v `PM_Z` – návratová hodnota by sa
# miešala s výstupom Planetilera, ktorý ide na stdout.
pmtiles_do_rozpoctu() { # $1 schéma $2 výstup $3 maxzoom $4 strop MB $5 dno $6 popis $7 rada
  PM_Z="$3"
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
    [ "$PM_MB" -le "$4" ] && break

    if [ "$PM_Z" -le "$5" ]; then
      echo "::warning::$6 majú ${PM_MB} MB ani pri maxzoome ${5} – $7"
      break
    fi
    PM_Z=$(( PM_Z - 1 ))
    echo "::warning::$6 sú nad stropom ${4} MB – skúšam maxzoom ${PM_Z}."
  done
}

T_PM=$(date +%s)
pmtiles_do_rozpoctu workers/contours.yml contours-out/contours.pmtiles \
  "$CZ" "$CBUDGET_MB" 10 "Vrstevnice" \
  "zvýš contour_interval (napr. 20 m) alebo ich pre toto územie vypni."
CZ="$PM_Z"

pmtiles_do_rozpoctu workers/rocks.yml contours-out/rocks.pmtiles \
  "$RZ" "$RBUDGET_MB" 12 "Skaly" \
  "zvýš rock_min_area alebo zmenši výrez."
RZ="$PM_Z"
echo "$RZ" > contours-out/rock-maxzoom.txt

# Skutočne použitý maxzoom si odloží aj cache, nech štýl vie, po
# ktorý zoom vrstevnice naozaj existujú. To isté platí pre zdroj
# výšok (ide do atribúcie mapy) a prah sklonu skál (do manifestu).
echo "$CZ" > contours-out/maxzoom.txt
# Do atribúcie ide model, z ktorého sú vrstevnice; keď sú vypnuté,
# ten, z ktorého sú skaly – v tej vrstve je aj tak len jedno z nich.
# Dva riadky namiesto vnorenej expanzie `${A:-${B:-...}`: tá končí
# dvomi zloženými zátvorkami za sebou a GitHub taký súbor NEPRIJME –
# workflow sa po pushnutí objaví ako beh bez jobov, pomenovaný
# cestou k súboru. Stráži to krok „Zátvorky výrazov v run blokoch"
# v Lint workflows; aj tento komentár preto tie dve zátvorky
# opisuje slovami.
DEM_FOR_STYLE="$CONTOUR_DEM"
[ -n "$DEM_FOR_STYLE" ] || DEM_FOR_STYLE="$ROCK_DEM_USED"
echo "$DEM_FOR_STYLE" > contours-out/dem-source.txt
# Prah sklonu má zmysel len pri skalách z DEM. Pri `tienovanie` žiadny
# sklon neexistuje, takže do manifestu ide `off` a namiesto neho
# sa tam píše zdroj skál.
if [ "$OPT_ROCKS" = 'true' ] \
   && [ "$OPT_ROCK_SOURCE" != 'tienovanie' ]; then
  echo "$ROCK_SLOPE" > contours-out/rock-slope.txt
else
  echo "off" > contours-out/rock-slope.txt
fi
if [ "$OPT_ROCKS" = 'true' ]; then
  echo "$OPT_ROCK_SOURCE" > contours-out/rock-source.txt
else
  echo "off" > contours-out/rock-source.txt
fi
ls -lh contours-out/
printf '%s\t%s\t%s\t%s\n' "50" "Vrstevnice a skaly → PMTiles" "$(( $(date +%s) - T_PM ))" \
  "vrstevnice z$CZ ($(du -h contours-out/contours.pmtiles | cut -f1)), skaly z$RZ ($(du -h contours-out/rocks.pmtiles | cut -f1))" \
  >> steps-out/contours.tsv

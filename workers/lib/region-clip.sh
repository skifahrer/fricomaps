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
# bboxu, nie z tvaru regiónu. Bbox Prešovského kraja je 199 × 82 km, takmer
# dvojnásobok jeho plochy, takže mapa pokračuje do Poľska aj na Ukrajinu.
# `--polygon` je v Planetileri „emit any tile that intersects the shape",
# takže sa dlaždice, ktoré sa tvaru nedotknú, prestanú vyrábať.
#
# ČO V TÝCH DLAŽDICIACH ZA HRANICOU NAOZAJ JE – NAMERANÉ, lebo pôvodné
# vysvetlenie („vodstvo, pobrežia a Natural Earth, ktoré sú celosvetové")
# na vysokých zoomoch NEPLATÍ. Kontrolný pokus: ten istý PBF Bratislavského
# kraja a `--bounds=16.30,47.90,16.70,48.20`, teda obdĺžnik CELÝ mimo kraja.
# Na z12 z neho vypadli TRI dlaždice a v každej jedna čiara trajektu – žiadne
# vodné polygóny, žiadny Natural Earth. Dôvod: pobrežné polygóny sú OCEÁN
# (Slovensko žiadny nemá) a Natural Earth končí na nízkych zoomoch, kde
# dlaždica pretína kraj tak či tak.
#
# Za hranicou teda vzniká to, čo cez rez PBF PRESAHUJE – a v našich dátach to
# je riedke. Rozpis z dlaždíc navyše (Bratislavský kraj, maxzoom 14): na z14
# 232 dlaždíc s 628 prvkami dokopy, čiže ~2,7 prvku na dlaždicu – obecné
# hranice (`admin_level` 8 a 10), mená jazier, CHKO, lesy a polia, potoky,
# poľné cesty, trajekty. Všetko OSM, nič celosvetové.
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
# ═══ VYPÍNAČ `region_clip` (voľba, DOČASNE `false`) ═══
#
# `region_clip=false` orez PRESKOČÍ a dá Planetileru `--bounds`, teda obdĺžnik
# bboxu. Mapa tým vyzerá rovnako – hranicu kreslí až maska v štýle
# (`workers/deploy/region-mask.py`), ktorá je „celý svet mínus región" a
# prekryje všetko, čo za hranicou vznikne. Ušetrené sa tým NIČ nemá; je to
# vypínač na skúšanie, nie zrýchlenie.
#
# Namerané (Bratislavský kraj, planetiler.jar z releases, maxzoom 14, tie isté
# prepínače, aké dáva `workers/tiles/build.sh`; kraj je 57 % svojho bboxu):
#
#                       dlaždíc     bajtov      čas
#     --polygon            1271   19 330 130     56 s
#     --bounds             1607   19 467 060     54 s
#                         +27 %       +0,7 %     rovnako
#
# A po zoomoch, lebo priemer to skresľuje: do z9 VRÁTANE nepribudla ani jedna
# dlaždica – tam sa kraja dotýka každá, takže orez nemá čo vypustiť. Celý
# rozdiel je hore: z10 +1, z11 +9, z12 +27, z13 +70, z14 +232, dokopy 339
# dlaždíc a 138 209 B.
#
# Prázdne nie sú, ale ani plné (rozpis vyššie: ~2,7 prvku na dlaždicu na z14).
# Je to lem cez hranicu kraja – presne to, čo maska aj tak prekryje.
#
# Kým je vypínač v `false`, hovorí to `::warning::` v každom behu – aby sa
# nezabudlo, že sa v stiahnutej mape vezie územie za hranicou (pravidlo 8).
# Späť sa zapína `region_clip=true` v inpute `options`.
#
# Použitie:
#   mapfile -t CLIP < <(workers/lib/region-clip.sh "$REGION_BBOX")
#   java -jar planetiler.jar … "${CLIP[@]}"
set -euo pipefail

BBOX="${1:-}"
POLY="${2:-data/region.poly}"
# PREDVOLENE ZAPNUTÝ, keď premenná nepríde: vypnúť orez sa má dať len tak, že
# to niekto NAPÍŠE. Skript púšťajú aj kontroly a lokálne spustenia, a tam by
# „nič som nenastavil" nemalo znamenať „mapa presahuje".
CLIP_ON="${OPT_REGION_CLIP:-true}"

# Argumenty idú na stdout (volajúci si ich načíta), vysvetlenie do logu.
if [ -s "$POLY" ] && [ "$CLIP_ON" != 'true' ]; then
  if [ -n "$BBOX" ]; then echo "--bounds=$BBOX"; fi
  echo "::warning::Orez na región je vypnutý (\`region_clip=false\`), takže sa dlaždice vyrobia na celom obdĺžniku bboxu – na Bratislavskom kraji je to o 27 % dlaždíc a 0,7 % bajtov viac a je v nich lem dát cez hranicu kraja. V mape to nevidno, lebo hranicu dokresľuje maska v štýle. Späť to zapneš \`region_clip=true\` v inpute \`options\`." >&2
elif [ -s "$POLY" ]; then
  echo "--polygon=$POLY"
  echo "Orez na región: $POLY – dlaždice mimo regiónu sa nevyrobia. (\`--bounds\` sa zámerne NEPRIDÁVA, tichý vypínač polygónu – viď hlavičku skriptu.)" >&2
else
  # (`set -e`: `[ … ] && echo` by pri prázdnom bboxe zhodilo skript.)
  if [ -n "$BBOX" ]; then echo "--bounds=$BBOX"; fi
  echo "::warning::Polygón regiónu ($POLY) nie je, takže sa dlaždice vyrobia na CELOM obdĺžniku bboxu – mapa bude siahať aj za región (vodstvo a Natural Earth kreslí Planetiler všade). Zvyčajne to znamená, že sa v jobe \`plan\` nestiahol \`.poly\`; skús beh zopakovať." >&2
fi

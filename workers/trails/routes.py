#!/usr/bin/env python3
"""
Značené trasy z OSM: turistické chodníky, cyklotrasy, bežky, jazdecké trasy.

**Prečo vlastný krok a nie OpenMapTiles.** Trasa nie je cesta – je to
`type=route` **relácia**, ktorá zbiera cudzie cesty a nesie značenie
(`osmc:symbol`, `colour`, `network`, `name`). Schéma OpenMapTiles relácie
trás nemá: v dlaždiciach je len cesta (`class=path`), takže z nej nijako
nezistíš, či po nej vedie červená turistická, dve cyklotrasy, alebo nič.

**Do dlaždíc ide aj ZNAČKA, nie len farba.** `osmc:symbol` je predpis toho,
čo je namaľované na strome (biely alebo žltý štvorec s farebným pásom,
trojuholník na vrchol, bicykel na cyklotrase) – rozoberá ho `tags.py` na
trojicu `mark`, `mark_bg`, `mark_fg` a štýl z nej skladá meno obrázka
v sprite. Farba pásika (`colour`) je iná otázka: tá hovorí, čím trasu
NAKRESLIŤ, značka je obrázok tabuľky, ktorú má človek v teréne hľadať.

**Jedna línia na dvojicu (cesta, trasa).** Po jednej ceste vedie bežne
viac trás naraz (napr. červená aj modrá turistická + cyklotrasa). Preto sa
každá cesta zapíše toľkokrát, koľko trás po nej vedie, a každá kópia dostane
svoj **pruh** (`side` + `off`) – v štýle je to `line-offset`, takže sa trasy
kreslia ako farebné pásiky **vedľa** cesty a samotná cesta zostane vidieť aj
s tým, aká je (chodník, lesná cesta, asfaltka).

**Pešie trasy sú na jednej strane, kolesové na druhej.** Po jednom chodníku
vedie bežne turistická značka aj cyklotrasa; keby boli v jednom rade, tlačili
by sa od cesty ďalej a ďalej. `side` je preto +1 (pešie: turistická, ferrata,
bežky, jazdecké) alebo −1 (kolesové: cyklo, MTB), `off` je poradie v rade **na
tej svojej strane**, číslované od cesty von od nuly:

    ━━ cyklotrasa (side −1, off 0) ━━
    ── chodník ──────────────────────
    ━━ červená    (side +1, off 0) ━━
    ━━ modrá      (side +1, off 1) ━━

Ako ďaleko od cesty ten rad začne a aký je krok, rozhoduje štýl – nie tieto
dáta. Preto sa sem posiela aj `way`: `road` (asfaltka, ktorá je v mape široká,
takže pásik musí ísť za jej okraj) alebo `path` (chodník a lesná cesta, kde
stačí jemný odstup, nech je pod pásikom vidieť aj samotný chodník).

Poradie pruhov závisí len od vlastností trasy (sieť → druh → farba → id),
nikdy nie od poradia členov v relácii. Vďaka tomu si trasy na susedných
úsekoch pruhy neprehadzujú – dôležitejšia je vždy bližšie k ceste. Keď
niektorá trasa začne alebo skončí, ostatné sa o pruh posunú (inak by
vznikla diera), ale ich vzájomné poradie ostane.

**Smer čiary sa neurčuje z nej samej, ale z toho, na čo nadväzuje.**
`line-offset` posúva podľa smeru geometrie, takže dve susedné cesty nakreslené
proti sebe majú pásik raz vľavo a raz vpravo. Kým sa smer normalizoval „od
západnejšieho konca", rozhodovala o ňom pri severojužnom chodníku pár metrov
široká kľukatina – a pásik preskakoval na druhú stranu na každom druhom úseku:

    úsek A (mierne na východ)  → kreslí sa na sever → pásik vpravo
    úsek B (mierne na západ)   → kreslí sa na juh   → pásik VĽAVO
    úsek C (mierne na východ)  → kreslí sa na sever → pásik vpravo

Preto sa cesty najprv **poreťazia podľa spoločných uzlov** (`orient_ways`) a
smer sa im pridelí tak, aby na seba nadväzovali. Rozhoduje teda susedstvo, nie
tvar jednej čiary.

Nad PBF sa preto ide TRIKRÁT: relácie (kto kade vedie) → koncové uzly ciest
(kto s kým susedí, bez súradníc, takže bez indexu) → geometria. Tretí priechod
je jediný drahý; medzi druhým a tretím sa rozhodne o smeroch.

**Zlom nad 120° sa rozdelí (`ease_corners`).** Pásik sa v zákrute zošíva
spojom `miter`, ale ten sa nedostane nad dvojnásobok odstupu – teda nad zlom
120° – a ostrejší zlom MapLibre zreže, takže pásik v zákrute vyzerá zúžený.
Taký zlom sa preto rozdelí na niekoľko po 60°; reže sa pritom len 2 m, čiže
pásik ide tade, kade chodník (0,6 px pri z16).

Vstup je PBF **predfiltrovaný** na `type=route` aj s členmi:

    osmium tags-filter region.osm.pbf \\
      r/route=hiking,foot,bicycle,mtb,ski,horse,via_ferrata \\
      -o data/trails.osm.pbf

Použitie:
    python3 workers/trails/routes.py --pbf=data/trails.osm.pbf \\
        --out=data/trails.geojson --stats=trail-stats.txt
"""
import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict, deque

import osmium

# Čo o trase hovoria jej TAGY (farba pásika, sieť, značka), je vo vedľajšom
# `tags.py`: iná otázka než „kade trasa vedie", ktorú rieši tento súbor.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tags import (  # noqa: E402  (až za `sys.path`, inak sa modul nenájde)
    TIER_ORDER,
    resolve_colour,
    resolve_mark,
    resolve_tier,
)

# ---------------------------------------------------------------- druhy trás
# Kľúč je hodnota `route` v relácii, hodnota je náš druh – štýl podľa neho
# kreslí trasy rôznou farbou, hrúbkou a ikonou.
ROUTE_TYPES = {
    "hiking": "hiking",
    "foot": "hiking",
    "walking": "hiking",
    "bicycle": "bicycle",
    "mtb": "mtb",
    "ski": "ski",
    "nordic": "ski",
    "skitour": "ski",
    "horse": "horse",
    # Ferrata je značená trasa ako každá iná – relácia `type=route` nad
    # cudzími cestami – ale vlastný druh: vedie po skale, nie po chodníku,
    # a v mape má byť na prvý pohľad odlíšená od turistickej značky.
    "via_ferrata": "ferrata",
}

# Poradie druhov v pruhoch – pešie značky najbližšie k ceste, potom kolesá.
ROUTE_ORDER = {"hiking": 0, "ferrata": 1, "bicycle": 2, "mtb": 3, "ski": 4,
               "horse": 5}

# NA KTORÚ STRANU CESTY IDE PÁSIK. Kolesové trasy na opačnú než pešie: po
# jednom chodníku vedie bežne turistická značka aj cyklotrasa a v jednom rade
# by sa druhá z nich odsunula od cesty tak ďaleko, že by pri nej už nebolo
# vidieť, ku ktorej ceste patrí. Takto obe začínajú hneď pri ceste, každá zo
# svojej strany. +1 je vpravo v smere čiary (a ten je normalizovaný), −1 vľavo.
SIDE_BY_ROUTE = {"hiking": 1, "ferrata": 1, "ski": 1, "horse": 1,
                 "bicycle": -1, "mtb": -1}

# ------------------------------------------------------ po čom trasa vedie
# Odstup pásika od čiary pod ním nie je jedno číslo: asfaltka je v mape
# niekoľkonásobne širšia než chodník, takže odstup, pri ktorom sa pásik lepí
# na chodník, leží uprostred cesty. Do dlaždíc preto ide, po čom trasa vedie,
# a odstup pre každý z tých dvoch prípadov si drží štýl.
#
# `path` sú chodníky a lesné/poľné cesty (tenká prerušovaná čiara, kde pásik
# potrebuje jemný odstup, nech je pod ním vidieť aj samotný chodník),
# `road` je všetko ostatné (široká čiara s obrysom – pásik musí za jej okraj).
PATH_HIGHWAYS = {
    "path", "footway", "bridleway", "steps", "track", "cycleway", "corridor",
}


def way_class(tags):
    """Po čom trasa vedie: `path` (chodník, lesná cesta) alebo `road`."""
    return "path" if (tags.get("highway") or "").strip().lower() \
        in PATH_HIGHWAYS else "road"

# ------------------------------------------------ zlom, ktorý sa nedá zošiť
# Pásik trasy sa kreslí `line-offset`, teda posunutím KAŽDÉHO VRCHOLA čiary.
# Ako ďaleko sa vrchol posunie, rozhoduje spoj čiary v štýle – a ten je
# `miter` (rozpis v `TRAIL_JOIN` v poc/web/themes.js): vrchol ide po osi zlomu
# o `odstup / cos(zlom/2)`, čo je presne roh rovnobežky, takže pásik má ten
# istý ostrý uhol ako chodník pod ním a rovnakú hrúbku ako na rovine.
#
# LENŽE `miter` MÁ STROP. MapLibre pakuje ten posun do bajtu (63 jednotiek na
# šírku čiary), takže sa nad DVOJNÁSOBOK odstupu nedostane – a dvojnásobok je
# presne zlom 120°. Ostrejší zlom preto zreže na `bevel`: obe ramená pásika sa
# skončia pred zlomom a v zákrute ostane diera. V mape to vyzerá, že sa pásik
# v zákrute ZÚŽIL, prípadne sa na chvíľu stratil.
#
# ČO S TÝM. Zlom nad 120° sa rozdelí na niekoľko zlomov po 60° (posun 1,15×
# odstupu, čiže hlboko pod stropom) – každý z nich už `miter` zošije a pásik
# ide zákrutou v rovnakej hrúbke. Delí sa krátkym oblúkom, nie posunutím
# vrcholu inam:
#
#   * REŽE SA LEN 2 m, čiže vrchol pásika je od zlomu chodníka najviac 1 m.
#     Pri z16 je to 0,6 px a pri z14 (strop dlaždíc trás) 0,15 px – čiže
#     pásik ide zákrutou tade, kade ide chodník. Menej sa rezať nedá: dlaždice
#     trás majú pri z14 rozlíšenie 0,39 m a Planetiler ich pred zápisom ešte
#     zjednodušuje (~0,6 m), takže kratší oblúk by sa v nich stratil a zlom by
#     bol späť.
#   * ZLOMOV NAD 120° JE MÁLO. Namerané na 419 tatranských cestách (22 238
#     bodov z Overpassu): 354 zlomov, teda 1,6 % vrcholov. Zaobľovať aj miernejšie
#     zlomy nemá zmysel (`miter` ich zošije) a nie je zadarmo – pri hranici
#     30° má tretina vrcholov zlom nad ňou a geometria narastie o 108 %.
#   * VLÁSENKA nad ~150° ostane vlásenkou: tam sa pásik pri hrote skončí
#     (odrezať špičku o desiatky metrov, aby sa zošila, by bolo horšie).
#   * KRAJNÉ BODY sa nehýbu vôbec.
EASE_ABOVE_DEG = 120.0
MAX_TURN_DEG = 60.0
CUT_M = 2.0
# Body bližšie než toto sú po zaoblení to isté miesto – dva vrcholy na sebe
# nemajú definovaný smer, takže by z nich `line-offset` vyrobil ďalší výbežok.
MIN_STEP_M = 0.2


def ease_corners(coords, above_deg=EASE_ABOVE_DEG, max_turn_deg=MAX_TURN_DEG,
                 cut_m=CUT_M):
    """Zaoblí zlomy ostrejšie než `above_deg`; vracia `(body, počet)`.

    Oblúk sa delí na kúsky pod `max_turn_deg`. Počíta sa v metroch (rovinná
    aproximácia okolo stredu čiary – pri dĺžke cesty do pár kilometrov je
    skreslenie pod percento), krajné body sa nehýbu: cesty na seba musia
    nadväzovať tými istými uzlami ako v OSM.
    """
    if len(coords) < 3:
        return coords, 0
    lat_mid = sum(c[1] for c in coords) / len(coords)
    mx = 111320.0 * math.cos(math.radians(lat_mid))
    my = 110540.0
    pts = [((lon - coords[0][0]) * mx, (lat - coords[0][1]) * my)
           for lon, lat in coords]
    max_turn = math.radians(max_turn_deg)
    above = math.radians(above_deg)

    out = [pts[0]]
    eased = 0
    for i in range(1, len(pts) - 1):
        (px, py), (x, y), (nx, ny) = pts[i - 1], pts[i], pts[i + 1]
        ax, ay, bx, by = x - px, y - py, nx - x, ny - y
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if la == 0 or lb == 0:
            continue
        dot = max(-1.0, min(1.0, (ax * bx + ay * by) / (la * lb)))
        turn = math.acos(dot)
        if turn <= above:
            out.append((x, y))
            continue
        eased += 1
        cut = min(cut_m, la * 0.45, lb * 0.45)
        a = (x - ax / la * cut, y - ay / la * cut)
        b = (x + bx / lb * cut, y + by / lb * cut)
        # Kvadratická Bezierova krivka s pôvodným vrcholom ako riadiacim
        # bodom: začína aj končí v smere ramena, takže na oblúk nadväzuje bez
        # zlomu, a rozdelí zlom na `n` kúskov. Kúsok navyše je preto, že
        # Bezier nerozdeľuje uhol rovnomerne – bez neho ostane na jeho konci
        # zlom o pár stupňov väčší, než je MAX_TURN.
        n = max(2, math.ceil(turn / max_turn) + 1)
        out.append(a)
        for k in range(1, n):
            t = k / n
            s = 1 - t
            out.append((s * s * a[0] + 2 * s * t * x + t * t * b[0],
                        s * s * a[1] + 2 * s * t * y + t * t * b[1]))
        out.append(b)
    out.append(pts[-1])

    # Späť na stupne; body, ktoré po zaoblení splynuli, sa zahodia. Krajné
    # body sa NEPREPOČÍTAVAJÚ, berú sa pôvodné: cesty na seba nadväzujú
    # spoločným uzlom a ten sa nesmie pohnúť ani o stotinu sekundy, inak
    # Planetiler susedné úseky trasy nezlepí a popisok nemá na čom stáť.
    lon0, lat0 = coords[0]
    res = []
    for j, (x, y) in enumerate(out):
        last = j == len(out) - 1
        if res and not last and \
                math.hypot(x - res[-1][1], y - res[-1][2]) < MIN_STEP_M:
            continue
        if j == 0:
            res.append((list(coords[0]), x, y))
        elif last:
            res.append((list(coords[-1]), x, y))
        else:
            res.append(([round(lon0 + x / mx, 7),
                         round(lat0 + y / my, 7)], x, y))
    return [ll for ll, _, _ in res], eased


# Role členov, ktoré nie sú samotnou trasou (rozcestníky, značky, zastávky).
SKIP_ROLES = {
    "guidepost", "marker", "sign", "signpost", "stop", "platform",
    "site", "label", "map", "fixme", "shelter", "info",
}

# Trasy, ktoré ešte neexistujú, do mapy nepatria.
SKIP_STATES = {"proposed", "planned", "abandoned", "removed", "disused"}


# --------------------------------------------------- smer čiar (reťazenie)

def orient_ways(ends):
    """Ktoré cesty otočiť, aby na seba pásiky nadväzovali.

    `ends` je `{id cesty: (prvý uzol, posledný uzol)}` – teda len susedstvo,
    žiadne súradnice. Vracia `(množina ciest na otočenie, spory, reťaze)`.

    AKO. Cesty sú hrany grafu, uzly OSM sú jeho vrcholy. Od každej neprebranej
    cesty sa ide do šírky a susedovi sa pridelí smer tak, aby v spoločnom uzle
    jedna KONČILA a druhá ZAČÍNALA – presne to znamená „ísť ďalej rovnako".
    Pásik potom drží stranu cez celý chodník bez ohľadu na to, ako kto ktorý
    úsek nakreslil.

    ČO TO NEVYRIEŠI, a vedieť sa to má: na križovatke troch a viac chodníkov
    „nadväzovať" nie je definované – dve vetvy z uzla vychádzajú a tretia doň
    vchádza, takže niektorá stranu prehodí. Je to ale križovatka, kde sa trasa
    aj tak vetví, nie prostriedok chodníka. Koľko takých miest v území je,
    hovorí návratový `spory` – keď to číslo skočí, niečo sa pokazilo.

    Smer prvej cesty v reťazi (a tým fyzická strana celého chodníka) je
    ľubovoľný, ale STÁLY: berie sa najmenšie id cesty a v nej menšie id uzla.
    Dôležité je, že sa strana nemení pozdĺž trasy, nie to, či je to práve
    severná.
    """
    at = defaultdict(list)
    for wid, (first, last) in ends.items():
        at[first].append(wid)
        # Uzavretý okruh sa dotýka svojho uzla dvakrát; do susedstva patrí raz.
        if last != first:
            at[last].append(wid)

    flip = {}
    chains = 0
    for seed in sorted(ends):
        if seed in flip:
            continue
        chains += 1
        first, last = ends[seed]
        flip[seed] = first > last
        queue = deque([seed])
        while queue:
            wid = queue.popleft()
            first, last = ends[wid]
            tail, head = (last, first) if flip[wid] else (first, last)
            # Dopredu od hlavy (sused má v tom uzle ZAČÍNAŤ) aj dozadu od
            # päty (sused má v ňom KONČIŤ).
            for node, starts_there in ((head, True), (tail, False)):
                for nxt in at.get(node, ()):
                    if nxt in flip:
                        continue
                    nfirst, nlast = ends[nxt]
                    flip[nxt] = nfirst != node if starts_there else nlast != node
                    queue.append(nxt)

    # Spory sa počítajú len tam, kde je „nadväzovať" vôbec definované – teda
    # v uzle, kde sa stretávajú práve dve cesty. Keď tam obe končia (alebo obe
    # začínajú), pásik na tom mieste stranu prehodí.
    conflicts = 0
    for node, wids in at.items():
        if len(wids) != 2:
            continue
        heads = 0
        for wid in wids:
            first, last = ends[wid]
            head = first if flip[wid] else last
            heads += head == node
        if heads != 1:
            conflicts += 1

    return {wid for wid, rev in flip.items() if rev}, conflicts, chains


class Ends(osmium.SimpleHandler):
    """2. priechod: koncové uzly ciest, po ktorých nejaká trasa vedie.

    Bez súradníc, takže bez indexu uzlov – je to len `{cesta: (uzol, uzol)}`
    a je z toho vidieť, ktoré cesty na seba nadväzujú.
    """

    def __init__(self, by_way):
        super().__init__()
        self.by_way = by_way
        self.ends = {}

    def way(self, w):
        if w.id not in self.by_way or len(w.nodes) < 2:
            return
        self.ends[w.id] = (w.nodes[0].ref, w.nodes[-1].ref)


class Routes(osmium.SimpleHandler):
    """1. priechod: z relácií vyrobí zoznam trás na každej ceste."""

    def __init__(self):
        super().__init__()
        self.by_way = defaultdict(list)
        self.routes = 0
        self.skipped = Counter()

    def relation(self, r):
        tags = {t.k: t.v for t in r.tags}
        if tags.get("type") != "route":
            return
        route = ROUTE_TYPES.get((tags.get("route") or "").strip().lower())
        if not route:
            self.skipped[(tags.get("route") or "?")] += 1
            return
        if (tags.get("state") or "").strip().lower() in SKIP_STATES:
            self.skipped["state"] += 1
            return

        colour, hexcolour = resolve_colour(tags)
        tier, network = resolve_tier(tags)
        # Značka, ako je na strome – iná otázka než farba pásika: pásik
        # hovorí, čím trasu kresliť, značka je obrázok tabuľky, ktorú má
        # človek v teréne hľadať.
        mark, mark_bg, mark_fg = resolve_mark(tags, route, colour)
        info = {
            "route": route,
            "colour": colour,
            "hex": hexcolour,
            "mark": mark or "",
            "mark_bg": mark_bg or "",
            "mark_fg": mark_fg or "",
            "network": network,
            "tier": tier,
            "name": (tags.get("name:sk") or tags.get("name") or "").strip(),
            "ref": (tags.get("ref") or "").strip(),
            "rel": r.id,
        }
        self.routes += 1
        for m in r.members:
            if m.type != "w" or (m.role or "").strip().lower() in SKIP_ROLES:
                continue
            self.by_way[m.ref].append(info)


class Ways(osmium.SimpleHandler):
    """
    2. priechod: cesty, po ktorých nejaká trasa vedie, dostanú geometriu –
    a rovno toľko kópií, koľko trás po nich ide (každá vo svojom pruhu).
    """

    def __init__(self, by_way, out, flipped=frozenset()):
        super().__init__()
        self.by_way = by_way
        self.out = out
        # Ktoré cesty kresliť opačne, nech pásik drží stranu (`orient_ways`).
        self.flipped = flipped
        self.features = 0
        self.ways = 0
        self.no_geometry = 0
        self.by_type = Counter()
        self.by_colour = Counter()
        self.by_mark = Counter()
        self.by_tier = Counter()
        self.by_way_class = Counter()
        self.lanes = Counter()
        self.named = set()
        # Koľko zlomov sa rozdelilo a o koľko bodov z toho geometria narástla
        # – z toho je vidieť, či úprava nezačala robiť niečo iné, než má.
        self.eased = 0
        self.points_in = 0
        self.points_out = 0

    def way(self, w):
        routes = self.by_way.get(w.id)
        if not routes:
            return

        coords = []
        for n in w.nodes:
            if n.location.valid():
                coords.append([round(n.lon, 7), round(n.lat, 7)])
        if len(coords) < 2:
            self.no_geometry += 1
            return

        # Smer čiary určuje, na ktorú stranu ju `line-offset` posunie – a
        # rozhodlo sa o ňom v `orient_ways` podľa toho, na čo cesta NADVÄZUJE.
        # Kým sa normalizoval „od západnejšieho konca", rozhodovala o smere
        # severojužného chodníka pár metrov široká kľukatina a pásik
        # preskakoval na druhú stranu na každom druhom úseku.
        if w.id in self.flipped:
            coords.reverse()

        # Zlom nad 120° `miter` nezošije (rozpis pri `ease_corners`), takže sa
        # rozdelí. Raz na cestu, nie raz na pruh – všetky pruhy tej istej
        # cesty kreslia tú istú čiaru.
        self.points_in += len(coords)
        coords, eased = ease_corners(coords)
        self.points_out += len(coords)
        self.eased += eased

        lanes = self.lane_order(routes)
        way = way_class({t.k: t.v for t in w.tags})
        self.ways += 1
        self.lanes[len(lanes)] += 1
        self.by_way_class[way] += 1
        # Rady sa číslujú zvlášť pre každú stranu, takže prvá pešia aj prvá
        # kolesová trasa ležia hneď pri ceste – každá zo svojej.
        taken = Counter()
        for info in lanes:
            side = SIDE_BY_ROUTE.get(info["route"], 1)
            idx = taken[side]
            taken[side] += 1
            self.by_type[info["route"]] += 1
            self.by_colour[info["colour"] or "bez farby"] += 1
            self.by_mark[
                f'{info["mark_bg"]}-{info["mark_fg"]}-{info["mark"]}'
                if info["mark"] else "bez značky"
            ] += 1
            self.by_tier[info["tier"]] += 1
            if info["name"]:
                self.named.add(info["rel"])
            props = {
                "route": info["route"],
                "tier": info["tier"],
                # Pruhy sa číslujú od cesty von a vždy na tú istú stranu.
                # Keby boli vycentrované, koniec jednej trasy by posunul
                # všetky ostatné – takto ostanú, kde boli.
                "side": side,
                "off": idx,
                "way": way,
                "cnt": len(lanes),
                "rel": info["rel"],
            }
            for key in ("colour", "hex", "network", "name", "ref",
                        "mark", "mark_bg", "mark_fg"):
                if info[key]:
                    props[key] = info[key]
            self.write(coords, props)
            self.features += 1

    @staticmethod
    def lane_order(routes):
        """
        Poradie pruhov na ceste. Kľúč je len z vlastností trasy, takže dve
        trasy si na susedných úsekoch pruhy neprehodia.

        Zároveň sa zahodia duplikáty: nadradená trasa (superroute) a jej časť
        sú v OSM dve relácie na tých istých cestách – ako dva rovnaké pásiky
        vedľa seba by to bola len chyba v mape.
        """
        seen = {}
        for info in routes:
            key = (info["route"], info["colour"], info["hex"],
                   info["ref"] or info["name"])
            # Z rovnakých trás si necháme tú s názvom – má čo popísať.
            old = seen.get(key)
            if old is None or (not old["name"] and info["name"]):
                seen[key] = info
        return sorted(
            seen.values(),
            key=lambda i: (
                TIER_ORDER.get(i["tier"], 9),
                ROUTE_ORDER.get(i["route"], 9),
                i["colour"],
                i["ref"],
                i["name"],
                i["rel"],
            ),
        )

    def write(self, coords, props):
        """Features sa píšu priebežne – v pamäti by ich bol celý kraj naraz."""
        self.out.write("," if self.features else "")
        json.dump(
            {"type": "Feature", "properties": props,
             "geometry": {"type": "LineString", "coordinates": coords}},
            self.out, ensure_ascii=False, separators=(",", ":"),
        )
        self.out.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbf", required=True, help="PBF predfiltrovaný na relácie trás")
    ap.add_argument("--out", required=True, help="výstupný .geojson pre Planetiler")
    ap.add_argument("--stats", default="", help="kam zapísať čísla pre súhrn buildu")
    args = ap.parse_args()

    if not os.path.exists(args.pbf):
        print(f"::error::Vstup {args.pbf} neexistuje.", file=sys.stderr)
        return 1

    print(f"1/3 – hľadám relácie trás v {args.pbf} …", flush=True)
    routes = Routes()
    routes.apply_file(args.pbf)
    print(f"    trás: {routes.routes}, ciest s trasou: {len(routes.by_way)}")
    if routes.skipped:
        top = ", ".join(f"{k}={v}" for k, v in routes.skipped.most_common(6))
        print(f"    preskočené relácie (iný druh alebo stav): {top}")

    if not routes.routes:
        print("::warning::V tomto území nie je ani jedna značená trasa – "
              "mapa pôjde bez nich.")

    # Smer čiary rozhoduje, na ktorú stranu cesty pásik padne, takže sa
    # nesmie brať z tvaru jednej čiary (rozpis v hlavičke). Tento priechod je
    # lacný: číta len koncové uzly, teda bez indexu súradníc.
    print("2/3 – kto s kým susedí (smer pásikov) …", flush=True)
    ends = Ends(routes.by_way)
    ends.apply_file(args.pbf)
    flipped, conflicts, chains = orient_ways(ends.ends)
    print(f"    ciest {len(ends.ends)} v {chains} reťaziach, "
          f"otočených {len(flipped)}")
    # Spor = uzol, kde sa stretávajú dve cesty a pásik na ňom stranu prehodí.
    # Nula sa čakať nedá (križovatky troch chodníkov), ale je to číslo, ktoré
    # má byť malé – keď skočí, smerovanie sa pokazilo.
    pct = 100.0 * conflicts / max(1, len(ends.ends))
    print(f"    miest, kde pásik napriek tomu prehodí stranu: {conflicts} "
          f"({pct:.1f} % ciest)")
    if pct > 5:
        print("::warning::Pásiky trás prehadzujú stranu na "
              f"{pct:.0f} % ciest – to je veľa. Pozri `orient_ways` vo "
              "workers/trails/routes.py; malo by to byť pod 5 %.")

    print("3/3 – skladám geometriu ciest …", flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write('{"type":"FeatureCollection","features":[\n')
        ways = Ways(routes.by_way, fh, flipped)
        # `locations=True` doplní súradnice uzlov – predfiltrovaný PBF ich má
        # v sebe, takže index nemusí byť na celé Slovensko.
        ways.apply_file(args.pbf, locations=True, idx="flex_mem")
        fh.write("]}\n")

    size_mb = os.path.getsize(args.out) / 1048576
    print(f"✓ {args.out}: {ways.features} úsekov na {ways.ways} cestách "
          f"({size_mb:.1f} MB)")
    if ways.no_geometry:
        print(f"::warning::{ways.no_geometry} ciest nemá v PBF súradnice "
              "(člen mimo územia) – tie úseky v mape nebudú.")
    order = sorted(ways.by_type.items(), key=lambda kv: -kv[1])
    print("  druhy:  " + ", ".join(f"{k} {v}" for k, v in order))
    print("  farby:  " + ", ".join(f"{k} {v}" for k, v in ways.by_colour.most_common()))
    print("  značky: " + ", ".join(
        f"{k} {v}" for k, v in ways.by_mark.most_common(8))
        + "  (podklad-farba-tvar; „bez značky\" kreslí ikonku druhu trasy)")
    print("  siete:  " + ", ".join(f"{k} {v}" for k, v in ways.by_tier.most_common()))
    print("  vedú po: " + ", ".join(
        f"{k} {v}" for k, v in ways.by_way_class.most_common())
        + "  (`path` = chodník a lesná cesta, `road` = ostatné; štýl podľa "
          "toho volí odstup pásika)")
    multi = sum(n for lanes, n in ways.lanes.items() if lanes > 1)
    print(f"  ciest s viac než jednou trasou: {multi} "
          f"(najviac naraz: {max(ways.lanes, default=0)})")
    grew = 100.0 * (ways.points_out - ways.points_in) / max(1, ways.points_in)
    print(f"  rozdelených zlomov nad {EASE_ABOVE_DEG:.0f}°: {ways.eased} "
          f"(bodov {ways.points_in} → {ways.points_out}, {grew:+.1f} %) – nad "
          "nimi spoj `miter` pásik nezošije a v zákrute sa zúži")

    if args.stats:
        with open(args.stats, "w", encoding="utf-8") as fh:
            fh.write(f"routes={routes.routes}\n")
            fh.write(f"named={len(ways.named)}\n")
            fh.write(f"ways={ways.ways}\n")
            fh.write(f"features={ways.features}\n")
            fh.write(f"multi={multi}\n")
            fh.write(f"chains={chains}\n")
            fh.write(f"side_flips={conflicts}\n")
            fh.write(f"eased={ways.eased}\n")
            fh.write(f"max_lanes={max(ways.lanes, default=0)}\n")
            for key, count in ways.by_type.items():
                fh.write(f"type_{key}={count}\n")
            for key, count in ways.by_tier.items():
                fh.write(f"tier_{key}={count}\n")
            # Hodnota má medzery aj zátvorky a súhrn buildu si súbor načíta
            # cez `.` (source) – bez úvodzoviek by to shell nezobral.
            fh.write('colours="' + ", ".join(
                f"{k} {v}" for k, v in ways.by_colour.most_common()) + '"\n')
            # Koľko úsekov dostalo naozajstnú značku (biely/žltý štvorec).
            # Zvyšok sa kreslí ikonkou druhu trasy – z toho čísla je vidieť,
            # či sa `osmc:symbol` v tomto kraji vôbec používa.
            fh.write(f"marked={sum(v for k, v in ways.by_mark.items() if k != 'bez značky')}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

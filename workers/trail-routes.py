#!/usr/bin/env python3
"""
Značené trasy z OSM: turistické chodníky, cyklotrasy, bežky, jazdecké trasy.

**Prečo vlastný krok a nie OpenMapTiles.** Trasa nie je cesta – je to
`type=route` **relácia**, ktorá zbiera cudzie cesty a nesie značenie
(`osmc:symbol`, `colour`, `network`, `name`). Schéma OpenMapTiles relácie
trás nemá: v dlaždiciach je len cesta (`class=path`), takže z nej nijako
nezistíš, či po nej vedie červená turistická, dve cyklotrasy, alebo nič.

**Jedna línia na dvojicu (cesta, trasa).** Po jednej ceste vedie bežne
viac trás naraz (napr. červená aj modrá turistická + cyklotrasa). Preto sa
každá cesta zapíše toľkokrát, koľko trás po nej vedie, a každá kópia dostane
svoj **pruh** (`off`) – v štýle je to `line-offset`, takže sa trasy kreslia
ako farebné pásiky **vedľa** cesty a samotná cesta zostane vidieť aj s tým,
aká je (chodník, lesná cesta, asfaltka).

Pruhy sú číslované od cesty von (0,5 · 1,5 · 2,5 …), vždy na tú istú stranu:

    ── cesta ────────────────────────
    ━━ červená (off 0,5) ━━━━━━━━━━━━
    ━━ modrá   (off 1,5) ━━━━━━━━━━━━

Poradie pruhov závisí len od vlastností trasy (sieť → druh → farba → id),
nikdy nie od poradia členov v relácii. Vďaka tomu si trasy na susedných
úsekoch pruhy neprehadzujú – dôležitejšia je vždy bližšie k ceste. Keď
niektorá trasa začne alebo skončí, ostatné sa o pruh posunú (inak by
vznikla diera), ale ich vzájomné poradie ostane.

Smer čiary sa normalizuje (vždy od západnejšieho konca), lebo `line-offset`
posúva podľa smeru geometrie – dve susedné cesty nakreslené proti sebe by
inak mali pásik raz vľavo a raz vpravo.

Vstup je PBF **predfiltrovaný** na `type=route` aj s členmi:

    osmium tags-filter region.osm.pbf \\
      r/route=hiking,foot,bicycle,mtb,ski,horse,via_ferrata \\
      -o data/trails.osm.pbf

Použitie:
    python3 workers/trail-routes.py --pbf=data/trails.osm.pbf \\
        --out=data/trails.geojson --stats=trail-stats.txt
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

import osmium

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

# ------------------------------------------------------------------- siete
# `network` hovorí, aká je trasa dôležitá: i = medzinárodná, n = národná,
# r = regionálna, l = miestna (iwn/nwn/rwn/lwn pre pešie, icn/… pre cyklo,
# ihn/… pre jazdecké). Z toho je `tier`, ktorý riadi, od akého zoomu je
# trasa v dlaždiciach – diaľkové trasy majú byť vidieť aj z prehľadu.
TIER_BY_PREFIX = {"i": "international", "n": "national", "r": "regional", "l": "local"}
TIER_ORDER = {"international": 0, "national": 1, "regional": 2, "local": 3}

# Farby značiek, ktoré vie štýl prefarbiť cez paletu. Čokoľvek mimo tohto
# zoznamu ide do mapy ako surový hex z OSM (atribút `hex`).
NAMED_COLOURS = {
    "black": (0x00, 0x00, 0x00),
    "blue": (0x00, 0x00, 0xFF),
    "brown": (0x96, 0x4B, 0x00),
    "gray": (0x80, 0x80, 0x80),
    "green": (0x00, 0x80, 0x00),
    "orange": (0xFF, 0xA5, 0x00),
    "purple": (0x80, 0x00, 0x80),
    "red": (0xFF, 0x00, 0x00),
    "white": (0xFF, 0xFF, 0xFF),
    "yellow": (0xFF, 0xFF, 0x00),
}
COLOUR_ALIASES = {
    "grey": "gray",
    "silver": "gray",
    "lightgray": "gray",
    "lightgrey": "gray",
    "darkgray": "gray",
    "darkgrey": "gray",
    "violet": "purple",
    "magenta": "purple",
    "pink": "purple",
    "lightblue": "blue",
    "darkblue": "blue",
    "navy": "blue",
    "cyan": "blue",
    "lightgreen": "green",
    "darkgreen": "green",
    "lime": "green",
    "olive": "green",
    "gold": "yellow",
    "beige": "yellow",
    "maroon": "brown",
    "tan": "brown",
}
# Ako ďaleko smie byť hex od pomenovanej farby, aby sa na ňu ešte zaokrúhlil.
# 441 je maximum (čierna ↔ biela), 110 je „tá istá farba, iný odtieň“.
COLOUR_SNAP = 110

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Role členov, ktoré nie sú samotnou trasou (rozcestníky, značky, zastávky).
SKIP_ROLES = {
    "guidepost", "marker", "sign", "signpost", "stop", "platform",
    "site", "label", "map", "fixme", "shelter", "info",
}

# Trasy, ktoré ešte neexistujú, do mapy nepatria.
SKIP_STATES = {"proposed", "planned", "abandoned", "removed", "disused"}


def parse_hex(value):
    """`#a3b` / `a3b2c1` → (r, g, b); inak None."""
    m = HEX_RE.match(value.strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def resolve_colour(tags):
    """
    Farba značky ako (názov, hex).

    Poradie zdrojov je zámerné: `osmc:symbol` je *predpis značky* a jeho
    prvé pole je farba pásika na strome – to je presne to, čo je v teréne.
    `colour` býva to isté, ale niekedy chýba alebo nesie farbu podkladu.
    """
    raw = ""
    osmc = tags.get("osmc:symbol", "")
    if osmc:
        raw = osmc.split(":")[0].strip().lower()
    if not raw:
        raw = (tags.get("colour") or tags.get("color") or "").strip().lower()
    if not raw:
        return "", ""

    if raw in NAMED_COLOURS:
        return raw, ""
    if raw in COLOUR_ALIASES:
        return COLOUR_ALIASES[raw], ""

    rgb = parse_hex(raw)
    if rgb is None:
        return "", ""

    # Hex sa zaokrúhli na najbližšiu pomenovanú farbu, ale len keď je naozaj
    # blízko – #e01b24 je červená, #ff69b4 už nie. Čo sa nezaokrúhli, ide do
    # mapy tak, ako to je: štýl použije priamo tento hex.
    name, dist = min(
        ((n, sum((a - b) ** 2 for a, b in zip(rgb, ref)) ** 0.5)
         for n, ref in NAMED_COLOURS.items()),
        key=lambda x: x[1],
    )
    if dist <= COLOUR_SNAP:
        return name, ""
    return "", "#%02x%02x%02x" % rgb


def resolve_tier(tags):
    """Ako ďaleko je trasa vidieť: medzinárodná … miestna."""
    network = (tags.get("network") or "").strip().lower()
    base = network.split(":")[0]
    if len(base) == 3 and base[0] in TIER_BY_PREFIX and base[1:] in (
        "wn", "cn", "hn", "sn", "mn", "pn"
    ):
        return TIER_BY_PREFIX[base[0]], network

    # Bez siete rozhoduje dĺžka – diaľková trasa má byť vidieť aj z prehľadu,
    # aj keď ju nikto nezaradil do siete.
    try:
        km = float(re.sub(r"[^0-9.]", "", tags.get("distance", "")) or 0)
    except ValueError:
        km = 0
    if km >= 150:
        return "national", network
    if km >= 50:
        return "regional", network
    return "local", network


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
        info = {
            "route": route,
            "colour": colour,
            "hex": hexcolour,
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

    def __init__(self, by_way, out):
        super().__init__()
        self.by_way = by_way
        self.out = out
        self.features = 0
        self.ways = 0
        self.no_geometry = 0
        self.by_type = Counter()
        self.by_colour = Counter()
        self.by_tier = Counter()
        self.lanes = Counter()
        self.named = set()

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

        # Smer čiary určuje, na ktorú stranu ju `line-offset` posunie. Bez
        # normalizácie by pásik na susedných úsekoch preskakoval z jednej
        # strany cesty na druhú podľa toho, ako kto cestu nakreslil.
        if coords[0] > coords[-1]:
            coords.reverse()

        lanes = self.lane_order(routes)
        self.ways += 1
        self.lanes[len(lanes)] += 1
        for idx, info in enumerate(lanes):
            self.by_type[info["route"]] += 1
            self.by_colour[info["colour"] or "bez farby"] += 1
            self.by_tier[info["tier"]] += 1
            if info["name"]:
                self.named.add(info["rel"])
            props = {
                "route": info["route"],
                "tier": info["tier"],
                # Pruhy sa číslujú od cesty von a vždy na tú istú stranu.
                # Keby boli vycentrované, koniec jednej trasy by posunul
                # všetky ostatné – takto ostanú, kde boli.
                "off": idx + 0.5,
                "cnt": len(lanes),
                "rel": info["rel"],
            }
            for key in ("colour", "hex", "network", "name", "ref"):
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

    print(f"1/2 – hľadám relácie trás v {args.pbf} …", flush=True)
    routes = Routes()
    routes.apply_file(args.pbf)
    print(f"    trás: {routes.routes}, ciest s trasou: {len(routes.by_way)}")
    if routes.skipped:
        top = ", ".join(f"{k}={v}" for k, v in routes.skipped.most_common(6))
        print(f"    preskočené relácie (iný druh alebo stav): {top}")

    if not routes.routes:
        print("::warning::V tomto území nie je ani jedna značená trasa – "
              "mapa pôjde bez nich.")

    print("2/2 – skladám geometriu ciest …", flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write('{"type":"FeatureCollection","features":[\n')
        ways = Ways(routes.by_way, fh)
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
    print("  siete:  " + ", ".join(f"{k} {v}" for k, v in ways.by_tier.most_common()))
    multi = sum(n for lanes, n in ways.lanes.items() if lanes > 1)
    print(f"  ciest s viac než jednou trasou: {multi} "
          f"(najviac naraz: {max(ways.lanes, default=0)})")

    if args.stats:
        with open(args.stats, "w", encoding="utf-8") as fh:
            fh.write(f"routes={routes.routes}\n")
            fh.write(f"named={len(ways.named)}\n")
            fh.write(f"ways={ways.ways}\n")
            fh.write(f"features={ways.features}\n")
            fh.write(f"multi={multi}\n")
            fh.write(f"max_lanes={max(ways.lanes, default=0)}\n")
            for key, count in ways.by_type.items():
                fh.write(f"type_{key}={count}\n")
            for key, count in ways.by_tier.items():
                fh.write(f"tier_{key}={count}\n")
            # Hodnota má medzery aj zátvorky a súhrn buildu si súbor načíta
            # cez `.` (source) – bez úvodzoviek by to shell nezobral.
            fh.write('colours="' + ", ".join(
                f"{k} {v}" for k, v in ways.by_colour.most_common()) + '"\n')
    return 0


if __name__ == "__main__":
    sys.exit(main())

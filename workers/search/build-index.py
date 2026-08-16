#!/usr/bin/env python3
"""
Vyhľadávací index z OSM: názvy miest, hôrok, chát, trás – do SQLite FTS5.

Čítame prefiltrovaný PBF (cesty a relácie bez Planetilera), extraktorujeme
tituly, súradnice, tagy a geometriu, a skladujeme do SQLite s FTS5 pre
rýchle hľadanie s diakritikou.

Vstup: PBF predfiltrovaný na `osmium tags-filter --expressions` (bez bodov)
Výstup: SQLite s FTS5 tabuľkou `search_fts`
"""
import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict

import osmium


def tag_to_kind(tags):
    """OSM tags → kind (peak, hut, settlement, trail, water, amenity, place)."""
    # Osídlenia
    if tags.get("place"):
        place = tags["place"].lower()
        if place in ("city", "town"):
            return "settlement-city"
        if place in ("village", "hamlet"):
            return "settlement-village"
        if place == "locality":
            return "settlement-locality"
        return "settlement"

    # Prírodné body – vrchy, pramene, jaskyne
    natural = tags.get("natural", "").lower()
    if natural == "peak":
        return "peak"
    if natural in ("spring", "cave_entrance"):
        return "water"
    if natural in ("saddle",):
        return "pass"

    # Turizmus – chaty, táboriská, výhľadové body
    tourism = tags.get("tourism", "").lower()
    if tourism in ("alpine_hut", "mountain_hut", "wilderness_hut"):
        return "hut"
    if tourism in ("viewpoint",):
        return "viewpoint"
    if tourism in ("camp_site", "picnic_site"):
        return "amenity"

    # Dopytované – krčmy, reštaurácie, pamiatky
    amenity = tags.get("amenity", "").lower()
    if amenity in ("bar", "cafe", "restaurant"):
        return "amenity"
    if amenity in ("hotel", "guest_house", "hostel"):
        return "amenity"
    if amenity in ("memorial", "ruins"):
        return "amenity"
    if amenity == "fountain":
        return "amenity"

    # Cesty a trasy
    highway = tags.get("highway", "").lower()
    route = tags.get("route", "").lower()

    # Vymenovaná trasa (type=route relácia)
    if tags.get("type") == "route" and route:
        if route in ("hiking", "foot", "walking"):
            return "trail-hiking"
        if route == "bicycle":
            return "trail-bicycle"
        if route == "mtb":
            return "trail-mtb"
        if route == "ski":
            return "trail-ski"
        if route == "horse":
            return "trail-horse"
        if route == "via_ferrata":
            return "trail-ferrata"
        return "trail"

    # Vymenovaná cesta (nie relácia, ale cesta s menom)
    if highway and tags.get("name"):
        if highway in ("path", "footway", "track", "steps"):
            return "trail-hiking"
        if highway in ("cycleway",):
            return "trail-bicycle"
        return "trail"

    # Stavby – kríže, prírodné prvky
    man_made = tags.get("man_made", "").lower()
    if man_made in ("cross", "wayside_cross", "wayside_shrine", "tower", "mast"):
        return "landmark"

    # Všetko ostatné
    return "landmark"


def resolve_names(tags):
    """OSM tags → (primárny_názov, [alternatívne_názvy])."""
    # Poriadok priorit: name:sk > name > international variants
    primaries = ["name:sk", "name"]
    alternates_order = ["name:de", "name:hu", "name:en", "old_name", "alt_name"]

    primary = None
    for key in primaries:
        if tags.get(key):
            primary = tags[key].strip()
            break

    if not primary:
        return None, []

    # Zbierame alternatívne mená
    alts = []
    for key in alternates_order:
        if tags.get(key) and tags[key].strip() != primary:
            alts.append(tags[key].strip())

    return primary, alts


def extract_bbox_point(geom):
    """Geometria → (lon, lat, bbox)."""
    if not geom:
        return None, None, None, None, None, None

    if isinstance(geom, osmium.osm.WayNodeList):
        # Cesta: geometria zo všetkých uzlov
        lons = []
        lats = []
        for node in geom:
            lons.append(node.lon)
            lats.append(node.lat)
        if not lons:
            return None, None, None, None, None, None
        # Reprezentačný bod: center bbox
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)
        center_lon = (min_lon + max_lon) / 2
        center_lat = (min_lat + max_lat) / 2
        return center_lon, center_lat, min_lon, max_lon, min_lat, max_lat

    if isinstance(geom, osmium.osm.Node):
        # Bod: jeden bod je všetky štyri rohy
        return geom.lon, geom.lat, geom.lon, geom.lon, geom.lat, geom.lat

    return None, None, None, None, None, None


class SearchHandler(osmium.SimpleHandler):
    """Prvý priechod: zbierame uzly a relácie podľa typu."""

    def __init__(self):
        super().__init__()
        self.nodes = {}  # id → (lon, lat, tags)
        self.ways = {}  # id → (name, tags, geometry [nodes])
        self.relations = defaultdict(list)  # relation_id → [(way_id, role), ...]
        self.relation_data = {}  # id → (name, tags, type, route)

    def node(self, n):
        """Zbierame uzly so značkami."""
        if n.tags:
            self.nodes[n.id] = (n.lon, n.lat, dict(n.tags))

    def way(self, w):
        """Zbierame cesty so značkami."""
        if w.tags and w.tags.get("name"):
            self.ways[w.id] = (w.tags.get("name"), dict(w.tags), list(w.nd_ids()))

    def relation(self, r):
        """Zbierame relácie type=route."""
        if r.tags.get("type") == "route" and r.tags.get("route"):
            self.relation_data[r.id] = (
                r.tags.get("name"),
                dict(r.tags),
                r.tags.get("type"),
                r.tags.get("route"),
            )
            for member in r.members:
                if member.type == "w":
                    self.relations[r.id].append((member.ref, member.role or ""))


class Indexer:
    """Budovateľ FTS5 indexu."""

    def __init__(self, pbf_path, db_path, region_key, osm_id_field):
        self.pbf_path = pbf_path
        self.db_path = db_path
        self.region_key = region_key
        self.osm_id_field = osm_id_field
        self.conn = None
        self.cursor = None

    def init_db(self):
        """Vytvoríme SQLite s FTS5."""
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()

        # Tabuľka FTS5 – diakritikou-folding tokenizer
        self.cursor.execute("""
            CREATE VIRTUAL TABLE search_fts USING fts5(
                osm_id UNINDEXED,
                osm_type UNINDEXED,
                kind UNINDEXED,
                name,
                name_folded,
                alternate_names UNINDEXED,
                region_key UNINDEXED,
                lon UNINDEXED,
                lat UNINDEXED,
                bbox_min_lon UNINDEXED,
                bbox_max_lon UNINDEXED,
                bbox_min_lat UNINDEXED,
                bbox_max_lat UNINDEXED,
                tags_json UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 2'
            )
        """)

        # Indexovacia tabuľka – presné vyhľadávanie podľa osm_id + osm_type
        self.cursor.execute("""
            CREATE TABLE search_index (
                osm_id INTEGER,
                osm_type TEXT,
                kind TEXT,
                name TEXT,
                region_key TEXT,
                tags_json TEXT,
                PRIMARY KEY (osm_id, osm_type)
            )
        """)

        self.conn.commit()

    def index_nodes(self, handler):
        """Indexujeme OSM uzly (vrchy, pramene, haty...)."""
        count = 0
        for node_id, (lon, lat, tags) in handler.nodes.items():
            if not tags.get("name"):
                continue

            primary_name, alt_names = resolve_names(tags)
            if not primary_name:
                continue

            kind = tag_to_kind(tags)
            tags_json = json.dumps(tags, ensure_ascii=False, indent=None)

            # FTS5
            self.cursor.execute(
                """
                INSERT INTO search_fts
                (osm_id, osm_type, kind, name, name_folded, alternate_names,
                 region_key, lon, lat, bbox_min_lon, bbox_max_lon,
                 bbox_min_lat, bbox_max_lat, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    "node",
                    kind,
                    primary_name,
                    primary_name,  # FTS5 tokenizer zálozi folding
                    "|".join(alt_names) if alt_names else "",
                    self.region_key,
                    lon,
                    lat,
                    lon,
                    lon,
                    lat,
                    lat,
                    tags_json,
                ),
            )

            # Index tabuľka
            self.cursor.execute(
                "INSERT OR IGNORE INTO search_index VALUES (?, ?, ?, ?, ?, ?)",
                (node_id, "node", kind, primary_name, self.region_key, tags_json),
            )
            count += 1

        self.conn.commit()
        return count

    def index_ways(self, handler):
        """Indexujeme OSM cesty (menované chodníky, cyklotrasy...)."""
        count = 0
        for way_id, (name, tags, nd_ids) in handler.ways.items():
            if not name:
                continue

            primary_name, alt_names = resolve_names(tags)
            if not primary_name:
                continue

            kind = tag_to_kind(tags)
            tags_json = json.dumps(tags, ensure_ascii=False, indent=None)

            # Geometria z cesty: indexujeme všetky uzly
            # (teda všetky zariadky cesty v indexe)
            lons = []
            lats = []
            for nd_id in nd_ids:
                if nd_id in handler.nodes:
                    lon, lat, _ = handler.nodes[nd_id]
                    lons.append(lon)
                    lats.append(lat)

            if not lons:
                continue

            min_lon = min(lons)
            max_lon = max(lons)
            min_lat = min(lats)
            max_lat = max(lats)
            center_lon = (min_lon + max_lon) / 2
            center_lat = (min_lat + max_lat) / 2

            # FTS5
            self.cursor.execute(
                """
                INSERT INTO search_fts
                (osm_id, osm_type, kind, name, name_folded, alternate_names,
                 region_key, lon, lat, bbox_min_lon, bbox_max_lon,
                 bbox_min_lat, bbox_max_lat, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    way_id,
                    "way",
                    kind,
                    primary_name,
                    primary_name,  # FTS5 tokenizer zálozi folding
                    "|".join(alt_names) if alt_names else "",
                    self.region_key,
                    center_lon,
                    center_lat,
                    min_lon,
                    max_lon,
                    min_lat,
                    max_lat,
                    tags_json,
                ),
            )

            # Index tabuľka
            self.cursor.execute(
                "INSERT OR IGNORE INTO search_index VALUES (?, ?, ?, ?, ?, ?)",
                (way_id, "way", kind, primary_name, self.region_key, tags_json),
            )
            count += 1

        self.conn.commit()
        return count

    def index_relations(self, handler):
        """Indexujeme OSM relácie (trasy: hiking, bike, ski...)."""
        count = 0
        for rel_id, members in handler.relations.items():
            if rel_id not in handler.relation_data:
                continue

            name, tags, rel_type, route_type = handler.relation_data[rel_id]
            if not name:
                continue

            primary_name, alt_names = resolve_names(tags)
            if not primary_name:
                continue

            kind = tag_to_kind(tags)
            tags_json = json.dumps(tags, ensure_ascii=False, indent=None)

            # Geometria z relácii: zbierame všetky uzly všetkých ciest
            lons = []
            lats = []
            for way_id, role in members:
                if way_id in handler.ways:
                    _, _, nd_ids = handler.ways[way_id]
                    for nd_id in nd_ids:
                        if nd_id in handler.nodes:
                            lon, lat, _ = handler.nodes[nd_id]
                            lons.append(lon)
                            lats.append(lat)

            if not lons:
                continue

            min_lon = min(lons)
            max_lon = max(lons)
            min_lat = min(lats)
            max_lat = max(lats)
            center_lon = (min_lon + max_lon) / 2
            center_lat = (min_lat + max_lat) / 2

            # FTS5
            self.cursor.execute(
                """
                INSERT INTO search_fts
                (osm_id, osm_type, kind, name, name_folded, alternate_names,
                 region_key, lon, lat, bbox_min_lon, bbox_max_lon,
                 bbox_min_lat, bbox_max_lat, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rel_id,
                    "relation",
                    kind,
                    primary_name,
                    primary_name,  # FTS5 tokenizer zálozi folding
                    "|".join(alt_names) if alt_names else "",
                    self.region_key,
                    center_lon,
                    center_lat,
                    min_lon,
                    max_lon,
                    min_lat,
                    max_lat,
                    tags_json,
                ),
            )

            # Index tabuľka
            self.cursor.execute(
                "INSERT OR IGNORE INTO search_index VALUES (?, ?, ?, ?, ?, ?)",
                (rel_id, "relation", kind, primary_name, self.region_key, tags_json),
            )
            count += 1

        self.conn.commit()
        return count

    def close(self):
        """Zatvoriť databázu."""
        if self.conn:
            self.conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Vyhľadávací index z OSM → SQLite FTS5"
    )
    parser.add_argument(
        "--pbf", required=True, help="Cesta k prefiltrovanému PBF súboru"
    )
    parser.add_argument("--out", required=True, help="Cesta k výstupnému SQLite")
    parser.add_argument(
        "--region-key", required=True, help="Kľúč regiónu (pre metadata)"
    )
    parser.add_argument(
        "--osm-id-field",
        default="osm_id",
        help="Pole pre OSM ID (z SPIKE-ID-FINDINGS.md)",
    )

    args = parser.parse_args()

    try:
        # Prvý priechod: zbierame vseto
        print(f"Čítame {args.pbf}...", file=sys.stderr)
        handler = SearchHandler()
        handler.apply_file(args.pbf)
        print(
            f"  Uzly: {len(handler.nodes)}, Cesty: {len(handler.ways)}, Relácie: {len(handler.relation_data)}",
            file=sys.stderr,
        )

        # Budovateľ indexu
        indexer = Indexer(args.out, args.out, args.region_key, args.osm_id_field)
        indexer.init_db()

        # Indexovanie
        nodes_count = indexer.index_nodes(handler)
        ways_count = indexer.index_ways(handler)
        relations_count = indexer.index_relations(handler)
        total_count = nodes_count + ways_count + relations_count

        print(
            f"Index postavený: {args.out}; "
            f"{nodes_count} uzlov, {ways_count} ciest, {relations_count} relácií = {total_count} položiek",
            file=sys.stderr,
        )

        indexer.close()

        # Skúška: môžeme načítať index späť?
        test_conn = sqlite3.connect(args.out)
        test_cursor = test_conn.cursor()
        test_cursor.execute("SELECT count(*) FROM search_fts")
        (final_count,) = test_cursor.fetchone()
        test_conn.close()

        print(f"Finálny počet: {final_count}", file=sys.stderr)

        if final_count != total_count:
            print(
                f"UPOZORNENIE: Počet položiek sa nehoduje (indexované {total_count}, v DB {final_count})",
                file=sys.stderr,
            )

    except Exception as e:
        print(f"CHYBA: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

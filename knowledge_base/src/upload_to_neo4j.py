"""
upload_to_neo4j.py

Load slices_with_embed.jsonl(.gz) and import data into Neo4j with the following structure:
  (:Category)<-[:IN_CATEGORY]-(p:Page)-[:HAS_SLICE]->(:Slice)-[:HAS_IMAGE]->(:Image)

For every page, if the URL (lowercase, hyphen-separated) contains any brand slug
(e.g., "drumstick", "kit-kat", "iogo", etc.), create a (:Brand {slug: ..., name: ...})
node and establish a (p)-[:HAS_BRAND]->(b) relationship.

Finally, create a full-text index `sliceFulltext` on all :Slice(content) properties.
"""

import os
import json
import gzip
import itertools
import re
from pathlib import Path

from neo4j import GraphDatabase
from dotenv import load_dotenv

# ────────── Configuration ────────────────────────────────────────────────
SLICE_FILE       = Path("slices_with_embed.jsonl.gz")
BATCH_SIZE_NEO4J = 500

# ────────── Original list of brand names (may include spaces, uppercase, accents, etc.) ──
BRANDS = [
    "Aero", "COFFEE CRISP", "Coffee Mate", "Nescaf", "Drumstick", "Kit Kat",
    "Smarties", "Turtles", "After Eight", "Big Turk", "Crunch",
    "Quality Street", "Rolo", "Mackintosh Toffee", "Mirage",
    "Confectionery Frozen Desserts", "Häagen-Dazs", "iÖGO", "Del Monte", "Parlour",
    "Real Dairy", "Nido", "Nestlé Materna", "MAGGI", "BOOST Kids", "Boost",
    "Essentia", "Maison Perrier", "Perrier", "San Pellegrino", "GoodHost",
    "Milo", "NESTEA", "Nesfruta", "Carnation Hot Chocolate", "Nesquik"
]

# ────────── Convert each brand name into a URL slug: lowercase, spaces → "-", remove most non-alphanumeric characters ───
def brand_to_slug(name: str) -> str:
    """
    Convert a brand name into a slug suitable for URL matching:
    1) Normalize accent marks using NFKD and remove combining characters.
    2) Remove all characters except letters, digits, spaces, underscores, and hyphens.
    3) Replace spaces or underscores with hyphens and collapse multiple hyphens.
    4) Convert to lowercase.
    """
    import unicodedata
    nfd = unicodedata.normalize("NFD", name)
    no_combining = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^A-Za-z0-9\s_-]", "", no_combining)
    hyphened = re.sub(r"[\s_]+", "-", cleaned)
    return hyphened.lower()


# Pre-generate a mapping from slug to original brand name
BRAND_SLUGS_MAP = {brand_to_slug(b): b for b in BRANDS}


# ────────── Load environment variables and initialize Neo4j driver ──────────
load_dotenv()
URI      = os.getenv("NEO4J_URI")
USER     = os.getenv("NEO4J_USER")
PASSWORD = os.getenv("NEO4J_PASS")

driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))


# ────────── Utility functions ─────────────────────────────────────────────
def read_slices(path: Path):
    """
    Read the slices file (JSONL or compressed JSONL) line by line,
    yielding each JSON object.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            yield json.loads(line)


def batches(iterable, n):
    """
    Split an iterable into chunks of size at most n.
    """
    it = iter(iterable)
    while chunk := list(itertools.islice(it, n)):
        yield chunk


# ────────── Cypher query template ──────────────────────────────────────────
CYPHER = """
UNWIND $batch AS s

// 1) Category & Page
MERGE (cat:Category {name: s.category})
MERGE (p:Page {url: s.url})
  ON CREATE SET p.title = s.title
MERGE (p)-[:IN_CATEGORY]->(cat)

// 2) Slice
MERGE (sl:Slice {id: s.id})
  ON CREATE SET sl.content = s.content, sl.embedding = s.embedding
MERGE (p)-[:HAS_SLICE]->(sl)

// Keep p, sl, and s for the next steps
WITH p, sl, s

// 3) Images
FOREACH (idx IN range(0, size(s.images) - 1) |
  MERGE (img:Image {url: s.images[idx]})
    ON CREATE SET img.title = s.image_titles[idx]
    ON MATCH  SET img.title = coalesce(img.title, s.image_titles[idx])
  MERGE (sl)-[:HAS_IMAGE]->(img)
)

// After creating images, keep p and s for brand processing
WITH p, s

// 4) For each slug in s.brand_slugs, merge a Brand node and link to Page
FOREACH (bs IN s.brand_slugs |
  MERGE (b:Brand {slug: bs})
    ON CREATE SET b.name = bs
  MERGE (p)-[:HAS_BRAND]->(b)
)
"""


# ────────── Main upload function ──────────────────────────────────────────
def upload():
    with driver.session() as session:
        # Step 1: Create uniqueness constraints if they do not exist
        session.run("""
            CREATE CONSTRAINT slice_id IF NOT EXISTS
            FOR (s:Slice) REQUIRE s.id IS UNIQUE
        """)
        session.run("""
            CREATE CONSTRAINT page_url IF NOT EXISTS
            FOR (p:Page) REQUIRE p.url IS UNIQUE
        """)
        session.run("""
            CREATE CONSTRAINT category_name IF NOT EXISTS
            FOR (c:Category) REQUIRE c.name IS UNIQUE
        """)
        session.run("""
            CREATE CONSTRAINT image_url IF NOT EXISTS
            FOR (i:Image) REQUIRE i.url IS UNIQUE
        """)
        session.run("""
            CREATE CONSTRAINT brand_slug IF NOT EXISTS
            FOR (b:Brand) REQUIRE b.slug IS UNIQUE
        """)

        total = 0
        # Process slices in batches to avoid loading everything into memory at once
        for chunk in batches(read_slices(SLICE_FILE), BATCH_SIZE_NEO4J):
            payload = []
            for d in chunk:
                # Assemble a record containing required fields
                record = {
                    "id":            d["id"],
                    "url":           d["url"].lower(),  
                    "title":         d.get("website_title", d.get("title", "")),
                    "category":      d.get("category", "Uncategorized"),
                    "content":       d["content"],
                    "embedding":     d["embedding"],
                    "images":        d.get("images", []),
                    "image_titles":  d.get("image_titles", []),
                    "brand_slugs":   [],
                }

                url_lower = record["url"]

                # If URL is not empty, check which brand slugs appear in it
                if url_lower:
                    for bs, full_name in BRAND_SLUGS_MAP.items():
                        if bs in url_lower:
                            record["brand_slugs"].append(bs)

                payload.append(record)

            # Execute the Cypher statement for this batch
            session.run(CYPHER, batch=payload)
            total += len(payload)
            print(f"Uploaded {total} slices…")

        # Step 2: Create a full-text index on Slice content if it does not exist
        session.run("""
            CREATE FULLTEXT INDEX sliceFulltext IF NOT EXISTS
            FOR (s:Slice)
            ON EACH [s.content]
        """)

    driver.close()
    print("✅ All data synced to Neo4j, and fulltext index `sliceFulltext` created.")


if __name__ == "__main__":
    upload()

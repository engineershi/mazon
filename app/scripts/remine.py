#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nightly niche re-miner: refresh the products of seeded niches inside the
shipped sqlite DB.

Usage: python app/scripts/remine.py [seeds.txt] [app/mazon.db] [max-niches]

Behaviour
  * Reads broad seeds (one per line from the seeds file).
  * Mines each via the keyless Amazon path (autosuggest + search), which
    works best from a US-egress host (GitHub Actions runner) for USD data.
  * Upserts ONLY niches that produced products. Never deletes existing rows,
    so a transiently-empty seed can't wipe good data.
  * Leaves the DB byte-identical when nothing changed -> the workflow's
    `git diff --cached --quiet` check then skips the commit.

Env: MAZON_MARKET (com), MAZON_TAG (affiliate tag), MAZON_TOP (products/niche).
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import amazon
import niche

SCHEMA = """CREATE TABLE IF NOT EXISTS niches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    market TEXT NOT NULL,
    score REAL,
    saturation REAL,
    products TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)"""


def upsert(conn, keyword, market, score, saturation, products):
    """Update the row for (keyword, market) or insert a new one. Returns 'new'/'updated'."""
    row = conn.execute(
        "SELECT id FROM niches WHERE keyword=? AND market=?",
        (keyword, market)).fetchone()
    payload = json.dumps(products)
    if row:
        conn.execute(
            "UPDATE niches SET products=?, score=?, saturation=? WHERE id=?",
            (payload, score, saturation, row["id"]))
        return "updated"
    conn.execute(
        "INSERT INTO niches (keyword, market, score, saturation, products) "
        "VALUES (?,?,?,?,?)",
        (keyword, market, score, saturation, payload))
    return "new"


def main():
    seeds_file = sys.argv[1] if len(sys.argv) > 1 else "app/seeds.txt"
    db_path = sys.argv[2] if len(sys.argv) > 2 else "app/mazon.db"
    max_niches = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    top = int(os.environ.get("MAZON_TOP", "6"))
    market = os.environ.get("MAZON_MARKET", "com")

    amazon.set_market(market)
    amazon.set_tag(os.environ.get("MAZON_TAG", ""))
    amazon.MIN_INTERVAL = 1.0        # be polite to Amazon's public pages
    amazon.CACHE_TTL = 0             # always hit live data
    amazon.MAX_ATTEMPTS = 2

    if not os.path.exists(seeds_file):
        print("no seeds file at %s -- nothing to do (exit 0)" % seeds_file)
        return 0
    with open(seeds_file) as f:
        seeds = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not seeds:
        print("seeds file empty -- nothing to do (exit 0)")
        return 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)

    summary = {"new": 0, "updated": 0, "failures": 0, "empty": []}
    for seed in seeds:
        try:
            niches, _meta = niche.mine_niche(seed, top=top, max_niches=max_niches)
        except Exception as exc:  # one bad seed must not kill the run
            summary["failures"] += 1
            print("seed %r FAILED: %s" % (seed, exc))
            continue
        touched = 0
        for n in niches:
            if not n.get("products"):
                continue
            kind = upsert(conn, n["keyword"], market,
                          n.get("score"), n.get("saturation"), n["products"])
            summary[kind] += 1
            touched += 1
        conn.commit()
        if touched:
            print("seed %r -> %d niche(s) refreshed" % (seed, touched))
        else:
            summary["empty"].append(seed)
            print("seed %r -> no products this run (kept existing rows)" % seed)
    conn.close()

    print("SUMMARY %s" % json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
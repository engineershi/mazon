#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Submit the site's indexable URLs to IndexNow (Bing/Yandex/Naver/Seznam)
so fresh content is crawled within minutes instead of days.

Used by .github/workflows/remine.yml every night and safe to run manually:
    PSTORE_URL=https://pstore.onrender.com python app/scripts/ping_indexnow.py

Exit code 1 on any failure so CI surfaces problems.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import seo
import indexnow

BASE = os.environ.get("PSTORE_URL", "https://pstore.onrender.com").rstrip("/")


def saved_niches(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT keyword, products FROM niches").fetchall()
    conn.close()
    return [{"keyword": r["keyword"],
             "products": json.loads(r["products"] or "[]")} for r in rows]


def main():
    db_path = os.environ.get("PSTORE_DB", "app/pstore.db")
    if not os.path.exists(db_path):
        print("no db at %s -- skipping ping (exit 0)" % db_path)
        return 0
    urls = seo.indexable_urls(saved_niches(db_path), base_url=BASE)
    ok, message = indexnow.submit_urls(urls, base_url=BASE)
    print("%s (%d urls)" % (message, len(urls)))
    if ok:
        print("|".join(urls))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
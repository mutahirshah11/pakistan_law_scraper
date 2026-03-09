#!/usr/bin/env python3
"""
Test: server-side truncation fix via index_search_full()
Targets YLR 2005 and YLR 2008 which were confirmed to return exactly 1000
cases (truncated) when queried without a court filter.
"""
import os
import sys
from scraper import PakistanLawScraper

USERNAME = os.environ.get("PLS_USERNAME", "LHCBAR8")
PASSWORD = os.environ.get("PLS_PASSWORD", "pakbar8")

def main():
    s = PakistanLawScraper(USERNAME, PASSWORD, delay_range=(0.5, 1.0), timeout=90)
    print("Logging in...")
    if not s.login():
        print("FATAL: login failed")
        sys.exit(1)
    print("Login OK\n")

    tests = [
        ("YLR", 2005),
        ("YLR", 2008),
    ]

    for book, year in tests:
        print(f"--- {book} {year} ---")

        # Raw (truncated) query
        raw = s.index_search(year, book)
        print(f"  index_search()      -> {len(raw)} cases")

        if len(raw) == s.TRUNCATION_THRESHOLD:
            courts = sorted({c.get('court','').strip() for c in raw if c.get('court','').strip()})
            print(f"  Courts in first {len(raw)}: {courts}")

        # Full (de-truncated) query
        full = s.index_search_full(year, book)
        print(f"  index_search_full() -> {len(full)} cases")

        if len(full) > len(raw):
            print(f"  RECOVERED {len(full) - len(raw)} additional cases beyond truncation!")
        elif len(raw) < s.TRUNCATION_THRESHOLD:
            print(f"  No truncation detected (count < {s.TRUNCATION_THRESHOLD})")
        else:
            print(f"  WARNING: full count same as raw — may still be truncated or all cases fit in court sub-queries")
        print()

if __name__ == "__main__":
    main()

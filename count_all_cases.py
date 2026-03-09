#!/usr/bin/env python3
"""
Exact Case Count — All 16 Journals x All Years (1947-2026)
=============================================================
Hits every single journal+year combo via IndexSearch (no detail fetch).
Reports exact total cases on the site.

Run: python count_all_cases.py
ETA: ~20-25 minutes
"""

import os
import sys
import time
import json
from datetime import datetime
from scraper import PakistanLawScraper, SessionExpiredError

USERNAME = os.environ.get("PLS_USERNAME", "LHCBAR8")
PASSWORD = os.environ.get("PLS_PASSWORD", "pakbar8")

YEAR_START = 1947
YEAR_END   = 2026
SAVE_FILE  = "count_results.json"


def fmt(n):
    return f"{n:,}"


def main():
    print("=" * 65)
    print("  EXACT CASE COUNT  (all 16 journals x 1947-2026)")
    print("=" * 65)
    print(f"  This will make {16 * (YEAR_END - YEAR_START + 1):,} requests.")
    print(f"  ETA: ~20-25 minutes.\n")

    scraper = PakistanLawScraper(USERNAME, PASSWORD, delay_range=(0.4, 0.8), timeout=90)
    if not scraper.login():
        print("FATAL: login failed")
        sys.exit(1)
    print("  Login OK\n")

    # Load previous partial results if any
    results = {}
    if os.path.exists(SAVE_FILE):
        with open(SAVE_FILE) as f:
            results = json.load(f)
        already = sum(sum(v.values()) for v in results.values())
        combos_done = sum(len(v) for v in results.values())
        print(f"  Resuming: {combos_done} combos already counted ({fmt(already)} cases)\n")

    journals = scraper.INDEX_JOURNALS
    total_years = YEAR_END - YEAR_START + 1
    total_combos = len(journals) * total_years
    done = 0
    errors = 0
    start_time = time.time()

    for journal in journals:
        if journal not in results:
            results[journal] = {}

        journal_total = sum(results[journal].values())

        for year in range(YEAR_START, YEAR_END + 1):
            year_str = str(year)

            # Skip already counted
            if year_str in results[journal]:
                done += 1
                continue

            # Retry logic
            count = None
            for attempt in range(3):
                try:
                    cases = scraper.index_search(year=year, book=journal)
                    count = len(cases)
                    break
                except SessionExpiredError:
                    print(f"  [{journal} {year}] Session expired, re-authing...")
                    if scraper._try_reauth():
                        continue
                    else:
                        print(f"  [{journal} {year}] Re-auth failed!")
                        count = -1  # mark as error
                        errors += 1
                        break
                except Exception as e:
                    backoff = 15 * (attempt + 1)
                    print(f"  [{journal} {year}] Error (attempt {attempt+1}/3): {e} — retry in {backoff}s")
                    time.sleep(backoff)

            if count is None:
                count = -1
                errors += 1

            results[journal][year_str] = count
            done += 1
            journal_total += max(count, 0)

            # Progress line
            elapsed = time.time() - start_time
            rate = done / elapsed if elapsed > 0 else 0
            remaining = (total_combos - done) / rate if rate > 0 else 0
            grand_total = sum(
                max(v, 0)
                for jd in results.values()
                for v in jd.values()
            )

            if count > 0:
                print(f"  {journal:<12} {year}: {count:>5} cases  |  "
                      f"[{done}/{total_combos}]  ETA: {remaining/60:.1f}m  "
                      f"Running total: {fmt(grand_total)}")
            elif count == 0:
                pass  # silent for 0-case combos
            else:
                print(f"  {journal:<12} {year}: ERROR")

            # Auto-save every 50 combos
            if done % 50 == 0:
                with open(SAVE_FILE, 'w') as f:
                    json.dump(results, f, indent=2)

        # Journal summary
        journal_total = sum(max(v, 0) for v in results[journal].values())
        years_with_data = sum(1 for v in results[journal].values() if v > 0)
        print(f"\n  {journal:<12} TOTAL: {fmt(journal_total):>10} cases "
              f"({years_with_data} years with data)\n")

    # Final save
    with open(SAVE_FILE, 'w') as f:
        json.dump(results, f, indent=2)

    # ── Summary ────────────────────────────────────────────────────
    print("=" * 65)
    print("  FINAL RESULTS")
    print("=" * 65)
    grand_total = 0
    print(f"  {'JOURNAL':<14} {'CASES':>10}  {'YEARS':>5}")
    print(f"  {'-'*35}")
    for journal in journals:
        jdata = results.get(journal, {})
        jtotal = sum(max(v, 0) for v in jdata.values())
        yrs    = sum(1 for v in jdata.values() if v > 0)
        grand_total += jtotal
        print(f"  {journal:<14} {fmt(jtotal):>10}  {yrs:>5} years")

    print(f"  {'-'*35}")
    print(f"  {'GRAND TOTAL':<14} {fmt(grand_total):>10}")
    print()
    print(f"  Errors/timeouts: {errors} combos")
    elapsed_total = time.time() - start_time
    print(f"  Total time: {elapsed_total/60:.1f} minutes")
    print(f"  Results saved to: {SAVE_FILE}")
    print("=" * 65)


if __name__ == "__main__":
    main()

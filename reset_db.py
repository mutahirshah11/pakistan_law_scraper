#!/usr/bin/env python3
"""
Full DB Reset — TRUNCATE + sequence restart
============================================
Run this AFTER stopping Railway scraper, BEFORE restarting it.

    python reset_db.py
"""

import os
import sys
import db

def main():
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL environment variable not set.")
        print("Set it first:  set DATABASE_URL=postgresql://...")
        sys.exit(1)

    print("=" * 50)
    print("  FULL DB RESET")
    print("=" * 50)
    print("  This will DELETE all cases and progress data.")
    print("  IDs will restart from 1.")
    print()

    confirm = input("  Type YES to confirm: ").strip()
    if confirm != "YES":
        print("  Cancelled.")
        sys.exit(0)

    print("\n  Running TRUNCATE...")
    db.reset_all()

    # Verify
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM cases")
        cases_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM scrape_progress")
        progress_count = cur.fetchone()[0]
    conn.close()

    print(f"  cases table rows     : {cases_count}")
    print(f"  scrape_progress rows : {progress_count}")
    print()

    if cases_count == 0 and progress_count == 0:
        print("  Reset complete! IDs will start from 1 on next scrape.")
    else:
        print("  WARNING: Some rows still exist. Check DB manually.")

    print("=" * 50)


if __name__ == "__main__":
    main()

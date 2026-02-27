#!/usr/bin/env python3
"""
Full Website Scraper
====================
Poori pakistanlawsite.com scrape karta hai.
16 journals x 80 saal = 1,280 combinations.

Run karo:  python full_scrape.py
"""

from scraper import PakistanLawScraper
import os
import time

# ─── Settings — YAHAN CHANGE KARO ────────────────────────
USERNAME    = os.environ.get("PLS_USERNAME", "LHCBAR8")
PASSWORD    = os.environ.get("PLS_PASSWORD", "pakbar8")
OUTPUT_FILE = "saari_website.csv"   # Result file ka naam
PROGRESS    = "scrape_progress.json"  # Progress save hoti hai (resume ke liye)
GET_DETAILS = True                  # True = headnotes+full text bhi | False = sirf citations

# Journals — None matlab SARE 16 journals
# Specific ke liye: JOURNALS = ['PLD', 'SCMR']
JOURNALS    = None  # None = sab | Example: ['PLD'] ya ['PLD','SCMR','CLC']

# Years — None matlab 1947 se 2026 tak
YEAR_START  = None  # None = 1947 | Example: 2010
YEAR_END    = None  # None = 2026 | Example: 2026
# ──────────────────────────────────────────────────────────


def main():
    print()
    print("=" * 55)
    print("  FULL WEBSITE SCRAPE SHURU HO RAHI HAI")
    print("=" * 55)
    print()
    j_list   = JOURNALS if JOURNALS else ['PLD','SCMR','CLC','CLD','YLR','PCrLJ',
                                           'PLC','PLC(CS)','PTD','MLD','GBLR','CLCN',
                                           'YLRN','PCRLJN','PLCN','PLC(CS)N']
    y_start  = YEAR_START or 1947
    y_end    = YEAR_END   or 2026
    combos   = len(j_list) * (y_end - y_start + 1)

    print(f"  Output file : {OUTPUT_FILE}")
    print(f"  Full details: {'Haan (headnotes + text)' if GET_DETAILS else 'Nahi (sirf citation)'}")
    print()
    print(f"  Journals    : {', '.join(j_list)}")
    print(f"  Years       : {y_start} - {y_end}")
    print(f"  Combinations: {len(j_list)} journals x {y_end-y_start+1} years = {combos:,}")
    print()

    # Agar pehle se progress file hai toh resume option
    if os.path.exists(PROGRESS):
        print(f"  [!] Pehli scrape ki progress mili: {PROGRESS}")
        choice = input("      Wahan se resume karo? (y/n) [y]: ").strip().lower()
        if choice == 'n':
            os.remove(PROGRESS)
            if os.path.exists(OUTPUT_FILE):
                os.remove(OUTPUT_FILE)
            print("      Progress reset. Naye sire se shuru hoga.\n")
        else:
            print("      Resume kar raha hai...\n")

    # Login
    print("  Login ho raha hai...")
    scraper = PakistanLawScraper(
        username=USERNAME,
        password=PASSWORD,
        delay_range=(1.0, 2.0)
    )

    if not scraper.login():
        print("\n  [FAIL] Login nahi hua! Check karo username/password.")
        return

    print("  [OK] Login successful!\n")
    print("  Scraping shuru... (Ctrl+C se rok sakte ho, progress save rahegi)\n")
    print("-" * 55)

    start_time = time.time()
    last_count = [0]

    def on_progress(data):
        done  = data.get('completed_count', 0)
        total = data.get('total_combinations', 0)
        cases = data.get('total_cases_found', 0)
        pct   = int(done / total * 100) if total else 0

        # Har 10 combinations pe ek line print karo
        if done % 10 == 0 or done == total:
            elapsed = time.time() - start_time
            hrs = int(elapsed // 3600)
            mins = int((elapsed % 3600) // 60)
            print(f"  [{done:4}/{total}] {pct:3}% done | "
                  f"Cases mili: {cases:,} | "
                  f"Time: {hrs}h {mins}m")

    def on_case(count):
        if count % 100 == 0:
            print(f"  --> {count:,} cases scrape ho gayi!", flush=True)

    try:
        total = scraper.scrape_all_index(
            output_file=OUTPUT_FILE,
            progress_file=PROGRESS,
            get_details=GET_DETAILS,
            journals=JOURNALS,
            year_start=YEAR_START,
            year_end=YEAR_END,
            on_progress=on_progress,
            on_case_scraped=on_case,
        )
    except KeyboardInterrupt:
        print("\n\n  [STOPPED] Aapne rok diya.")
        print(f"  Progress save hai: {PROGRESS}")
        print(f"  Dobara run karo resume ke liye.")
        return

    # Done!
    elapsed = time.time() - start_time
    hrs  = int(elapsed // 3600)
    mins = int((elapsed % 3600) // 60)

    print()
    print("=" * 55)
    print("  SCRAPE MUKAMMAL!")
    print("=" * 55)
    print(f"  Total cases scraped : {total:,}")
    print(f"  File                : {OUTPUT_FILE}")
    print(f"  Total time          : {hrs}h {mins}m")
    print()
    print("  Ab Excel mein kholo:")
    print(f"  {os.path.abspath(OUTPUT_FILE)}")
    print()


if __name__ == "__main__":
    main()

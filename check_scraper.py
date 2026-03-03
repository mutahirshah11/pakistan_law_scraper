#!/usr/bin/env python3
"""
Manual Scraper Check Tool
=========================
Yeh script step-by-step check karta hai ke scraping sahi ho rahi hai ya nahi.
Run karo: python check_scraper.py
"""

from scraper import PakistanLawScraper
import json 
import os

# ─── Credentials ──────────────────────────────────────────
USERNAME = os.environ.get("PLS_USERNAME", "LHCBAR8")
PASSWORD = os.environ.get("PLS_PASSWORD", "pakbar8")
# ──────────────────────────────────────────────────────────


def separator(title=""):
    print()
    print("=" * 60)
    if title:
        print(f"  {title}")
        print("=" * 60)


def check_login(scraper):
    separator("STEP 1: LOGIN CHECK")
    print(f"Username: {USERNAME}")
    print("Logging in...")

    ok = scraper.login()

    if ok:
        print("\n[OK] Login successful!")
        cookie = scraper.session.cookies.get("ASP.NET_SessionId", "")
        print(f"     Session cookie: {cookie[:15]}...")
    else:
        print("\n[FAIL] Login failed!")
        print("       Diagnostics:", json.dumps(scraper.last_login_diag, indent=6))

    return ok


def check_search(scraper):
    separator("STEP 2: SEARCH CHECK (PLD, last 5 years)")
    print("Searching cases...")

    cases, total = scraper.search_cases(keyword="PLD", year="5", row=0)

    if cases:
        print(f"\n[OK] Search worked!")
        print(f"     Total cases on site: {total}")
        print(f"     Cases returned this page: {len(cases)}")
        print()
        print("  First 3 cases:")
        for i, c in enumerate(cases[:3], 1):
            print(f"  [{i}] ID      : {c.get('case_id', 'N/A')}")
            print(f"      Citation: {c.get('citation', 'N/A')}")
            print(f"      Parties : {c.get('parties_full', 'N/A')[:70]}")
            print(f"      Court   : {c.get('court', 'N/A')}")
            print(f"      Year    : {c.get('year', 'N/A')} | Journal: {c.get('journal', 'N/A')}")
            print()
    else:
        print("\n[FAIL] No cases returned! Session expired ya login issue.")

    return cases


def check_pagination(scraper):
    separator("STEP 3: PAGINATION CHECK (row=50)")
    print("Loading page 2 (row=50)...")

    cases2, total2 = scraper.search_cases(keyword="SCMR", year="5", row=50)

    if cases2:
        print(f"\n[OK] Pagination worked!")
        print(f"     Cases returned: {len(cases2)} (starting from row 50)")
        print(f"     First case ID: {cases2[0].get('case_id', 'N/A')}")
    else:
        print("\n[FAIL] Pagination failed!")

    return cases2


def check_case_details(scraper, case_id):
    separator(f"STEP 4: CASE DETAILS CHECK ({case_id})")
    print(f"Fetching headnotes + full description...")

    try:
        details = scraper.get_case_details(case_id)

        hn = details.get("head_notes", "")
        fd = details.get("full_description", "")

        print(f"\n[OK] Details fetched!")
        print(f"     Head Notes   : {len(hn)} characters")
        print(f"     Full Desc    : {len(fd)} characters")
        print()
        print("  --- Head Notes (first 300 chars) ---")
        print(hn[:300])
        print()
        print("  --- Full Description (first 300 chars) ---")
        print(fd[:300])

    except Exception as e:
        print(f"\n[FAIL] Details fetch failed: {e}")
        details = {}

    return details


def check_index_search(scraper):
    separator("STEP 5: INDEX SEARCH CHECK (PLD 2025)")
    print("Fetching citation index for PLD 2025...")

    cases = scraper.index_search(year=2025, book="PLD")

    if cases:
        print(f"\n[OK] Index search worked!")
        print(f"     Total PLD 2025 cases: {len(cases)}")
        print()
        print("  First 3 index cases:")
        for i, c in enumerate(cases[:3], 1):
            print(f"  [{i}] ID      : {c.get('case_id', 'N/A')}")
            print(f"      Citation: {c.get('citation', 'N/A')}")
            print(f"      Parties : {c.get('parties_full', 'N/A')[:60]}")
    else:
        print("\n[FAIL] Index search returned nothing!")

    return cases


def check_csv_output(scraper):
    separator("STEP 6: CSV OUTPUT CHECK (3 cases)")
    print("Scraping 3 cases and saving to CSV...")

    df = scraper.scrape_all(
        keywords=["PLD"],
        year="5",
        output_file="manual_check_output.csv",
        get_details=True,
        max_cases=3,
    )

    if len(df) > 0:
        print(f"\n[OK] CSV saved: manual_check_output.csv")
        print(f"     Rows    : {len(df)}")
        print(f"     Columns : {list(df.columns)}")
        print()

        # Show CSV content in readable form
        for i, row in df.iterrows():
            print(f"  Case {i+1}:")
            for col in ["case_id", "citation", "year", "journal", "court",
                        "petitioner", "respondent"]:
                val = str(row.get(col, "")).strip()
                if val and val != "nan":
                    print(f"    {col:<18}: {val[:70]}")

            hn_len = len(str(row.get("head_notes", "")))
            fd_len = len(str(row.get("full_description", "")))
            print(f"    {'head_notes':<18}: {hn_len} chars")
            print(f"    {'full_description':<18}: {fd_len} chars")
            print()
    else:
        print("\n[FAIL] CSV is empty!")

    return df


def final_report(login_ok, cases, cases2, details, idx_cases, df):
    separator("FINAL REPORT")

    checks = [
        ("Login",          login_ok),
        ("Search",         bool(cases)),
        ("Pagination",     bool(cases2)),
        ("Case Details",   bool(details)),
        ("Index Search",   bool(idx_cases)),
        ("CSV Output",     len(df) > 0 if hasattr(df, '__len__') else False),
    ]

    all_ok = True
    for name, ok in checks:
        status = "[OK]  " if ok else "[FAIL]"
        print(f"  {status} {name}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print("  RESULT: Scraper fully working!")
    else:
        print("  RESULT: Some checks failed. Check output above.")
    separator()


def main():
    print()
    print("=" * 60)
    print("  PAKISTAN LAW SCRAPER - MANUAL CHECK TOOL")
    print("=" * 60)

    scraper = PakistanLawScraper(
        username=USERNAME,
        password=PASSWORD,
        delay_range=(1.0, 2.0)
    )

    # Run all checks
    login_ok  = check_login(scraper)
    if not login_ok:
        print("\nLogin failed - baaki checks skip ho rahe hain.")
        return

    cases     = check_search(scraper)
    cases2    = check_pagination(scraper)

    first_id  = cases[0].get("case_id") if cases else None
    details   = check_case_details(scraper, first_id) if first_id else {}

    idx_cases = check_index_search(scraper)
    df        = check_csv_output(scraper)

    final_report(login_ok, cases, cases2, details, idx_cases, df)


if __name__ == "__main__":
    main()

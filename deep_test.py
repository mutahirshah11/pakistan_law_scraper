#!/usr/bin/env python3
"""
Deep Test & Estimation Script
==============================
1. Estimates total cases across all 16 journals x 80 years
2. Deep-tests every critical code path
3. Reports all bugs found

Run: python deep_test.py
"""

import os
import sys
import time
import threading
import traceback
from scraper import PakistanLawScraper, SessionExpiredError, EmptyContentError, FieldFetchError

USERNAME = os.environ.get("PLS_USERNAME", "LHCBAR8")
PASSWORD = os.environ.get("PLS_PASSWORD", "pakbar8")

PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"
INFO = "[INFO]"

results = []  # (test_name, passed, detail)

def sep(title=""):
    print()
    print("=" * 65)
    if title:
        print(f"  {title}")
        print("=" * 65)

def record(name, passed, detail=""):
    status = PASS if passed else FAIL
    results.append((name, passed, detail))
    print(f"  {status} {name}")
    if detail:
        print(f"         {detail}")

# ─────────────────────────────────────────────────────────────────
# TEST 1: Login
# ─────────────────────────────────────────────────────────────────
def test_login():
    sep("TEST 1: LOGIN")
    scraper = PakistanLawScraper(USERNAME, PASSWORD, delay_range=(0.5, 1.0))
    ok = scraper.login()
    record("Login succeeds", ok)
    if not ok:
        print("  FATAL: cannot continue without login")
        sys.exit(1)
    return scraper

# ─────────────────────────────────────────────────────────────────
# TEST 2: Session expiry detection — unit test with fake HTML
# ─────────────────────────────────────────────────────────────────
def test_session_expiry_detection(scraper):
    sep("TEST 2: SESSION EXPIRY DETECTION (unit test, no network)")

    # 2a: empty string -> should return [] (no exception raised — but log warns)
    try:
        cases = scraper._parse_index_results("")
        record("Empty HTML -> returns [] (not crash)", True, f"got {len(cases)} cases")
        # Bug: empty response should ideally warn — it will silently mark combo complete
        if len(cases) == 0:
            print(f"         {WARN} Empty response returns [] -> combo will be marked 'completed 0 cases'")
            print(f"         This means session expiry without HTML tag is SILENT DATA LOSS")
    except Exception as e:
        record("Empty HTML -> returns []", False, str(e))

    # 2b: full login page HTML (no archivedpatientGrid) -> should raise SessionExpiredError
    login_html = "<html><body><form><input name='Login.UserName'/></form></body></html>"
    try:
        scraper._parse_index_results(login_html)
        record("Login page HTML -> raises SessionExpiredError", False, "no exception raised!")
    except SessionExpiredError:
        record("Login page HTML -> raises SessionExpiredError", True)
    except Exception as e:
        record("Login page HTML -> raises SessionExpiredError", False, str(e))

    # 2c: valid but empty results (archivedpatientGrid present, 0 rows) -> should return []
    empty_grid_html = '<table id="archivedpatientGrid"><tr><th>Citation</th></tr></table>'
    try:
        cases = scraper._parse_index_results(empty_grid_html)
        record("Empty grid HTML -> returns []", len(cases) == 0, f"got {len(cases)} cases")
    except Exception as e:
        record("Empty grid HTML -> returns []", False, str(e))

    # 2d: search result session expiry (caseLawTable absent)
    login_html2 = "<html><body><p>Please login</p></body></html>"
    try:
        scraper._parse_search_results(login_html2, "PLD")
        record("Search login page -> raises SessionExpiredError", False, "no exception!")
    except SessionExpiredError:
        record("Search login page -> raises SessionExpiredError", True)
    except Exception as e:
        record("Search login page -> raises SessionExpiredError", False, str(e))

    # 2e: empty string for search results -> should NOT raise (returns [], 0)
    try:
        cases, total = scraper._parse_search_results("", "PLD")
        record("Empty search HTML -> returns ([], 0) without crash", True, f"cases={len(cases)}, total={total}")
        if len(cases) == 0:
            print(f"         {WARN} Empty search response silently returns 0 results")
    except SessionExpiredError:
        record("Empty search HTML -> returns ([], 0) without crash", False, "incorrectly raised SessionExpiredError")
    except Exception as e:
        record("Empty search HTML -> returns ([], 0) without crash", False, str(e))

# ─────────────────────────────────────────────────────────────────
# TEST 3: Citation parser edge cases
# ─────────────────────────────────────────────────────────────────
def test_citation_parser(scraper):
    sep("TEST 3: CITATION PARSER")

    test_cases = [
        ("2024 PLD 1 SUPREME-COURT",          {"year": "2024", "journal": "PLD",      "page": "1",    "court": "SUPREME-COURT"}),
        ("2025 SCMR 1706 SUPREME-COURT",       {"year": "2025", "journal": "SCMR",     "page": "1706", "court": "SUPREME-COURT"}),
        ("2024  PLD  272  LAHORE-HIGH-COURT",  {"year": "2024", "journal": "PLD",      "page": "272",  "court": "LAHORE-HIGH-COURT"}),
        ("2020 PLC(CS) 100 FEDERAL-SERVICE-TRIBUNAL", {"year": "2020", "journal": "PLC(CS)", "page": "100"}),
        ("2019 PLC N 50 LAHORE-HIGH-COURT",    {"year": "2019", "journal": "PLC N",    "page": "50"}),
        ("2010 CLC 999",                        {"year": "2010", "journal": "CLC",      "page": "999"}),
        ("2000 YLR 1",                          {"year": "2000", "journal": "YLR",      "page": "1"}),
        ("",                                    {"year": "",     "journal": "",          "page": ""}),
    ]

    for citation, expected in test_cases:
        result = scraper._parse_citation(citation)
        ok = all(result.get(k, "").strip() == v for k, v in expected.items())
        record(
            f"_parse_citation: '{citation[:45].strip()}'",
            ok,
            f"got year={result.get('year')} journal={result.get('journal')} page={result.get('page')}" if not ok else ""
        )

# ─────────────────────────────────────────────────────────────────
# TEST 4: Throttle thread safety
# ─────────────────────────────────────────────────────────────────
def test_throttle_thread_safety(scraper):
    sep("TEST 4: THROTTLE THREAD SAFETY")

    call_times = []
    errors = []

    def call_throttle():
        try:
            scraper._throttle()
            call_times.append(time.time())
        except Exception as e:
            errors.append(str(e))

    # Launch 5 threads simultaneously
    threads = [threading.Thread(target=call_throttle) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    record("_throttle() called from 5 threads simultaneously — no crash", len(errors) == 0,
           f"errors: {errors}" if errors else "")

    if len(call_times) >= 2:
        min_gap = min(call_times[i+1] - call_times[i] for i in range(len(call_times)-1))
        # With delay_range=(0.5, 1.0) and min_interval=0.15, threads should be spread out
        record("_throttle() enforces some delay between calls", min_gap >= 0.05,
               f"min gap between calls: {min_gap:.3f}s")

# ─────────────────────────────────────────────────────────────────
# TEST 5: Live IndexSearch — all 16 journals × 3 sample years
# ─────────────────────────────────────────────────────────────────
JOURNAL_SAMPLE_YEARS = {
    # Known start years per journal (approximate)
    'PLD':      1947,
    'SCMR':     1972,
    'CLC':      1990,
    'CLD':      1999,
    'YLR':      1990,
    'PCrLJ':    1990,
    'PLC':      1990,
    'PLC(CS)':  2005,
    'PTD':      1990,
    'MLD':      1999,
    'GBLR':     2009,
    'CLCN':     2002,
    'YLRN':     1999,
    'PCRLJN':   2002,
    'PLCN':     2002,
    'PLC(CS)N': 2010,
}

def test_all_journals_estimate(scraper):
    sep("TEST 5: CASE COUNT ESTIMATION (all 16 journals × 6 sample years)")
    print("  Sampling years: 1980, 1990, 2000, 2010, 2020, 2024 per journal")
    print("  This takes ~3-4 minutes...\n")

    SAMPLE_YEARS = [1980, 1990, 2000, 2010, 2020, 2024]
    YEAR_RANGE = list(range(1947, 2027))  # 80 years

    journal_totals = {}
    journal_details = {}

    grand_total_estimate = 0
    journals_with_data = 0

    for journal in scraper.INDEX_JOURNALS:
        start_year = JOURNAL_SAMPLE_YEARS.get(journal, 1990)
        active_sample_years = [y for y in SAMPLE_YEARS if y >= start_year]
        active_total_years = [y for y in YEAR_RANGE if y >= start_year]

        if not active_sample_years:
            print(f"  {journal:<12}: skipping (no active years in sample)")
            journal_totals[journal] = 0
            continue

        year_counts = {}
        for year in active_sample_years:
            try:
                cases = scraper.index_search(year=year, book=journal)
                year_counts[year] = len(cases)
                print(f"  {journal:<12} {year}: {len(cases):>4} cases")
            except SessionExpiredError:
                print(f"  {journal:<12} {year}: SESSION EXPIRED — re-authing...")
                if scraper._try_reauth():
                    try:
                        cases = scraper.index_search(year=year, book=journal)
                        year_counts[year] = len(cases)
                        print(f"  {journal:<12} {year}: {len(cases):>4} cases (after reauth)")
                    except Exception:
                        year_counts[year] = 0
                else:
                    year_counts[year] = 0
            except Exception as e:
                print(f"  {journal:<12} {year}: ERROR — {e}")
                year_counts[year] = 0

        # Extrapolate: avg cases per sampled year × total active years
        sampled_counts = [c for c in year_counts.values()]
        if sampled_counts:
            avg_per_year = sum(sampled_counts) / len(sampled_counts)
            estimated_total = int(avg_per_year * len(active_total_years))
        else:
            avg_per_year = 0
            estimated_total = 0

        journal_totals[journal] = estimated_total
        journal_details[journal] = {
            'sampled': year_counts,
            'avg_per_year': avg_per_year,
            'active_years': len(active_total_years),
            'estimated_total': estimated_total,
        }

        if estimated_total > 0:
            journals_with_data += 1
            grand_total_estimate += estimated_total
            print(f"  {'':>12} -> avg {avg_per_year:.0f}/year × {len(active_total_years)} years = ~{estimated_total:,} cases")
        print()

    sep("ESTIMATION SUMMARY")
    print(f"  {'JOURNAL':<14} {'AVG/YR':>8} {'YEARS':>6} {'ESTIMATE':>10}")
    print(f"  {'-'*40}")
    for journal in scraper.INDEX_JOURNALS:
        d = journal_details.get(journal)
        if d:
            print(f"  {journal:<14} {d['avg_per_year']:>8.0f} {d['active_years']:>6}   ~{d['estimated_total']:>8,}")
        else:
            print(f"  {journal:<14} {'N/A':>8}")
    print(f"  {'-'*40}")
    print(f"  {'TOTAL ESTIMATE':<14} {'':>8} {'':>6}   ~{grand_total_estimate:>8,}")
    print()
    print(f"  Journals with data: {journals_with_data}/16")
    print(f"  ESTIMATED TOTAL CASES: ~{grand_total_estimate:,}")

    record(
        f"Total case estimation completed",
        grand_total_estimate > 0,
        f"~{grand_total_estimate:,} cases across all journals"
    )

    return journal_totals, grand_total_estimate

# ─────────────────────────────────────────────────────────────────
# TEST 6: Case details fetch
# ─────────────────────────────────────────────────────────────────
def test_case_details(scraper):
    sep("TEST 6: CASE DETAILS FETCH")

    # Get a known live case
    print("  Getting a live case ID from PLD 2024...")
    cases = scraper.index_search(year=2024, book="PLD")
    if not cases:
        record("index_search returned cases for details test", False, "no cases returned")
        return

    test_case = cases[0]
    case_id = test_case.get('case_id')
    print(f"  Using case_id: {case_id}")

    record("index_search returns cases for details test", bool(case_id), f"case_id={case_id}")

    # 6a: fetch both fields
    try:
        details = scraper.get_case_details(case_id)
        hn = details.get('head_notes', '')
        fd = details.get('full_description', '')
        record("get_case_details returns head_notes",       len(hn) > 50, f"{len(hn)} chars")
        record("get_case_details returns full_description", len(fd) > 50, f"{len(fd)} chars")
    except SessionExpiredError:
        record("get_case_details — no session expiry on fresh login", False, "SessionExpiredError!")
    except EmptyContentError as e:
        record("get_case_details returns content", False, str(e))
    except Exception as e:
        record("get_case_details — no crash", False, traceback.format_exc())

    # 6b: fetch only head_notes
    try:
        details = scraper.get_case_details(case_id, get_head_notes=True, get_full_description=False)
        record("get_case_details(head_only) works", 'head_notes' in details, str(details.keys()))
    except Exception as e:
        record("get_case_details(head_only) works", False, str(e))

    # 6c: fetch only description
    try:
        details = scraper.get_case_details(case_id, get_head_notes=False, get_full_description=True)
        record("get_case_details(desc_only) works", 'full_description' in details, str(details.keys()))
    except Exception as e:
        record("get_case_details(desc_only) works", False, str(e))

    # 6d: invalid case_id -> should raise FieldFetchError or EmptyContentError, not crash
    try:
        details = scraper.get_case_details("INVALID_CASE_ID_99999")
        record("Invalid case_id -> raises error (not returns garbage)", False,
               f"returned {details}")
    except (EmptyContentError, FieldFetchError):
        record("Invalid case_id -> raises FieldFetchError/EmptyContentError", True)
    except SessionExpiredError:
        record("Invalid case_id -> raises error", False, "unexpected SessionExpiredError")
    except Exception as e:
        record("Invalid case_id -> raises error (not silent)", True, f"raised {type(e).__name__}: {e}")

# ─────────────────────────────────────────────────────────────────
# TEST 7: End-to-end mini scrape (no DB, CSV only, 2 combos)
# ─────────────────────────────────────────────────────────────────
def test_end_to_end_mini_scrape(scraper):
    sep("TEST 7: END-TO-END MINI SCRAPE (PLD 2025, max 5 cases with details)")
    print("  Running scrape_all_index for PLD 2025 only (max 5 cases)...\n")

    progress_file = '_test_progress.json'
    output_file   = '_test_output.csv'

    # Clean up from previous runs
    for f in [progress_file, output_file, progress_file + '.tmp']:
        if os.path.exists(f):
            os.remove(f)

    cases_scraped = []
    combos_done   = []

    def on_progress(p):
        combos_done.append(p.get('completed_count', 0))

    def on_case(n):
        pass

    # Monkey-patch to cap at 5 cases
    original_get = scraper.get_case_details
    call_count = [0]
    def capped_get(case_id, **kwargs):
        call_count[0] += 1
        return original_get(case_id, **kwargs)
    scraper.get_case_details = capped_get

    # Patch stop after 5 case details
    stop_flag = [False]
    def should_stop():
        return stop_flag[0]

    # Override on_case_scraped to stop after 5
    case_count = [0]
    def on_case_scraped(n):
        case_count[0] = n
        if n >= 5:
            stop_flag[0] = True

    try:
        total_new = scraper.scrape_all_index(
            output_file=output_file,
            progress_file=progress_file,
            get_details=True,
            journals=['PLD'],
            year_start=2025,
            year_end=2025,
            on_progress=on_progress,
            should_stop=should_stop,
            on_case_scraped=on_case_scraped,
            db=None,
        )

        record("scrape_all_index runs without crashing", True)
        record("scrape_all_index scrapes > 0 cases", case_count[0] > 0,
               f"cases scraped: {case_count[0]}")

        # Check CSV was created
        import os as _os
        csv_exists = _os.path.exists(output_file)
        record("Output CSV created", csv_exists, output_file)

        if csv_exists:
            import csv
            with open(output_file, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            record("CSV has expected columns",
                   all(col in (rows[0].keys() if rows else [])
                       for col in ['case_id', 'citation', 'head_notes', 'full_description']),
                   f"columns: {list(rows[0].keys()) if rows else 'empty'}")
            record("CSV rows have head_notes content",
                   any(len(r.get('head_notes', '')) > 50 for r in rows),
                   f"sample head_notes len: {len(rows[0].get('head_notes','')) if rows else 0}")
            record("CSV rows have full_description content",
                   any(len(r.get('full_description', '')) > 50 for r in rows),
                   f"sample desc len: {len(rows[0].get('full_description','')) if rows else 0}")

    except Exception as e:
        record("scrape_all_index runs without crashing", False, traceback.format_exc())
    finally:
        scraper.get_case_details = original_get
        for f in [progress_file, output_file, progress_file + '.tmp']:
            if os.path.exists(f):
                os.remove(f)

# ─────────────────────────────────────────────────────────────────
# TEST 8: Duplicate dedup check
# ─────────────────────────────────────────────────────────────────
def test_duplicate_dedup(scraper):
    sep("TEST 8: DUPLICATE DEDUP (processed_ids set)")

    cases = scraper.index_search(year=2024, book="PLD")
    if len(cases) < 2:
        record("Enough cases to test dedup", False, f"only {len(cases)} cases")
        return

    # Simulate scrape_all dedup logic using processed_case_ids
    scraper.processed_case_ids = set()
    seen = []
    for case in cases[:5]:
        cid = case.get('case_id', '')
        if cid in scraper.processed_case_ids:
            seen.append(cid)
        else:
            scraper.processed_case_ids.add(cid)

    record("No duplicates in single journal+year results", len(seen) == 0,
           f"duplicates found: {seen}")

    # Check case_ids are unique
    all_ids = [c.get('case_id') for c in cases]
    unique_ids = set(all_ids)
    record(
        "All case_ids unique within one combo",
        len(all_ids) == len(unique_ids),
        f"{len(all_ids)} total, {len(unique_ids)} unique"
    )
    scraper.processed_case_ids = set()

# ─────────────────────────────────────────────────────────────────
# TEST 9: Session verification still works after many requests
# ─────────────────────────────────────────────────────────────────
def test_session_still_valid(scraper):
    sep("TEST 9: SESSION STILL VALID AFTER ALL TESTS")
    still_valid = scraper._verify_login()
    record("Session still valid after all tests", still_valid)
    if not still_valid:
        print(f"  {WARN} Session expired mid-test — re-auth logic must work during full scrape!")

# ─────────────────────────────────────────────────────────────────
# FINAL REPORT
# ─────────────────────────────────────────────────────────────────
def final_report():
    sep("FINAL REPORT")
    total   = len(results)
    passed  = sum(1 for _, p, _ in results if p)
    failed  = total - passed
    warnings = [r for r in results if not r[1]]

    for name, ok, detail in results:
        status = PASS if ok else FAIL
        print(f"  {status} {name}")

    print()
    print(f"  {passed}/{total} tests passed  |  {failed} failed")
    print()

    if warnings:
        print("  FAILURES:")
        for name, _, detail in warnings:
            print(f"    - {name}")
            if detail:
                print(f"      {detail[:200]}")
    else:
        print("  ALL TESTS PASSED — scraper ready for full run!")

    sep()
    return failed == 0


if __name__ == "__main__":
    start = time.time()

    scraper = test_login()
    test_session_expiry_detection(scraper)
    test_citation_parser(scraper)
    test_throttle_thread_safety(scraper)
    journal_totals, grand_total = test_all_journals_estimate(scraper)
    test_case_details(scraper)
    test_end_to_end_mini_scrape(scraper)
    test_duplicate_dedup(scraper)
    test_session_still_valid(scraper)

    elapsed = time.time() - start
    print(f"\n  Total time: {elapsed/60:.1f} minutes")

    ok = final_report()
    sys.exit(0 if ok else 1)

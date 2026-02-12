#!/usr/bin/env python3
"""Check database status."""

from db import SupabaseDB

db = SupabaseDB()

# Check stats
print('=== DATABASE STATUS ===')
stats = db.get_stats()
print(f"Total cases: {stats['total_cases']}")
print(f"Pending details: {stats['pending_details']}")
print(f"Fetched details: {stats['fetched_details']}")

# Check a sample case
result = db.client.table('cases').select('case_id,head_notes,full_description,detail_status').limit(5).execute()
print()
print('=== SAMPLE CASES ===')
for r in result.data:
    hn = r.get('head_notes') or ''
    fd = r.get('full_description') or ''
    print(f"{r['case_id']}: head_notes={len(hn)} chars, full_desc={len(fd)} chars, status={r['detail_status']}")

# Check scrape_runs
print()
print('=== SCRAPE RUNS ===')
runs = db.client.table('scrape_runs').select('*').order('started_at', desc=True).limit(5).execute()
for r in runs.data:
    print(f"Run {r['id']}: status={r['status']}, scraped={r.get('cases_scraped', 0)}, detailed={r.get('cases_detailed', 0)}")

if not runs.data:
    print('No scrape runs found - dashboard never started a run!')

# Check progress
print()
print('=== SCRAPE PROGRESS ===')
progress = db.get_all_progress()
for p in progress:
    print(f"{p['keyword']}/{p['year']}: phase={p['phase']}, cases_found={p['cases_found']}")

if not progress:
    print('No progress records found!')

#!/usr/bin/env python3
"""Check data saved in Supabase."""

from db import SupabaseDB

db = SupabaseDB()

# Get all cases
result = db.client.table('cases').select('*').execute()

print(f"Total cases in DB: {len(result.data)}")
print()

for case in result.data:
    print('=' * 60)
    print(f"case_id: {case.get('case_id')}")
    print(f"citation: {case.get('citation')}")
    print(f"year: {case.get('year')}")
    print(f"journal: {case.get('journal')}")
    print(f"court: {case.get('court')}")
    print(f"parties_full: {case.get('parties_full')}")
    print(f"petitioner: {case.get('petitioner')}")
    print(f"respondent: {case.get('respondent')}")
    print(f"keywords: {case.get('keywords', '')[:100] if case.get('keywords') else 'EMPTY'}")
    print(f"summary: {case.get('summary', '')[:100] if case.get('summary') else 'EMPTY'}...")
    head = case.get('head_notes')
    print(f"head_notes: {head[:100] if head else 'EMPTY'}...")
    desc = case.get('full_description')
    print(f"full_description: {desc[:100] if desc else 'EMPTY'}...")
    print(f"detail_status: {case.get('detail_status')}")
    print()

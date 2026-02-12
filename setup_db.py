#!/usr/bin/env python3
"""
Supabase Database Setup
========================
One-time script to create tables for the production scraper.

Run: python setup_db.py
"""

import os
import sys
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# SQL statements to create tables
TABLES_SQL = [
    # Main cases table
    """
    CREATE TABLE IF NOT EXISTS cases (
        id BIGSERIAL PRIMARY KEY,
        case_id TEXT UNIQUE NOT NULL,
        citation TEXT,
        year TEXT,
        journal TEXT,
        page TEXT,
        court TEXT,
        parties_full TEXT,
        petitioner TEXT,
        respondent TEXT,
        keywords TEXT,
        summary TEXT,
        head_notes TEXT,
        full_description TEXT,
        scraped_at TIMESTAMPTZ DEFAULT NOW(),
        search_keyword TEXT,
        detail_status TEXT DEFAULT 'pending'
    );
    """,
    # Scrape progress tracking
    """
    CREATE TABLE IF NOT EXISTS scrape_progress (
        id BIGSERIAL PRIMARY KEY,
        keyword TEXT NOT NULL,
        year TEXT NOT NULL,
        last_row INTEGER DEFAULT 0,
        total_found INTEGER DEFAULT 0,
        phase TEXT DEFAULT 'search',
        cases_found INTEGER DEFAULT 0,
        cases_detailed INTEGER DEFAULT 0,
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(keyword, year)
    );
    """,
    # Scrape runs for monitoring
    """
    CREATE TABLE IF NOT EXISTS scrape_runs (
        id BIGSERIAL PRIMARY KEY,
        started_at TIMESTAMPTZ DEFAULT NOW(),
        status TEXT DEFAULT 'running',
        total_cases INTEGER DEFAULT 0,
        cases_scraped INTEGER DEFAULT 0,
        cases_detailed INTEGER DEFAULT 0,
        errors_count INTEGER DEFAULT 0,
        last_error TEXT,
        config JSONB
    );
    """,
]

INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_cases_case_id ON cases(case_id);",
    "CREATE INDEX IF NOT EXISTS idx_cases_detail_status ON cases(detail_status);",
    "CREATE INDEX IF NOT EXISTS idx_cases_search_keyword ON cases(search_keyword);",
    "CREATE INDEX IF NOT EXISTS idx_cases_year ON cases(year);",
    "CREATE INDEX IF NOT EXISTS idx_scrape_progress_keyword_year ON scrape_progress(keyword, year);",
    "CREATE INDEX IF NOT EXISTS idx_scrape_runs_status ON scrape_runs(status);",
]


def setup():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Set SUPABASE_URL and SUPABASE_KEY in .env")
        sys.exit(1)

    print(f"Connecting to Supabase: {SUPABASE_URL}")
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("\nCreating tables...")
    for sql in TABLES_SQL:
        table_name = sql.split("CREATE TABLE IF NOT EXISTS ")[1].split(" (")[0].strip()
        try:
            client.postgrest.rpc("exec_sql", {"query": sql}).execute()
            print(f"  [OK] {table_name}")
        except Exception as e:
            # Supabase anon key can't run raw SQL via RPC
            # Tables need to be created via Supabase Dashboard SQL Editor
            print(f"  [!!] {table_name} - Cannot create via API (expected)")

    print("\n" + "=" * 60)
    print("IMPORTANT: Supabase anon keys cannot execute raw SQL.")
    print("Please create the tables using the Supabase Dashboard:")
    print(f"  {SUPABASE_URL.replace('.co', '.co')}/project/default/sql")
    print("\nCopy and paste the SQL below into the SQL Editor:")
    print("=" * 60)

    print("\n-- ===== COPY EVERYTHING BELOW INTO SUPABASE SQL EDITOR =====\n")

    for sql in TABLES_SQL:
        print(sql.strip())
        print()

    for sql in INDEXES_SQL:
        print(sql)

    # RLS policies to allow anon key access
    print("""
-- Enable Row Level Security but allow all operations for anon key
ALTER TABLE cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE scrape_progress ENABLE ROW LEVEL SECURITY;
ALTER TABLE scrape_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for anon" ON cases FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for anon" ON scrape_progress FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for anon" ON scrape_runs FOR ALL USING (true) WITH CHECK (true);
""")

    print("-- ===== END OF SQL =====\n")

    # Verify connection by trying to read from tables
    print("Verifying table access...")
    for table in ["cases", "scrape_progress", "scrape_runs"]:
        try:
            result = client.table(table).select("*").limit(1).execute()
            print(f"  [OK] {table} - accessible ({len(result.data)} rows)")
        except Exception as e:
            error_msg = str(e)
            if "does not exist" in error_msg or "42P01" in error_msg:
                print(f"  [!!] {table} - table not found (create it first)")
            else:
                print(f"  [!!] {table} - {error_msg[:80]}")

    print("\nSetup complete. Run the SQL above in Supabase Dashboard if tables don't exist.")


if __name__ == "__main__":
    setup()

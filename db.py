#!/usr/bin/env python3
"""
Database layer for Pakistan Law Scraper
========================================
Uses psycopg2-binary to talk to Neon PostgreSQL.
All functions are synchronous. Falls back gracefully when DATABASE_URL is not set.
"""

import os
import logging
from datetime import datetime

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_connection():
    """Return a new psycopg2 connection using DATABASE_URL."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(DATABASE_URL)


def init_tables():
    """CREATE TABLE IF NOT EXISTS for both tables. Safe to call on every startup."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cases (
                    id               SERIAL PRIMARY KEY,
                    case_id          VARCHAR(50) NOT NULL UNIQUE,
                    citation         TEXT NOT NULL DEFAULT '',
                    year             VARCHAR(4) NOT NULL DEFAULT '',
                    journal          VARCHAR(20) NOT NULL DEFAULT '',
                    page             VARCHAR(20) NOT NULL DEFAULT '',
                    court            TEXT NOT NULL DEFAULT '',
                    parties_full     TEXT NOT NULL DEFAULT '',
                    petitioner       TEXT NOT NULL DEFAULT '',
                    respondent       TEXT NOT NULL DEFAULT '',
                    keywords         TEXT,
                    summary          TEXT,
                    head_notes       TEXT,
                    full_description TEXT,
                    scraped_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    source           VARCHAR(30),
                    search_journal   VARCHAR(20),
                    search_year      INTEGER,
                    search_keyword   VARCHAR(255)
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_cases_year ON cases (year);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_cases_journal ON cases (journal);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_cases_court ON cases (court);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_cases_scraped_at ON cases (scraped_at);")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS scrape_progress (
                    id            SERIAL PRIMARY KEY,
                    journal       VARCHAR(20) NOT NULL,
                    year          VARCHAR(4) NOT NULL,
                    status        VARCHAR(20) NOT NULL DEFAULT 'pending',
                    cases_found   INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(journal, year)
                );
            """)
        conn.commit()
        logger.info("Database tables initialized")
    finally:
        conn.close()


def insert_case(case_dict):
    """INSERT one case, skipping on conflict (duplicate case_id).

    Returns True on success, False on error (logged, never raises).
    Retries once on transient failure (idempotent due to ON CONFLICT DO NOTHING).
    """
    import time as _time
    case_id = case_dict.get('case_id', '?')

    for attempt in range(2):
        conn = None
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cases (
                        case_id, citation, year, journal, page, court,
                        parties_full, petitioner, respondent,
                        keywords, summary, head_notes, full_description,
                        scraped_at, source, search_journal, search_year, search_keyword
                    ) VALUES (
                        %(case_id)s, %(citation)s, %(year)s, %(journal)s, %(page)s, %(court)s,
                        %(parties_full)s, %(petitioner)s, %(respondent)s,
                        %(keywords)s, %(summary)s, %(head_notes)s, %(full_description)s,
                        %(scraped_at)s, %(source)s, %(search_journal)s, %(search_year)s, %(search_keyword)s
                    ) ON CONFLICT (case_id) DO NOTHING
                """, _normalize_case(case_dict))
            conn.commit()
            return True
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if attempt == 0:
                logger.warning(f"insert_case attempt 1 failed for {case_id}: {e}, retrying in 2s...")
                _time.sleep(2)
            else:
                logger.error(f"insert_case failed for {case_id} after 2 attempts: {e}")
                return False
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    return False


def insert_cases_batch(cases_list):
    """Batch insert cases in one transaction. Skips duplicates.

    Falls back to per-case inserts on batch failure so partial data is still saved.
    """
    if not cases_list:
        return
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            for case in cases_list:
                cur.execute("""
                    INSERT INTO cases (
                        case_id, citation, year, journal, page, court,
                        parties_full, petitioner, respondent,
                        keywords, summary, head_notes, full_description,
                        scraped_at, source, search_journal, search_year, search_keyword
                    ) VALUES (
                        %(case_id)s, %(citation)s, %(year)s, %(journal)s, %(page)s, %(court)s,
                        %(parties_full)s, %(petitioner)s, %(respondent)s,
                        %(keywords)s, %(summary)s, %(head_notes)s, %(full_description)s,
                        %(scraped_at)s, %(source)s, %(search_journal)s, %(search_year)s, %(search_keyword)s
                    ) ON CONFLICT (case_id) DO NOTHING
                """, _normalize_case(case))
        conn.commit()
        logger.info(f"Batch inserted {len(cases_list)} cases")
    except Exception as e:
        logger.error(f"Batch insert failed ({len(cases_list)} cases): {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        # Fallback: try inserting each case individually
        logger.info("Falling back to per-case inserts...")
        ok = 0
        for case in cases_list:
            if insert_case(case):
                ok += 1
        logger.info(f"Fallback inserted {ok}/{len(cases_list)} cases")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _normalize_case(case):
    """Ensure all expected keys exist with sensible defaults."""
    return {
        'case_id': case.get('case_id', ''),
        'citation': case.get('citation', ''),
        'year': case.get('year', ''),
        'journal': case.get('journal', ''),
        'page': case.get('page', ''),
        'court': case.get('court', ''),
        'parties_full': case.get('parties_full', ''),
        'petitioner': case.get('petitioner', ''),
        'respondent': case.get('respondent', ''),
        'keywords': case.get('keywords'),
        'summary': case.get('summary'),
        'head_notes': case.get('head_notes'),
        'full_description': case.get('full_description'),
        'scraped_at': case.get('scraped_at', datetime.now().isoformat()),
        'source': case.get('source'),
        'search_journal': case.get('search_journal'),
        'search_year': case.get('search_year'),
        'search_keyword': case.get('search_keyword'),
    }


def get_processed_ids():
    """Return a set of all case_ids in the database (for dedup)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT case_id FROM cases")
            return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def get_progress():
    """
    Return progress as the same nested dict shape as index_progress.json:
    {
        'journals': {
            'PLD': { '2024': {'status': 'completed', 'cases_found': 42}, ... },
            ...
        },
        'completed_count': N,
        'total_cases_found': M,
        'total_combinations': 0,  # caller sets this
    }
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT journal, year, status, cases_found, error_message FROM scrape_progress")
            rows = cur.fetchall()

        journals = {}
        completed_count = 0
        total_cases_found = 0

        for row in rows:
            journal = row['journal']
            year = row['year']
            if journal not in journals:
                journals[journal] = {}
            entry = {
                'status': row['status'],
                'cases_found': row['cases_found'],
            }
            if row['error_message']:
                entry['error_message'] = row['error_message']
            journals[journal][year] = entry

            if row['status'] == 'completed':
                completed_count += 1
                total_cases_found += row['cases_found']

        return {
            'journals': journals,
            'completed_count': completed_count,
            'total_cases_found': total_cases_found,
            'total_combinations': 0,
        }
    finally:
        conn.close()


def update_progress(journal, year, status, cases_found=0, error_message=None):
    """UPSERT one journal+year progress row."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO scrape_progress (journal, year, status, cases_found, error_message, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (journal, year)
                DO UPDATE SET status = EXCLUDED.status,
                              cases_found = EXCLUDED.cases_found,
                              error_message = EXCLUDED.error_message,
                              updated_at = NOW()
            """, (journal, str(year), status, cases_found, error_message))
        conn.commit()
    finally:
        conn.close()


def get_case_count():
    """Return total number of cases in the database."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cases")
            return cur.fetchone()[0]
    finally:
        conn.close()


def get_dashboard_stats():
    """Return KPI stats for the dashboard in a single DB round-trip.

    Returns dict with all KPI fields, or None on error.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*)                                                          AS total_cases,
                    COUNT(*) FILTER (WHERE head_notes IS NOT NULL AND head_notes != '')  AS cases_with_headnotes,
                    COUNT(*) FILTER (WHERE full_description IS NOT NULL AND full_description != '') AS cases_with_description,
                    COUNT(*) FILTER (WHERE scraped_at >= NOW() - INTERVAL '1 hour')   AS cases_last_hour,
                    COUNT(*) FILTER (WHERE scraped_at >= NOW() - INTERVAL '24 hours') AS cases_last_24h,
                    MAX(scraped_at)                                                   AS latest_scraped_at
                FROM cases
            """)
            row = cur.fetchone()
            total_cases = row[0] or 0
            cases_with_headnotes = row[1] or 0
            cases_with_description = row[2] or 0
            cases_last_hour = row[3] or 0
            cases_last_24h = row[4] or 0
            latest_scraped_at = row[5].isoformat() if row[5] else None

            # Hourly rate: cases in the last hour
            hourly_rate = cases_last_hour

            # Top 5 journals
            cur.execute("""
                SELECT journal, COUNT(*) AS cnt
                FROM cases
                WHERE journal != ''
                GROUP BY journal
                ORDER BY cnt DESC
                LIMIT 5
            """)
            cases_by_journal = [{'journal': r[0], 'count': r[1]} for r in cur.fetchall()]

            # Combo stats from scrape_progress (single pass)
            cur.execute("""
                SELECT
                    COUNT(*)                                             AS combos_total,
                    COUNT(*) FILTER (WHERE status = 'completed')         AS combos_completed,
                    COUNT(*) FILTER (WHERE status = 'in_progress')       AS combos_in_progress,
                    COUNT(*) FILTER (WHERE status = 'error')             AS combos_error,
                    COUNT(*) FILTER (WHERE status = 'pending')           AS combos_pending,
                    COALESCE(SUM(cases_found), 0)                        AS total_cases_found_in_combos
                FROM scrape_progress
            """)
            crow = cur.fetchone()

        return {
            'total_cases': total_cases,
            'cases_with_headnotes': cases_with_headnotes,
            'cases_with_description': cases_with_description,
            'cases_last_hour': cases_last_hour,
            'cases_last_24h': cases_last_24h,
            'hourly_rate': hourly_rate,
            'cases_by_journal': cases_by_journal,
            'combos_total': crow[0] or 0,
            'combos_completed': crow[1] or 0,
            'combos_in_progress': crow[2] or 0,
            'combos_error': crow[3] or 0,
            'combos_pending': crow[4] or 0,
            'total_cases_found_in_combos': crow[5] or 0,
            'latest_scraped_at': latest_scraped_at,
        }
    except Exception as e:
        logger.error(f"get_dashboard_stats failed: {e}")
        return None
    finally:
        conn.close()


def reset_all():
    """Full reset: delete all rows from scrape_progress and cases tables."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM scrape_progress")
            cur.execute("DELETE FROM cases")
        conn.commit()
        logger.info("Full reset: deleted all rows from scrape_progress and cases")
    finally:
        conn.close()


def reset_in_progress():
    """Crash recovery: set all in_progress rows back to pending."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE scrape_progress
                SET status = 'pending', updated_at = NOW()
                WHERE status = 'in_progress'
            """)
        conn.commit()
        logger.info("Reset in_progress entries to pending")
    finally:
        conn.close()

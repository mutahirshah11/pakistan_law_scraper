#!/usr/bin/env python3
"""
Supabase Database Layer
========================
Handles all database operations for the production scraper.
Provides atomic upserts, progress tracking, and stats queries.
"""

import os
import csv
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # Max rows per upsert


class SupabaseDB:
    """Database abstraction layer for Supabase/PostgreSQL"""

    def __init__(self, url: str = None, key: str = None):
        self.url = url or os.environ.get("SUPABASE_URL")
        self.key = key or os.environ.get("SUPABASE_KEY")
        if not self.url or not self.key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY required (set in .env)")
        self.client = create_client(self.url, self.key)
        logger.info("Connected to Supabase")

    # ── Case Operations ──────────────────────────────────────────

    def upsert_case(self, case: dict) -> bool:
        """Insert or update a single case. Returns True on success."""
        try:
            row = self._prepare_case_row(case)
            self.client.table("cases").upsert(
                row, on_conflict="case_id"
            ).execute()
            return True
        except Exception as e:
            logger.error(f"upsert_case failed for {case.get('case_id', '?')}: {e}")
            return False

    def upsert_cases_batch(self, cases: list) -> int:
        """Batch upsert cases. Returns count of successfully upserted rows."""
        if not cases:
            return 0
        rows = [self._prepare_case_row(c) for c in cases]
        inserted = 0
        # Chunk into BATCH_SIZE
        for i in range(0, len(rows), BATCH_SIZE):
            chunk = rows[i : i + BATCH_SIZE]
            try:
                self.client.table("cases").upsert(
                    chunk, on_conflict="case_id"
                ).execute()
                inserted += len(chunk)
            except Exception as e:
                logger.error(f"Batch upsert failed (chunk {i}): {e}")
                # Fall back to individual inserts for this chunk
                for row in chunk:
                    try:
                        self.client.table("cases").upsert(
                            row, on_conflict="case_id"
                        ).execute()
                        inserted += 1
                    except Exception as e2:
                        logger.error(f"Individual upsert failed {row.get('case_id', '?')}: {e2}")
        return inserted

    def get_pending_details(self, limit: int = 1000) -> list:
        """Get case_ids where detail_status='pending'."""
        try:
            result = (
                self.client.table("cases")
                .select("case_id")
                .eq("detail_status", "pending")
                .limit(limit)
                .execute()
            )
            return [r["case_id"] for r in result.data]
        except Exception as e:
            logger.error(f"get_pending_details failed: {e}")
            return []

    def update_detail_status(
        self, case_id: str, status: str, head_notes: str = None, full_description: str = None
    ) -> bool:
        """Update a case's detail fields and status."""
        try:
            update = {"detail_status": status}
            if head_notes is not None:
                update["head_notes"] = head_notes
            if full_description is not None:
                update["full_description"] = full_description
            self.client.table("cases").update(update).eq("case_id", case_id).execute()
            return True
        except Exception as e:
            logger.error(f"update_detail_status failed for {case_id}: {e}")
            return False

    def case_id_exists(self, case_id: str) -> bool:
        """Check if a case_id already exists."""
        try:
            result = (
                self.client.table("cases")
                .select("case_id")
                .eq("case_id", case_id)
                .limit(1)
                .execute()
            )
            return len(result.data) > 0
        except Exception:
            return False

    def get_existing_case_ids(self, case_ids: list) -> set:
        """Check which case_ids from a list already exist. Returns set of existing IDs."""
        if not case_ids:
            return set()
        existing = set()
        # Query in chunks (Supabase has URL length limits for .in_())
        for i in range(0, len(case_ids), 100):
            chunk = case_ids[i : i + 100]
            try:
                result = (
                    self.client.table("cases")
                    .select("case_id")
                    .in_("case_id", chunk)
                    .execute()
                )
                existing.update(r["case_id"] for r in result.data)
            except Exception as e:
                logger.error(f"get_existing_case_ids failed: {e}")
        return existing

    # ── Progress Tracking ────────────────────────────────────────

    def get_progress(self, keyword: str, year: str) -> dict | None:
        """Get scrape progress for a keyword/year combo."""
        try:
            result = (
                self.client.table("scrape_progress")
                .select("*")
                .eq("keyword", keyword)
                .eq("year", year)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"get_progress failed: {e}")
            return None

    def update_progress(
        self,
        keyword: str,
        year: str,
        last_row: int = None,
        total_found: int = None,
        phase: str = None,
        cases_found: int = None,
        cases_detailed: int = None,
    ):
        """Upsert scrape progress for a keyword/year."""
        try:
            row = {
                "keyword": keyword,
                "year": year,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if last_row is not None:
                row["last_row"] = last_row
            if total_found is not None:
                row["total_found"] = total_found
            if phase is not None:
                row["phase"] = phase
            if cases_found is not None:
                row["cases_found"] = cases_found
            if cases_detailed is not None:
                row["cases_detailed"] = cases_detailed

            self.client.table("scrape_progress").upsert(
                row, on_conflict="keyword,year"
            ).execute()
        except Exception as e:
            logger.error(f"update_progress failed for {keyword}/{year}: {e}")

    def get_all_progress(self) -> list:
        """Get all progress records for monitoring."""
        try:
            result = (
                self.client.table("scrape_progress")
                .select("*")
                .order("keyword")
                .execute()
            )
            return result.data
        except Exception as e:
            logger.error(f"get_all_progress failed: {e}")
            return []

    # ── Scrape Runs ──────────────────────────────────────────────

    def create_run(self, config: dict = None) -> int | None:
        """Create a new scrape run record. Returns run ID."""
        try:
            row = {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "running",
                "config": config or {},
            }
            result = self.client.table("scrape_runs").insert(row).execute()
            if result.data:
                return result.data[0]["id"]
        except Exception as e:
            logger.error(f"create_run failed: {e}")
        return None

    def update_run(self, run_id: int, **kwargs):
        """Update a scrape run record."""
        if not run_id:
            return
        try:
            self.client.table("scrape_runs").update(kwargs).eq("id", run_id).execute()
        except Exception as e:
            logger.error(f"update_run failed: {e}")

    def get_latest_run(self) -> dict | None:
        """Get the most recent scrape run."""
        try:
            result = (
                self.client.table("scrape_runs")
                .select("*")
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"get_latest_run failed: {e}")
            return None

    # ── Stats & Monitoring ───────────────────────────────────────

    def get_stats(self) -> dict:
        """Get aggregate stats for monitoring."""
        stats = {
            "total_cases": 0,
            "pending_details": 0,
            "fetched_details": 0,
            "failed_details": 0,
            "keywords_tracked": 0,
        }
        try:
            # Total cases
            result = self.client.table("cases").select("id", count="exact").execute()
            stats["total_cases"] = result.count or 0

            # By detail_status
            for status in ["pending", "fetched", "failed"]:
                result = (
                    self.client.table("cases")
                    .select("id", count="exact")
                    .eq("detail_status", status)
                    .execute()
                )
                stats[f"{status}_details"] = result.count or 0

            # Keywords tracked
            result = self.client.table("scrape_progress").select("id", count="exact").execute()
            stats["keywords_tracked"] = result.count or 0

        except Exception as e:
            logger.error(f"get_stats failed: {e}")
        return stats

    # ── Export ────────────────────────────────────────────────────

    def export_csv(self, filepath: str, batch_size: int = 1000) -> int:
        """Export all cases to CSV. Returns total rows exported."""
        columns = [
            "case_id", "citation", "year", "journal", "page", "court",
            "parties_full", "petitioner", "respondent", "keywords", "summary",
            "head_notes", "full_description", "scraped_at", "search_keyword",
            "detail_status",
        ]
        total = 0
        offset = 0
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            while True:
                try:
                    result = (
                        self.client.table("cases")
                        .select(",".join(columns))
                        .order("id")
                        .range(offset, offset + batch_size - 1)
                        .execute()
                    )
                    if not result.data:
                        break
                    for row in result.data:
                        writer.writerow(row)
                    total += len(result.data)
                    offset += batch_size
                    if len(result.data) < batch_size:
                        break
                except Exception as e:
                    logger.error(f"export_csv failed at offset {offset}: {e}")
                    break
        logger.info(f"Exported {total} cases to {filepath}")
        return total

    # ── Reset ────────────────────────────────────────────────────

    def reset_progress(self):
        """Clear progress table (keeps case data intact)."""
        try:
            # Delete all rows by matching any id > 0
            self.client.table("scrape_progress").delete().gte("id", 0).execute()
            logger.info("Progress table cleared")
        except Exception as e:
            logger.error(f"reset_progress failed: {e}")

    def reset_failed_details(self):
        """Reset failed detail fetches back to pending for retry."""
        try:
            self.client.table("cases").update(
                {"detail_status": "pending"}
            ).eq("detail_status", "failed").execute()
            logger.info("Failed details reset to pending")
        except Exception as e:
            logger.error(f"reset_failed_details failed: {e}")

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _prepare_case_row(case: dict) -> dict:
        """Normalize a case dict to match the DB schema columns."""
        return {
            "case_id": case.get("case_id", ""),
            "citation": case.get("citation", ""),
            "year": case.get("year", ""),
            "journal": case.get("journal", ""),
            "page": case.get("page", ""),
            "court": case.get("court", ""),
            "parties_full": case.get("parties_full", ""),
            "petitioner": case.get("petitioner", ""),
            "respondent": case.get("respondent", ""),
            "keywords": case.get("keywords", ""),
            "summary": case.get("summary", ""),
            "head_notes": case.get("head_notes", ""),
            "full_description": case.get("full_description", ""),
            "scraped_at": case.get("scraped_at", datetime.now(timezone.utc).isoformat()),
            "search_keyword": case.get("search_keyword", ""),
            "detail_status": case.get("detail_status", "pending"),
        }

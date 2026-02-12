#!/usr/bin/env python3
"""Run Phase 2 to fetch head_notes and full_description for pending cases."""

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

from scraper_prod import ProductionScraper

async def run_phase2():
    print("=" * 60)
    print("RUNNING PHASE 2 - FETCH DETAILS")
    print("=" * 60)
    
    scraper = ProductionScraper(concurrency=10)
    
    # Check pending
    pending = scraper.db.get_pending_details(limit=100)
    print(f"\nPending cases to fetch: {len(pending)}")
    
    if not pending:
        print("No pending cases!")
        return
    
    await scraper.start_session()
    
    if not await scraper.authenticate():
        print("[FAILED] Authentication failed")
        await scraper.close()
        return
    
    print("[OK] Authenticated")
    print("\nFetching details...")
    
    await scraper.phase2_fetch_details()
    
    await scraper.close()
    
    # Final stats
    stats = scraper.db.get_stats()
    print("\n" + "=" * 60)
    print("PHASE 2 COMPLETE")
    print(f"Total cases: {stats['total_cases']}")
    print(f"Fetched details: {stats['fetched_details']}")
    print(f"Pending details: {stats['pending_details']}")
    print(f"Failed details: {stats['failed_details']}")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_phase2())

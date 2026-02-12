#!/usr/bin/env python3
"""
Production Scraper CLI
=======================
VPS-ready command-line runner for the production scraper.

Usage:
    python run_production.py start              # Start or resume full scrape
    python run_production.py start --phase2     # Only fetch missing details
    python run_production.py start --keywords PLD,SCMR --year 5
    python run_production.py status             # Show progress from Supabase
    python run_production.py export             # Export Supabase -> CSV
    python run_production.py reset              # Clear progress (keep data)
    python run_production.py reset --failed     # Re-queue failed details
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

load_dotenv()

# ── Logging Setup ────────────────────────────────────────────────

LOG_FILE = os.path.join(os.path.dirname(__file__), "scraper_prod.log")


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    # Rotating file handler: 10MB per file, keep 5 backups
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(fmt))

    logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])

    # Quiet down noisy libraries
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)

# ── Commands ─────────────────────────────────────────────────────

scraper_instance = None  # Global for signal handler access


async def cmd_start(args):
    """Start or resume scraping."""
    from scraper_prod import ProductionScraper, JOURNALS

    global scraper_instance

    keywords = None
    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",")]
    elif not args.phase2:
        keywords = JOURNALS

    scraper = ProductionScraper(
        concurrency=args.concurrency,
        delay_range=(args.delay_min, args.delay_max),
        timeout=args.timeout,
    )
    scraper_instance = scraper

    # Register signal handlers for graceful shutdown
    def handle_signal(sig, frame):
        logger.info(f"Received signal {sig} — requesting graceful shutdown...")
        scraper.request_stop()

    # On Windows, only SIGINT (Ctrl+C) is reliably supported
    signal.signal(signal.SIGINT, handle_signal)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, handle_signal)

    try:
        await scraper.run(
            keywords=keywords,
            year=args.year,
            skip_search=args.phase2,
            skip_details=args.no_details,
        )
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — shutting down gracefully")
        scraper.request_stop()
    except Exception as e:
        logger.error(f"Scraper crashed: {e}", exc_info=True)
        sys.exit(1)


def cmd_status(args):
    """Show current progress from Supabase."""
    from db import SupabaseDB

    db = SupabaseDB()
    stats = db.get_stats()
    progress = db.get_all_progress()
    latest_run = db.get_latest_run()

    print("\n" + "=" * 60)
    print("  PRODUCTION SCRAPER STATUS")
    print("=" * 60)

    print(f"\n  Total cases in DB:     {stats['total_cases']:,}")
    print(f"  Details fetched:       {stats['fetched_details']:,}")
    print(f"  Details pending:       {stats['pending_details']:,}")
    print(f"  Details failed:        {stats['failed_details']:,}")
    print(f"  Keywords tracked:      {stats['keywords_tracked']}")

    if latest_run:
        print(f"\n  Latest run:")
        print(f"    Status:    {latest_run['status']}")
        print(f"    Started:   {latest_run['started_at']}")
        print(f"    Scraped:   {latest_run['cases_scraped']:,}")
        print(f"    Detailed:  {latest_run['cases_detailed']:,}")
        print(f"    Errors:    {latest_run['errors_count']}")
        if latest_run.get("last_error"):
            print(f"    Last err:  {latest_run['last_error'][:80]}")

    if progress:
        print(f"\n  Per-keyword progress:")
        print(f"  {'Keyword':<12} {'Year':<6} {'Phase':<10} {'Row':<8} {'Found':<8} {'Total':<8}")
        print(f"  {'-'*12} {'-'*6} {'-'*10} {'-'*8} {'-'*8} {'-'*8}")
        for p in progress:
            print(
                f"  {p['keyword']:<12} {p['year']:<6} {p['phase']:<10} "
                f"{p['last_row']:<8} {p['cases_found']:<8} {p['total_found']:<8}"
            )

    print()


def cmd_export(args):
    """Export Supabase data to CSV."""
    from db import SupabaseDB

    db = SupabaseDB()
    filepath = args.output or "export_cases.csv"
    count = db.export_csv(filepath)
    print(f"Exported {count:,} cases to {filepath}")


def cmd_reset(args):
    """Reset progress or failed details."""
    from db import SupabaseDB

    db = SupabaseDB()

    if args.failed:
        db.reset_failed_details()
        print("Reset failed details back to pending")
    else:
        confirm = input("This will clear all progress tracking (case data is kept). Continue? [y/N] ")
        if confirm.lower() == "y":
            db.reset_progress()
            print("Progress table cleared. Case data untouched.")
        else:
            print("Cancelled")


# ── Main ─────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Production Pakistan Law Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_production.py start                      Full scrape (all journals, all years)
  python run_production.py start --keywords PLD,SCMR  Specific journals only
  python run_production.py start --year 5             Last 5 years only
  python run_production.py start --phase2             Only fetch missing details
  python run_production.py start --concurrency 20     20 parallel detail fetchers
  python run_production.py status                     Show Supabase stats
  python run_production.py export -o cases.csv        Export to CSV
  python run_production.py reset                      Clear progress tracking
  python run_production.py reset --failed             Re-queue failed details
        """,
    )

    sub = parser.add_subparsers(dest="command")

    # start
    start_p = sub.add_parser("start", help="Start or resume scraping")
    start_p.add_argument("--keywords", type=str, help="Comma-separated journal keywords (default: all)")
    start_p.add_argument("--year", type=str, default="200", help="Year filter: 5/10/15/20/200 (default: 200=all)")
    start_p.add_argument("--concurrency", type=int, default=15, help="Max parallel requests (default: 15)")
    start_p.add_argument("--delay-min", type=float, default=0.3, help="Min delay between requests (default: 0.3)")
    start_p.add_argument("--delay-max", type=float, default=0.8, help="Max delay between requests (default: 0.8)")
    start_p.add_argument("--timeout", type=int, default=30, help="Request timeout seconds (default: 30)")
    start_p.add_argument("--phase2", action="store_true", help="Skip search, only fetch pending details")
    start_p.add_argument("--no-details", action="store_true", help="Skip detail fetching (search only)")
    start_p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    # status
    sub.add_parser("status", help="Show progress from Supabase")

    # export
    export_p = sub.add_parser("export", help="Export Supabase data to CSV")
    export_p.add_argument("-o", "--output", type=str, default="export_cases.csv", help="Output CSV file")

    # reset
    reset_p = sub.add_parser("reset", help="Clear progress tracking")
    reset_p.add_argument("--failed", action="store_true", help="Only reset failed details to pending")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    setup_logging(getattr(args, "verbose", False))

    if args.command == "start":
        asyncio.run(cmd_start(args))
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "reset":
        cmd_reset(args)


if __name__ == "__main__":
    main()

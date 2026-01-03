#!/usr/bin/env python3
"""
Quick Start Script for Pakistan Law Site Scraper
================================================

This script provides a simple way to run the scraper.
It will attempt auto-login first, and prompt for cookies if that fails.
"""

from scraper import PakistanLawScraper
import logging
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# =============================================================================
# CONFIGURATION
# =============================================================================

# Your login credentials (set via environment variables)
USERNAME = os.environ.get("PLS_USERNAME", "")
PASSWORD = os.environ.get("PLS_PASSWORD", "")

# Session cookies (can be set via environment variables)
SESSION_ID = os.environ.get("PLS_SESSION_ID", "")
VERIFICATION_TOKEN = os.environ.get("PLS_VERIFICATION_TOKEN", "")

# Scraping settings
DELAY_MIN = 1.5  # Minimum seconds between requests
DELAY_MAX = 3.0  # Maximum seconds between requests


def get_cookies_from_user():
    """Prompt user for cookies from browser"""
    print("\n" + "="*60)
    print("AUTO-LOGIN FAILED - Manual cookies required")
    print("="*60)
    print("""
HOW TO GET YOUR COOKIES:
1. Open Chrome, go to https://www.pakistanlawsite.com and log in
2. Press F12 to open Developer Tools
3. Go to Network tab
4. Refresh the page or click on any link
5. Click on any request (like "Check" or "SearchCaseLaw")
6. In the Headers tab, find "Cookie:" under Request Headers
7. Copy the values for ASP.NET_SessionId and __RequestVerificationToken
""")

    session_id = input("Enter ASP.NET_SessionId: ").strip()
    verification_token = input("Enter __RequestVerificationToken: ").strip()

    if not session_id or not verification_token:
        print("\n❌ Both cookies are required!")
        return None, None

    return session_id, verification_token


def setup_scraper():
    """Initialize and authenticate scraper"""
    scraper = PakistanLawScraper(
        username=USERNAME,
        password=PASSWORD,
        delay_range=(DELAY_MIN, DELAY_MAX)
    )

    # Check if cookies are provided via environment variables
    if SESSION_ID and VERIFICATION_TOKEN:
        print("\nUsing cookies from environment variables...")
        scraper.set_cookies(SESSION_ID, VERIFICATION_TOKEN)
        # Verify the cookies work
        if scraper._verify_login():
            print("✅ Environment cookies are valid!")
            return scraper
        else:
            print("⚠️  Environment cookies are invalid or expired.")

    # Try auto-login
    print("\nAttempting auto-login...")
    if scraper.login():
        print("✅ Auto-login successful!")
        return scraper

    # Auto-login failed, prompt for cookies
    print("\n⚠️  Auto-login failed.")
    session_id, verification_token = get_cookies_from_user()

    if session_id and verification_token:
        scraper.set_cookies(session_id, verification_token)
        # Verify the manual cookies work
        if scraper._verify_login():
            print("✅ Cookies verified successfully!")
            return scraper
        else:
            print("❌ Cookies are invalid or expired. Please get fresh cookies.")
            return None

    print("❌ Cannot proceed without valid authentication.")
    return None


def run_test():
    """Run a small test scrape"""
    print("\n" + "="*60)
    print("RUNNING TEST SCRAPE")
    print("Scraping 50 cases with keyword 'contract'")
    print("="*60)

    scraper = setup_scraper()
    if not scraper:
        return

    # Run test scrape
    df = scraper.scrape_all(
        keywords=['contract'],
        year='5',  # Last 5 years
        output_file='test_output.csv',
        get_details=True,
        max_cases=50
    )

    print(f"\n{'='*60}")
    print(f"✅ Test complete!")
    print(f"📊 Scraped {len(df)} cases")
    print(f"📁 Saved to: test_output.csv")

    if len(df) > 0:
        print(f"\n📋 Columns: {list(df.columns)}")
        print(f"\n📋 First case preview:")
        first = df.iloc[0]
        print(f"   Case ID: {first.get('case_id', 'N/A')}")
        print(f"   Citation: {first.get('citation', 'N/A')}")
        print(f"   Parties: {first.get('parties_full', 'N/A')[:80]}...")
    else:
        print("\n⚠️  No cases scraped. Check if your session is valid.")


def run_full_scrape():
    """Run full database scrape"""
    print("\n" + "="*60)
    print("RUNNING FULL SCRAPE")
    print("This will take several days to complete!")
    print("="*60)

    scraper = setup_scraper()
    if not scraper:
        return

    # Load any existing checkpoint
    scraper.load_checkpoint('all_cases_checkpoint.csv')

    # Run full scrape
    df = scraper.scrape_all(
        keywords=None,  # All journals
        year='200',     # All years
        output_file='all_cases.csv',
        get_details=True,
        checkpoint_every=100
    )

    print(f"\n✅ Full scrape complete!")
    print(f"📊 Total cases: {len(df)}")


def run_custom_scrape():
    """Run custom scrape with user-defined parameters"""
    print("\n" + "="*60)
    print("CUSTOM SCRAPE")
    print("="*60)

    scraper = setup_scraper()
    if not scraper:
        return

    print("\nAvailable journals:", ', '.join(scraper.JOURNALS))
    keywords_input = input("\nEnter keywords (comma-separated) or press Enter for all journals: ").strip()
    keywords = [k.strip() for k in keywords_input.split(',')] if keywords_input else None

    print("\nYear options: 5 (last 5 years), 10, 15, 20, 200 (all years)")
    year_input = input("Enter year option [default: 5]: ").strip() or "5"

    max_input = input("Enter max cases (or press Enter for unlimited): ").strip()
    max_cases = int(max_input) if max_input else None

    output_file = input("Enter output filename [default: custom_cases.csv]: ").strip() or "custom_cases.csv"

    df = scraper.scrape_all(
        keywords=keywords,
        year=year_input,
        output_file=output_file,
        get_details=True,
        max_cases=max_cases,
        checkpoint_every=50
    )

    print(f"\n✅ Custom scrape complete!")
    print(f"📊 Total cases: {len(df)}")
    print(f"📁 Saved to: {output_file}")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("PAKISTAN LAW SITE SCRAPER")
    print("="*60)
    print("\nOptions:")
    print("1. Test scrape (50 cases)")
    print("2. Full database scrape (all journals, all years)")
    print("3. Custom scrape")

    choice = input("\nEnter choice (1/2/3): ").strip()

    if choice == "1":
        run_test()
    elif choice == "2":
        confirm = input("\n⚠️  Full scrape may take days. Continue? (y/n): ").strip().lower()
        if confirm == 'y':
            run_full_scrape()
        else:
            print("Cancelled.")
    elif choice == "3":
        run_custom_scrape()
    else:
        print("Invalid choice. Running test by default...")
        run_test()

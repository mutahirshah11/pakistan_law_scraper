#!/usr/bin/env python3
"""
Interactive Scraper Interface
==============================
Clean, simple interface to start, stop, and monitor the scraper.

Usage:
    python interactive.py
"""

import os
import sys
import signal
import threading
import time
from datetime import datetime
from scraper import PakistanLawScraper
import logging

# Database layer — only active when DATABASE_URL is set
_db = None
try:
    import db as database
    if os.environ.get("DATABASE_URL"):
        database.init_tables()
        _db = database
except ImportError:
    pass

# Colors for terminal
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_header():
    clear_screen()
    print(f"""
{Colors.CYAN}╔══════════════════════════════════════════════════════════╗
║           PAKISTAN LAW SITE SCRAPER                      ║
║                Interactive Mode                           ║
╚══════════════════════════════════════════════════════════╝{Colors.END}
""")


class ScraperController:
    """Controls the scraper with start/stop functionality"""

    def __init__(self):
        self.scraper = None
        self.is_running = False
        self.should_stop = False
        self.thread = None
        self.cases_scraped = 0
        self.current_keyword = ""
        self.errors = []
        self.start_time = None

        # Credentials (set via environment variables)
        self.username = os.environ.get("PLS_USERNAME", "")
        self.password = os.environ.get("PLS_PASSWORD", "")
        self.session_id = os.environ.get("PLS_SESSION_ID", "")
        self.verification_token = os.environ.get("PLS_VERIFICATION_TOKEN", "")

        # Scrape settings
        self.keywords = ['contract']
        self.year = '5'
        self.max_cases = 50
        self.output_file = 'scraped_cases.csv'
        self.get_details = True

        # Index mode state
        self.index_mode = False
        self.current_journal = ""
        self.current_year = ""
        self.combos_completed = 0
        self.combos_total = 0
        self.num_workers = 3

    def setup_scraper(self) -> bool:
        """Initialize and authenticate the scraper"""
        self.scraper = PakistanLawScraper(
            username=self.username,
            password=self.password,
            delay_range=(0.2, 0.5)
        )

        # Try cookies first
        if self.session_id and self.verification_token:
            print(f"{Colors.YELLOW}Using provided cookies...{Colors.END}")
            self.scraper.set_cookies(self.session_id, self.verification_token)
            if self.scraper._verify_login():
                print(f"{Colors.GREEN}✓ Cookies valid!{Colors.END}")
                return True
            else:
                print(f"{Colors.RED}✗ Cookies expired{Colors.END}")

        # Try auto-login
        print(f"{Colors.YELLOW}Attempting auto-login...{Colors.END}")
        if self.scraper.login():
            print(f"{Colors.GREEN}✓ Login successful!{Colors.END}")
            return True

        # Need manual cookies
        print(f"\n{Colors.RED}Auto-login failed. Manual cookies required.{Colors.END}")
        return False

    def prompt_cookies(self):
        """Prompt user for cookies"""
        print(f"""
{Colors.YELLOW}HOW TO GET COOKIES:{Colors.END}
1. Open Chrome, go to pakistanlawsite.com and login
2. Press F12 → Network tab
3. Click any request, find Cookie header
4. Copy ASP.NET_SessionId and __RequestVerificationToken
""")
        self.session_id = input("ASP.NET_SessionId: ").strip()
        self.verification_token = input("__RequestVerificationToken: ").strip()

        if self.session_id and self.verification_token:
            self.scraper.set_cookies(self.session_id, self.verification_token)
            if self.scraper._verify_login():
                print(f"{Colors.GREEN}✓ Cookies valid!{Colors.END}")
                return True

        print(f"{Colors.RED}✗ Invalid cookies{Colors.END}")
        return False

    def _scrape_worker(self):
        """Worker thread for scraping"""
        try:
            all_cases = []

            for keyword in self.keywords:
                if self.should_stop:
                    break

                self.current_keyword = keyword
                row = 0

                while not self.should_stop:
                    cases, total = self.scraper.search_cases(
                        keyword=keyword,
                        year=self.year,
                        row=row
                    )

                    if not cases:
                        break

                    for case in cases:
                        if self.should_stop:
                            break

                        case_id = case.get('case_id', '')
                        if case_id in self.scraper.processed_case_ids:
                            continue

                        self.scraper.processed_case_ids.add(case_id)

                        # Get details if requested
                        if self.get_details and case_id:
                            try:
                                details = self.scraper.get_case_details(case_id)
                                case.update(details)
                            except Exception as e:
                                self.errors.append(f"Details error for {case_id}: {str(e)}")

                        case['scraped_at'] = datetime.now().isoformat()
                        case['search_keyword'] = keyword
                        all_cases.append(case)
                        self.cases_scraped = len(all_cases)

                        # Check max limit
                        if self.max_cases and len(all_cases) >= self.max_cases:
                            self.should_stop = True
                            break

                    if len(cases) < 50:
                        break

                    row += 50

            # Save results
            if all_cases:
                import pandas as pd
                df = pd.DataFrame(all_cases)
                df.to_csv(self.output_file, index=False, encoding='utf-8-sig')

        except Exception as e:
            self.errors.append(f"Scrape error: {str(e)}")
        finally:
            self.is_running = False

    def _index_scrape_worker(self):
        """Worker thread for index-based scraping"""
        try:
            def on_progress(progress_data):
                self.combos_completed = progress_data.get('completed_count', 0)
                self.combos_total = progress_data.get('total_combinations', 0)
                for journal in progress_data.get('journals', {}):
                    for year_str, info in progress_data['journals'][journal].items():
                        if info.get('status') == 'in_progress':
                            self.current_journal = journal
                            self.current_year = year_str

            def on_case_scraped(count):
                self.cases_scraped = count

            def should_stop():
                return self.should_stop

            self.scraper.scrape_all_index(
                output_file=self.output_file,
                progress_file='index_progress.json',
                get_details=self.get_details,
                on_progress=on_progress,
                should_stop=should_stop,
                on_case_scraped=on_case_scraped,
                db=_db,
                num_workers=self.num_workers
            )
        except Exception as e:
            self.errors.append(f"Index scrape error: {str(e)}")
        finally:
            self.is_running = False

    def start(self):
        """Start scraping in background thread"""
        if self.is_running:
            print(f"{Colors.YELLOW}Already running!{Colors.END}")
            return

        self.should_stop = False
        self.cases_scraped = 0
        self.errors = []
        self.start_time = datetime.now()
        self.is_running = True

        if self.index_mode:
            self.combos_completed = 0
            self.combos_total = 0
            self.current_journal = ""
            self.current_year = ""
            self.thread = threading.Thread(target=self._index_scrape_worker, daemon=True)
        else:
            self.thread = threading.Thread(target=self._scrape_worker, daemon=True)
        self.thread.start()
        mode_label = "Index scraper" if self.index_mode else "Scraper"
        print(f"{Colors.GREEN}✓ {mode_label} started{Colors.END}")

    def stop(self):
        """Stop the scraper"""
        if not self.is_running:
            print(f"{Colors.YELLOW}Not running{Colors.END}")
            return

        print(f"{Colors.YELLOW}Stopping...{Colors.END}")
        self.should_stop = True

        # Wait for thread to finish
        if self.thread:
            self.thread.join(timeout=10)

        print(f"{Colors.GREEN}✓ Stopped{Colors.END}")

    def get_status(self) -> dict:
        """Get current status"""
        elapsed = ""
        if self.start_time and self.is_running:
            delta = datetime.now() - self.start_time
            elapsed = str(delta).split('.')[0]

        result = {
            'running': self.is_running,
            'cases': self.cases_scraped,
            'keyword': self.current_keyword,
            'elapsed': elapsed,
            'errors': len(self.errors),
            'index_mode': self.index_mode,
            'current_journal': self.current_journal,
            'current_year': self.current_year,
            'combos_completed': self.combos_completed,
            'combos_total': self.combos_total,
        }
        return result


def show_menu(controller: ScraperController):
    """Show main menu"""
    status = controller.get_status()

    status_text = f"{Colors.GREEN}RUNNING{Colors.END}" if status['running'] else f"{Colors.YELLOW}STOPPED{Colors.END}"

    mode_text = f"{Colors.CYAN}INDEX MODE{Colors.END}" if status.get('index_mode') else "KEYWORD MODE"

    print(f"""
{Colors.BOLD}STATUS:{Colors.END} {status_text}  |  {mode_text}
{Colors.BOLD}Cases scraped:{Colors.END} {status['cases']}""")

    if status['running']:
        if status.get('index_mode'):
            print(f"{Colors.BOLD}Current:{Colors.END} {status['current_journal']} {status['current_year']}")
            print(f"{Colors.BOLD}Combos:{Colors.END} {status['combos_completed']} / {status['combos_total']}")
        else:
            print(f"{Colors.BOLD}Current keyword:{Colors.END} {status['keyword']}")
        print(f"{Colors.BOLD}Elapsed:{Colors.END} {status['elapsed']}")

    if status['errors']:
        print(f"{Colors.RED}Errors: {status['errors']}{Colors.END}")

    print(f"""
{Colors.CYAN}─────────────────────────────────────{Colors.END}
  {Colors.BOLD}1{Colors.END}. Start scraper
  {Colors.BOLD}2{Colors.END}. Stop scraper
  {Colors.BOLD}3{Colors.END}. View errors
  {Colors.BOLD}4{Colors.END}. Configure settings
  {Colors.BOLD}5{Colors.END}. Set cookies
  {Colors.BOLD}6{Colors.END}. Refresh status
  {Colors.BOLD}7{Colors.END}. Citation Index Scrape (100% coverage)
  {Colors.BOLD}q{Colors.END}. Quit
{Colors.CYAN}─────────────────────────────────────{Colors.END}
""")


def configure_settings(controller: ScraperController):
    """Configure scrape settings"""
    print(f"""
{Colors.CYAN}Current Settings:{Colors.END}
  Keywords: {controller.keywords}
  Year: {controller.year}
  Max cases: {controller.max_cases}
  Output file: {controller.output_file}
  Get details: {controller.get_details}
""")

    # Keywords
    kw = input(f"Keywords (comma-separated) [{', '.join(controller.keywords)}]: ").strip()
    if kw:
        controller.keywords = [k.strip() for k in kw.split(',')]

    # Year
    yr = input(f"Year filter (5/10/15/20/200) [{controller.year}]: ").strip()
    if yr:
        controller.year = yr

    # Max cases
    mx = input(f"Max cases (0 for unlimited) [{controller.max_cases}]: ").strip()
    if mx:
        controller.max_cases = int(mx) if mx != '0' else None

    # Output file
    out = input(f"Output file [{controller.output_file}]: ").strip()
    if out:
        controller.output_file = out

    # Details
    det = input(f"Get full details? (y/n) [{'y' if controller.get_details else 'n'}]: ").strip().lower()
    if det:
        controller.get_details = det == 'y'

    print(f"{Colors.GREEN}✓ Settings updated{Colors.END}")


def view_errors(controller: ScraperController):
    """View error log"""
    print(f"\n{Colors.CYAN}═══ ERROR LOG ═══{Colors.END}")

    if not controller.errors:
        print(f"{Colors.GREEN}No errors{Colors.END}")
    else:
        for i, err in enumerate(controller.errors[-20:], 1):  # Last 20 errors
            print(f"{Colors.RED}{i}. {err}{Colors.END}")

    # Also check scraper.log
    if os.path.exists('scraper.log'):
        print(f"\n{Colors.YELLOW}Recent log entries:{Colors.END}")
        try:
            with open('scraper.log', 'r') as f:
                lines = f.readlines()
                for line in lines[-10:]:  # Last 10 lines
                    if 'ERROR' in line:
                        print(f"{Colors.RED}{line.strip()}{Colors.END}")
                    elif 'WARNING' in line:
                        print(f"{Colors.YELLOW}{line.strip()}{Colors.END}")
        except:
            pass

    input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.END}")


def main():
    """Main interactive loop"""
    # Suppress logging to console (we handle our own output)
    logging.getLogger().setLevel(logging.WARNING)

    controller = ScraperController()

    print_header()
    print(f"{Colors.YELLOW}Initializing...{Colors.END}")

    # Setup scraper
    if not controller.setup_scraper():
        if not controller.prompt_cookies():
            print(f"{Colors.RED}Cannot proceed without valid authentication{Colors.END}")
            return

    # Main loop
    while True:
        print_header()
        show_menu(controller)

        choice = input(f"{Colors.BOLD}Choose option: {Colors.END}").strip().lower()

        if choice == '1':
            controller.start()
            time.sleep(1)

        elif choice == '2':
            controller.stop()
            time.sleep(1)

        elif choice == '3':
            view_errors(controller)

        elif choice == '4':
            configure_settings(controller)
            input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.END}")

        elif choice == '5':
            controller.prompt_cookies()
            input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.END}")

        elif choice == '6':
            continue  # Just refresh

        elif choice == '7':
            # Configure and start index scrape
            print(f"\n{Colors.CYAN}═══ CITATION INDEX SCRAPE ═══{Colors.END}")
            print("This mode iterates 16 journals x 80 years = 1,280 combinations")
            print("for 100% coverage. Progress is saved - stop and resume anytime.\n")

            out = input(f"Output file [{controller.output_file}]: ").strip()
            if out:
                controller.output_file = out
            else:
                controller.output_file = 'all_cases_index.csv'

            det = input("Fetch full details? (y/n) [y]: ").strip().lower()
            controller.get_details = det != 'n'

            workers_input = input(f"Concurrent workers (1-10) [3]: ").strip()
            if workers_input:
                try:
                    controller.num_workers = max(1, min(10, int(workers_input)))
                except ValueError:
                    controller.num_workers = 3

            controller.index_mode = True

            confirm = input(f"\n{Colors.YELLOW}Start index scrape with {controller.num_workers} workers? (y/n): {Colors.END}").strip().lower()
            if confirm == 'y':
                controller.start()
            else:
                controller.index_mode = False
                print("Cancelled.")

            input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.END}")

        elif choice == 'q':
            if controller.is_running:
                controller.stop()
            print(f"\n{Colors.GREEN}Goodbye!{Colors.END}")
            break

        else:
            print(f"{Colors.RED}Invalid option{Colors.END}")
            time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Interrupted{Colors.END}")
        sys.exit(0)

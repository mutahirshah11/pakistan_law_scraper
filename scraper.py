#!/usr/bin/env python3
"""
Pakistan Law Site Scraper
=========================
Scrapes legal cases from pakistanlawsite.com including:
- Case citations
- Parties information
- Case summaries
- Full head notes
- Complete case descriptions

Author: Built for Hassan/Zensbot
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
import re
import json
import os
from datetime import datetime
from urllib.parse import urljoin
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scraper.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class PakistanLawScraper:
    """Scraper for Pakistan Law Site"""
    
    BASE_URL = "https://www.pakistanlawsite.com"
    LOGIN_URL = f"{BASE_URL}/Login/Check"
    SEARCH_URL = f"{BASE_URL}/Login/SearchCaseLaw"
    LOAD_MORE_URL = f"{BASE_URL}/Login/LoadMoreCaseLaw"  # Pagination endpoint
    CASE_FILE_URL = f"{BASE_URL}/Login/GetCaseFile"
    
    # Year options from the website dropdown
    YEAR_OPTIONS = {
        'last_5': '5',
        'last_10': '10', 
        'last_15': '15',
        'last_20': '20',
        'all': '200'
    }
    
    # All available journals
    JOURNALS = [
        'PLD', 'SCMR', 'CLC', 'CLD', 'YLR', 'PCrLJ', 'PLC', 'PLC(CS)',
        'PTD', 'MLD', 'GBLR', 'CLCN', 'YLRN', 'PCRLJN', 'PLCN', 'PLC(CS)N'
    ]
    
    def __init__(self, username: str, password: str, delay_range: tuple = (1, 3), timeout: int = 30):
        """
        Initialize the scraper

        Args:
            username: Login username
            password: Login password
            delay_range: Tuple of (min, max) seconds to wait between requests
            timeout: Request timeout in seconds
        """
        self.username = username
        self.password = password
        self.delay_range = delay_range
        self.timeout = timeout
        self.session = requests.Session()
        self.is_logged_in = False
        self.processed_case_ids = set()

        # Set up session headers
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
        })
    
    def _delay(self):
        """Add random delay between requests to be polite"""
        delay = random.uniform(*self.delay_range)
        time.sleep(delay)
    
    def login(self) -> bool:
        """
        Login to Pakistan Law Site

        Returns:
            True if login successful, False otherwise
        """
        logger.info(f"Attempting login as {self.username}...")

        try:
            # Step 1: Get the login page to extract CSRF token
            logger.info("Fetching login page...")
            response = self.session.get(self.LOGIN_URL, timeout=self.timeout)
            response.raise_for_status()

            # Extract CSRF token from the page
            soup = BeautifulSoup(response.text, 'html.parser')
            token_input = soup.find('input', {'name': '__RequestVerificationToken'})
            csrf_token = token_input.get('value', '') if token_input else ''

            if csrf_token:
                logger.info("Found CSRF token")
            else:
                logger.warning("No CSRF token found, proceeding without it")

            # Step 2: Submit login form
            login_data = {
                'UserName': self.username,
                'Password': self.password,
                '__RequestVerificationToken': csrf_token
            }

            # Set form headers
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': self.BASE_URL,
                'Referer': self.LOGIN_URL,
            }

            logger.info("Submitting login credentials...")
            response = self.session.post(
                self.LOGIN_URL,
                data=login_data,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=True
            )

            # Step 3: Check cookies were set
            session_cookie = self.session.cookies.get('ASP.NET_SessionId')
            if session_cookie:
                logger.info(f"Session cookie obtained: {session_cookie[:10]}...")
            else:
                logger.warning("No session cookie received")

            # Step 4: ALWAYS verify by trying a test search
            # This is the only reliable way to know if we're authenticated
            logger.info("Verifying login with test search...")
            if self._verify_login():
                self.is_logged_in = True
                logger.info("Login verified successfully!")
                return True

            logger.error("Login failed - search verification returned no results")
            logger.error("Please provide manual cookies using set_cookies()")
            self.is_logged_in = False
            return False

        except requests.exceptions.Timeout:
            logger.error("Login failed: Request timed out")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Login failed: Network error - {e}")
            return False
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return False

    def _verify_login(self) -> bool:
        """Verify login by attempting a small test search"""
        try:
            data = {
                'year': '5',
                'book': 'PLD',
                'code': '',
                'court': '',
                'searchType': 'caselaw',
                'judge': '',
                'lawyer': '',
                'party': '',
                'Row': 0
            }
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'Origin': self.BASE_URL,
                'Referer': self.LOGIN_URL,
            }
            response = self.session.post(
                self.SEARCH_URL,
                data=data,
                headers=headers,
                timeout=self.timeout
            )
            # If we get case tables back, we're logged in
            return 'caseLawTable' in response.text or 'Citation Name' in response.text
        except Exception as e:
            logger.warning(f"Login verification failed: {e}")
            return False
    
    def set_cookies(self, session_id: str, verification_token: str):
        """
        Manually set session cookies (useful if login doesn't work automatically)
        
        Args:
            session_id: ASP.NET_SessionId cookie value
            verification_token: __RequestVerificationToken cookie value
        """
        self.session.cookies.set('ASP.NET_SessionId', session_id, domain='www.pakistanlawsite.com')
        self.session.cookies.set('__RequestVerificationToken', verification_token, domain='www.pakistanlawsite.com')
        self.is_logged_in = True
        logger.info("Cookies set manually")
    
    def search_cases(self, keyword: str = "", year: str = "200", court: str = "",
                     row: int = 0) -> tuple:
        """
        Search for cases - uses initial search for row=0, LoadMore for pagination

        Args:
            keyword: Search keyword (book parameter)
            year: Year filter (5, 10, 15, 20, or 200 for all)
            court: Court filter
            row: Starting row for pagination (0, 50, 100, etc.)

        Returns:
            Tuple of (list of cases, total count)
        """
        self._delay()

        # Use different endpoints for initial search vs pagination
        if row == 0:
            return self._initial_search(keyword, year, court)
        else:
            return self._load_more_cases(keyword, year, row)

    def _initial_search(self, keyword: str, year: str, court: str) -> tuple:
        """Initial search using SearchCaseLaw endpoint"""
        data = {
            'year': year,
            'book': keyword,
            'code': '',
            'court': court,
            'searchType': 'caselaw',
            'judge': '',
            'lawyer': '',
            'party': '',
            'Row': 0
        }

        headers = {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': self.BASE_URL,
            'Referer': self.LOGIN_URL,
        }

        try:
            response = self.session.post(
                self.SEARCH_URL,
                data=data,
                headers=headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            return self._parse_search_results(response.text, keyword)

        except requests.exceptions.Timeout:
            logger.error(f"Initial search timed out for '{keyword}'")
            return [], 0
        except requests.exceptions.RequestException as e:
            logger.error(f"Initial search failed for '{keyword}': {e}")
            return [], 0
        except Exception as e:
            logger.error(f"Initial search failed for '{keyword}': {e}")
            return [], 0

    def _load_more_cases(self, keyword: str, year: str, row: int) -> tuple:
        """Load more cases using LoadMoreCaseLaw endpoint (GET request)"""
        params = {
            'book': keyword,
            'row': row,
            'year': year,
            'caseTypeId': 0
        }

        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': self.LOGIN_URL,
        }

        try:
            response = self.session.get(
                self.LOAD_MORE_URL,
                params=params,
                headers=headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            return self._parse_search_results(response.text, keyword)

        except requests.exceptions.Timeout:
            logger.error(f"Load more timed out for '{keyword}' at row {row}")
            return [], 0
        except requests.exceptions.RequestException as e:
            logger.error(f"Load more failed for '{keyword}' at row {row}: {e}")
            return [], 0
        except Exception as e:
            logger.error(f"Load more failed for '{keyword}' at row {row}: {e}")
            return [], 0
    
    def _parse_search_results(self, html: str, keyword: str = "") -> tuple:
        """
        Parse search results HTML

        Args:
            html: Raw HTML response
            keyword: The search keyword used (for logging)

        Returns:
            Tuple of (list of case dicts, total count)
        """
        soup = BeautifulSoup(html, 'html.parser')
        cases = []

        # Extract total count
        total_count = 0
        count_text = soup.find('p', string=re.compile(r'Your Search returned total'))
        if count_text:
            match = re.search(r'total\s+(\d+)\s+records', count_text.get_text())
            if match:
                total_count = int(match.group(1))
        else:
            # Try alternative pattern - look for red span with number
            count_span = soup.find('span', style=re.compile('color.*red'))
            if count_span:
                try:
                    total_count = int(count_span.get_text().strip())
                except ValueError:
                    pass

        # Find all case tables
        tables = soup.find_all('table', class_='caseLawTable')

        for table in tables:
            try:
                case = self._parse_case_table(table)
                if case and case.get('case_id'):
                    cases.append(case)
            except Exception as e:
                logger.warning(f"Failed to parse case table: {e}")
                continue

        logger.info(f"Parsed {len(cases)} cases from search results for '{keyword}' (Total: {total_count})")
        return cases, total_count
    
    def _parse_case_table(self, table) -> dict:
        """
        Parse a single case table from search results
        
        Args:
            table: BeautifulSoup table element
            
        Returns:
            Dictionary with case data
        """
        rows = table.find_all('tr')
        if len(rows) < 5:
            return None
        
        case = {}
        
        # Row 0: Citation and Case ID
        citation_cell = rows[0].find('td')
        if citation_cell:
            # Extract case ID from bookmark span
            bookmark_span = citation_cell.find('span', class_='bookmarklogo')
            if bookmark_span:
                case['case_id'] = bookmark_span.get('casename', '')
            
            # Extract citation text
            citation_text = citation_cell.get_text(strip=True)
            citation_text = re.sub(r'Bookmark this Case.*', '', citation_text).strip()
            citation_text = citation_text.replace('Citation Name:', '').strip()
            case['citation'] = citation_text
            
            # Parse citation components
            parsed = self._parse_citation(citation_text)
            case.update(parsed)
        
        # Row 1: Parties
        if len(rows) > 1:
            parties_cell = rows[1].find('td')
            if parties_cell:
                parties_text = parties_cell.get_text(strip=True)
                case['parties_full'] = parties_text
                
                # Split petitioner and respondent
                if ' VS ' in parties_text.upper():
                    parts = re.split(r'\s+VS\s+', parties_text, flags=re.IGNORECASE)
                    case['petitioner'] = parts[0].strip() if len(parts) > 0 else ''
                    case['respondent'] = parts[1].strip() if len(parts) > 1 else ''
                else:
                    case['petitioner'] = parties_text
                    case['respondent'] = ''
        
        # Row 4 (index 4): Summary
        if len(rows) > 4:
            summary_cell = rows[4].find('td')
            if summary_cell:
                # Extract keywords (highlighted in red)
                keywords = []
                for bold in summary_cell.find_all('b', style=re.compile(r'color.*red', re.I)):
                    keywords.append(bold.get_text(strip=True))
                case['keywords'] = ', '.join(keywords)
                
                # Get full summary text
                case['summary'] = summary_cell.get_text(strip=True)
        
        return case
    
    def _parse_citation(self, citation: str) -> dict:
        """
        Parse citation string into components
        
        Args:
            citation: Citation string like "2025 PLC(CS) 1046 SUPREME-COURT-AZAD-KASHMIR"
            
        Returns:
            Dictionary with year, journal, page, court
        """
        result = {
            'year': '',
            'journal': '',
            'page': '',
            'court': ''
        }
        
        # Pattern: YEAR JOURNAL PAGE COURT
        # Example: 2025 PLC(CS) 1046 SUPREME-COURT-AZAD-KASHMIR
        
        parts = citation.split()
        if len(parts) >= 4:
            result['year'] = parts[0]
            result['journal'] = parts[1]
            result['page'] = parts[2]
            result['court'] = ' '.join(parts[3:])
        elif len(parts) == 3:
            result['year'] = parts[0]
            result['journal'] = parts[1]
            result['page'] = parts[2]
        elif len(parts) == 2:
            result['year'] = parts[0]
            result['journal'] = parts[1]
        
        return result
    
    def _clean_html_content(self, html: str) -> str:
        """
        Clean HTML content from Microsoft Word format to readable plain text

        Args:
            html: Raw HTML string (may be JSON-encoded)

        Returns:
            Clean, readable text
        """
        if not html:
            return ''

        # Check if the content is JSON-encoded (starts with quote)
        if html.startswith('"') and html.endswith('"'):
            try:
                # Decode JSON string
                html = json.loads(html)
            except json.JSONDecodeError:
                pass

        # Decode unicode escapes if present (e.g., \u003c -> <)
        try:
            if '\\u' in html:
                html = html.encode().decode('unicode_escape')
        except Exception:
            pass

        # Parse HTML
        soup = BeautifulSoup(html, 'html.parser')

        # Remove script, style, head, and meta elements
        for element in soup(['script', 'style', 'head', 'meta', 'link', 'title']):
            element.decompose()

        # Remove Word/Office XML comments (<!--[if gte mso 9]> ... <![endif]-->)
        import re as regex_module
        for comment in soup.find_all(string=lambda t: t and ('<!--' in str(t) or 'mso' in str(t).lower())):
            if hasattr(comment, 'extract'):
                comment.extract()

        # Get text content
        text = soup.get_text(separator='\n')

        # Clean up the text
        lines = []
        for line in text.split('\n'):
            # Strip whitespace
            line = line.strip()
            # Skip empty lines and lines with only special characters
            if line and not re.match(r'^[\s\-_=\*]+$', line):
                # Skip Word style definitions and XML artifacts
                if not any(skip in line.lower() for skip in [
                    'mso-', '@font-face', 'font-family:', '{', '}',
                    '<!--', '-->', 'xmlns:', 'xml:', 'style='
                ]):
                    lines.append(line)

        # Join lines and clean up multiple spaces/newlines
        text = '\n'.join(lines)

        # Remove multiple consecutive newlines
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Remove multiple spaces
        text = re.sub(r' {2,}', ' ', text)

        # Clean up common Word artifacts
        text = text.replace('\r\n', '\n')
        text = text.replace('\r', '\n')
        text = text.replace('\t', ' ')

        # Remove any remaining HTML entities
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&quot;', '"')
        text = text.replace('&#305;', 'i')  # Turkish dotless i
        text = text.replace('&nbsp;', ' ')

        return text.strip()

    def get_case_details(self, case_id: str, get_head_notes: bool = True,
                         get_full_description: bool = True) -> dict:
        """
        Get detailed case content

        Args:
            case_id: The case identifier (e.g., "2025S818")
            get_head_notes: Whether to fetch head notes
            get_full_description: Whether to fetch full case description

        Returns:
            Dictionary with head_notes and/or full_description
        """
        details = {}

        headers = {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': self.BASE_URL,
            'Referer': self.LOGIN_URL,
        }

        if get_head_notes:
            self._delay()
            try:
                response = self.session.post(
                    self.CASE_FILE_URL,
                    data={'caseName': case_id, 'headNotes': 1},
                    headers=headers,
                    timeout=self.timeout
                )
                if response.ok:
                    details['head_notes'] = self._clean_html_content(response.text)
                else:
                    details['head_notes'] = ''
            except Exception as e:
                logger.warning(f"Failed to get head notes for {case_id}: {e}")
                details['head_notes'] = ''

        if get_full_description:
            self._delay()
            try:
                response = self.session.post(
                    self.CASE_FILE_URL,
                    data={'caseName': case_id, 'headNotes': 0},
                    headers=headers,
                    timeout=self.timeout
                )
                if response.ok:
                    details['full_description'] = self._clean_html_content(response.text)
                else:
                    details['full_description'] = ''
            except Exception as e:
                logger.warning(f"Failed to get full description for {case_id}: {e}")
                details['full_description'] = ''

        return details
    
    def scrape_all(self, keywords: list = None, year: str = "200", 
                   output_file: str = "cases.csv", checkpoint_every: int = 100,
                   get_details: bool = True, max_cases: int = None) -> pd.DataFrame:
        """
        Scrape all cases matching criteria
        
        Args:
            keywords: List of keywords to search (if None, uses journals)
            year: Year filter
            output_file: CSV file to save results
            checkpoint_every: Save checkpoint every N cases
            get_details: Whether to fetch full case details
            max_cases: Maximum number of cases to scrape (None for unlimited)
            
        Returns:
            DataFrame with all scraped cases
        """
        if not self.is_logged_in:
            logger.error("Not logged in. Call login() first.")
            return pd.DataFrame()
        
        all_cases = []
        
        # Use journals as keywords if none provided
        if keywords is None:
            keywords = self.JOURNALS
        
        for keyword in keywords:
            logger.info(f"\n{'='*50}")
            logger.info(f"Searching for: {keyword}")
            logger.info(f"{'='*50}")
            
            row = 0
            keyword_cases = []
            
            while True:
                cases, total = self.search_cases(keyword=keyword, year=year, row=row)
                
                if not cases:
                    logger.info(f"No more results for '{keyword}' at row {row}")
                    break
                
                for case in cases:
                    case_id = case.get('case_id', '')
                    
                    # Skip duplicates
                    if case_id in self.processed_case_ids:
                        continue
                    
                    self.processed_case_ids.add(case_id)
                    
                    # Get detailed content if requested
                    if get_details and case_id:
                        logger.info(f"Fetching details for case: {case_id}")
                        details = self.get_case_details(case_id)
                        case.update(details)
                    
                    # Add metadata
                    case['scraped_at'] = datetime.now().isoformat()
                    case['search_keyword'] = keyword
                    
                    keyword_cases.append(case)
                    all_cases.append(case)
                    
                    # Check max cases limit
                    if max_cases and len(all_cases) >= max_cases:
                        logger.info(f"Reached max_cases limit: {max_cases}")
                        break
                    
                    # Checkpoint save
                    if len(all_cases) % checkpoint_every == 0:
                        self._save_checkpoint(all_cases, output_file)
                
                # Check if we should stop
                if max_cases and len(all_cases) >= max_cases:
                    break
                
                # Check if we've reached the end
                if len(cases) < 50:
                    logger.info(f"Reached end of results for '{keyword}'")
                    break
                
                row += 50
                logger.info(f"Moving to row {row}...")
            
            logger.info(f"Scraped {len(keyword_cases)} cases for '{keyword}'")
        
        # Final save
        df = pd.DataFrame(all_cases)
        df.to_csv(output_file, index=False, encoding='utf-8-sig')
        logger.info(f"\nTotal cases scraped: {len(all_cases)}")
        logger.info(f"Saved to: {output_file}")
        
        return df
    
    def _save_checkpoint(self, cases: list, output_file: str):
        """Save checkpoint to file"""
        checkpoint_file = output_file.replace('.csv', '_checkpoint.csv')
        df = pd.DataFrame(cases)
        df.to_csv(checkpoint_file, index=False, encoding='utf-8-sig')
        logger.info(f"Checkpoint saved: {len(cases)} cases to {checkpoint_file}")
    
    def load_checkpoint(self, checkpoint_file: str) -> set:
        """
        Load processed case IDs from checkpoint file
        
        Args:
            checkpoint_file: Path to checkpoint CSV
            
        Returns:
            Set of processed case IDs
        """
        if os.path.exists(checkpoint_file):
            df = pd.read_csv(checkpoint_file)
            self.processed_case_ids = set(df['case_id'].dropna().tolist())
            logger.info(f"Loaded {len(self.processed_case_ids)} processed cases from checkpoint")
            return self.processed_case_ids
        return set()


def main():
    """Main entry point"""

    # Configuration from environment variables
    USERNAME = os.environ.get("PLS_USERNAME", "")
    PASSWORD = os.environ.get("PLS_PASSWORD", "")

    if not USERNAME or not PASSWORD:
        print("Set PLS_USERNAME and PLS_PASSWORD environment variables")
        print("Or use dashboard.py for the web interface")
        return

    # Initialize scraper
    scraper = PakistanLawScraper(
        username=USERNAME,
        password=PASSWORD,
        delay_range=(1.5, 3.0)  # Be polite to the server
    )
    
    # Login
    if not scraper.login():
        logger.error("Failed to login. Trying with manual cookies...")
        # You can manually set cookies here if auto-login fails
        # scraper.set_cookies(
        #     session_id="your_session_id_here",
        #     verification_token="your_token_here"
        # )
    
    # Option 1: Scrape with specific keyword (for testing)
    print("\n" + "="*60)
    print("PAKISTAN LAW SITE SCRAPER")
    print("="*60)
    print("\nOptions:")
    print("1. Test scrape (100 cases with keyword 'contract')")
    print("2. Full scrape (all journals, all years)")
    print("3. Custom scrape")
    
    choice = input("\nEnter choice (1/2/3): ").strip()
    
    if choice == "1":
        # Test scrape
        df = scraper.scrape_all(
            keywords=['contract'],
            year='5',  # Last 5 years
            output_file='test_cases.csv',
            get_details=True,
            max_cases=100
        )
        print(f"\nTest complete! Scraped {len(df)} cases.")
        
    elif choice == "2":
        # Full scrape
        df = scraper.scrape_all(
            keywords=None,  # Uses all journals
            year='200',  # All years
            output_file='all_cases.csv',
            get_details=True,
            checkpoint_every=50
        )
        print(f"\nFull scrape complete! Scraped {len(df)} cases.")
        
    elif choice == "3":
        # Custom scrape
        keywords_input = input("Enter keywords (comma-separated) or press Enter for all journals: ").strip()
        keywords = [k.strip() for k in keywords_input.split(',')] if keywords_input else None
        
        year_input = input("Enter year option (5/10/15/20/200 for all): ").strip() or "200"
        max_input = input("Enter max cases (or press Enter for unlimited): ").strip()
        max_cases = int(max_input) if max_input else None
        
        df = scraper.scrape_all(
            keywords=keywords,
            year=year_input,
            output_file='custom_cases.csv',
            get_details=True,
            max_cases=max_cases
        )
        print(f"\nCustom scrape complete! Scraped {len(df)} cases.")
    
    else:
        print("Invalid choice")


if __name__ == "__main__":
    main()

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
from bs4 import BeautifulSoup, Comment
import pandas as pd
import time
import random
import re
import json
import os
from datetime import datetime
from urllib.parse import urljoin
import logging
import threading
from queue import Queue, Empty

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


class SessionExpiredError(Exception):
    """Raised when the session has expired and re-authentication is needed"""
    pass


class EmptyContentError(Exception):
    """Raised when server returns HTTP 200 but content is empty/too short"""
    pass


class FieldFetchError(Exception):
    """Raised when a single field (headnotes or description) fetch fails"""
    def __init__(self, field, case_id, reason):
        self.field = field
        self.case_id = case_id
        super().__init__(f"{field} fetch failed for {case_id}: {reason}")


class PakistanLawScraper:
    """Scraper for Pakistan Law Site"""
    
    BASE_URL = "https://www.pakistanlawsite.com"
    LOGIN_URL = f"{BASE_URL}/Login/MainPage"   # GET - login page (for CSRF token)
    LOGIN_POST_URL = f"{BASE_URL}/Login/Login"  # POST - actual form action
    SEARCH_URL = f"{BASE_URL}/Login/SearchCaseLaw"
    LOAD_MORE_URL = f"{BASE_URL}/Login/LoadMoreCaseLaw"  # Pagination endpoint
    CASE_FILE_URL = f"{BASE_URL}/Login/GetCaseFile"
    INDEX_SEARCH_URL = f"{BASE_URL}/Login/IndexSearch"

    # Journal name -> POST value mapping for IndexSearch
    # Note: PLCN has a space in its POST value ("PLC N")
    JOURNAL_POST_VALUES = {
        'PLD': 'PLD',
        'SCMR': 'SCMR',
        'CLC': 'CLC',
        'CLD': 'CLD',
        'YLR': 'YLR',
        'PCrLJ': 'PCrLJ',
        'PLC': 'PLC',
        'PLC(CS)': 'PLC(CS)',
        'PTD': 'PTD',
        'MLD': 'MLD',
        'GBLR': 'GBLR',
        'CLCN': 'CLCN',
        'YLRN': 'YLRN',
        'PCRLJN': 'PCRLJN',
        'PLCN': 'PLC N',
        'PLC(CS)N': 'PLC(CS)N',
    }
    INDEX_JOURNALS = list(JOURNAL_POST_VALUES.keys())
    YEAR_RANGE_START = 1947
    YEAR_RANGE_END = 2026

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
    
    def __init__(self, username: str, password: str, delay_range: tuple = (0.1, 0.3), timeout: int = 30):
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
        self._backoff_until = 0
        self._backoff_extra = 0
        self._min_interval = 0.15
        self._last_request_time = 0

        # Last login attempt diagnostics (for debugging Railway failures)
        self.last_login_diag = {}

        # Set up session headers (browser-like to reduce WAF false positives)
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Cache-Control': 'max-age=0',
            'Upgrade-Insecure-Requests': '1',
            'Connection': 'keep-alive',
        })
    
    def _throttle(self):
        """Enforce minimum interval between requests with adaptive backoff"""
        now = time.time()
        elapsed = now - self._last_request_time
        # Use delay_range for actual throttling (was previously ignoring it)
        target_delay = random.uniform(self.delay_range[0], self.delay_range[1])
        wait = target_delay - elapsed
        if time.time() < self._backoff_until:
            wait = max(wait, self._backoff_extra)
        if wait > 0:
            time.sleep(wait)
        self._last_request_time = time.time()

    def _handle_response_status(self, response, context="request"):
        """Check HTTP response status and raise on errors instead of swallowing.

        - 429/503: adds backoff and raises
        - 401/403: raises SessionExpiredError
        - Other non-OK: raises RuntimeError
        - Clears backoff when requests succeed
        """
        if response.ok:
            # Clear backoff on success
            if time.time() >= self._backoff_until and self._backoff_extra > 0:
                self._backoff_extra = 0
            return

        if response.status_code in (429, 503):
            self._backoff_extra = min(self._backoff_extra + 5, 30)
            self._backoff_until = time.time() + 60
            logger.warning(f"Rate limited ({response.status_code}) during {context}, backoff +{self._backoff_extra}s for 60s")
            raise RuntimeError(f"HTTP {response.status_code} rate limited during {context}")

        if response.status_code in (401, 403):
            raise SessionExpiredError(f"HTTP {response.status_code} during {context}")

        raise RuntimeError(f"HTTP {response.status_code} during {context}")
    
    def login(self) -> bool:
        """
        Login to Pakistan Law Site with retry and diagnostics.

        Retries up to 2 times with exponential backoff (5s, 15s).
        Stores diagnostic info in self.last_login_diag for debugging.

        Returns:
            True if login successful, False otherwise
        """
        max_attempts = 3
        backoff_delays = [0, 5, 15]  # seconds before each attempt

        for attempt in range(max_attempts):
            if backoff_delays[attempt] > 0:
                logger.info(f"Login retry {attempt}/{max_attempts-1}, waiting {backoff_delays[attempt]}s...")
                time.sleep(backoff_delays[attempt])

            diag = {
                'attempt': attempt + 1,
                'timestamp': datetime.now().isoformat(),
                'username': self.username,
                'csrf_found': False,
                'post_status': None,
                'post_url': None,
                'post_response_snippet': '',
                'cookies_received': {},
                'error': None,
                'verified': False,
            }

            try:
                # Fresh session on retry to clear stale cookies/state
                if attempt > 0:
                    logger.info("Creating fresh session for retry...")
                    old_headers = dict(self.session.headers)
                    self.session = requests.Session()
                    self.session.headers.update(old_headers)

                logger.info(f"Attempting login as {self.username} (attempt {attempt+1}/{max_attempts})...")

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
                    diag['csrf_found'] = True
                else:
                    # Fail fast — server will reject login without CSRF token
                    diag['error'] = 'No CSRF token found on login page'
                    diag['post_response_snippet'] = response.text[:500]
                    logger.error(f"No CSRF token found (attempt {attempt+1}). Login page snippet: {response.text[:300]}")
                    self.last_login_diag = diag
                    continue  # retry

                # Step 2: Submit login form
                login_data = {
                    'Login.UserName': self.username,
                    'Login.Password': self.password,
                    '__RequestVerificationToken': csrf_token,
                }

                headers = {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Origin': self.BASE_URL,
                    'Referer': self.LOGIN_URL,
                }

                logger.info("Submitting login credentials...")
                response = self.session.post(
                    self.LOGIN_POST_URL,   # /Login/Login (actual form action)
                    data=login_data,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=True
                )

                # Step 3: Diagnostic logging
                diag['post_status'] = response.status_code
                diag['post_url'] = str(response.url)
                diag['post_response_snippet'] = response.text[:500]
                diag['cookies_received'] = {k: v[:20] + '...' if len(v) > 20 else v
                                             for k, v in self.session.cookies.get_dict().items()}

                logger.info(f"Login POST response: status={response.status_code}, url={response.url}")
                logger.info(f"Cookies after login: {list(self.session.cookies.get_dict().keys())}")
                logger.debug(f"Response snippet: {response.text[:500]}")

                # Step 4: Check HTTP status — fail fast on clear errors
                if response.status_code in (401, 403):
                    diag['error'] = f'HTTP {response.status_code} — access denied by server (possible WAF/IP block)'
                    logger.error(f"Login blocked: HTTP {response.status_code}. Response: {response.text[:300]}")
                    self.last_login_diag = diag
                    continue  # retry
                elif response.status_code == 429:
                    diag['error'] = f'HTTP 429 — rate limited by server'
                    logger.error(f"Login rate limited (429). Response: {response.text[:300]}")
                    self.last_login_diag = diag
                    continue  # retry
                elif response.status_code >= 500:
                    diag['error'] = f'HTTP {response.status_code} — server error'
                    logger.error(f"Login server error: HTTP {response.status_code}. Response: {response.text[:300]}")
                    self.last_login_diag = diag
                    continue  # retry

                # Step 5: Check response content for known error indicators
                response_lower = response.text.lower()
                for indicator in ['invalid username', 'invalid password', 'account locked',
                                  'temporarily blocked', 'access denied', 'too many']:
                    if indicator in response_lower:
                        diag['error'] = f'Server response contains "{indicator}"'
                        logger.error(f"Login rejected: found '{indicator}' in response. Snippet: {response.text[:300]}")
                        self.last_login_diag = diag
                        break
                if diag['error']:
                    continue  # retry

                # Step 6: Check cookies were set
                session_cookie = self.session.cookies.get('ASP.NET_SessionId')
                if session_cookie:
                    logger.info(f"Session cookie obtained: {session_cookie[:10]}...")
                else:
                    logger.warning("No session cookie received after login POST")

                # Step 7: Verify by trying a test search
                logger.info("Verifying login with test search...")
                if self._verify_login():
                    diag['verified'] = True
                    diag['error'] = None
                    self.last_login_diag = diag
                    self.is_logged_in = True
                    logger.info("Login verified successfully!")
                    return True

                diag['error'] = 'Search verification failed — login POST may have been silently rejected'
                logger.error(f"Login verification failed (attempt {attempt+1}). POST was {response.status_code} but search returned no results.")
                self.last_login_diag = diag

            except requests.exceptions.Timeout:
                diag['error'] = 'Request timed out'
                logger.error(f"Login timed out (attempt {attempt+1})")
                self.last_login_diag = diag
            except requests.exceptions.RequestException as e:
                diag['error'] = f'Network error: {e}'
                logger.error(f"Login network error (attempt {attempt+1}): {e}")
                self.last_login_diag = diag
            except Exception as e:
                diag['error'] = f'Unexpected error: {e}'
                logger.error(f"Login unexpected error (attempt {attempt+1}): {e}")
                self.last_login_diag = diag

        # All attempts failed
        logger.error(f"Login failed after {max_attempts} attempts. Last diagnostics: {self.last_login_diag}")
        logger.error("Please provide manual cookies using set_cookies()")
        self.is_logged_in = False
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
        self._throttle()

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
            self._handle_response_status(response, context=f"initial_search {keyword}")
            return self._parse_search_results(response.text, keyword)

        except SessionExpiredError:
            raise  # Let caller handle re-authentication
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
            self._handle_response_status(response, context=f"load_more {keyword} row {row}")
            return self._parse_search_results(response.text, keyword)

        except SessionExpiredError:
            raise  # Let caller handle re-authentication
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
        # Detect session expiry: server returns a full HTML login page instead of results
        if ('<html' in html.lower()
                and 'caseLawTable' not in html
                and 'Citation Name' not in html):
            raise SessionExpiredError(
                "Session expired - received login page instead of search results")

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
        # Special case: "PLC N" journal has a space, e.g.: 2025 PLC N 123 LAHORE-HIGH-COURT

        # Known two-word journal abbreviations (journal name contains a space)
        TWO_WORD_JOURNALS = {'PLC N'}

        parts = citation.split()

        # Check for two-word journal match first
        if len(parts) >= 3:
            candidate = f"{parts[1]} {parts[2]}"
            if candidate in TWO_WORD_JOURNALS:
                result['year'] = parts[0]
                result['journal'] = candidate
                result['page'] = parts[3] if len(parts) > 3 else ''
                result['court'] = ' '.join(parts[4:]) if len(parts) > 4 else ''
                return result

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
    
    def _parse_index_results(self, html: str) -> list:
        """
        Parse index search results HTML (archivedpatientGrid table format)

        Args:
            html: Raw HTML response from IndexSearch

        Returns:
            List of case dicts

        Raises:
            SessionExpiredError: If the response indicates session expiry
        """
        # Detect session expiry: full HTML page without expected table
        if '<html' in html.lower() and 'archivedpatientGrid' not in html:
            raise SessionExpiredError("Session expired - received login page instead of results")

        soup = BeautifulSoup(html, 'html.parser')
        cases = []

        rows = soup.find_all('tr', class_='caseType')

        for row in rows:
            try:
                cells = row.find_all('td')
                if len(cells) < 5:
                    continue

                case = {}

                # Cell 1: citation text
                citation_text = cells[1].get_text(strip=True)
                case['citation'] = citation_text

                # Parse citation into components
                parsed = self._parse_citation(citation_text)
                case.update(parsed)

                # Cell 2: parties (split on VS, ignore <br> and <span>)
                parties_text = cells[2].get_text(separator=' ', strip=True)
                # Clean up extra whitespace
                parties_text = re.sub(r'\s+', ' ', parties_text).strip()
                case['parties_full'] = parties_text

                if ' VS ' in parties_text.upper():
                    parts = re.split(r'\s+VS\s+', parties_text, flags=re.IGNORECASE)
                    case['petitioner'] = parts[0].strip() if len(parts) > 0 else ''
                    case['respondent'] = parts[1].strip() if len(parts) > 1 else ''
                else:
                    case['petitioner'] = parties_text
                    case['respondent'] = ''

                # Cell 3: court
                case['court'] = cells[3].get_text(strip=True)

                # Cell 4: case_id from input button's casetypeid attribute
                button = cells[4].find('input', attrs={'casetypeid': True})
                if button:
                    case['case_id'] = button.get('casetypeid', '')
                else:
                    # Skip rows without a case_id
                    continue

                cases.append(case)

            except Exception as e:
                logger.warning(f"Failed to parse index row: {e}")
                continue

        logger.info(f"Parsed {len(cases)} cases from index results")
        return cases

    def index_search(self, year: int, book: str, court: str = "") -> list:
        """
        Search the citation index for a specific journal + year combination

        Args:
            year: The year to search (e.g., 2024)
            book: Journal name (e.g., 'PLD', 'PLCN')
            court: Optional court filter

        Returns:
            List of case dicts
        """
        self._throttle()

        # Map journal name to POST value
        post_book = self.JOURNAL_POST_VALUES.get(book, book)

        data = {
            'year': str(year),
            'book': post_book,
            'court': court,
        }

        headers = {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': self.BASE_URL,
            'Referer': self.LOGIN_URL,
        }

        try:
            response = self.session.post(
                self.INDEX_SEARCH_URL,
                data=data,
                headers=headers,
                timeout=self.timeout
            )
            self._handle_response_status(response, context=f"index_search {book} {year}")
            return self._parse_index_results(response.text)

        except SessionExpiredError:
            raise  # Let caller handle re-auth
        except requests.exceptions.Timeout:
            logger.error(f"Index search timed out for {book} {year}")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Index search failed for {book} {year}: {e}")
            return []

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
        # NOTE: html.encode().decode('unicode_escape') is NOT used here because
        # it corrupts non-ASCII characters (e.g., Urdu/Arabic text in cases).
        # Instead, we use regex to replace only \uXXXX sequences safely.
        try:
            if '\\u' in html:
                html = re.sub(r'\\u([0-9a-fA-F]{4})',
                              lambda m: chr(int(m.group(1), 16)), html)
        except Exception:
            pass

        # Parse HTML
        soup = BeautifulSoup(html, 'html.parser')

        # Remove script, style, head, and meta elements
        for element in soup(['script', 'style', 'head', 'meta', 'link', 'title']):
            element.decompose()

        # Remove HTML comments (includes Word/Office XML like <!--[if gte mso 9]>...<![endif]-->)
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()
        # Also remove text nodes containing Word style markers (mso-*)
        for node in soup.find_all(string=lambda t: t and 'mso' in str(t).lower()):
            if hasattr(node, 'extract'):
                node.extract()

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

    def _check_case_response(self, response, case_id, field):
        """Check if a GetCaseFile response indicates session expiry.

        Raises SessionExpiredError if the server returned a login page,
        an auth-related HTTP status, or an empty body (session lost).
        """
        if not response.ok and response.status_code in (401, 403):
            raise SessionExpiredError(f"HTTP {response.status_code} fetching {field} for {case_id}")
        if response.ok and not response.text.strip():
            raise SessionExpiredError(f"Empty response body for {field} of {case_id} — session likely expired")
        if response.ok and '<html' in response.text.lower() and 'Login' in response.text:
            raise SessionExpiredError(f"Login page returned instead of {field} for {case_id}")

    def _fetch_single_field(self, case_id: str, field: str, headers: dict) -> str:
        """Fetch a single field (head_notes or full_description) for a case.

        Args:
            case_id: Case identifier
            field: 'head_notes' or 'full_description'
            headers: HTTP headers dict

        Returns:
            Cleaned text content

        Raises:
            FieldFetchError: On any non-auth failure (HTTP error, empty content, network)
            SessionExpiredError: On authentication issues (propagated immediately)
        """
        head_notes_flag = 1 if field == 'head_notes' else 0
        self._throttle()
        try:
            response = self.session.post(
                self.CASE_FILE_URL,
                data={'caseName': case_id, 'headNotes': head_notes_flag},
                headers=headers,
                timeout=self.timeout
            )
            self._check_case_response(response, case_id, field)
            self._handle_response_status(response, context=f"{field} for {case_id}")
            cleaned = self._clean_html_content(response.text)
            if len(cleaned) < 10:
                raise FieldFetchError(field, case_id, f"content too short ({len(cleaned)} chars)")
            return cleaned
        except (SessionExpiredError, FieldFetchError):
            raise
        except Exception as e:
            raise FieldFetchError(field, case_id, str(e))

    def get_case_details(self, case_id: str, get_head_notes: bool = True,
                         get_full_description: bool = True) -> dict:
        """
        Get detailed case content using concurrent fetching.

        Fetches headnotes and description in parallel using threads.
        Returns partial results when possible (e.g. headnotes OK but description failed).

        Args:
            case_id: The case identifier (e.g., "2025S818")
            get_head_notes: Whether to fetch head notes
            get_full_description: Whether to fetch full case description

        Returns:
            Dictionary with head_notes and/or full_description keys
            (only keys with actual content are included)

        Raises:
            SessionExpiredError: If session has expired (always re-raised immediately)
            EmptyContentError: Only if ALL requested fields failed
            FieldFetchError: If a single field failed (caller can retry just that field)
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        headers = {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': self.BASE_URL,
            'Referer': self.LOGIN_URL,
        }

        fields_to_fetch = []
        if get_head_notes:
            fields_to_fetch.append('head_notes')
        if get_full_description:
            fields_to_fetch.append('full_description')

        if not fields_to_fetch:
            return {}

        details = {}
        failed_fields = []
        session_error = None

        if len(fields_to_fetch) == 1:
            # Single field — no need for thread pool
            field = fields_to_fetch[0]
            try:
                details[field] = self._fetch_single_field(case_id, field, headers)
            except SessionExpiredError:
                raise
            except FieldFetchError:
                failed_fields.append(field)
        else:
            # Two fields — fetch concurrently
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_to_field = {
                    executor.submit(self._fetch_single_field, case_id, field, headers): field
                    for field in fields_to_fetch
                }
                for future in as_completed(future_to_field):
                    field = future_to_field[future]
                    try:
                        details[field] = future.result()
                    except SessionExpiredError as e:
                        session_error = e
                    except FieldFetchError:
                        failed_fields.append(field)

            # Propagate session errors immediately
            if session_error:
                raise session_error

        # If ALL requested fields failed, raise EmptyContentError
        if len(failed_fields) == len(fields_to_fetch):
            raise EmptyContentError(f"All fields empty for {case_id}: {failed_fields}")

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
                try:
                    cases, total = self.search_cases(keyword=keyword, year=year, row=row)
                except SessionExpiredError:
                    logger.warning(f"Session expired searching '{keyword}' at row {row}, re-authenticating...")
                    if not self._try_reauth():
                        logger.error(f"Re-auth failed, skipping remaining results for '{keyword}'")
                        break
                    # Retry same row after successful re-auth
                    try:
                        cases, total = self.search_cases(keyword=keyword, year=year, row=row)
                    except Exception as e:
                        logger.error(f"Retry after re-auth also failed for '{keyword}': {e}")
                        break

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
                        best_details = {}
                        need_head = True
                        need_desc = True
                        for _attempt in range(3):
                            try:
                                details = self.get_case_details(
                                    case_id,
                                    get_head_notes=need_head,
                                    get_full_description=need_desc
                                )
                                for k, v in details.items():
                                    if v and (k not in best_details or not best_details[k]):
                                        best_details[k] = v
                                # Update what's still needed
                                if 'head_notes' in best_details:
                                    need_head = False
                                if 'full_description' in best_details:
                                    need_desc = False
                                if not need_head and not need_desc:
                                    break  # All fields obtained
                            except SessionExpiredError:
                                logger.warning(f"Session expired fetching details for {case_id}, re-authenticating...")
                                if not self._try_reauth():
                                    logger.error(f"Re-auth failed, skipping details for {case_id}")
                                    break
                            except (EmptyContentError, FieldFetchError):
                                backoff = 5 * (2 ** _attempt)
                                logger.warning(f"Field fetch issue for {case_id}, attempt {_attempt+1}/3, backing off {backoff}s...")
                                time.sleep(backoff)
                            except Exception as e:
                                logger.warning(f"Failed to get details for {case_id}: {e}")
                                break
                        if best_details:
                            case.update(best_details)

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

    # ===== Citation Index Scrape Methods =====

    def _load_progress(self, progress_file: str) -> dict:
        """Load or create index scrape progress file"""
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load progress file, creating new: {e}")

        return {
            'version': 1,
            'started_at': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'output_file': '',
            'total_combinations': len(self.INDEX_JOURNALS) * (self.YEAR_RANGE_END - self.YEAR_RANGE_START + 1),
            'completed_count': 0,
            'total_cases_found': 0,
            'journals': {}
        }

    def _save_progress(self, progress_file: str, data: dict):
        """Atomically save progress file (write to .tmp then rename)"""
        data['last_updated'] = datetime.now().isoformat()
        tmp_file = progress_file + '.tmp'
        try:
            with open(tmp_file, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_file, progress_file)
        except Exception as e:
            logger.error(f"Failed to save progress: {e}")

    def _append_cases_to_csv(self, cases: list, output_file: str):
        """Append cases to CSV file (creates with header if new)"""
        if not cases:
            return

        df = pd.DataFrame(cases)
        write_header = not os.path.exists(output_file) or os.path.getsize(output_file) == 0
        df.to_csv(output_file, mode='a', header=write_header, index=False, encoding='utf-8-sig')

    def _reload_processed_ids(self, output_file: str) -> set:
        """Load case_id column from existing CSV for deduplication"""
        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            try:
                df = pd.read_csv(output_file, usecols=['case_id'])
                ids = set(df['case_id'].dropna().astype(str).tolist())
                logger.info(f"Loaded {len(ids)} existing case IDs from {output_file}")
                return ids
            except Exception as e:
                logger.warning(f"Failed to load existing case IDs: {e}")
        return set()

    def _try_reauth(self) -> bool:
        """Attempt to re-authenticate using stored credentials"""
        logger.info("Attempting re-authentication...")
        try:
            if self.login():
                logger.info("Re-authentication successful")
                return True
        except Exception as e:
            logger.error(f"Re-authentication failed: {e}")
        return False

    def scrape_all_index(self, output_file: str = 'all_cases_index.csv',
                         progress_file: str = 'index_progress.json',
                         get_details: bool = True,
                         journals: list = None, year_start: int = None,
                         year_end: int = None,
                         on_progress=None, should_stop=None,
                         on_case_scraped=None,
                         db=None, num_workers=1) -> int:
        """
        Scrape all cases using citation index search for 100% coverage

        Args:
            output_file: CSV file to append results to (ignored when db is set)
            progress_file: JSON file tracking completion state (ignored when db is set)
            get_details: Whether to fetch head_notes and full_description
            journals: List of journal names (None = all 16)
            year_start: First year to scrape (None = YEAR_RANGE_START)
            year_end: Last year to scrape (None = YEAR_RANGE_END)
            on_progress: Callback(progress_data) called after each combo
            should_stop: Callback() returning True to stop early
            on_case_scraped: Callback(count) called after each case
            db: Database module (db.py). When set, uses DB instead of CSV/JSON.
            num_workers: Number of concurrent scraper threads (>1 requires db)

        Returns:
            Total number of new cases scraped
        """
        if not self.is_logged_in:
            logger.error("Not logged in. Call login() first.")
            return 0

        journals = journals or self.INDEX_JOURNALS
        year_start = year_start or self.YEAR_RANGE_START
        year_end = year_end or self.YEAR_RANGE_END

        # Concurrent mode: multiple workers with DB
        if num_workers > 1 and db:
            return self._scrape_index_concurrent(
                journals=journals, year_start=year_start, year_end=year_end,
                get_details=get_details, db=db, on_progress=on_progress,
                should_stop=should_stop, on_case_scraped=on_case_scraped,
                num_workers=num_workers
            )

        # Load progress — DB or JSON file
        if db:
            db.reset_in_progress()
            progress = db.get_progress()
            processed_ids = db.get_processed_ids()
        else:
            progress = self._load_progress(progress_file)
            progress['output_file'] = output_file
            processed_ids = self._reload_processed_ids(output_file)
            # Reset any in_progress -> pending (crash recovery)
            for journal_key in progress.get('journals', {}):
                for year_key in progress['journals'][journal_key]:
                    if progress['journals'][journal_key][year_key].get('status') == 'in_progress':
                        progress['journals'][journal_key][year_key]['status'] = 'pending'

        total_new_cases = 0
        failed_inserts = 0

        # Recalculate total_combinations based on actual scope
        progress['total_combinations'] = len(journals) * (year_end - year_start + 1)

        # Recount completed
        completed_count = 0
        for journal_key in progress.get('journals', {}):
            for year_key in progress['journals'].get(journal_key, {}):
                if progress['journals'][journal_key][year_key].get('status') == 'completed':
                    completed_count += 1
        progress['completed_count'] = completed_count

        if not db:
            self._save_progress(progress_file, progress)

        last_request_time = time.time()

        for journal in journals:
            if should_stop and should_stop():
                logger.info("Stop requested, halting index scrape")
                break

            if journal not in progress['journals']:
                progress['journals'][journal] = {}

            for year in range(year_start, year_end + 1):
                if should_stop and should_stop():
                    break

                year_str = str(year)

                # Skip completed combos
                combo_status = progress['journals'].get(journal, {}).get(year_str, {})
                if combo_status.get('status') == 'completed':
                    continue

                # Mark in_progress
                progress['journals'][journal][year_str] = {
                    'status': 'in_progress',
                    'cases_found': 0
                }
                if db:
                    db.update_progress(journal, year_str, 'in_progress')
                else:
                    self._save_progress(progress_file, progress)

                # Check if we need to re-verify login (>15min since last request)
                if time.time() - last_request_time > 900:
                    logger.info("Checking session validity (>15min idle)...")
                    if not self._verify_login():
                        if not self._try_reauth():
                            progress['journals'][journal][year_str] = {
                                'status': 'error',
                                'error_message': 'Session expired, re-auth failed'
                            }
                            if db:
                                db.update_progress(journal, year_str, 'error', error_message='Session expired, re-auth failed')
                            else:
                                self._save_progress(progress_file, progress)
                            continue

                # Retry logic
                cases = None
                last_error = None
                for attempt in range(3):
                    try:
                        cases = self.index_search(year, journal)
                        last_request_time = time.time()
                        break
                    except SessionExpiredError:
                        logger.warning(f"Session expired during {journal} {year}, re-authenticating...")
                        if self._try_reauth():
                            continue
                        else:
                            last_error = 'Session expired, re-auth failed'
                            break
                    except Exception as e:
                        last_error = str(e)
                        backoff = 10 * (attempt + 1)
                        logger.warning(f"Attempt {attempt+1}/3 failed for {journal} {year}: {e}, retrying in {backoff}s")
                        time.sleep(backoff)

                if cases is None:
                    progress['journals'][journal][year_str] = {
                        'status': 'error',
                        'error_message': last_error or 'Unknown error'
                    }
                    if db:
                        db.update_progress(journal, year_str, 'error', error_message=last_error or 'Unknown error')
                    else:
                        self._save_progress(progress_file, progress)
                    if on_progress:
                        on_progress(progress)
                    continue

                # Process cases
                new_cases = []
                stopped_mid_combo = False
                for case in cases:
                    # Check stop before expensive detail fetch
                    if should_stop and should_stop():
                        stopped_mid_combo = True
                        break

                    case_id = case.get('case_id', '')
                    if not case_id or case_id in processed_ids:
                        continue

                    processed_ids.add(case_id)

                    # Fetch details if requested (with targeted retry)
                    if get_details:
                        best_details = {}
                        need_head = True
                        need_desc = True
                        for _attempt in range(3):
                            try:
                                details = self.get_case_details(
                                    case_id,
                                    get_head_notes=need_head,
                                    get_full_description=need_desc
                                )
                                for k, v in details.items():
                                    if v and (k not in best_details or not best_details[k]):
                                        best_details[k] = v
                                last_request_time = time.time()
                                if 'head_notes' in best_details:
                                    need_head = False
                                if 'full_description' in best_details:
                                    need_desc = False
                                if not need_head and not need_desc:
                                    break
                            except SessionExpiredError:
                                logger.warning(f"Session expired fetching details for {case_id}, re-authenticating...")
                                if not self._try_reauth():
                                    logger.error(f"Re-auth failed, skipping details for {case_id}")
                                    break
                            except (EmptyContentError, FieldFetchError):
                                backoff = 5 * (2 ** _attempt)
                                logger.warning(f"Field fetch issue for {case_id}, attempt {_attempt+1}/3, backing off {backoff}s...")
                                time.sleep(backoff)
                            except Exception as e:
                                logger.warning(f"Failed to get details for {case_id}: {e}")
                                break
                        if best_details:
                            case.update(best_details)

                    case['scraped_at'] = datetime.now().isoformat()
                    case['source'] = 'index_search'
                    case['search_journal'] = journal
                    case['search_year'] = year

                    # Save immediately to DB (zero data loss on crash)
                    if db:
                        try:
                            if not db.insert_case(case):
                                failed_inserts += 1
                        except Exception as e:
                            logger.error(f"DB insert failed for {case_id}: {e}")
                            failed_inserts += 1

                    new_cases.append(case)
                    total_new_cases += 1

                    if on_case_scraped:
                        on_case_scraped(total_new_cases)

                # Save cases to CSV (DB cases already saved per-case above)
                if not db:
                    self._append_cases_to_csv(new_cases, output_file)

                if stopped_mid_combo:
                    # Mark as pending so it resumes correctly next time
                    progress['journals'][journal][year_str] = {
                        'status': 'pending',
                        'cases_found': 0
                    }
                    if db:
                        db.update_progress(journal, year_str, 'pending')
                    else:
                        self._save_progress(progress_file, progress)
                    logger.info(f"Stop requested mid-combo {journal} {year}, wrote {len(new_cases)} partial cases")
                    break

                # Mark completed
                progress['journals'][journal][year_str] = {
                    'status': 'completed',
                    'cases_found': len(cases)
                }
                progress['completed_count'] = completed_count + 1
                completed_count += 1
                progress['total_cases_found'] = progress.get('total_cases_found', 0) + len(cases)
                if db:
                    db.update_progress(journal, year_str, 'completed', cases_found=len(cases))
                else:
                    self._save_progress(progress_file, progress)

                logger.info(f"[{completed_count}/{progress['total_combinations']}] {journal} {year}: {len(cases)} found, {len(new_cases)} new")

                if on_progress:
                    on_progress(progress)

        if failed_inserts > 0:
            logger.warning(f"Index scrape finished with {failed_inserts} failed DB inserts")
        logger.info(f"Index scrape complete: {total_new_cases} new cases scraped")
        return total_new_cases

    def _scrape_index_concurrent(self, journals, year_start, year_end, get_details,
                                  db, on_progress, should_stop, on_case_scraped,
                                  num_workers):
        """Run index scrape with multiple concurrent workers (requires DB)"""
        from concurrent.futures import ThreadPoolExecutor

        # Reset in_progress from any previous crash
        db.reset_in_progress()

        # Load existing progress and build combo queue
        progress = db.get_progress()
        combo_queue = Queue()
        completed_count = 0
        total_combos = len(journals) * (year_end - year_start + 1)

        for journal in journals:
            for year in range(year_start, year_end + 1):
                year_str = str(year)
                combo_status = progress['journals'].get(journal, {}).get(year_str, {})
                if combo_status.get('status') == 'completed':
                    completed_count += 1
                else:
                    combo_queue.put((journal, year_str))

        pending = combo_queue.qsize()
        logger.info(f"Concurrent scrape: {pending} combos pending, {completed_count} already completed")

        if pending == 0:
            logger.info("All combos already completed")
            return 0

        # Load processed IDs (thread-safe with lock)
        processed_ids = db.get_processed_ids()
        ids_lock = threading.Lock()

        # Shared counters
        counters_lock = threading.Lock()
        counters = {'total_new': 0, 'completed': completed_count, 'failed_inserts': 0}

        # Create worker scrapers — reuse self as worker 0
        workers = [self]
        for i in range(1, num_workers):
            w = PakistanLawScraper(
                username=self.username,
                password=self.password,
                delay_range=self.delay_range,
                timeout=self.timeout
            )
            if w.login():
                workers.append(w)
                logger.info(f"Worker {i+1}/{num_workers} logged in")
            else:
                # Retry login once after 5s
                logger.warning(f"Worker {i+1}/{num_workers} login failed, retrying in 5s...")
                time.sleep(5)
                if w.login():
                    workers.append(w)
                    logger.info(f"Worker {i+1}/{num_workers} logged in on retry")
                else:
                    logger.error(f"Worker {i+1}/{num_workers} failed to login after retry, skipping")

        if len(workers) < num_workers:
            logger.warning(f"CAPACITY REDUCED: only {len(workers)}/{num_workers} workers logged in")
        logger.info(f"Running with {len(workers)} concurrent workers")

        def worker_fn(scraper, worker_id):
            last_request_time = time.time()

            while True:
                if should_stop and should_stop():
                    break

                try:
                    journal, year_str = combo_queue.get_nowait()
                except Empty:
                    break

                year = int(year_str)

                # Mark in_progress
                db.update_progress(journal, year_str, 'in_progress')

                # Check session validity (>15min idle)
                if time.time() - last_request_time > 900:
                    logger.info(f"W{worker_id}: Checking session validity...")
                    if not scraper._verify_login():
                        if not scraper._try_reauth():
                            db.update_progress(journal, year_str, 'error',
                                             error_message='Session expired, re-auth failed')
                            continue

                # Retry index_search
                cases = None
                last_error = None
                for attempt in range(3):
                    try:
                        cases = scraper.index_search(year, journal)
                        last_request_time = time.time()
                        break
                    except SessionExpiredError:
                        logger.warning(f"W{worker_id}: Session expired on {journal} {year_str}, re-auth...")
                        if scraper._try_reauth():
                            continue
                        else:
                            last_error = 'Session expired, re-auth failed'
                            break
                    except Exception as e:
                        last_error = str(e)
                        backoff = 10 * (attempt + 1)
                        logger.warning(f"W{worker_id}: Attempt {attempt+1}/3 for {journal} {year_str}: {e}, retry in {backoff}s")
                        time.sleep(backoff)

                if cases is None:
                    db.update_progress(journal, year_str, 'error',
                                     error_message=last_error or 'Unknown error')
                    continue

                # Process cases
                new_count = 0
                stopped = False

                for case in cases:
                    if should_stop and should_stop():
                        stopped = True
                        break

                    case_id = case.get('case_id', '')
                    if not case_id:
                        continue

                    with ids_lock:
                        if case_id in processed_ids:
                            continue
                        processed_ids.add(case_id)

                    if get_details:
                        best_details = {}
                        need_head = True
                        need_desc = True
                        for _attempt in range(3):
                            try:
                                details = scraper.get_case_details(
                                    case_id,
                                    get_head_notes=need_head,
                                    get_full_description=need_desc
                                )
                                for k, v in details.items():
                                    if v and (k not in best_details or not best_details[k]):
                                        best_details[k] = v
                                last_request_time = time.time()
                                if 'head_notes' in best_details:
                                    need_head = False
                                if 'full_description' in best_details:
                                    need_desc = False
                                if not need_head and not need_desc:
                                    break
                            except SessionExpiredError:
                                logger.warning(f"W{worker_id}: Session expired fetching details for {case_id}, re-authenticating...")
                                if not scraper._try_reauth():
                                    logger.error(f"W{worker_id}: Re-auth failed, skipping details for {case_id}")
                                    break
                            except (EmptyContentError, FieldFetchError):
                                backoff = 5 * (2 ** _attempt)
                                logger.warning(f"W{worker_id}: Field fetch issue for {case_id}, attempt {_attempt+1}/3, backing off {backoff}s...")
                                time.sleep(backoff)
                            except Exception as e:
                                logger.warning(f"W{worker_id}: Details failed for {case_id}: {e}")
                                break
                        if best_details:
                            case.update(best_details)

                    case['scraped_at'] = datetime.now().isoformat()
                    case['source'] = 'index_search'
                    case['search_journal'] = journal
                    case['search_year'] = year

                    # Save immediately to DB
                    insert_ok = False
                    try:
                        insert_ok = db.insert_case(case)
                    except Exception as e:
                        logger.error(f"W{worker_id}: DB insert failed for {case_id}: {e}")

                    if insert_ok:
                        new_count += 1
                    else:
                        with counters_lock:
                            counters['failed_inserts'] += 1

                    with counters_lock:
                        counters['total_new'] += 1
                        current_total = counters['total_new']

                    if on_case_scraped:
                        on_case_scraped(current_total)

                if stopped:
                    db.update_progress(journal, year_str, 'pending')
                    logger.info(f"W{worker_id}: Stopped mid-combo {journal} {year_str}, saved {new_count} cases")
                    break

                # Mark completed
                db.update_progress(journal, year_str, 'completed', cases_found=len(cases))

                with counters_lock:
                    counters['completed'] += 1
                    comp = counters['completed']

                logger.info(f"W{worker_id}: [{comp}/{total_combos}] {journal} {year_str}: {len(cases)} found, {new_count} new")

                if on_progress:
                    on_progress({
                        'completed_count': comp,
                        'total_combinations': total_combos,
                        'journals': {journal: {year_str: {'status': 'in_progress'}}}
                    })

        # Launch workers
        with ThreadPoolExecutor(max_workers=len(workers)) as executor:
            futures = [executor.submit(worker_fn, w, i) for i, w in enumerate(workers)]
            for f in futures:
                f.result()

        if counters['failed_inserts'] > 0:
            logger.warning(f"Concurrent scrape finished with {counters['failed_inserts']} failed DB inserts")
        logger.info(f"Concurrent scrape complete: {counters['total_new']} new cases")
        return counters['total_new']


def main():
    """Main entry point"""

    # Configuration from environment variables
    USERNAME = os.environ.get("PLS_USERNAME", "LHCBAR8")
    PASSWORD = os.environ.get("PLS_PASSWORD", "pakbar8")

    if not USERNAME or not PASSWORD:
        print("Set PLS_USERNAME and PLS_PASSWORD environment variables")
        print("Or use dashboard.py for the web interface")
        return

    # Initialize scraper
    scraper = PakistanLawScraper(
        username=USERNAME,
        password=PASSWORD,
        delay_range=(0.1, 0.3)
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

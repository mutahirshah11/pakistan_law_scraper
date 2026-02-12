#!/usr/bin/env python3
"""
Production Pakistan Law Scraper
================================
Async, concurrent scraper with Supabase persistence and crash recovery.

Two-phase architecture:
  Phase 1: Sequential search per keyword -> immediate DB writes
  Phase 2: Concurrent detail fetching (10-20 parallel) -> DB updates

Features:
  - asyncio + aiohttp for concurrency
  - Semaphore-based rate limiting
  - Exponential backoff retry (3x)
  - Auto re-authentication on session expiry
  - Exact-position resume on crash
  - Zero data loss (every case written to DB immediately)
"""

import asyncio
import json
import logging
import os
import random
import re
import signal
import time
from datetime import datetime, timezone

import aiohttp
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from db import SupabaseDB

load_dotenv()
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────

BASE_URL = "https://www.pakistanlawsite.com"
LOGIN_URL = f"{BASE_URL}/Login/Check"
SEARCH_URL = f"{BASE_URL}/Login/SearchCaseLaw"
LOAD_MORE_URL = f"{BASE_URL}/Login/LoadMoreCaseLaw"
CASE_FILE_URL = f"{BASE_URL}/Login/GetCaseFile"

JOURNALS = [
    "PLD", "SCMR", "CLC", "CLD", "YLR", "PCrLJ", "PLC", "PLC(CS)",
    "PTD", "MLD", "GBLR", "CLCN", "YLRN", "PCRLJN", "PLCN", "PLC(CS)N",
]

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

AJAX_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "Referer": LOGIN_URL,
}


class ProductionScraper:
    """Async production scraper with Supabase persistence."""

    def __init__(
        self,
        concurrency: int = 15,
        delay_range: tuple = (0.3, 0.8),
        timeout: int = 30,
        max_retries: int = 5,  # Increased for resilience against server errors
    ):
        self.concurrency = concurrency
        self.delay_range = delay_range
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.semaphore = asyncio.Semaphore(concurrency)

        self.db = SupabaseDB()
        self.session: aiohttp.ClientSession | None = None
        self.cookies: dict = {}
        self.auth_lock = asyncio.Lock()
        self.is_authenticated = False

        # Graceful shutdown
        self.should_stop = False

        # Stats (in-memory, also persisted to scrape_runs)
        self.run_id: int | None = None
        self.cases_found = 0
        self.cases_detailed = 0
        self.errors_count = 0
        self.last_error = ""
        self.start_time: float | None = None
        self.reauth_count = 0
        self.current_keyword = ""
        self.current_phase = ""

    # ── Lifecycle ────────────────────────────────────────────────

    async def start_session(self):
        """Create aiohttp session with default headers."""
        if self.session and not self.session.closed:
            await self.session.close()
        jar = aiohttp.CookieJar(unsafe=True)
        self.session = aiohttp.ClientSession(
            headers=DEFAULT_HEADERS,
            timeout=self.timeout,
            cookie_jar=jar,
        )

    async def close(self):
        """Close HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()

    def request_stop(self):
        """Signal graceful shutdown."""
        self.should_stop = True
        logger.info("Stop requested — finishing current batch...")

    # ── Authentication ───────────────────────────────────────────

    async def authenticate(self) -> bool:
        """Authenticate using cookies from config or env, then auto-login."""
        # Try saved cookies first
        config_path = os.path.join(os.path.dirname(__file__), "scraper_config.json")
        session_id = ""
        token = ""

        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                session_id = config.get("session_id", "")
                token = config.get("verification_token", "")
            except Exception:
                pass

        if session_id and token:
            self._set_cookies(session_id, token)
            if await self._verify_auth():
                logger.info("Authenticated with saved cookies")
                self.is_authenticated = True
                return True
            logger.warning("Saved cookies expired, trying auto-login...")

        # Auto-login
        username = os.environ.get("PLS_USERNAME", "")
        password = os.environ.get("PLS_PASSWORD", "")
        if username and password:
            if await self._auto_login(username, password):
                self.is_authenticated = True
                return True

        logger.error("Authentication failed. Set cookies in scraper_config.json or credentials in .env")
        self.is_authenticated = False
        return False

    async def _auto_login(self, username: str, password: str) -> bool:
        """Login with username/password, extract CSRF token."""
        try:
            # Get login page for CSRF token
            async with self.session.get(LOGIN_URL) as resp:
                html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")
            token_input = soup.find("input", {"name": "__RequestVerificationToken"})
            csrf = token_input.get("value", "") if token_input else ""

            # Submit login form
            data = {
                "UserName": username,
                "Password": password,
                "__RequestVerificationToken": csrf,
            }
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": BASE_URL,
                "Referer": LOGIN_URL,
            }
            async with self.session.post(LOGIN_URL, data=data, headers=headers) as resp:
                # Session cookies are automatically stored in the cookie jar
                pass

            if await self._verify_auth():
                logger.info("Auto-login successful")
                # Save cookies for future use
                self._save_session_cookies()
                return True

            logger.warning("Auto-login verification failed")
            return False
        except Exception as e:
            logger.error(f"Auto-login failed: {e}")
            return False

    def _set_cookies(self, session_id: str, token: str):
        """Set cookies on the aiohttp session."""
        self.cookies = {"session_id": session_id, "token": token}
        # Set cookies directly on the cookie jar
        from http.cookies import SimpleCookie
        from yarl import URL

        url = URL(BASE_URL)
        self.session.cookie_jar.update_cookies(
            {"ASP.NET_SessionId": session_id, "__RequestVerificationToken": token},
            url,
        )

    def _save_session_cookies(self):
        """Save current session cookies to config file."""
        try:
            from yarl import URL
            cookies = {}
            for cookie in self.session.cookie_jar:
                cookies[cookie.key] = cookie.value

            session_id = cookies.get("ASP.NET_SessionId", "")
            token = cookies.get("__RequestVerificationToken", "")
            if session_id:
                config_path = os.path.join(os.path.dirname(__file__), "scraper_config.json")
                config = {}
                if os.path.exists(config_path):
                    with open(config_path, "r") as f:
                        config = json.load(f)
                config["session_id"] = session_id
                if token:
                    config["verification_token"] = token
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save session cookies: {e}")

    async def _verify_auth(self) -> bool:
        """Verify authentication by attempting a small test search."""
        try:
            data = {
                "year": "5",
                "book": "PLD",
                "code": "",
                "court": "",
                "searchType": "caselaw",
                "judge": "",
                "lawyer": "",
                "party": "",
                "Row": 0,
            }
            async with self.session.post(SEARCH_URL, data=data, headers=AJAX_HEADERS) as resp:
                text = await resp.text()
                return "caseLawTable" in text or "Citation Name" in text
        except Exception as e:
            logger.warning(f"Auth verification failed: {e}")
            return False

    async def _check_and_reauth(self, response_text: str = "", status: int = 200) -> bool:
        """Detect session expiry and re-authenticate. Returns True if re-authed."""
        needs_reauth = False
        if status in (302, 401, 403):
            needs_reauth = True
        elif status == 200 and response_text:
            # Check if redirected to login page
            if "Login" in response_text[:500] and "caseLawTable" not in response_text:
                needs_reauth = True

        if not needs_reauth:
            return False

        async with self.auth_lock:
            # Double-check — another coroutine may have already re-authed
            if await self._verify_auth():
                return True
            logger.warning("Session expired — re-authenticating...")
            self.reauth_count += 1
            success = await self.authenticate()
            if success:
                logger.info("Re-authentication successful")
            else:
                logger.error("Re-authentication FAILED")
            return success

    # ── HTTP with Retry ──────────────────────────────────────────

    async def _request_with_retry(
        self, method: str, url: str, data: dict = None, params: dict = None
    ) -> str:
        """Make HTTP request with exponential backoff retry."""
        headers = AJAX_HEADERS if data else {"X-Requested-With": "XMLHttpRequest", "Referer": LOGIN_URL}

        for attempt in range(self.max_retries):
            if self.should_stop:
                return ""
            try:
                # Random delay between requests
                await asyncio.sleep(random.uniform(*self.delay_range))

                if method == "POST":
                    async with self.session.post(url, data=data, headers=headers) as resp:
                        status = resp.status
                        text = await resp.text()
                elif method == "GET":
                    async with self.session.get(url, params=params, headers=headers) as resp:
                        status = resp.status
                        text = await resp.text()
                else:
                    return ""

                if status == 200:
                    # Check for stealth redirect to login page
                    if "caseLawTable" not in text and "Login" in text[:500] and len(text) < 2000:
                        reauthed = await self._check_and_reauth(text, status)
                        if reauthed:
                            continue
                    return text

                if status == 429:
                    # Rate limited — back off more aggressively
                    wait = 2 ** (attempt + 2) + random.uniform(1, 3)
                    logger.warning(f"Rate limited (429). Waiting {wait:.1f}s...")
                    await asyncio.sleep(wait)
                    continue

                if status >= 500:
                    # Server error — back off and retry
                    wait = 2 ** (attempt + 1) + random.uniform(2, 5)
                    logger.warning(f"HTTP {status} for {url}. Waiting {wait:.1f}s before retry...")
                    await asyncio.sleep(wait)
                    continue

                if status in (302, 401, 403):
                    reauthed = await self._check_and_reauth(text, status)
                    if reauthed:
                        continue
                    return ""

                logger.warning(f"HTTP {status} for {url}")

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Request error (attempt {attempt + 1}): {e}")
            except Exception as e:
                logger.error(f"Unexpected error (attempt {attempt + 1}): {e}")

            # Exponential backoff: 1s, 2s, 4s
            backoff = 2 ** attempt + random.uniform(0, 1)
            await asyncio.sleep(backoff)

        self.errors_count += 1
        self.last_error = f"Max retries for {url}"
        return ""

    # ── Parsing (reused from original scraper) ───────────────────

    @staticmethod
    def _parse_search_results(html: str) -> tuple:
        """Parse search HTML -> (list of case dicts, total_count)."""
        soup = BeautifulSoup(html, "html.parser")
        cases = []
        seen_ids = set()  # Track seen case_ids for deduplication

        # Total count
        total_count = 0
        count_text = soup.find("p", string=re.compile(r"Your Search returned total"))
        if count_text:
            match = re.search(r"total\s+(\d+)\s+records", count_text.get_text())
            if match:
                total_count = int(match.group(1))
        else:
            count_span = soup.find("span", style=re.compile("color.*red"))
            if count_span:
                try:
                    total_count = int(count_span.get_text().strip())
                except ValueError:
                    pass

        # Case tables
        tables = soup.find_all("table", class_="caseLawTable")
        tables_on_page = len(tables)  # Track original count for pagination
        
        for table in tables:
            try:
                case = ProductionScraper._parse_case_table(table)
                if case and case.get("case_id"):
                    # Deduplicate by case_id
                    case_id = case["case_id"]
                    if case_id not in seen_ids:
                        seen_ids.add(case_id)
                        cases.append(case)
            except Exception as e:
                logger.warning(f"Failed to parse case table: {e}")

        # Return: (cases, total_count, tables_on_page)
        return cases, total_count, tables_on_page

    @staticmethod
    def _parse_case_table(table) -> dict | None:
        """Parse a single case table from search results."""
        rows = table.find_all("tr")
        if len(rows) < 5:
            return None

        case = {}

        # Row 0: Citation and Case ID
        citation_cell = rows[0].find("td")
        if citation_cell:
            bookmark = citation_cell.find("span", class_="bookmarklogo")
            if bookmark:
                case["case_id"] = bookmark.get("casename", "")

            citation_text = citation_cell.get_text(strip=True)
            citation_text = re.sub(r"Bookmark this Case.*", "", citation_text).strip()
            citation_text = citation_text.replace("Citation Name:", "").strip()
            case["citation"] = citation_text

            # Parse citation components
            parts = citation_text.split()
            if len(parts) >= 4:
                case["year"] = parts[0]
                case["journal"] = parts[1]
                case["page"] = parts[2]
                case["court"] = " ".join(parts[3:])
            elif len(parts) == 3:
                case["year"] = parts[0]
                case["journal"] = parts[1]
                case["page"] = parts[2]
            elif len(parts) == 2:
                case["year"] = parts[0]
                case["journal"] = parts[1]

        # Row 1: Parties
        if len(rows) > 1:
            parties_cell = rows[1].find("td")
            if parties_cell:
                parties_text = parties_cell.get_text(strip=True)
                case["parties_full"] = parties_text
                
                # Handle edge cases (e.g., Presidential References with no parties)
                if parties_text.upper().strip() in ("VS", "V", ""):
                    case["petitioner"] = ""
                    case["respondent"] = ""
                    case["parties_full"] = "(No parties - Special Reference)"
                elif " VS " in parties_text.upper():
                    parts = re.split(r"\s+VS\s+", parties_text, flags=re.IGNORECASE)
                    case["petitioner"] = parts[0].strip() if len(parts) > 0 else ""
                    case["respondent"] = parts[1].strip() if len(parts) > 1 else ""
                else:
                    case["petitioner"] = parties_text
                    case["respondent"] = ""

        # Row 4: Summary and Keywords
        if len(rows) > 4:
            summary_cell = rows[4].find("td")
            if summary_cell:
                summary_text = summary_cell.get_text(strip=True)
                case["summary"] = summary_text
                
                # Extract keywords from summary (format: "Section---Keyword1---Keyword2---...")
                # Keywords are separated by "---" in the legal summary
                keywords_list = []
                if "---" in summary_text:
                    parts = summary_text.split("---")
                    for part in parts[1:]:  # Skip first part (usually section reference)
                        # Clean up the keyword
                        keyword = part.strip()
                        # Stop at long text (actual content starts)
                        if len(keyword) > 50:
                            break
                        if keyword and not keyword[0].isdigit():
                            keywords_list.append(keyword)
                case["keywords"] = ", ".join(keywords_list[:5])  # Max 5 keywords

        return case

    @staticmethod
    def _clean_html_content(html: str) -> str:
        """Clean HTML content from Microsoft Word format."""
        if not html:
            return ""
        if html.startswith('"') and html.endswith('"'):
            try:
                html = json.loads(html)
            except json.JSONDecodeError:
                pass
        try:
            if "\\u" in html:
                html = html.encode().decode("unicode_escape")
        except Exception:
            pass

        soup = BeautifulSoup(html, "html.parser")
        for el in soup(["script", "style", "head", "meta", "link", "title"]):
            el.decompose()

        text = soup.get_text(separator="\n")
        lines = []
        for line in text.split("\n"):
            line = line.strip()
            if line and not re.match(r"^[\s\-_=\*]+$", line):
                if not any(
                    skip in line.lower()
                    for skip in ["mso-", "@font-face", "font-family:", "{", "}", "<!--", "-->", "xmlns:", "xml:", "style="]
                ):
                    lines.append(line)

        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#305;", "i").replace("&nbsp;", " ")
        return text.strip()

    # ── Phase 1: Search & Collect ────────────────────────────────

    async def phase1_search(self, keywords: list, year: str):
        """Sequential search per keyword — immediate DB writes."""
        self.current_phase = "search"
        logger.info(f"Phase 1: Searching {len(keywords)} keywords, year={year}")

        for keyword in keywords:
            if self.should_stop:
                break

            self.current_keyword = keyword
            progress = self.db.get_progress(keyword, year)
            start_row = progress["last_row"] if progress else 0
            phase = progress["phase"] if progress else "search"
            total_found = progress["total_found"] if progress else 0
            cases_found_kw = progress["cases_found"] if progress else 0

            if phase == "done":
                logger.info(f"[{keyword}] Already completed, skipping search phase")
                continue

            # Check if we stopped prematurely (have more cases to fetch)
            # This happens when total_found >> cases_found
            if phase == "details" and total_found > 0 and cases_found_kw < total_found * 0.9:
                logger.info(
                    f"[{keyword}] Incomplete scrape detected: {cases_found_kw}/{total_found} cases. "
                    f"Continuing search from row {start_row}..."
                )
                phase = "search"  # Reset to search phase to continue

            if start_row > 0:
                logger.info(f"[{keyword}] Resuming from row {start_row} ({cases_found_kw} cases so far, {total_found} total on server)")
            else:
                logger.info(f"[{keyword}] Starting fresh search")

            row = start_row
            consecutive_failures = 0

            while not self.should_stop:
                # Use different endpoint for row=0 vs pagination
                if row == 0:
                    html = await self._request_with_retry(
                        "POST",
                        SEARCH_URL,
                        data={
                            "year": year,
                            "book": keyword,
                            "code": "",
                            "court": "",
                            "searchType": "caselaw",
                            "judge": "",
                            "lawyer": "",
                            "party": "",
                            "Row": 0,
                        },
                    )
                else:
                    html = await self._request_with_retry(
                        "GET",
                        LOAD_MORE_URL,
                        params={
                            "book": keyword,
                            "row": row,
                            "year": year,
                            "caseTypeId": 0,
                        },
                    )

                if not html:
                    # Empty response could be server error or end of results
                    # Try skipping this row and continuing to next
                    consecutive_failures += 1
                    self.errors_count += 1
                    if consecutive_failures >= 3:  # Allow up to 3 consecutive failures
                        logger.warning(f"[{keyword}] Too many consecutive empty responses, stopping at row {row}")
                        break
                    logger.warning(f"[{keyword}] Empty response at row {row}, skipping to next row...")
                    row += 50  # Skip to next row
                    continue

                cases, total, tables_on_page = self._parse_search_results(html)
                if total > 0:
                    total_found = total
                
                # Reset consecutive failure counter on successful response
                consecutive_failures = 0

                if not cases:
                    logger.info(f"[{keyword}] No more results at row {row}")
                    break

                # Add metadata
                for c in cases:
                    c["search_keyword"] = keyword
                    c["scraped_at"] = datetime.now(timezone.utc).isoformat()
                    c["detail_status"] = "pending"

                # Batch upsert to Supabase
                inserted = self.db.upsert_cases_batch(cases)
                cases_found_kw += inserted
                self.cases_found += inserted

                # Update progress
                next_row = row + 50
                self.db.update_progress(
                    keyword, year,
                    last_row=next_row,
                    total_found=total_found,
                    phase="search",
                    cases_found=cases_found_kw,
                )

                # Update run stats
                if self.run_id:
                    self.db.update_run(
                        self.run_id,
                        cases_scraped=self.cases_found,
                        errors_count=self.errors_count,
                        last_error=self.last_error or None,
                    )

                logger.info(
                    f"[{keyword}] Row {row}: +{len(cases)} unique cases "
                    f"({tables_on_page} on page, {cases_found_kw} total for keyword, {total_found} on server)"
                )

                # Use original table count for pagination, not deduped count
                # This fixes the bug where deduplication caused early termination
                if tables_on_page < 50:
                    logger.info(f"[{keyword}] Reached end of results (page had {tables_on_page} < 50 cases)")
                    break

                row = next_row

            # Mark search phase done for this keyword
            if not self.should_stop:
                self.db.update_progress(keyword, year, phase="details")
                logger.info(f"[{keyword}] Search complete: {cases_found_kw} cases found")

        if not self.should_stop:
            logger.info(f"Phase 1 complete: {self.cases_found} total cases in DB")

    # ── Phase 2: Fetch Details (Concurrent) ──────────────────────

    async def phase2_fetch_details(self):
        """Concurrent detail fetching from pending cases."""
        self.current_phase = "details"
        logger.info(f"Phase 2: Fetching details (concurrency={self.concurrency})")

        batch_num = 0
        while not self.should_stop:
            pending = self.db.get_pending_details(limit=500)
            if not pending:
                logger.info("No more pending details to fetch")
                break

            batch_num += 1
            logger.info(f"Detail batch #{batch_num}: {len(pending)} cases")

            tasks = [self._fetch_detail(case_id) for case_id in pending]
            await asyncio.gather(*tasks)

            # Update run stats
            if self.run_id:
                self.db.update_run(
                    self.run_id,
                    cases_detailed=self.cases_detailed,
                    errors_count=self.errors_count,
                    last_error=self.last_error or None,
                )

        logger.info(f"Phase 2 complete: {self.cases_detailed} cases detailed")

    async def _fetch_detail(self, case_id: str):
        """Fetch head_notes + full_description for one case, with semaphore."""
        if self.should_stop:
            return

        async with self.semaphore:
            try:
                # Fetch head notes
                head_html = await self._request_with_retry(
                    "POST",
                    CASE_FILE_URL,
                    data={"caseName": case_id, "headNotes": 1},
                )
                head_notes = self._clean_html_content(head_html) if head_html else ""

                # Fetch full description
                desc_html = await self._request_with_retry(
                    "POST",
                    CASE_FILE_URL,
                    data={"caseName": case_id, "headNotes": 0},
                )
                full_desc = self._clean_html_content(desc_html) if desc_html else ""

                if head_notes or full_desc:
                    self.db.update_detail_status(case_id, "fetched", head_notes, full_desc)
                    self.cases_detailed += 1
                else:
                    self.db.update_detail_status(case_id, "failed")
                    self.errors_count += 1
                    self.last_error = f"Empty details for {case_id}"

            except Exception as e:
                logger.error(f"Detail fetch failed for {case_id}: {e}")
                self.db.update_detail_status(case_id, "failed")
                self.errors_count += 1
                self.last_error = str(e)[:200]

    # ── Main Entry Point ─────────────────────────────────────────

    async def run(
        self,
        keywords: list = None,
        year: str = "200",
        skip_search: bool = False,
        skip_details: bool = False,
        reset_progress: bool = False,
    ):
        """Full scrape run: authenticate -> search -> fetch details.
        
        Args:
            keywords: List of journal keywords to search (defaults to all JOURNALS)
            year: Year filter (5, 10, 15, 20, or 200 for all)
            skip_search: If True, skip Phase 1 (search) and only do Phase 2 (details)
            skip_details: If True, skip Phase 2 (details) and only do Phase 1 (search)
            reset_progress: If True, clear progress tracking and start fresh
        """
        self.start_time = time.time()
        self.should_stop = False

        if keywords is None:
            keywords = JOURNALS

        # Reset progress if requested (allows fresh start)
        if reset_progress:
            logger.info("Resetting progress - starting fresh scrape")
            self.db.reset_progress()

        logger.info(f"Starting production scrape: {len(keywords)} keywords, year={year}")
        logger.info(f"Concurrency={self.concurrency}, delay={self.delay_range}")

        await self.start_session()

        # Authenticate
        if not await self.authenticate():
            logger.error("Cannot proceed without authentication")
            await self.close()
            return

        # Create scrape run record
        self.run_id = self.db.create_run(
            config={
                "keywords": keywords,
                "year": year,
                "concurrency": self.concurrency,
                "skip_search": skip_search,
                "skip_details": skip_details,
            }
        )

        try:
            # Phase 1: Search
            if not skip_search:
                await self.phase1_search(keywords, year)

            # Phase 2: Details
            if not skip_details and not self.should_stop:
                await self.phase2_fetch_details()

            # Mark run complete
            status = "completed" if not self.should_stop else "paused"
            if self.run_id:
                elapsed = time.time() - self.start_time
                self.db.update_run(
                    self.run_id,
                    status=status,
                    total_cases=self.cases_found,
                    cases_scraped=self.cases_found,
                    cases_detailed=self.cases_detailed,
                    errors_count=self.errors_count,
                )

            elapsed = time.time() - self.start_time
            logger.info(
                f"Scrape {status}: "
                f"{self.cases_found} found, {self.cases_detailed} detailed, "
                f"{self.errors_count} errors, "
                f"{elapsed / 60:.1f} minutes, "
                f"{self.reauth_count} re-auths"
            )

        except Exception as e:
            logger.error(f"Scrape failed: {e}")
            if self.run_id:
                self.db.update_run(self.run_id, status="failed", last_error=str(e)[:500])
            raise
        finally:
            await self.close()

    # ── Status for Dashboard ─────────────────────────────────────

    def get_live_status(self) -> dict:
        """Return current scraper status for dashboard polling."""
        elapsed = time.time() - self.start_time if self.start_time else 0
        rate = self.cases_found / (elapsed / 60) if elapsed > 60 else 0

        return {
            "running": not self.should_stop and self.start_time is not None,
            "phase": self.current_phase,
            "keyword": self.current_keyword,
            "cases_found": self.cases_found,
            "cases_detailed": self.cases_detailed,
            "errors": self.errors_count,
            "last_error": self.last_error,
            "elapsed_minutes": round(elapsed / 60, 1),
            "rate_per_minute": round(rate, 1),
            "reauth_count": self.reauth_count,
        }

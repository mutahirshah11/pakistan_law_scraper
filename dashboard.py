#!/usr/bin/env python3
"""
Web Dashboard for Pakistan Law Scraper
=======================================
Simple HTML/CSS dashboard to start, stop, and monitor the scraper.

Run: python dashboard.py
Open: http://localhost:5000
"""

import os
import json
import logging
import threading
import time
from datetime import datetime
from flask import Flask, render_template_string, jsonify, request, send_file
from scraper import PakistanLawScraper, SessionExpiredError, EmptyContentError

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Database layer — only active when DATABASE_URL is set
_db = None
_db_error = ""
try:
    import db as database
    _db_url = os.environ.get("DATABASE_URL", "")
    if _db_url:
        database.init_tables()
        _db = database
    else:
        _db_error = "DATABASE_URL not set — running in CSV-only mode"
except Exception as e:
    _db_error = f"DB init failed: {e}"

if _db_error:
    print(f"[WARNING] {_db_error}")

# Config file for persistent storage
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'scraper_config.json')


class ScraperState:
    """Global scraper state"""
    scraper = None
    is_running = False
    should_stop = False
    thread = None
    cases_scraped = 0
    total_cases = 0
    current_keyword = ""
    errors = []
    start_time = None
    last_case_id = ""

    # Index mode state
    mode = 'keyword'  # 'keyword' or 'index'
    current_journal = ""
    current_year = ""
    combos_completed = 0
    combos_total = 0
    progress_file = 'index_progress.json'
    num_workers = 3

    # Index scope (None = all)
    index_journals = None   # None = all journals
    index_year_start = None # None = 1947
    index_year_end = None   # None = 2026

    # Auto-restart settings
    auto_restart = True         # enabled by default
    restart_count = 0           # times auto-restarted in current session
    max_restarts = 5            # cap to prevent infinite restart loops
    restart_delay = 30          # seconds to wait before auto-restart

    # Settings
    keywords = ['contract']
    year = '5'
    max_cases = 50
    output_file = 'scraped_cases.csv'
    get_details = True


state = ScraperState()


def load_config():
    """Load saved configuration including cookies"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_config(config):
    """Save configuration to file"""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def setup_scraper():
    """Initialize scraper — auto-login first, saved cookies as fallback"""
    config = load_config()
    username = config.get('username', os.environ.get('PLS_USERNAME', 'LHCBAR8'))

    state.scraper = PakistanLawScraper(
        username=username,
        password=config.get('password', os.environ.get('PLS_PASSWORD', 'pakbar8')),
        delay_range=(1.5, 3.0)
    )

    # Primary: auto-login with credentials
    if state.scraper.login():
        return True, f"Logged in as {username}"

    # Fallback: saved cookies
    session_id = config.get('session_id', '')
    token = config.get('verification_token', '')
    if session_id and token:
        state.scraper.set_cookies(session_id, token)
        if state.scraper._verify_login():
            return True, "Logged in with saved cookies"
        state.scraper.is_logged_in = False

    state.scraper.is_logged_in = False
    return False, "Auto-login failed - check credentials in .env"


def scrape_worker():
    """Background scraping worker"""
    crashed = False
    try:
        all_cases = []

        for keyword in state.keywords:
            if state.should_stop:
                break

            state.current_keyword = keyword
            row = 0

            # Get initial count
            _, total = state.scraper.search_cases(keyword=keyword, year=state.year, row=0)
            state.total_cases = total

            while not state.should_stop:
                cases, _ = state.scraper.search_cases(
                    keyword=keyword,
                    year=state.year,
                    row=row
                )

                if not cases:
                    break

                for case in cases:
                    if state.should_stop:
                        break

                    case_id = case.get('case_id', '')
                    if case_id in state.scraper.processed_case_ids:
                        continue

                    state.scraper.processed_case_ids.add(case_id)
                    state.last_case_id = case_id

                    if state.get_details and case_id:
                        best_details = {}
                        for _attempt in range(3):
                            try:
                                details = state.scraper.get_case_details(case_id)
                                for k, v in details.items():
                                    if v and (k not in best_details or not best_details[k]):
                                        best_details[k] = v
                                break  # Full success
                            except SessionExpiredError:
                                state.errors.append(f"{case_id}: Session expired, re-authenticating...")
                                if not state.scraper._try_reauth():
                                    state.errors.append(f"{case_id}: Re-auth failed")
                                    break
                            except EmptyContentError:
                                backoff = 5 * (2 ** _attempt)
                                state.errors.append(f"{case_id}: Empty content, attempt {_attempt+1}/3, backing off {backoff}s...")
                                time.sleep(backoff)
                            except Exception as e:
                                state.errors.append(f"{case_id}: {str(e)[:50]}")
                                break
                        if best_details:
                            case.update(best_details)

                    case['scraped_at'] = datetime.now().isoformat()
                    case['search_keyword'] = keyword
                    all_cases.append(case)
                    state.cases_scraped = len(all_cases)

                    if state.max_cases and len(all_cases) >= state.max_cases:
                        state.should_stop = True
                        break

                if len(cases) < 50:
                    break

                row += 50

        # Save results
        if all_cases:
            import pandas as pd
            df = pd.DataFrame(all_cases)
            df.to_csv(state.output_file, index=False, encoding='utf-8-sig')

    except Exception as e:
        state.errors.append(f"Fatal: {str(e)}")
        crashed = True
    finally:
        state.is_running = False
        if crashed and not state.should_stop and state.auto_restart and state.restart_count < state.max_restarts:
            _schedule_restart()


def index_scrape_worker():
    """Background worker for index-based scraping"""
    crashed = False
    try:
        def on_progress(progress_data):
            state.combos_completed = progress_data.get('completed_count', 0)
            state.combos_total = progress_data.get('total_combinations', 0)
            # Extract current journal/year from in_progress entries
            for journal in progress_data.get('journals', {}):
                for year_str, info in progress_data['journals'][journal].items():
                    if info.get('status') == 'in_progress':
                        state.current_journal = journal
                        state.current_year = year_str

        def on_case_scraped(count):
            state.cases_scraped = count

        def should_stop():
            return state.should_stop

        state.scraper.scrape_all_index(
            output_file=state.output_file,
            progress_file=state.progress_file,
            get_details=state.get_details,
            journals=state.index_journals,
            year_start=state.index_year_start,
            year_end=state.index_year_end,
            on_progress=on_progress,
            should_stop=should_stop,
            on_case_scraped=on_case_scraped,
            db=_db,
            num_workers=state.num_workers
        )

    except Exception as e:
        state.errors.append(f"Fatal: {str(e)}")
        crashed = True
    finally:
        state.is_running = False
        if crashed and not state.should_stop and state.auto_restart and state.restart_count < state.max_restarts:
            _schedule_restart()


def _schedule_restart():
    """Schedule an auto-restart after a delay."""
    state.restart_count += 1
    delay = state.restart_delay
    logger.info(f"Auto-restart {state.restart_count}/{state.max_restarts} scheduled in {delay}s...")
    state.errors.append(f"Auto-restart {state.restart_count}/{state.max_restarts} in {delay}s...")

    def _do_restart():
        if state.should_stop or state.is_running:
            return  # User stopped or already restarted manually

        # Re-authenticate if needed
        if state.scraper and not state.scraper._verify_login():
            if not state.scraper._try_reauth():
                state.errors.append("Auto-restart failed: re-auth failed")
                return

        # Re-launch the worker
        state.is_running = True
        state.should_stop = False
        if state.mode == 'index':
            state.thread = threading.Thread(target=index_scrape_worker, daemon=True)
        else:
            state.thread = threading.Thread(target=scrape_worker, daemon=True)
        state.thread.start()
        logger.info(f"Auto-restart {state.restart_count}: scraper resumed")

    timer = threading.Timer(delay, _do_restart)
    timer.daemon = True
    timer.start()


# HTML Template
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pakistan Law Scraper</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a; color: #e2e8f0; min-height: 100vh; padding: 20px;
        }
        .container { max-width: 1100px; margin: 0 auto; }
        header { text-align: center; padding: 24px 0; border-bottom: 1px solid #334155; margin-bottom: 24px; }
        h1 { font-size: 26px; color: #38bdf8; }
        .section-title { font-size: 13px; color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }

        /* KPI Cards Row */
        .kpi-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 20px; }
        .kpi-card {
            background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155;
            text-align: center;
        }
        .kpi-card .kpi-value { font-size: 36px; font-weight: 700; color: #f8fafc; line-height: 1.1; }
        .kpi-card .kpi-label { color: #64748b; font-size: 12px; margin-top: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
        .kpi-card .kpi-sub { color: #94a3b8; font-size: 13px; margin-top: 4px; }
        .kpi-card .kpi-bar { height: 6px; background: #334155; border-radius: 3px; margin-top: 10px; overflow: hidden; }
        .kpi-card .kpi-bar-fill { height: 100%; background: linear-gradient(90deg, #38bdf8, #22c55e); transition: width 0.5s; }

        /* Two-column rows */
        .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
        .card {
            background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155;
        }
        .card-full { grid-column: 1 / -1; }

        /* Status section */
        .status-indicator { display: flex; align-items: center; gap: 10px; font-size: 22px; font-weight: 600; }
        .status-dot { width: 12px; height: 12px; border-radius: 50%; }
        .status-dot.running { background: #22c55e; box-shadow: 0 0 10px #22c55e; animation: pulse 2s infinite; }
        .status-dot.stopped { background: #64748b; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
        .current-task { color: #38bdf8; font-size: 14px; margin-top: 8px; }
        .status-details { margin-top: 12px; font-size: 13px; color: #94a3b8; line-height: 1.8; }
        .status-details span { color: #f8fafc; font-weight: 600; }

        /* Controls */
        .controls { display: flex; gap: 10px; flex-wrap: wrap; }
        button {
            padding: 12px 20px; border: none; border-radius: 8px; font-size: 14px;
            font-weight: 600; cursor: pointer; transition: all 0.2s;
        }
        .btn-start { background: #22c55e; color: white; }
        .btn-start:hover { background: #16a34a; }
        .btn-stop { background: #ef4444; color: white; }
        .btn-stop:hover { background: #dc2626; }
        .btn-download { background: #a855f7; color: white; }
        .btn-download:hover { background: #9333ea; }
        .btn-reset { background: #991b1b; color: #fca5a5; border: 1px solid #dc2626; }
        .btn-reset:hover { background: #b91c1c; }
        .btn-disabled { background: #334155 !important; color: #64748b !important; cursor: not-allowed !important; border-color: #334155 !important; }
        .btn-save { background: #38bdf8; color: #0f172a; }
        .btn-save:hover { background: #0ea5e9; }
        .controls-row { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
        .controls-row label { font-size: 13px; color: #94a3b8; white-space: nowrap; }
        .controls-row input[type="number"] {
            width: 60px; padding: 8px; background: #0f172a; border: 1px solid #334155;
            border-radius: 6px; color: #e2e8f0; font-size: 14px;
        }
        .controls-row input[type="checkbox"] { width: auto; }

        /* Journal bars */
        .journal-bars { margin-top: 8px; }
        .journal-bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
        .journal-bar-label { width: 70px; text-align: right; font-size: 12px; color: #94a3b8; font-weight: 600; }
        .journal-bar-track { flex: 1; height: 20px; background: #334155; border-radius: 4px; overflow: hidden; position: relative; }
        .journal-bar-fill { height: 100%; background: linear-gradient(90deg, #38bdf8, #818cf8); border-radius: 4px; transition: width 0.5s; min-width: 2px; }
        .journal-bar-count { font-size: 11px; color: #94a3b8; width: 50px; text-align: right; }
        .latest-ts { margin-top: 10px; font-size: 12px; color: #64748b; }

        /* Matrix */
        .matrix-container { overflow-x: auto; margin-top: 12px; }
        .matrix-table { border-collapse: collapse; font-size: 10px; width: 100%; }
        .matrix-table th { padding: 3px 2px; color: #94a3b8; font-weight: normal; text-align: center; position: sticky; top: 0; background: #1e293b; }
        .matrix-table td { padding: 0; text-align: center; }
        .matrix-table .journal-label { text-align: right; padding-right: 6px; color: #94a3b8; font-weight: 600; white-space: nowrap; }
        .matrix-cell { width: 10px; height: 10px; margin: 1px auto; border-radius: 2px; }
        .matrix-cell.pending { background: #334155; }
        .matrix-cell.in_progress { background: #eab308; box-shadow: 0 0 4px #eab308; }
        .matrix-cell.completed { background: #22c55e; }
        .matrix-cell.error { background: #ef4444; }
        .matrix-legend { display: flex; gap: 16px; margin-top: 10px; font-size: 12px; color: #94a3b8; }
        .matrix-legend span { display: flex; align-items: center; gap: 4px; }
        .legend-dot { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }

        /* Auth + Errors */
        .form-group { margin-bottom: 12px; }
        .form-group label { display: block; font-size: 13px; color: #94a3b8; margin-bottom: 4px; }
        input[type="text"] {
            width: 100%; padding: 9px 12px; background: #0f172a; border: 1px solid #334155;
            border-radius: 6px; color: #e2e8f0; font-size: 14px;
        }
        input:focus { outline: none; border-color: #38bdf8; }
        .error-log { background: #0f172a; border-radius: 6px; padding: 12px; max-height: 180px; overflow-y: auto; font-family: monospace; font-size: 12px; }
        .error-item { color: #f87171; padding: 3px 0; border-bottom: 1px solid #1e293b; }
        .no-errors { color: #22c55e; }

        /* DB Warning Banner */
        .db-banner {
            background: #451a03; border: 1px solid #92400e; border-radius: 8px; padding: 10px 16px;
            margin-bottom: 16px; display: none; font-size: 13px; color: #fbbf24;
        }
        .db-banner .db-banner-title { font-weight: 600; margin-bottom: 2px; }
        .db-banner .db-banner-detail { color: #f59e0b; font-size: 12px; }

        /* Toast */
        .toast { position: fixed; bottom: 20px; right: 20px; padding: 12px 20px; background: #22c55e; color: white; border-radius: 8px; font-size: 14px; opacity: 0; transform: translateY(20px); transition: all 0.3s; }
        .toast.show { opacity: 1; transform: translateY(0); }
        .toast.error { background: #ef4444; }

        @media (max-width: 768px) {
            .kpi-row { grid-template-columns: repeat(2, 1fr) !important; }
            .two-col { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Pakistan Law Scraper</h1>
        </header>

        <!-- DB Warning Banner -->
        <div class="db-banner" id="dbBanner">
            <div class="db-banner-title">DB not connected — showing live session counts only</div>
            <div class="db-banner-detail" id="dbBannerDetail"></div>
        </div>

        <!-- KPI Cards Row -->
        <div class="kpi-row">
            <div class="kpi-card">
                <div class="kpi-value" id="kpiTotal">--</div>
                <div class="kpi-label">Total Cases</div>
                <div class="kpi-sub" id="kpiTotalSub"></div>
            </div>
            <div class="kpi-card">
                <div class="kpi-value" id="kpiRate">--</div>
                <div class="kpi-label">Cases / Hour</div>
                <div class="kpi-sub" id="kpiRateSub"></div>
            </div>
            <div class="kpi-card">
                <div class="kpi-value" id="kpiCompletion">--%</div>
                <div class="kpi-label">Completion</div>
                <div class="kpi-sub" id="kpiCompletionSub">headnotes present</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-value" id="kpiWithDesc">--</div>
                <div class="kpi-label">With Description</div>
                <div class="kpi-bar"><div class="kpi-bar-fill" id="kpiWithDescBar" style="width:0%"></div></div>
                <div class="kpi-sub" id="kpiWithDescSub"></div>
            </div>
            <div class="kpi-card">
                <div class="kpi-value" id="kpiNoHead">--</div>
                <div class="kpi-label">Without Headnotes</div>
                <div class="kpi-bar"><div class="kpi-bar-fill" id="kpiNoHeadBar" style="width:0%;background:linear-gradient(90deg,#ef4444,#f97316);"></div></div>
                <div class="kpi-sub" id="kpiNoHeadSub"></div>
            </div>
            <div class="kpi-card">
                <div class="kpi-value" id="kpiCombos">--</div>
                <div class="kpi-label">Combos Done</div>
                <div class="kpi-bar"><div class="kpi-bar-fill" id="kpiCombosBar" style="width:0%"></div></div>
                <div class="kpi-sub" id="kpiCombosSub"></div>
            </div>
        </div>

        <!-- Status + Controls Row -->
        <div class="two-col">
            <div class="card">
                <div class="section-title">Status</div>
                <div class="status-indicator">
                    <span class="status-dot" id="statusDot"></span>
                    <span id="statusText">Stopped</span>
                </div>
                <p class="current-task" id="currentTask"></p>
                <div class="status-details">
                    <div>Workers: <span id="detailWorkers">--</span></div>
                    <div>Cases (24h): <span id="detail24h">--</span></div>
                    <div>Auto-restarts: <span id="detailRestarts">0/5</span></div>
                </div>
            </div>
            <div class="card">
                <div class="section-title">Controls</div>
                <div class="controls">
                    <button class="btn-start" id="btnStart" onclick="startScraper()">Start</button>
                    <button class="btn-stop btn-disabled" id="btnStop" onclick="stopScraper()" disabled>Stop</button>
                    <button class="btn-reset btn-disabled" id="btnReset" onclick="resetProgress()" disabled>Reset</button>
                    <button class="btn-download btn-disabled" id="btnDownload" onclick="downloadCSV()" disabled>CSV</button>
                </div>
                <div class="controls-row">
                    <label>Workers</label>
                    <input type="number" id="indexWorkers" value="3" min="1" max="10">
                    <label><input type="checkbox" id="indexGetDetails" checked> Fetch details</label>
                    <label><input type="checkbox" id="autoRestart" checked> Auto-restart</label>
                </div>
                <div id="fileInfo" style="margin-top: 8px; font-size: 12px; color: #64748b;"></div>
            </div>
        </div>

        <!-- Journal Breakdown -->
        <div class="card" style="margin-bottom: 20px;">
            <div class="section-title">Top Journals</div>
            <div class="journal-bars" id="journalBars">
                <p style="color: #64748b; font-size: 13px;">Loading...</p>
            </div>
            <div class="latest-ts" id="latestTs"></div>
        </div>

        <!-- Coverage Matrix -->
        <div class="card" style="margin-bottom: 20px;">
            <div class="section-title">Coverage Matrix</div>
            <div class="matrix-container" id="matrixContainer">
                <p style="color: #64748b;">Loading matrix...</p>
            </div>
            <div class="matrix-legend">
                <span><span class="legend-dot" style="background:#334155;"></span> Pending</span>
                <span><span class="legend-dot" style="background:#eab308;"></span> In Progress</span>
                <span><span class="legend-dot" style="background:#22c55e;"></span> Completed</span>
                <span><span class="legend-dot" style="background:#ef4444;"></span> Error</span>
            </div>
        </div>

        <!-- Auth + Errors Row -->
        <div class="two-col">
            <div class="card">
                <div class="section-title">Authentication</div>
                <div id="authStatus" style="margin-bottom: 10px; color: #64748b; font-size: 13px;">Checking...</div>
                <button class="btn-save" onclick="relogin()" style="width: 100%; margin-bottom: 12px;">Re-login</button>
                <details style="margin-top: 8px;">
                    <summary style="cursor: pointer; color: #64748b; font-size: 12px;">Advanced: Manual Cookies</summary>
                    <div style="margin-top: 10px;">
                        <div class="form-group">
                            <label>Session ID</label>
                            <input type="text" id="sessionId" placeholder="ASP.NET_SessionId">
                        </div>
                        <div class="form-group">
                            <label>Verification Token</label>
                            <input type="text" id="verificationToken" placeholder="__RequestVerificationToken">
                        </div>
                        <button class="btn-save" onclick="saveCookies()" style="width: 100%;">Save Cookies</button>
                    </div>
                </details>
            </div>
            <div class="card">
                <div class="section-title">Error Log</div>
                <div class="error-log" id="errorLog">
                    <div class="no-errors">No errors</div>
                </div>
            </div>
        </div>

        <!-- Backfill Card -->
        <div class="card" style="margin-bottom: 20px;">
            <div class="section-title">Backfill Missing Descriptions</div>
            <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
                <button class="btn-start" id="btnBackfill" onclick="startBackfill()">Start Backfill</button>
                <button class="btn-stop btn-disabled" id="btnBackfillStop" onclick="stopBackfill()" disabled>Stop</button>
                <span id="backfillMsg" style="font-size: 13px; color: #94a3b8;"></span>
            </div>
            <div class="kpi-bar" style="height: 8px; margin-bottom: 8px;">
                <div class="kpi-bar-fill" id="backfillBar" style="width: 0%; background: linear-gradient(90deg, #a855f7, #38bdf8);"></div>
            </div>
            <div style="font-size: 12px; color: #64748b;">
                Processed: <span id="backfillProcessed" style="color: #f8fafc;">0</span> / <span id="backfillTotal" style="color: #f8fafc;">0</span>
                &nbsp;&bull;&nbsp; Fixed: <span id="backfillFixed" style="color: #22c55e;">0</span>
                &nbsp;&bull;&nbsp; Errors: <span id="backfillErrors" style="color: #f87171;">0</span>
            </div>
        </div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
        let dbStatsAvailable = false;  // tracks whether /api/dashboard-stats ever succeeded

        function showToast(msg, isError) {
            const t = document.getElementById('toast');
            t.textContent = msg;
            t.className = 'toast show' + (isError ? ' error' : '');
            setTimeout(() => t.className = 'toast', 3000);
        }

        function fmt(n) {
            if (n === null || n === undefined) return '--';
            return Number(n).toLocaleString();
        }

        /* ---- Status polling (2s) ---- */
        function updateStatus(d) {
            const dot = document.getElementById('statusDot');
            const txt = document.getElementById('statusText');
            const task = document.getElementById('currentTask');

            if (d.running) {
                dot.className = 'status-dot running';
                txt.textContent = 'Running';
                task.textContent = d.current_journal && d.current_year
                    ? 'Index: ' + d.current_journal + ' ' + d.current_year
                    : '';
            } else {
                dot.className = 'status-dot stopped';
                txt.textContent = 'Stopped';
                task.textContent = '';
            }

            document.getElementById('detailWorkers').textContent = d.num_workers || '--';
            document.getElementById('detailRestarts').textContent = (d.restart_count || 0) + '/' + (d.max_restarts || 5);
            document.getElementById('autoRestart').checked = d.auto_restart !== false;

            // DB connection banner
            const banner = document.getElementById('dbBanner');
            if (d.db_connected === false) {
                banner.style.display = 'block';
                document.getElementById('dbBannerDetail').textContent = d.db_error || '';
            } else {
                banner.style.display = 'none';
            }

            // KPI fallback: when DB stats are not available, use in-memory data from /api/status
            if (!dbStatsAvailable) {
                document.getElementById('kpiTotal').textContent = fmt(d.cases);
                document.getElementById('kpiTotalSub').textContent = d.db_connected === false ? '(session count)' : '';

                document.getElementById('kpiRate').textContent = '--';
                document.getElementById('kpiRateSub').textContent = d.db_connected === false ? 'needs DB' : '';

                document.getElementById('kpiCompletion').textContent = '--%';
                document.getElementById('kpiCompletionSub').textContent = d.db_connected === false ? 'needs DB' : '';

                document.getElementById('kpiWithDesc').textContent = '--';
                document.getElementById('kpiWithDescSub').textContent = d.db_connected === false ? 'needs DB' : '';

                document.getElementById('kpiNoHead').textContent = '--';
                document.getElementById('kpiNoHeadSub').textContent = d.db_connected === false ? 'needs DB' : '';

                if (d.combos_total > 0) {
                    document.getElementById('kpiCombos').textContent = fmt(d.combos_completed) + ' / ' + fmt(d.combos_total);
                    const comboPct = (d.combos_completed / d.combos_total) * 100;
                    document.getElementById('kpiCombosBar').style.width = comboPct + '%';
                    document.getElementById('kpiCombosSub').textContent = '';
                }
            }

            // Buttons
            document.getElementById('btnStart').disabled = d.running;
            document.getElementById('btnStart').className = d.running ? 'btn-start btn-disabled' : 'btn-start';
            document.getElementById('btnStop').disabled = !d.running;
            document.getElementById('btnStop').className = d.running ? 'btn-stop' : 'btn-stop btn-disabled';
            document.getElementById('btnReset').disabled = d.running;
            document.getElementById('btnReset').className = d.running ? 'btn-reset btn-disabled' : 'btn-reset';

            // Auth
            document.getElementById('authStatus').textContent = d.auth_status;
            document.getElementById('authStatus').style.color = d.authenticated ? '#22c55e' : '#f87171';

            // Download
            const btn = document.getElementById('btnDownload');
            const fi = document.getElementById('fileInfo');
            if (d.file_exists) {
                btn.disabled = false; btn.className = 'btn-download';
                fi.textContent = d.file_name + ' (' + d.file_size + ')';
            } else {
                btn.disabled = true; btn.className = 'btn-download btn-disabled';
                fi.textContent = '';
            }

            // Errors
            const el = document.getElementById('errorLog');
            if (d.errors && d.errors.length > 0) {
                el.innerHTML = d.errors.map(e => '<div class="error-item">' + e + '</div>').join('');
            } else {
                el.innerHTML = '<div class="no-errors">No errors</div>';
            }
        }

        async function fetchStatus() {
            try {
                const r = await fetch('/api/status');
                updateStatus(await r.json());
            } catch(e) { console.error('status err', e); }
        }

        /* ---- Dashboard stats polling (5s) ---- */
        function updateDashboard(s) {
            dbStatsAvailable = true;

            document.getElementById('kpiTotal').textContent = fmt(s.total_cases);
            document.getElementById('kpiTotalSub').textContent = fmt(s.cases_last_24h) + ' in last 24h';

            document.getElementById('kpiRate').textContent = fmt(s.cases_last_hour);
            document.getElementById('kpiRateSub').textContent = 'live (last hour)';

            const comp = s.total_cases > 0 ? ((s.cases_with_headnotes / s.total_cases) * 100).toFixed(1) : 0;
            document.getElementById('kpiCompletion').textContent = comp + '%';
            document.getElementById('kpiCompletionSub').textContent = fmt(s.cases_with_headnotes) + ' / ' + fmt(s.total_cases) + ' with headnotes';

            // With Description card
            const descPct = s.total_cases > 0 ? ((s.cases_with_description / s.total_cases) * 100).toFixed(1) : 0;
            document.getElementById('kpiWithDesc').textContent = descPct + '%';
            document.getElementById('kpiWithDescBar').style.width = descPct + '%';
            document.getElementById('kpiWithDescSub').textContent = fmt(s.cases_with_description) + ' / ' + fmt(s.total_cases) + ' with description';

            // Without Headnotes card
            const noHead = s.total_cases - (s.cases_with_headnotes || 0);
            const noHeadPct = s.total_cases > 0 ? ((noHead / s.total_cases) * 100).toFixed(1) : 0;
            document.getElementById('kpiNoHead').textContent = fmt(noHead);
            document.getElementById('kpiNoHeadBar').style.width = noHeadPct + '%';
            document.getElementById('kpiNoHeadSub').textContent = noHeadPct + '% missing headnotes';

            document.getElementById('kpiCombos').textContent = fmt(s.combos_completed) + ' / ' + fmt(s.combos_total);
            const comboPct = s.combos_total > 0 ? ((s.combos_completed / s.combos_total) * 100) : 0;
            document.getElementById('kpiCombosBar').style.width = comboPct + '%';
            document.getElementById('kpiCombosSub').textContent =
                (s.combos_in_progress || 0) + ' running, ' + (s.combos_error || 0) + ' errors';

            document.getElementById('detail24h').textContent = fmt(s.cases_last_24h);

            // Journal bars
            const jc = document.getElementById('journalBars');
            if (s.cases_by_journal && s.cases_by_journal.length > 0) {
                const maxCount = s.cases_by_journal[0].count;
                jc.innerHTML = s.cases_by_journal.map(j => {
                    const pct = maxCount > 0 ? ((j.count / maxCount) * 100) : 0;
                    return '<div class="journal-bar-row">' +
                        '<div class="journal-bar-label">' + j.journal + '</div>' +
                        '<div class="journal-bar-track"><div class="journal-bar-fill" style="width:' + pct + '%"></div></div>' +
                        '<div class="journal-bar-count">' + fmt(j.count) + '</div></div>';
                }).join('');
            } else {
                jc.innerHTML = '<p style="color:#64748b;font-size:13px;">No data yet</p>';
            }

            // Latest timestamp
            const ts = document.getElementById('latestTs');
            if (s.latest_scraped_at) {
                const d = new Date(s.latest_scraped_at);
                ts.textContent = 'Latest: ' + d.toLocaleString();
            } else {
                ts.textContent = '';
            }
        }

        async function fetchDashboardStats() {
            try {
                const r = await fetch('/api/dashboard-stats');
                if (r.ok) updateDashboard(await r.json());
            } catch(e) { console.error('dashboard-stats err', e); }
        }

        /* ---- Matrix polling (10s) ---- */
        function renderMatrix(progress) {
            const container = document.getElementById('matrixContainer');
            const journals = ['PLD','SCMR','CLC','CLD','YLR','PCrLJ','PLC','PLC(CS)','PTD','MLD','GBLR','CLCN','YLRN','PCRLJN','PLCN','PLC(CS)N'];
            const startYear = 1947, endYear = 2026;

            if (!progress.journals || Object.keys(progress.journals).length === 0) {
                container.innerHTML = '<p style="color:#64748b;">No progress data yet.</p>';
                return;
            }

            let decades = [];
            for (let y = startYear; y <= endYear; y += 10) decades.push(y);

            let html = '<table class="matrix-table"><thead><tr><th></th>';
            for (let d of decades) {
                let span = Math.min(10, endYear - d + 1);
                html += '<th colspan="' + span + '">' + d + 's</th>';
            }
            html += '</tr></thead><tbody>';

            for (let journal of journals) {
                html += '<tr><td class="journal-label">' + journal + '</td>';
                for (let y = startYear; y <= endYear; y++) {
                    let st = 'pending';
                    if (progress.journals[journal] && progress.journals[journal][y]) {
                        st = progress.journals[journal][y].status || 'pending';
                    }
                    let title = journal + ' ' + y + ': ' + st;
                    if (progress.journals[journal] && progress.journals[journal][y] && progress.journals[journal][y].cases_found !== undefined) {
                        title += ' (' + progress.journals[journal][y].cases_found + ' cases)';
                    }
                    html += '<td title="' + title + '"><div class="matrix-cell ' + st + '"></div></td>';
                }
                html += '</tr>';
            }
            html += '</tbody></table>';
            container.innerHTML = html;
        }

        async function fetchMatrix() {
            try {
                const r = await fetch('/api/index-progress');
                renderMatrix(await r.json());
            } catch(e) { console.error('matrix err', e); }
        }

        /* ---- Actions ---- */
        async function startScraper() {
            const settings = {
                mode: 'index',
                output_file: 'all_cases_index.csv',
                get_details: document.getElementById('indexGetDetails').checked,
                num_workers: parseInt(document.getElementById('indexWorkers').value) || 3,
                auto_restart: document.getElementById('autoRestart').checked
            };
            try {
                const r = await fetch('/api/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(settings)
                });
                const d = await r.json();
                showToast(d.message, !d.success);
            } catch(e) { showToast('Failed to start', true); }
        }

        async function stopScraper() {
            try {
                const r = await fetch('/api/stop', {method: 'POST'});
                const d = await r.json();
                showToast(d.message);
            } catch(e) { showToast('Failed to stop', true); }
        }

        async function resetProgress() {
            if (!confirm('Delete ALL scraped data and progress?')) return;
            try {
                const r = await fetch('/api/reset', {method: 'POST'});
                const d = await r.json();
                showToast(d.message, !d.success);
                if (d.success) { fetchStatus(); fetchDashboardStats(); fetchMatrix(); }
            } catch(e) { showToast('Failed to reset', true); }
        }

        function downloadCSV() { window.location.href = '/api/download'; }

        async function saveCookies() {
            const cookies = {
                session_id: document.getElementById('sessionId').value,
                verification_token: document.getElementById('verificationToken').value
            };
            try {
                const r = await fetch('/api/cookies', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(cookies)
                });
                const d = await r.json();
                showToast(d.message, !d.success);
                fetchStatus();
            } catch(e) { showToast('Failed to save cookies', true); }
        }

        async function relogin() {
            try {
                const r = await fetch('/api/relogin', {method: 'POST'});
                const d = await r.json();
                showToast(d.message, !d.success);
                fetchStatus();
            } catch(e) { showToast('Re-login failed', true); }
        }

        async function startBackfill() {
            try {
                const r = await fetch('/api/backfill', {method: 'POST'});
                const d = await r.json();
                showToast(d.message, !d.success);
            } catch(e) { showToast('Failed to start backfill', true); }
        }

        async function stopBackfill() {
            try {
                const r = await fetch('/api/backfill/stop', {method: 'POST'});
                const d = await r.json();
                showToast(d.message, !d.success);
            } catch(e) { showToast('Failed to stop backfill', true); }
        }

        async function fetchBackfillStatus() {
            try {
                const r = await fetch('/api/backfill/status');
                const d = await r.json();
                document.getElementById('backfillProcessed').textContent = fmt(d.processed);
                document.getElementById('backfillTotal').textContent = fmt(d.total);
                document.getElementById('backfillFixed').textContent = fmt(d.fixed);
                document.getElementById('backfillErrors').textContent = fmt(d.errors);
                document.getElementById('backfillMsg').textContent = d.message || '';
                const pct = d.total > 0 ? ((d.processed / d.total) * 100) : 0;
                document.getElementById('backfillBar').style.width = pct + '%';

                document.getElementById('btnBackfill').disabled = d.is_running;
                document.getElementById('btnBackfill').className = d.is_running ? 'btn-start btn-disabled' : 'btn-start';
                document.getElementById('btnBackfillStop').disabled = !d.is_running;
                document.getElementById('btnBackfillStop').className = d.is_running ? 'btn-stop' : 'btn-stop btn-disabled';
            } catch(e) {}
        }

        async function loadSavedCookies() {
            try {
                const r = await fetch('/api/cookies');
                const d = await r.json();
                if (d.session_id) document.getElementById('sessionId').value = d.session_id;
                if (d.verification_token) document.getElementById('verificationToken').value = d.verification_token;
            } catch(e) {}
        }

        // Boot
        loadSavedCookies();
        fetchStatus();
        fetchDashboardStats();
        fetchMatrix();
        fetchBackfillStatus();

        // Polling intervals
        setInterval(fetchStatus, 2000);
        setInterval(fetchDashboardStats, 5000);
        setInterval(fetchMatrix, 10000);
        setInterval(fetchBackfillStatus, 2000);
    </script>
</body>
</html>
'''


@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)


def get_file_size(filepath):
    """Get human-readable file size"""
    if not os.path.exists(filepath):
        return ""
    size = os.path.getsize(filepath)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


@app.route('/api/status')
def get_status():
    config = load_config()
    authenticated = False
    auth_status = "Not authenticated"

    if state.scraper:
        authenticated = state.scraper.is_logged_in
        if authenticated:
            auth_status = f"Logged in as {state.scraper.username}"
        else:
            auth_status = "Not authenticated"
    elif config.get('session_id'):
        auth_status = "Cookies saved (not verified)"

    # Check if output file exists
    file_exists = os.path.exists(state.output_file)
    file_size = get_file_size(state.output_file) if file_exists else ""

    result = {
        'running': state.is_running,
        'cases': state.cases_scraped,
        'total': state.total_cases,
        'keyword': state.current_keyword,
        'last_case': state.last_case_id,
        'errors': state.errors[-10:],
        'authenticated': authenticated,
        'auth_status': auth_status,
        'file_exists': file_exists,
        'file_name': state.output_file,
        'file_size': file_size,
        'mode': state.mode,
        'current_journal': state.current_journal,
        'current_year': state.current_year,
        'combos_completed': state.combos_completed,
        'combos_total': state.combos_total,
        'num_workers': state.num_workers,
        'db_connected': _db is not None,
        'db_error': _db_error,
        'auto_restart': state.auto_restart,
        'restart_count': state.restart_count,
        'max_restarts': state.max_restarts,
    }
    return jsonify(result)


@app.route('/api/dashboard-stats')
def get_dashboard_stats():
    """Return KPI stats from DB for the dashboard."""
    if not _db:
        return jsonify({'error': 'No database configured'}), 503
    stats = _db.get_dashboard_stats()
    if stats is None:
        return jsonify({'error': 'Failed to query stats'}), 500
    return jsonify(stats)


@app.route('/api/start', methods=['POST'])
def start_scraper():
    if state.is_running:
        return jsonify({'success': False, 'message': 'Already running'})

    # Setup scraper if needed
    if not state.scraper:
        success, msg = setup_scraper()
        if not success:
            return jsonify({'success': False, 'message': msg})

    # Verify authentication — try re-login before failing
    if not state.scraper._verify_login():
        if not state.scraper._try_reauth():
            return jsonify({'success': False, 'message': 'Auto-login failed. Click Re-login or check credentials.'})

    # Get settings from request
    data = request.json or {}
    state.mode = data.get('mode', 'keyword')
    state.output_file = data.get('output_file', 'scraped_cases.csv')
    state.get_details = data.get('get_details', True)
    state.auto_restart = data.get('auto_restart', True)

    # Reset state
    state.should_stop = False
    state.cases_scraped = 0
    state.errors = []
    state.start_time = datetime.now()
    state.is_running = True
    state.restart_count = 0

    if state.mode == 'index':
        # Index mode
        test_mode = data.get('test_mode', False)
        if test_mode:
            state.index_journals = ['PLD']
            state.index_year_start = 2024
            state.index_year_end = 2025
            state.num_workers = 1
        else:
            state.index_journals = None
            state.index_year_start = None
            state.index_year_end = None
            state.num_workers = data.get('num_workers', 3)

        # Calculate combos for progress display
        journals_list = state.index_journals or state.scraper.INDEX_JOURNALS
        yr_start = state.index_year_start or state.scraper.YEAR_RANGE_START
        yr_end = state.index_year_end or state.scraper.YEAR_RANGE_END
        state.combos_completed = 0
        state.combos_total = len(journals_list) * (yr_end - yr_start + 1)
        state.current_journal = ""
        state.current_year = ""
        state.output_file = data.get('output_file', 'all_cases_index.csv')
        state.progress_file = 'index_progress.json'
        state.thread = threading.Thread(target=index_scrape_worker, daemon=True)
        state.thread.start()
        label = 'Test mode (PLD 2024-2025)' if test_mode else f'{state.num_workers} workers'
        return jsonify({'success': True, 'message': f'Index scraper started: {label}'})
    else:
        # Keyword mode
        state.keywords = [k.strip() for k in data.get('keywords', 'contract').split(',')]
        state.year = data.get('year', '5')
        state.max_cases = data.get('max_cases', 50)
        state.scraper.processed_case_ids.clear()
        state.thread = threading.Thread(target=scrape_worker, daemon=True)
        state.thread.start()
        return jsonify({'success': True, 'message': 'Scraper started'})


@app.route('/api/stop', methods=['POST'])
def stop_scraper():
    if not state.is_running:
        return jsonify({'success': False, 'message': 'Not running'})

    state.should_stop = True
    return jsonify({'success': True, 'message': 'Stopping...'})


@app.route('/api/reset', methods=['POST'])
def reset_progress():
    """Full reset: delete all progress, cases, and start fresh."""
    if state.is_running:
        return jsonify({'success': False, 'message': 'Cannot reset while scraper is running. Stop it first.'})

    errors = []

    # 1. Reset database tables
    if _db:
        try:
            _db.reset_all()
        except Exception as e:
            errors.append(f"DB reset failed: {e}")

    # 2. Delete index_progress.json
    progress_file = getattr(state, 'progress_file', 'index_progress.json')
    if os.path.exists(progress_file):
        try:
            os.remove(progress_file)
        except Exception as e:
            errors.append(f"Failed to delete {progress_file}: {e}")

    # 3. Reset in-memory ScraperState
    state.cases_scraped = 0
    state.total_cases = 0
    state.combos_completed = 0
    state.combos_total = 0
    state.current_journal = ""
    state.current_year = ""
    state.current_keyword = ""
    state.last_case_id = ""
    state.errors = []
    state.start_time = None

    # 4. Clear scraper's processed_case_ids
    if state.scraper and hasattr(state.scraper, 'processed_case_ids'):
        state.scraper.processed_case_ids.clear()

    if errors:
        return jsonify({'success': False, 'message': 'Partial reset. Errors: ' + '; '.join(errors)})

    return jsonify({'success': True, 'message': 'All progress reset. Ready for a fresh scrape.'})


@app.route('/api/index-progress')
def get_index_progress():
    """Return full index progress JSON for matrix UI"""
    # Prefer DB when available
    if _db:
        try:
            return jsonify(_db.get_progress())
        except Exception:
            pass

    progress_file = state.progress_file
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r') as f:
                return jsonify(json.load(f))
        except Exception:
            pass
    return jsonify({'journals': {}, 'completed_count': 0, 'total_combinations': 0})


@app.route('/api/download')
def download_file():
    """Download the scraped CSV file"""
    if not os.path.exists(state.output_file):
        return jsonify({'success': False, 'message': 'No file available'}), 404

    return send_file(
        state.output_file,
        mimetype='text/csv',
        as_attachment=True,
        download_name=state.output_file
    )


@app.route('/api/cookies', methods=['GET', 'POST'])
def handle_cookies():
    if request.method == 'GET':
        config = load_config()
        return jsonify({
            'session_id': config.get('session_id', ''),
            'verification_token': config.get('verification_token', '')
        })

    # POST - save cookies
    data = request.json
    session_id = data.get('session_id', '').strip()
    token = data.get('verification_token', '').strip()

    if not session_id or not token:
        return jsonify({'success': False, 'message': 'Both cookies required'})

    # Save to config
    config = load_config()
    config['session_id'] = session_id
    config['verification_token'] = token
    save_config(config)

    # Update scraper
    if not state.scraper:
        setup_scraper()
    else:
        state.scraper.set_cookies(session_id, token)

    # Verify — and sync is_logged_in to the actual result
    verified = state.scraper._verify_login()
    state.scraper.is_logged_in = verified

    if verified:
        return jsonify({'success': True, 'message': 'Cookies saved and verified!'})
    else:
        return jsonify({'success': False, 'message': 'Cookies saved but verification failed'})


class BackfillState:
    """State for the backfill worker"""
    is_running = False
    should_stop = False
    thread = None
    total = 0
    processed = 0
    fixed = 0
    errors = 0
    message = ""


backfill_state = BackfillState()


def backfill_worker():
    """Background worker to re-scrape cases with missing descriptions"""
    try:
        if not _db:
            backfill_state.message = "No database configured"
            return

        missing = _db.get_cases_missing_details(limit=500)
        backfill_state.total = len(missing)
        backfill_state.processed = 0
        backfill_state.fixed = 0
        backfill_state.errors = 0

        if not missing:
            backfill_state.message = "No cases with missing details found"
            return

        backfill_state.message = f"Backfilling {len(missing)} cases..."

        for item in missing:
            if backfill_state.should_stop:
                backfill_state.message = "Backfill stopped by user"
                break

            case_id = item['case_id']
            get_head = item['missing_head_notes']
            get_desc = item['missing_description']

            best_details = {}
            for _attempt in range(3):
                try:
                    details = state.scraper.get_case_details(
                        case_id,
                        get_head_notes=get_head,
                        get_full_description=get_desc
                    )
                    for k, v in details.items():
                        if v and (k not in best_details or not best_details[k]):
                            best_details[k] = v
                    break  # Full success
                except SessionExpiredError:
                    if not state.scraper._try_reauth():
                        break
                except EmptyContentError:
                    backoff = 5 * (2 ** _attempt)
                    time.sleep(backoff)
                except Exception:
                    break

            backfill_state.processed += 1

            if best_details:
                # Upsert to DB — insert_case uses ON CONFLICT to fill empty fields
                case_data = {
                    'case_id': case_id,
                    'citation': '', 'year': '', 'journal': '', 'page': '',
                    'court': '', 'parties_full': '', 'petitioner': '', 'respondent': '',
                    'keywords': None, 'summary': None,
                    'head_notes': best_details.get('head_notes'),
                    'full_description': best_details.get('full_description'),
                    'scraped_at': datetime.now().isoformat(),
                    'source': 'backfill', 'search_journal': None,
                    'search_year': None, 'search_keyword': None,
                }
                if _db.insert_case(case_data):
                    backfill_state.fixed += 1
                else:
                    backfill_state.errors += 1
            else:
                backfill_state.errors += 1

        backfill_state.message = f"Done: {backfill_state.fixed} fixed, {backfill_state.errors} errors out of {backfill_state.total}"

    except Exception as e:
        backfill_state.message = f"Backfill error: {e}"
    finally:
        backfill_state.is_running = False


@app.route('/api/backfill', methods=['POST'])
def start_backfill():
    """Start the backfill worker to re-scrape cases with missing details"""
    if backfill_state.is_running:
        return jsonify({'success': False, 'message': 'Backfill already running'})
    if state.is_running:
        return jsonify({'success': False, 'message': 'Main scraper is running — stop it first'})
    if not state.scraper or not state.scraper.is_logged_in:
        success, msg = setup_scraper()
        if not success:
            return jsonify({'success': False, 'message': msg})
    if not _db:
        return jsonify({'success': False, 'message': 'No database configured'})

    backfill_state.is_running = True
    backfill_state.should_stop = False
    backfill_state.total = 0
    backfill_state.processed = 0
    backfill_state.fixed = 0
    backfill_state.errors = 0
    backfill_state.message = "Starting..."
    backfill_state.thread = threading.Thread(target=backfill_worker, daemon=True)
    backfill_state.thread.start()
    return jsonify({'success': True, 'message': 'Backfill started'})


@app.route('/api/backfill/stop', methods=['POST'])
def stop_backfill():
    """Stop the backfill worker"""
    if not backfill_state.is_running:
        return jsonify({'success': False, 'message': 'Backfill not running'})
    backfill_state.should_stop = True
    return jsonify({'success': True, 'message': 'Stopping backfill...'})


@app.route('/api/backfill/status')
def backfill_status():
    """Return backfill progress"""
    return jsonify({
        'is_running': backfill_state.is_running,
        'total': backfill_state.total,
        'processed': backfill_state.processed,
        'fixed': backfill_state.fixed,
        'errors': backfill_state.errors,
        'message': backfill_state.message,
    })


@app.route('/api/relogin', methods=['POST'])
def relogin():
    """Force fresh auto-login with credentials, return diagnostics on failure"""
    if not state.scraper:
        success, msg = setup_scraper()
        if not success and state.scraper and state.scraper.last_login_diag:
            diag = state.scraper.last_login_diag
            detail = diag.get('error', 'Unknown error')
            status = diag.get('post_status')
            if status:
                detail = f"HTTP {status}: {detail}"
            msg = f"Login failed — {detail}"
        return jsonify({'success': success, 'message': msg})
    if state.scraper.login():
        return jsonify({'success': True, 'message': f'Logged in as {state.scraper.username}'})

    # Surface diagnostics from the failed login attempt
    diag = getattr(state.scraper, 'last_login_diag', {})
    detail = diag.get('error', 'Unknown error')
    status = diag.get('post_status')
    snippet = diag.get('post_response_snippet', '')[:200]
    msg = f"Login failed — {detail}"
    if status:
        msg = f"Login failed (HTTP {status}) — {detail}"
    if snippet:
        msg += f" | Server response: {snippet}"
    return jsonify({'success': False, 'message': msg})


@app.route('/api/login-debug')
def login_debug():
    """Return diagnostic info from the last login attempt for debugging"""
    if not state.scraper:
        return jsonify({'error': 'Scraper not initialized', 'diagnostics': {}})
    diag = getattr(state.scraper, 'last_login_diag', {})
    return jsonify({
        'diagnostics': diag,
        'is_logged_in': state.scraper.is_logged_in,
        'username': state.scraper.username,
    })


if __name__ == '__main__':
    print("\n" + "="*50)
    print("Pakistan Law Scraper - Web Dashboard")
    print("="*50)
    print("\nStarting server...")
    print("Open: http://localhost:5001")
    print("\nPress Ctrl+C to stop\n")

    # Initialize scraper on startup
    setup_scraper()

    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5001)), debug=False)

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
import threading
import time
from datetime import datetime
from flask import Flask, render_template_string, jsonify, request, send_file
from scraper import PakistanLawScraper, SessionExpiredError

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# Database layer — only active when DATABASE_URL is set
_db = None
try:
    import db as database
    if os.environ.get("DATABASE_URL"):
        database.init_tables()
        _db = database
except ImportError:
    pass

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
    """Initialize scraper with saved cookies"""
    config = load_config()

    state.scraper = PakistanLawScraper(
        username=config.get('username', os.environ.get('PLS_USERNAME', '')),
        password=config.get('password', os.environ.get('PLS_PASSWORD', '')),
        delay_range=(0.2, 0.5)
    )

    # Try saved cookies
    session_id = config.get('session_id', '')
    token = config.get('verification_token', '')

    if session_id and token:
        state.scraper.set_cookies(session_id, token)
        if state.scraper._verify_login():
            return True, "Logged in with saved cookies"

    # Try auto-login
    if state.scraper.login():
        return True, "Auto-login successful"

    return False, "Login failed - please set cookies"


def scrape_worker():
    """Background scraping worker"""
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
                        try:
                            details = state.scraper.get_case_details(case_id)
                            case.update(details)
                        except Exception as e:
                            state.errors.append(f"{case_id}: {str(e)[:50]}")

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
    finally:
        state.is_running = False


def index_scrape_worker():
    """Background worker for index-based scraping"""
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
    finally:
        state.is_running = False


# HTML Template
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pakistan Law Scraper</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            min-height: 100vh;
            padding: 20px;
        }

        .container {
            max-width: 900px;
            margin: 0 auto;
        }

        header {
            text-align: center;
            padding: 30px 0;
            border-bottom: 1px solid #334155;
            margin-bottom: 30px;
        }

        h1 {
            font-size: 28px;
            color: #38bdf8;
            margin-bottom: 8px;
        }

        .subtitle {
            color: #94a3b8;
            font-size: 14px;
        }

        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }

        .card {
            background: #1e293b;
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #334155;
        }

        .card-full {
            grid-column: 1 / -1;
        }

        .card h2 {
            font-size: 14px;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 15px;
        }

        .status-indicator {
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 24px;
            font-weight: 600;
        }

        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }

        .status-dot.running {
            background: #22c55e;
            box-shadow: 0 0 10px #22c55e;
        }

        .status-dot.stopped {
            background: #64748b;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .stat-value {
            font-size: 36px;
            font-weight: 700;
            color: #f8fafc;
        }

        .stat-label {
            color: #64748b;
            font-size: 13px;
            margin-top: 5px;
        }

        .controls {
            display: flex;
            gap: 12px;
            margin-top: 20px;
        }

        button {
            flex: 1;
            padding: 14px 24px;
            border: none;
            border-radius: 8px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }

        .btn-start {
            background: #22c55e;
            color: white;
        }

        .btn-start:hover {
            background: #16a34a;
        }

        .btn-stop {
            background: #ef4444;
            color: white;
        }

        .btn-stop:hover {
            background: #dc2626;
        }

        .btn-disabled {
            background: #334155;
            color: #64748b;
            cursor: not-allowed;
        }

        .form-group {
            margin-bottom: 15px;
        }

        label {
            display: block;
            font-size: 13px;
            color: #94a3b8;
            margin-bottom: 6px;
        }

        input, select {
            width: 100%;
            padding: 10px 12px;
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 6px;
            color: #e2e8f0;
            font-size: 14px;
        }

        input:focus, select:focus {
            outline: none;
            border-color: #38bdf8;
        }

        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
        }

        .error-log {
            background: #0f172a;
            border-radius: 6px;
            padding: 12px;
            max-height: 150px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 12px;
        }

        .error-item {
            color: #f87171;
            padding: 4px 0;
            border-bottom: 1px solid #1e293b;
        }

        .no-errors {
            color: #22c55e;
        }

        .progress-bar {
            height: 6px;
            background: #334155;
            border-radius: 3px;
            overflow: hidden;
            margin-top: 10px;
        }

        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #38bdf8, #22c55e);
            transition: width 0.3s;
        }

        .cookie-section {
            margin-top: 15px;
            padding-top: 15px;
            border-top: 1px solid #334155;
        }

        .btn-save {
            background: #38bdf8;
            color: #0f172a;
            margin-top: 10px;
        }

        .btn-save:hover {
            background: #0ea5e9;
        }

        .btn-download {
            background: #a855f7;
            color: white;
        }

        .btn-download:hover {
            background: #9333ea;
        }

        .toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            padding: 12px 20px;
            background: #22c55e;
            color: white;
            border-radius: 8px;
            font-size: 14px;
            opacity: 0;
            transform: translateY(20px);
            transition: all 0.3s;
        }

        .toast.show {
            opacity: 1;
            transform: translateY(0);
        }

        .toast.error {
            background: #ef4444;
        }

        .current-task {
            color: #38bdf8;
            font-size: 14px;
            margin-top: 8px;
        }

        /* Mode toggle */
        .mode-toggle {
            display: flex;
            gap: 0;
            margin-bottom: 20px;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid #334155;
        }

        .mode-btn {
            flex: 1;
            padding: 12px;
            border: none;
            border-radius: 0;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            background: #1e293b;
            color: #94a3b8;
            transition: all 0.2s;
        }

        .mode-btn.active {
            background: #38bdf8;
            color: #0f172a;
        }

        .mode-btn:hover:not(.active) {
            background: #334155;
        }

        /* Progress matrix */
        .matrix-container {
            overflow-x: auto;
            margin-top: 12px;
        }

        .matrix-table {
            border-collapse: collapse;
            font-size: 10px;
            width: 100%;
        }

        .matrix-table th {
            padding: 3px 2px;
            color: #94a3b8;
            font-weight: normal;
            text-align: center;
            position: sticky;
            top: 0;
            background: #1e293b;
        }

        .matrix-table td {
            padding: 0;
            text-align: center;
        }

        .matrix-table .journal-label {
            text-align: right;
            padding-right: 6px;
            color: #94a3b8;
            font-weight: 600;
            white-space: nowrap;
        }

        .matrix-cell {
            width: 10px;
            height: 10px;
            margin: 1px auto;
            border-radius: 2px;
        }

        .matrix-cell.pending { background: #334155; }
        .matrix-cell.in_progress { background: #eab308; box-shadow: 0 0 4px #eab308; }
        .matrix-cell.completed { background: #22c55e; }
        .matrix-cell.error { background: #ef4444; }

        .matrix-legend {
            display: flex;
            gap: 16px;
            margin-top: 10px;
            font-size: 12px;
            color: #94a3b8;
        }

        .matrix-legend span {
            display: flex;
            align-items: center;
            gap: 4px;
        }

        .legend-dot {
            width: 10px;
            height: 10px;
            border-radius: 2px;
            display: inline-block;
        }

        .combo-counter {
            font-size: 18px;
            font-weight: 600;
            color: #f8fafc;
            margin-bottom: 8px;
        }

        .hidden { display: none; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Pakistan Law Scraper</h1>
            <p class="subtitle">Web Dashboard</p>
        </header>

        <!-- Mode Toggle -->
        <div class="mode-toggle">
            <button class="mode-btn active" id="modeKeyword" onclick="setMode('keyword')">Keyword Search</button>
            <button class="mode-btn" id="modeIndex" onclick="setMode('index')">Citation Index (100% Coverage)</button>
        </div>

        <div class="grid">
            <!-- Status Card -->
            <div class="card">
                <h2>Status</h2>
                <div class="status-indicator">
                    <span class="status-dot" id="statusDot"></span>
                    <span id="statusText">Stopped</span>
                </div>
                <p class="current-task" id="currentTask"></p>
            </div>

            <!-- Cases Card -->
            <div class="card">
                <h2>Progress</h2>
                <div class="stat-value" id="casesCount">0</div>
                <div class="stat-label" id="casesLabel">cases scraped</div>
                <div id="comboProgress" class="hidden">
                    <div class="combo-counter" id="comboCounter">0 / 0 combinations</div>
                </div>
                <div class="progress-bar">
                    <div class="progress-fill" id="progressBar" style="width: 0%"></div>
                </div>
            </div>

            <!-- Keyword Settings Card -->
            <div class="card" id="keywordSettings">
                <h2>Keyword Settings</h2>
                <div class="form-group">
                    <label>Keywords (comma-separated)</label>
                    <input type="text" id="keywords" value="contract" placeholder="contract, civil, criminal">
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Year Range</label>
                        <select id="year">
                            <option value="5">Last 5 years</option>
                            <option value="10">Last 10 years</option>
                            <option value="15">Last 15 years</option>
                            <option value="20">Last 20 years</option>
                            <option value="200">All years</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Max Cases</label>
                        <input type="number" id="maxCases" value="50" min="1">
                    </div>
                </div>
                <div class="form-group">
                    <label>Output File</label>
                    <input type="text" id="outputFile" value="scraped_cases.csv">
                </div>
            </div>

            <!-- Index Settings Card -->
            <div class="card hidden" id="indexSettings">
                <h2>Index Settings</h2>
                <div class="form-group">
                    <label>Output File</label>
                    <input type="text" id="indexOutputFile" value="all_cases_index.csv">
                </div>
                <div class="form-group">
                    <label>
                        <input type="checkbox" id="indexGetDetails" checked style="width: auto; margin-right: 8px;">
                        Fetch full details (head notes + description) - slower but complete
                    </label>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Concurrent Workers</label>
                        <input type="number" id="indexWorkers" value="3" min="1" max="10">
                    </div>
                    <div class="form-group" style="display: flex; align-items: end;">
                        <p style="font-size: 12px; color: #64748b;">3 workers = ~2-3 days for 350K cases. More workers = faster but higher ban risk.</p>
                    </div>
                </div>
                <p style="font-size: 12px; color: #64748b; margin-top: 8px;">
                    Iterates all 16 journals x 80 years (1947-2026) = 1,280 combinations.
                    Progress is saved continuously - stop and resume anytime.
                </p>
                <div style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #334155;">
                    <button class="btn-start" onclick="startTestRun()" style="background: #eab308; color: #0f172a; flex: none; width: 100%;">
                        Quick Test (PLD 2024-2025, 1 worker)
                    </button>
                    <p style="font-size: 11px; color: #64748b; margin-top: 6px;">
                        Scrapes ~10-30 cases in under a minute. Verifies login, DB inserts, progress tracking, and resume.
                    </p>
                </div>
            </div>

            <!-- Progress Matrix (Index mode only) -->
            <div class="card card-full hidden" id="matrixCard">
                <h2>Coverage Matrix</h2>
                <div class="matrix-container" id="matrixContainer">
                    <p style="color: #64748b;">Start an index scrape to see the coverage matrix.</p>
                </div>
                <div class="matrix-legend">
                    <span><span class="legend-dot" style="background:#334155;"></span> Pending</span>
                    <span><span class="legend-dot" style="background:#eab308;"></span> In Progress</span>
                    <span><span class="legend-dot" style="background:#22c55e;"></span> Completed</span>
                    <span><span class="legend-dot" style="background:#ef4444;"></span> Error</span>
                </div>
            </div>

            <!-- Cookies Card -->
            <div class="card">
                <h2>Authentication</h2>
                <div id="authStatus" style="margin-bottom: 15px; color: #64748b;">Checking...</div>
                <div class="form-group">
                    <label>Session ID</label>
                    <input type="text" id="sessionId" placeholder="ASP.NET_SessionId">
                </div>
                <div class="form-group">
                    <label>Verification Token</label>
                    <input type="text" id="verificationToken" placeholder="__RequestVerificationToken">
                </div>
                <button class="btn-save" onclick="saveCookies()">Save Cookies</button>
            </div>

            <!-- Controls -->
            <div class="card card-full">
                <h2>Controls</h2>
                <div class="controls">
                    <button class="btn-start" id="btnStart" onclick="startScraper()">Start Scraper</button>
                    <button class="btn-stop btn-disabled" id="btnStop" onclick="stopScraper()" disabled>Stop Scraper</button>
                    <button class="btn-download btn-disabled" id="btnDownload" onclick="downloadCSV()" disabled>Download CSV</button>
                </div>
                <div id="fileInfo" style="margin-top: 12px; font-size: 13px; color: #64748b;"></div>
            </div>

            <!-- Errors Card -->
            <div class="card card-full">
                <h2>Error Log</h2>
                <div class="error-log" id="errorLog">
                    <div class="no-errors">No errors</div>
                </div>
            </div>
        </div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
        let currentMode = 'keyword';
        let matrixInterval = null;

        function showToast(message, isError = false) {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = 'toast show' + (isError ? ' error' : '');
            setTimeout(() => toast.className = 'toast', 3000);
        }

        function setMode(mode) {
            currentMode = mode;
            document.getElementById('modeKeyword').className = 'mode-btn' + (mode === 'keyword' ? ' active' : '');
            document.getElementById('modeIndex').className = 'mode-btn' + (mode === 'index' ? ' active' : '');
            document.getElementById('keywordSettings').className = mode === 'keyword' ? 'card' : 'card hidden';
            document.getElementById('indexSettings').className = mode === 'index' ? 'card' : 'card hidden';
            document.getElementById('matrixCard').className = mode === 'index' ? 'card card-full' : 'card card-full hidden';
            document.getElementById('comboProgress').className = mode === 'index' ? '' : 'hidden';

            if (mode === 'index') {
                fetchMatrix();
                if (!matrixInterval) {
                    matrixInterval = setInterval(fetchMatrix, 5000);
                }
            } else {
                if (matrixInterval) {
                    clearInterval(matrixInterval);
                    matrixInterval = null;
                }
            }
        }

        function updateUI(data) {
            // Status
            const statusDot = document.getElementById('statusDot');
            const statusText = document.getElementById('statusText');
            const currentTask = document.getElementById('currentTask');

            if (data.running) {
                statusDot.className = 'status-dot running';
                statusText.textContent = 'Running';
                if (data.mode === 'index') {
                    currentTask.textContent = 'Index: ' + data.current_journal + ' ' + data.current_year;
                } else {
                    currentTask.textContent = 'Scraping: ' + data.keyword + ' | Last: ' + data.last_case;
                }
            } else {
                statusDot.className = 'status-dot stopped';
                statusText.textContent = 'Stopped';
                currentTask.textContent = '';
            }

            // Cases count
            document.getElementById('casesCount').textContent = data.cases;

            // Index combo progress
            if (data.mode === 'index') {
                document.getElementById('comboCounter').textContent =
                    data.combos_completed + ' / ' + data.combos_total + ' combinations';
                const progress = data.combos_total > 0 ?
                    Math.min((data.combos_completed / data.combos_total) * 100, 100) : 0;
                document.getElementById('progressBar').style.width = progress + '%';
            } else {
                const progress = data.total > 0 ? Math.min((data.cases / data.total) * 100, 100) : 0;
                document.getElementById('progressBar').style.width = progress + '%';
            }

            // Buttons
            document.getElementById('btnStart').disabled = data.running;
            document.getElementById('btnStart').className = data.running ? 'btn-start btn-disabled' : 'btn-start';
            document.getElementById('btnStop').disabled = !data.running;
            document.getElementById('btnStop').className = data.running ? 'btn-stop' : 'btn-stop btn-disabled';

            // Auth status
            document.getElementById('authStatus').textContent = data.auth_status;
            document.getElementById('authStatus').style.color = data.authenticated ? '#22c55e' : '#f87171';

            // Download button
            const btnDownload = document.getElementById('btnDownload');
            const fileInfo = document.getElementById('fileInfo');
            if (data.file_exists) {
                btnDownload.disabled = false;
                btnDownload.className = 'btn-download';
                fileInfo.textContent = 'File: ' + data.file_name + ' (' + data.file_size + ')';
            } else {
                btnDownload.disabled = true;
                btnDownload.className = 'btn-download btn-disabled';
                fileInfo.textContent = 'No file available yet';
            }

            // Errors
            const errorLog = document.getElementById('errorLog');
            if (data.errors && data.errors.length > 0) {
                errorLog.innerHTML = data.errors.map(e => '<div class="error-item">' + e + '</div>').join('');
            } else {
                errorLog.innerHTML = '<div class="no-errors">No errors</div>';
            }

            // Auto-detect mode from running state
            if (data.running && data.mode === 'index' && currentMode !== 'index') {
                setMode('index');
            }
        }

        async function fetchStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                updateUI(data);
            } catch (e) {
                console.error('Status fetch failed:', e);
            }
        }

        async function fetchMatrix() {
            try {
                const response = await fetch('/api/index-progress');
                const data = await response.json();
                renderMatrix(data);
            } catch (e) {
                console.error('Matrix fetch failed:', e);
            }
        }

        function renderMatrix(progress) {
            const container = document.getElementById('matrixContainer');
            const journals = ['PLD','SCMR','CLC','CLD','YLR','PCrLJ','PLC','PLC(CS)','PTD','MLD','GBLR','CLCN','YLRN','PCRLJN','PLCN','PLC(CS)N'];
            const startYear = 1947;
            const endYear = 2026;

            if (!progress.journals || Object.keys(progress.journals).length === 0) {
                container.innerHTML = '<p style="color: #64748b;">No progress data yet. Start an index scrape to see coverage.</p>';
                return;
            }

            // Build decade headers
            let decades = [];
            for (let y = startYear; y <= endYear; y += 10) {
                decades.push(y);
            }

            let html = '<table class="matrix-table"><thead><tr><th></th>';
            for (let d of decades) {
                let span = Math.min(10, endYear - d + 1);
                html += '<th colspan="' + span + '">' + d + 's</th>';
            }
            html += '</tr></thead><tbody>';

            for (let journal of journals) {
                html += '<tr><td class="journal-label">' + journal + '</td>';
                for (let y = startYear; y <= endYear; y++) {
                    let status = 'pending';
                    if (progress.journals[journal] && progress.journals[journal][y]) {
                        status = progress.journals[journal][y].status || 'pending';
                    }
                    let title = journal + ' ' + y + ': ' + status;
                    if (progress.journals[journal] && progress.journals[journal][y] && progress.journals[journal][y].cases_found !== undefined) {
                        title += ' (' + progress.journals[journal][y].cases_found + ' cases)';
                    }
                    html += '<td title="' + title + '"><div class="matrix-cell ' + status + '"></div></td>';
                }
                html += '</tr>';
            }
            html += '</tbody></table>';
            container.innerHTML = html;
        }

        async function startScraper() {
            let settings;
            if (currentMode === 'index') {
                settings = {
                    mode: 'index',
                    output_file: document.getElementById('indexOutputFile').value,
                    get_details: document.getElementById('indexGetDetails').checked,
                    num_workers: parseInt(document.getElementById('indexWorkers').value) || 3
                };
            } else {
                settings = {
                    mode: 'keyword',
                    keywords: document.getElementById('keywords').value,
                    year: document.getElementById('year').value,
                    max_cases: parseInt(document.getElementById('maxCases').value),
                    output_file: document.getElementById('outputFile').value
                };
            }

            try {
                const response = await fetch('/api/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(settings)
                });
                const data = await response.json();
                showToast(data.message, !data.success);
            } catch (e) {
                showToast('Failed to start scraper', true);
            }
        }

        async function startTestRun() {
            const settings = {
                mode: 'index',
                output_file: document.getElementById('indexOutputFile').value,
                get_details: document.getElementById('indexGetDetails').checked,
                num_workers: 1,
                test_mode: true
            };
            try {
                const response = await fetch('/api/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(settings)
                });
                const data = await response.json();
                showToast(data.message, !data.success);
            } catch (e) {
                showToast('Failed to start test run', true);
            }
        }

        async function stopScraper() {
            try {
                const response = await fetch('/api/stop', {method: 'POST'});
                const data = await response.json();
                showToast(data.message);
            } catch (e) {
                showToast('Failed to stop scraper', true);
            }
        }

        async function saveCookies() {
            const cookies = {
                session_id: document.getElementById('sessionId').value,
                verification_token: document.getElementById('verificationToken').value
            };

            try {
                const response = await fetch('/api/cookies', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(cookies)
                });
                const data = await response.json();
                showToast(data.message, !data.success);
                fetchStatus();
            } catch (e) {
                showToast('Failed to save cookies', true);
            }
        }

        function downloadCSV() {
            window.location.href = '/api/download';
        }

        async function loadSavedCookies() {
            try {
                const response = await fetch('/api/cookies');
                const data = await response.json();
                if (data.session_id) {
                    document.getElementById('sessionId').value = data.session_id;
                }
                if (data.verification_token) {
                    document.getElementById('verificationToken').value = data.verification_token;
                }
            } catch (e) {}
        }

        // Initial load
        loadSavedCookies();
        fetchStatus();

        // Poll for updates
        setInterval(fetchStatus, 2000);
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
        auth_status = "Authenticated" if authenticated else "Not authenticated"
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
    }
    return jsonify(result)


@app.route('/api/start', methods=['POST'])
def start_scraper():
    if state.is_running:
        return jsonify({'success': False, 'message': 'Already running'})

    # Setup scraper if needed
    if not state.scraper:
        success, msg = setup_scraper()
        if not success:
            return jsonify({'success': False, 'message': msg})

    # Verify authentication
    if not state.scraper._verify_login():
        return jsonify({'success': False, 'message': 'Not authenticated - please save valid cookies'})

    # Get settings from request
    data = request.json or {}
    state.mode = data.get('mode', 'keyword')
    state.output_file = data.get('output_file', 'scraped_cases.csv')
    state.get_details = data.get('get_details', True)

    # Reset state
    state.should_stop = False
    state.cases_scraped = 0
    state.errors = []
    state.start_time = datetime.now()
    state.is_running = True

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

    # Verify
    if state.scraper._verify_login():
        return jsonify({'success': True, 'message': 'Cookies saved and verified!'})
    else:
        return jsonify({'success': False, 'message': 'Cookies saved but verification failed'})


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

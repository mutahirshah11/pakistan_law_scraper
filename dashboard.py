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
        username=config.get('username', os.environ.get('PLS_USERNAME', 'LHCBAR8')),
        password=config.get('password', os.environ.get('PLS_PASSWORD', 'pakbar8')),
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
                        for _attempt in range(3):
                            try:
                                details = state.scraper.get_case_details(case_id)
                                case.update(details)
                                break
                            except SessionExpiredError:
                                state.errors.append(f"{case_id}: Session expired, re-authenticating...")
                                if not state.scraper._try_reauth():
                                    state.errors.append(f"{case_id}: Re-auth failed")
                                    break
                            except Exception as e:
                                state.errors.append(f"{case_id}: {str(e)[:50]}")
                                break

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
        .kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 20px; }
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

        /* Toast */
        .toast { position: fixed; bottom: 20px; right: 20px; padding: 12px 20px; background: #22c55e; color: white; border-radius: 8px; font-size: 14px; opacity: 0; transform: translateY(20px); transition: all 0.3s; }
        .toast.show { opacity: 1; transform: translateY(0); }
        .toast.error { background: #ef4444; }

        @media (max-width: 768px) {
            .kpi-row { grid-template-columns: repeat(2, 1fr); }
            .two-col { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Pakistan Law Scraper</h1>
        </header>

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
            <div class="card">
                <div class="section-title">Error Log</div>
                <div class="error-log" id="errorLog">
                    <div class="no-errors">No errors</div>
                </div>
            </div>
        </div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
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
            document.getElementById('kpiTotal').textContent = fmt(s.total_cases);
            document.getElementById('kpiTotalSub').textContent = fmt(s.cases_last_24h) + ' in last 24h';

            document.getElementById('kpiRate').textContent = fmt(s.cases_last_hour);
            document.getElementById('kpiRateSub').textContent = 'live (last hour)';

            const comp = s.total_cases > 0 ? ((s.cases_with_headnotes / s.total_cases) * 100).toFixed(1) : 0;
            document.getElementById('kpiCompletion').textContent = comp + '%';
            document.getElementById('kpiCompletionSub').textContent = fmt(s.cases_with_headnotes) + ' / ' + fmt(s.total_cases) + ' with headnotes';

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
                num_workers: parseInt(document.getElementById('indexWorkers').value) || 3
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

        // Polling intervals
        setInterval(fetchStatus, 2000);
        setInterval(fetchDashboardStats, 5000);
        setInterval(fetchMatrix, 10000);
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

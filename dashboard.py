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
from scraper import PakistanLawScraper

app = Flask(__name__)

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
        delay_range=(1.5, 3.0)
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
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Pakistan Law Scraper</h1>
            <p class="subtitle">Web Dashboard</p>
        </header>

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
                <div class="stat-label">cases scraped</div>
                <div class="progress-bar">
                    <div class="progress-fill" id="progressBar" style="width: 0%"></div>
                </div>
            </div>

            <!-- Settings Card -->
            <div class="card">
                <h2>Settings</h2>
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
        function showToast(message, isError = false) {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = 'toast show' + (isError ? ' error' : '');
            setTimeout(() => toast.className = 'toast', 3000);
        }

        function updateUI(data) {
            // Status
            const statusDot = document.getElementById('statusDot');
            const statusText = document.getElementById('statusText');
            const currentTask = document.getElementById('currentTask');

            if (data.running) {
                statusDot.className = 'status-dot running';
                statusText.textContent = 'Running';
                currentTask.textContent = 'Scraping: ' + data.keyword + ' | Last: ' + data.last_case;
            } else {
                statusDot.className = 'status-dot stopped';
                statusText.textContent = 'Stopped';
                currentTask.textContent = '';
            }

            // Cases count
            document.getElementById('casesCount').textContent = data.cases;

            // Progress bar
            const progress = data.total > 0 ? Math.min((data.cases / data.total) * 100, 100) : 0;
            document.getElementById('progressBar').style.width = progress + '%';

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

        async function startScraper() {
            const settings = {
                keywords: document.getElementById('keywords').value,
                year: document.getElementById('year').value,
                max_cases: parseInt(document.getElementById('maxCases').value),
                output_file: document.getElementById('outputFile').value
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
                showToast('Failed to start scraper', true);
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

    return jsonify({
        'running': state.is_running,
        'cases': state.cases_scraped,
        'total': state.total_cases,
        'keyword': state.current_keyword,
        'last_case': state.last_case_id,
        'errors': state.errors[-10:],  # Last 10 errors
        'authenticated': authenticated,
        'auth_status': auth_status,
        'file_exists': file_exists,
        'file_name': state.output_file,
        'file_size': file_size
    })


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
    state.keywords = [k.strip() for k in data.get('keywords', 'contract').split(',')]
    state.year = data.get('year', '5')
    state.max_cases = data.get('max_cases', 50)
    state.output_file = data.get('output_file', 'scraped_cases.csv')

    # Reset state
    state.should_stop = False
    state.cases_scraped = 0
    state.errors = []
    state.start_time = datetime.now()
    state.is_running = True
    state.scraper.processed_case_ids.clear()

    # Start worker thread
    state.thread = threading.Thread(target=scrape_worker, daemon=True)
    state.thread.start()

    return jsonify({'success': True, 'message': 'Scraper started'})


@app.route('/api/stop', methods=['POST'])
def stop_scraper():
    if not state.is_running:
        return jsonify({'success': False, 'message': 'Not running'})

    state.should_stop = True
    return jsonify({'success': True, 'message': 'Stopping...'})


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

    app.run(host='0.0.0.0', port=5001, debug=False)

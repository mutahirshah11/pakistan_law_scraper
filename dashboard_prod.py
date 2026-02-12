#!/usr/bin/env python3
"""
Production Dashboard
=====================
Flask dashboard backed by Supabase for real-time monitoring.
Same dark theme as original, enhanced with per-keyword progress,
speed metrics, and session health indicators.

Run: python dashboard_prod.py
Open: http://localhost:5001
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
import traceback
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request, send_file

load_dotenv()

# ── Logging Setup ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Quiet down noisy libraries
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

from db import SupabaseDB
from scraper_prod import ProductionScraper, JOURNALS

app = Flask(__name__)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "scraper_config.json")


class DashboardState:
    """Shared state between Flask and scraper thread."""
    scraper: ProductionScraper | None = None
    thread: threading.Thread | None = None
    is_running: bool = False
    loop: asyncio.AbstractEventLoop | None = None
    last_error: str = ""
    startup_error: str = ""


state = DashboardState()


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(config: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_file_size(filepath: str) -> str:
    if not os.path.exists(filepath):
        return ""
    size = os.path.getsize(filepath)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def scrape_worker(keywords, year, concurrency, skip_search, skip_details, reset_progress=False):
    """Background thread running the async scraper."""
    print(f"[SCRAPER] Starting worker: keywords={keywords}, year={year}, reset={reset_progress}", flush=True)
    logger.info(f"Starting scraper worker: keywords={keywords}, year={year}, concurrency={concurrency}, reset={reset_progress}")
    state.startup_error = ""
    state.last_error = ""
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    state.loop = loop

    try:
        print("[SCRAPER] Creating ProductionScraper...", flush=True)
        scraper = ProductionScraper(concurrency=concurrency)
        state.scraper = scraper
        state.is_running = True
        print("[SCRAPER] Starting run...", flush=True)
        logger.info("ProductionScraper initialized, starting run...")

        loop.run_until_complete(
            scraper.run(
                keywords=keywords,
                year=year,
                skip_search=skip_search,
                skip_details=skip_details,
                reset_progress=reset_progress,
            )
        )
        print("[SCRAPER] Run completed successfully", flush=True)
        logger.info("Scraper run completed successfully")
    except Exception as e:
        error_msg = f"Scraper error: {e}"
        print(f"[SCRAPER ERROR] {error_msg}", flush=True)
        print(traceback.format_exc(), flush=True)
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        state.last_error = str(e)
        state.startup_error = str(e)
    finally:
        state.is_running = False
        loop.close()
        print("[SCRAPER] Worker finished", flush=True)
        logger.info("Scraper worker finished")


# ── HTML Template ────────────────────────────────────────────────

DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pakistan Law Scraper - Production</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            min-height: 100vh;
            padding: 20px;
        }

        .container { max-width: 1100px; margin: 0 auto; }

        header {
            text-align: center;
            padding: 30px 0;
            border-bottom: 1px solid #334155;
            margin-bottom: 30px;
        }

        h1 { font-size: 28px; color: #38bdf8; margin-bottom: 4px; }
        .subtitle { color: #94a3b8; font-size: 14px; }
        .badge { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; margin-left: 8px; }
        .badge-running { background: #22c55e22; color: #22c55e; border: 1px solid #22c55e44; }
        .badge-stopped { background: #64748b22; color: #94a3b8; border: 1px solid #64748b44; }

        .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 20px; }
        .grid-2 { grid-template-columns: 1fr 1fr; }

        .card {
            background: #1e293b;
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #334155;
        }
        .card-full { grid-column: 1 / -1; }
        .card h2 {
            font-size: 13px;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 12px;
        }

        .stat-value { font-size: 32px; font-weight: 700; color: #f8fafc; }
        .stat-label { color: #64748b; font-size: 12px; margin-top: 4px; }
        .stat-sub { color: #38bdf8; font-size: 13px; margin-top: 6px; }

        .progress-bar { height: 6px; background: #334155; border-radius: 3px; overflow: hidden; margin-top: 8px; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, #38bdf8, #22c55e); transition: width 0.5s; }

        .controls { display: flex; gap: 12px; margin-top: 16px; flex-wrap: wrap; }

        button {
            padding: 12px 20px;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            flex: 1;
            min-width: 120px;
        }

        .btn-start { background: #22c55e; color: white; }
        .btn-start:hover { background: #16a34a; }
        .btn-stop { background: #ef4444; color: white; }
        .btn-stop:hover { background: #dc2626; }
        .btn-export { background: #a855f7; color: white; }
        .btn-export:hover { background: #9333ea; }
        .btn-phase2 { background: #f59e0b; color: #0f172a; }
        .btn-phase2:hover { background: #d97706; }
        .btn-save { background: #38bdf8; color: #0f172a; margin-top: 10px; width: 100%; }
        .btn-save:hover { background: #0ea5e9; }
        .btn-disabled { background: #334155 !important; color: #64748b !important; cursor: not-allowed !important; }

        .form-group { margin-bottom: 12px; }
        label { display: block; font-size: 12px; color: #94a3b8; margin-bottom: 4px; }
        input, select {
            width: 100%;
            padding: 8px 10px;
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 6px;
            color: #e2e8f0;
            font-size: 13px;
        }
        input:focus, select:focus { outline: none; border-color: #38bdf8; }

        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { text-align: left; color: #94a3b8; font-weight: 600; padding: 8px 6px; border-bottom: 1px solid #334155; }
        td { padding: 6px; border-bottom: 1px solid #1e293b; }
        tr:hover td { background: #1e293b88; }

        .phase-search { color: #38bdf8; }
        .phase-details { color: #f59e0b; }
        .phase-done { color: #22c55e; }

        .error-log {
            background: #0f172a;
            border-radius: 6px;
            padding: 10px;
            max-height: 120px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 12px;
        }
        .error-item { color: #f87171; padding: 3px 0; border-bottom: 1px solid #1e293b; }
        .no-errors { color: #22c55e; }

        .toast {
            position: fixed; bottom: 20px; right: 20px;
            padding: 12px 20px; background: #22c55e; color: white;
            border-radius: 8px; font-size: 14px;
            opacity: 0; transform: translateY(20px); transition: all 0.3s;
        }
        .toast.show { opacity: 1; transform: translateY(0); }
        .toast.error { background: #ef4444; }

        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .pulse { animation: pulse 2s infinite; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Pakistan Law Scraper <span class="badge" id="statusBadge">STOPPED</span></h1>
            <p class="subtitle">Production Dashboard</p>
        </header>

        <!-- Stats Row -->
        <div class="grid" id="statsGrid">
            <div class="card">
                <h2>Total Cases</h2>
                <div class="stat-value" id="totalCases">-</div>
                <div class="stat-label">in Supabase</div>
            </div>
            <div class="card">
                <h2>Details Fetched</h2>
                <div class="stat-value" id="detailsFetched">-</div>
                <div class="stat-sub" id="detailsPending">- pending</div>
            </div>
            <div class="card">
                <h2>Speed</h2>
                <div class="stat-value" id="speedRate">-</div>
                <div class="stat-label">cases/min</div>
                <div class="stat-sub" id="elapsed">-</div>
            </div>
            <div class="card">
                <h2>Errors</h2>
                <div class="stat-value" id="errorsCount">0</div>
                <div class="stat-sub" id="reauthCount">0 re-auths</div>
            </div>
        </div>

        <!-- Progress + Controls -->
        <div class="grid grid-2">
            <!-- Settings -->
            <div class="card">
                <h2>Settings</h2>
                <div class="form-group">
                    <label>Keywords (comma-separated, blank = all journals)</label>
                    <input type="text" id="keywords" value="" placeholder="PLD, SCMR, CLC (blank = all 16 journals)">
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Year Range</label>
                        <select id="year">
                            <option value="5">Last 5 years</option>
                            <option value="10">Last 10 years</option>
                            <option value="15">Last 15 years</option>
                            <option value="20">Last 20 years</option>
                            <option value="200" selected>All years</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Concurrency</label>
                        <input type="number" id="concurrency" value="15" min="1" max="30">
                    </div>
                    <div class="form-group">
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                            <input type="checkbox" id="freshStart" style="width: auto;">
                            Fresh Start (ignore previous progress)
                        </label>
                    </div>
                </div>
                <div class="controls">
                    <button class="btn-start" id="btnStart" onclick="startScraper(false)">Start / Resume</button>
                    <button class="btn-phase2" id="btnPhase2" onclick="startScraper(true)">Fetch Details Only</button>
                    <button class="btn-stop btn-disabled" id="btnStop" onclick="stopScraper()" disabled>Stop</button>
                </div>
                <div class="controls">
                    <button class="btn-export" onclick="exportCSV()">Export CSV</button>
                </div>
            </div>

            <!-- Authentication -->
            <div class="card">
                <h2>Authentication</h2>
                <div id="authStatus" style="margin-bottom: 12px; color: #64748b;">Checking...</div>
                <div class="form-group">
                    <label>Session ID (ASP.NET_SessionId)</label>
                    <input type="text" id="sessionId" placeholder="Paste session cookie">
                </div>
                <div class="form-group">
                    <label>Verification Token</label>
                    <input type="text" id="verificationToken" placeholder="Paste verification token">
                </div>
                <button class="btn-save" onclick="saveCookies()">Save & Verify Cookies</button>
            </div>
        </div>

        <!-- Per-keyword progress table -->
        <div class="grid">
            <div class="card card-full">
                <h2>Per-Keyword Progress</h2>
                <div style="overflow-x: auto;">
                    <table id="progressTable">
                        <thead>
                            <tr>
                                <th>Keyword</th>
                                <th>Year</th>
                                <th>Phase</th>
                                <th>Rows Scanned</th>
                                <th>Cases Found</th>
                                <th>Server Total</th>
                                <th>Updated</th>
                            </tr>
                        </thead>
                        <tbody id="progressBody">
                            <tr><td colspan="7" style="color: #64748b;">Loading...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Error log -->
        <div class="grid">
            <div class="card card-full">
                <h2>Recent Errors</h2>
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

        function updateUI(data) {
            const s = data.stats || {};
            const live = data.live || {};
            const progress = data.progress || [];
            const run = data.latest_run || {};

            const running = live.running || false;

            // Badge
            const badge = document.getElementById('statusBadge');
            badge.textContent = running ? (live.phase === 'search' ? 'SEARCHING' : 'FETCHING DETAILS') : 'STOPPED';
            badge.className = 'badge ' + (running ? 'badge-running pulse' : 'badge-stopped');

            // Stats
            document.getElementById('totalCases').textContent = (s.total_cases || 0).toLocaleString();
            document.getElementById('detailsFetched').textContent = (s.fetched_details || 0).toLocaleString();
            document.getElementById('detailsPending').textContent = (s.pending_details || 0).toLocaleString() + ' pending';
            document.getElementById('errorsCount').textContent = live.errors || run.errors_count || 0;
            document.getElementById('reauthCount').textContent = (live.reauth_count || 0) + ' re-auths';

            if (running) {
                document.getElementById('speedRate').textContent = live.rate_per_minute || '-';
                document.getElementById('elapsed').textContent = (live.elapsed_minutes || 0) + ' min';
            }

            // Buttons
            document.getElementById('btnStart').disabled = running;
            document.getElementById('btnStart').className = running ? 'btn-start btn-disabled' : 'btn-start';
            document.getElementById('btnPhase2').disabled = running;
            document.getElementById('btnPhase2').className = running ? 'btn-phase2 btn-disabled' : 'btn-phase2';
            document.getElementById('btnStop').disabled = !running;
            document.getElementById('btnStop').className = running ? 'btn-stop' : 'btn-stop btn-disabled';

            // Auth
            document.getElementById('authStatus').textContent = data.auth_status || 'Unknown';
            document.getElementById('authStatus').style.color = data.authenticated ? '#22c55e' : '#f87171';

            // Progress table
            const tbody = document.getElementById('progressBody');
            if (progress.length > 0) {
                tbody.innerHTML = progress.map(p => {
                    const phaseClass = p.phase === 'done' ? 'phase-done' : p.phase === 'details' ? 'phase-details' : 'phase-search';
                    const updated = p.updated_at ? new Date(p.updated_at).toLocaleTimeString() : '-';
                    return '<tr>' +
                        '<td><strong>' + p.keyword + '</strong></td>' +
                        '<td>' + p.year + '</td>' +
                        '<td class="' + phaseClass + '">' + p.phase + '</td>' +
                        '<td>' + (p.last_row || 0).toLocaleString() + '</td>' +
                        '<td>' + (p.cases_found || 0).toLocaleString() + '</td>' +
                        '<td>' + (p.total_found || 0).toLocaleString() + '</td>' +
                        '<td>' + updated + '</td>' +
                    '</tr>';
                }).join('');
            } else {
                tbody.innerHTML = '<tr><td colspan="7" style="color: #64748b;">No progress data yet</td></tr>';
            }

            // Error log
            const errorLog = document.getElementById('errorLog');
            const lastErr = live.last_error || (run.last_error || '');
            if (lastErr) {
                errorLog.innerHTML = '<div class="error-item">' + lastErr + '</div>';
            } else {
                errorLog.innerHTML = '<div class="no-errors">No errors</div>';
            }
        }

        async function fetchStatus() {
            try {
                const r = await fetch('/api/status');
                updateUI(await r.json());
            } catch (e) { console.error('Status fetch failed:', e); }
        }

        async function startScraper(phase2Only) {
            const kw = document.getElementById('keywords').value.trim();
            const freshStart = document.getElementById('freshStart').checked;
            
            // Confirm fresh start
            if (freshStart && !confirm('Fresh Start will clear all progress tracking. Continue?')) {
                return;
            }
            
            const body = {
                keywords: kw || null,
                year: document.getElementById('year').value,
                concurrency: parseInt(document.getElementById('concurrency').value) || 15,
                phase2: phase2Only,
                reset: freshStart
            };
            try {
                const r = await fetch('/api/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)
                });
                const d = await r.json();
                showToast(d.message, !d.success);
            } catch (e) { showToast('Failed to start', true); }
        }

        async function stopScraper() {
            try {
                const r = await fetch('/api/stop', {method: 'POST'});
                const d = await r.json();
                showToast(d.message);
            } catch (e) { showToast('Failed to stop', true); }
        }

        async function saveCookies() {
            const body = {
                session_id: document.getElementById('sessionId').value.trim(),
                verification_token: document.getElementById('verificationToken').value.trim()
            };
            try {
                const r = await fetch('/api/cookies', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)
                });
                const d = await r.json();
                showToast(d.message, !d.success);
                fetchStatus();
            } catch (e) { showToast('Failed to save cookies', true); }
        }

        async function exportCSV() {
            showToast('Exporting...');
            try {
                const r = await fetch('/api/export', {method: 'POST'});
                const d = await r.json();
                if (d.success) {
                    showToast(d.message);
                    // Trigger download
                    window.location.href = '/api/download?file=' + encodeURIComponent(d.file);
                } else {
                    showToast(d.message, true);
                }
            } catch (e) { showToast('Export failed', true); }
        }

        async function loadSavedCookies() {
            try {
                const r = await fetch('/api/cookies');
                const d = await r.json();
                if (d.session_id) document.getElementById('sessionId').value = d.session_id;
                if (d.verification_token) document.getElementById('verificationToken').value = d.verification_token;
            } catch (e) {}
        }

        loadSavedCookies();
        fetchStatus();
        setInterval(fetchStatus, 3000);
    </script>
</body>
</html>
'''


# ── Routes ───────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/status")
def api_status():
    db = SupabaseDB()
    stats = db.get_stats()
    progress = db.get_all_progress()
    latest_run = db.get_latest_run()

    # Live status from running scraper
    live = {}
    if state.scraper and state.is_running:
        live = state.scraper.get_live_status()
    
    # Include any startup errors
    if state.startup_error:
        live["startup_error"] = state.startup_error
    if state.last_error:
        live["last_error"] = state.last_error

    # Auth status
    config = load_config()
    authenticated = False
    auth_status = "Not authenticated"
    if config.get("session_id"):
        auth_status = "Cookies saved"
        authenticated = True

    return jsonify({
        "stats": stats,
        "progress": progress,
        "latest_run": latest_run,
        "live": live,
        "authenticated": authenticated,
        "auth_status": auth_status,
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    logger.info("API /api/start called")
    
    if state.is_running:
        logger.warning("Start rejected - already running")
        return jsonify({"success": False, "message": "Already running"})

    data = request.json or {}
    kw_str = data.get("keywords")
    keywords = [k.strip() for k in kw_str.split(",")] if kw_str else None
    year = data.get("year", "200")
    concurrency = data.get("concurrency", 15)
    phase2 = data.get("phase2", False)
    reset_progress = data.get("reset", False)  # Fresh start option

    logger.info(f"Starting scraper: keywords={keywords}, year={year}, concurrency={concurrency}, phase2={phase2}, reset={reset_progress}")
    
    # Clear previous errors
    state.startup_error = ""
    state.last_error = ""

    state.thread = threading.Thread(
        target=scrape_worker,
        args=(keywords, year, concurrency, phase2, False, reset_progress),
        daemon=True,
    )
    state.thread.start()

    mode = "detail fetch (Phase 2 only)" if phase2 else "full scrape"
    if reset_progress:
        mode += " (fresh start)"
    logger.info(f"Started {mode} thread")
    return jsonify({"success": True, "message": f"Started {mode}"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    if not state.is_running or not state.scraper:
        return jsonify({"success": False, "message": "Not running"})
    state.scraper.request_stop()
    return jsonify({"success": True, "message": "Stop signal sent — finishing current batch..."})


@app.route("/api/cookies", methods=["GET", "POST"])
def api_cookies():
    if request.method == "GET":
        config = load_config()
        return jsonify({
            "session_id": config.get("session_id", ""),
            "verification_token": config.get("verification_token", ""),
        })

    data = request.json
    sid = data.get("session_id", "").strip()
    token = data.get("verification_token", "").strip()

    if not sid or not token:
        return jsonify({"success": False, "message": "Both cookies required"})

    config = load_config()
    config["session_id"] = sid
    config["verification_token"] = token
    save_config(config)

    return jsonify({"success": True, "message": "Cookies saved"})


@app.route("/api/export", methods=["POST"])
def api_export():
    try:
        db = SupabaseDB()
        filepath = os.path.join(os.path.dirname(__file__), "export_cases.csv")
        count = db.export_csv(filepath)
        return jsonify({"success": True, "message": f"Exported {count:,} cases", "file": filepath})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/download")
def api_download():
    filepath = request.args.get("file", "export_cases.csv")
    if not os.path.exists(filepath):
        return jsonify({"success": False, "message": "File not found"}), 404
    return send_file(filepath, mimetype="text/csv", as_attachment=True, download_name="cases_export.csv")


# ── Main ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  Pakistan Law Scraper - Production Dashboard")
    print("=" * 50)
    print("\n  Open: http://localhost:5001")
    print("  Press Ctrl+C to stop\n")

    app.run(host="0.0.0.0", port=5001, debug=False)

# Pakistan Law Scraper

A web scraper for **pakistanlawsite.com** with a modern dashboard interface.

> Created by **[Zensbot.com](https://zensbot.com)**

![Python](https://img.shields.io/badge/Python-3.8+-blue)
![License](https://img.shields.io/badge/License-MIT-green)

## Features

- Web dashboard with real-time progress
- Start/Stop scraper controls
- Download scraped data as CSV
- Persistent cookie storage
- Configurable search (keywords, year range, max cases)
- Checkpoint/resume support

## Quick Start

```bash
# Clone
git clone https://github.com/yourusername/pakistan-law-scraper.git
cd pakistan-law-scraper

# Setup
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run
python dashboard.py
```

Open **http://localhost:5001**

## First Time Setup

1. Login to [pakistanlawsite.com](https://www.pakistanlawsite.com) in Chrome
2. Press F12 → Application tab → Cookies
3. Copy `ASP.NET_SessionId` and `__RequestVerificationToken`
4. Paste in dashboard → Save Cookies

Cookies persist locally - only need to redo if they expire.

## Settings

| Option | Description |
|--------|-------------|
| Keywords | Search terms (comma-separated) |
| Year Range | Last 5/10/15/20 years or all |
| Max Cases | Limit cases to scrape |
| Output File | CSV filename |

## Output CSV Columns

`case_id`, `citation`, `year`, `journal`, `court`, `petitioner`, `respondent`, `summary`, `head_notes`, `full_description`, `scraped_at`

## Project Files

```
├── dashboard.py      # Web dashboard (main)
├── scraper.py        # Core scraper
├── interactive.py    # Terminal UI (alternative)
├── requirements.txt
└── README.md
```

## Environment Variables (Optional)

```bash
export PLS_USERNAME="your_username"
export PLS_PASSWORD="your_password"
```

## License

MIT License

---

**[Zensbot.com](https://zensbot.com)** - Automation Solutions

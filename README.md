# Pakistan Law Scraper

**Production-ready web scraper for Pakistan Law Site with Supabase persistence, automatic crash recovery, and real-time monitoring.**

> Created by **[Zensbot.com](https://zensbot.com)**

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![License](https://img.shields.io/badge/License-MIT-green)

## 🎯 Features

- ✅ **Two-phase scraping**: Sequential search + concurrent detail fetching
- ✅ **Zero data loss**: Immediate writes to Supabase cloud database
- ✅ **Automatic resume**: Continues from exact position after any interruption
- ✅ **Crash recovery**: Systemd auto-restart with state preservation
- ✅ **Web dashboard**: Real-time monitoring at port 5001
- ✅ **Rate limiting**: Configurable concurrency and delays
- ✅ **Re-authentication**: Automatic session renewal
- ✅ **CSV export**: One-click data export via dashboard or CLI

## 📦 What Gets Scraped

**Journals:** PLD, SCMR, CLC, CLD, YLR, PCrLJ, PLC, PLC(CS), PTD, MLD, GBLR, CLCN, YLRN, PCRLJN, PLCN, PLC(CS)N (16 total)

**Data Fields:** Citation, Parties, Court, Year, Journal, Page, Keywords, Summary, Head Notes, Full Judgment Description

**Estimated Dataset:** 50,000-100,000+ cases spanning 200+ years

## 🚀 VPS Deployment (Production)

### Prerequisites
- Ubuntu 20.04+ or Debian 11+ VPS
- 2GB RAM minimum (4GB recommended)
- Python 3.10+
- Supabase account (free tier works)

### Automated Deployment

```bash
# 1. SSH into your VPS
ssh user@your-vps-ip

# 2. Run deployment script
curl -sSL https://raw.githubusercontent.com/yourrepo/pakistan-law-scraper/main/deploy_to_vps.sh | bash

# 3. Access dashboard
# Open: http://YOUR_VPS_IP:5001
```

**For detailed deployment instructions**, see **[VPS_DEPLOYMENT_GUIDE.md](VPS_DEPLOYMENT_GUIDE.md)**

## 💻 Local Development

```bash
# Clone
git clone https://github.com/yourusername/pakistan-law-scraper.git
cd pakistan-law-scraper

# Setup virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create .env file with your credentials
# (see .env.example or VPS_DEPLOYMENT_GUIDE.md)

# Run dashboard
python dashboard_prod.py
```

Open **http://localhost:5001**

## 🎮 Usage

### Via Dashboard (Recommended)
1. Open `http://YOUR_VPS_IP:5001`
2. Configure settings (keywords, year range, concurrency)
3. Click "Start / Resume"
4. Monitor progress in real-time
5. Export data with "Export CSV"

### Via CLI
```bash
# Check status
python run_production.py status

# Start full scrape (all journals)
python run_production.py start

# Specific journals
python run_production.py start --keywords "PLD,SCMR,CLC"

# Last 5 years only
python run_production.py start --year 5

# Export data
python run_production.py export -o cases.csv
```

### Via Systemd (on VPS)
```bash
# Start/stop
sudo systemctl start pakistan-scraper.service
sudo systemctl stop pakistan-scraper.service

# View logs
sudo journalctl -u pakistan-scraper.service -f
```

## 🗄️ Data Storage

All data is stored in **Supabase** (cloud PostgreSQL):
- **Zero data loss** - Every case written immediately
- **Always accessible** - Access via Supabase dashboard 24/7
- **Resume capability** - Progress tracked per keyword
- **Exportable** - CSV export anytime

**Access your data:**
- Dashboard: Click "Export CSV"
- CLI: `python run_production.py export`
- Supabase: https://supabase.com/dashboard/project/YOUR_PROJECT_ID

## 📈 Performance

| VPS Specs | Concurrency | Time (Full Scrape) |
|-----------|-------------|--------------------|
| 2GB RAM | 10-15 | 32-48 hours |
| 4GB RAM | 15-20 | 24-40 hours |
| 8GB RAM | 20-25 | 16-32 hours |

## 📁 Project Structure

```
pakistan_law_scraper/
├── Production Files
│   ├── db.py                    # Supabase database layer
│   ├── scraper_prod.py          # Production scraper
│   ├── run_production.py        # CLI runner
│   ├── dashboard_prod.py        # Web dashboard
│   └── setup_db.py             # Database setup
├── Original Files (archived)
│   ├── scraper.py              # Original scraper
│   ├── dashboard.py            # Original dashboard
│   └── interactive.py          # Terminal UI
├── Deployment
│   ├── deploy_to_vps.sh        # Automated deployment
│   ├── systemd/                # Service files
│   ├── verify_deployment.py    # Pre-deployment checks
│   └── verify_data.py          # Data verification
├── Documentation
│   ├── VPS_DEPLOYMENT_GUIDE.md # Complete deployment guide
│   ├── DEPLOYMENT_SUMMARY.md   # Overview & architecture
│   ├── QUICK_REFERENCE.md      # Command reference
│   └── README.md               # This file
└── Configuration
    ├── .env                     # Credentials (create this)
    ├── requirements.txt         # Dependencies
    └── scraper_config.json      # Session cookies
```

## 📚 Documentation

| File | Purpose |
|------|---------|
| **[VPS_DEPLOYMENT_GUIDE.md](VPS_DEPLOYMENT_GUIDE.md)** | Complete deployment guide |
| **[DEPLOYMENT_SUMMARY.md](DEPLOYMENT_SUMMARY.md)** | High-level overview |
| **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** | Quick commands |

## 🔧 Configuration

Create `.env` file:
```env
# Pakistan Law Site credentials
PLS_USERNAME=your_username
PLS_PASSWORD=your_password

# Supabase configuration
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_service_role_key
```

## 🛡️ Reliability

- **Zero data loss** - Immediate Supabase writes
- **Auto recovery** - Systemd restart + resume from DB
- **Retry logic** - Exponential backoff (5x)
- **Re-authentication** - Automatic session renewal
- **Error handling** - Rate limits, server errors, network failures

## 🆘 Troubleshooting

See **[VPS_DEPLOYMENT_GUIDE.md](VPS_DEPLOYMENT_GUIDE.md)** → "Troubleshooting" section

Quick checks:
```bash
# Service status
sudo systemctl status pakistan-scraper.service

# View logs
sudo journalctl -u pakistan-scraper.service -f

# Test database
python -c "from db import SupabaseDB; db = SupabaseDB(); print(db.get_stats())"
```

## 📝 Requirements

- **System**: Ubuntu 20.04+, 2GB+ RAM, Python 3.10+
- **Python**: See `requirements.txt`

## 📄 License

MIT License

---

**Made with ❤️ by [Zensbot.com](https://zensbot.com)** - Automation Solutions

🚀 **Ready to deploy?** See [VPS_DEPLOYMENT_GUIDE.md](VPS_DEPLOYMENT_GUIDE.md)

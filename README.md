# Pakistan Law Scraper

**Pakistanlawsite.com** se automatically legal cases scrape karta hai aur CSV (Excel) file mein save karta hai.

> Created by **[Zensbot.com](https://zensbot.com)**

---

## Kya Kya Data Milta Hai?

Har case ke liye yeh columns milte hain:

| Column | Kya hai |
|--------|---------|
| `case_id` | Case ka unique ID (e.g. `2026L3`) |
| `citation` | Citation (e.g. `2026 PLD 17 LAHORE-HIGH-COURT`) |
| `year` | Saal |
| `journal` | Journal name (PLD, SCMR, CLC...) |
| `page` | Page number |
| `court` | Court name |
| `petitioner` | Petition karne wala |
| `respondent` | Doosri party |
| `summary` | Summary |
| `head_notes` | Headnotes (poora legal summary) |
| `full_description` | Case ka poora text |
| `scraped_at` | Kab scrape hua |

---

## Pehli Baar Setup

```bash
# 1. Folder mein jao
cd "C:\Users\ADMIN\Desktop\ScraperLaw\pakistan_law_scraper"

# 2. Packages install karo (sirf ek baar)
pip install -r requirements.txt
```

---

## CHECK KARO — Sab Sahi Chal Raha Hai?

**Hamesha pehle yeh run karo:**

```bash
python check_scraper.py
```

Yeh automatically 6 cheezein check karta hai:
- Login ho raha hai ya nahi
- Search kaam kar rahi hai
- Pagination sahi hai
- Case details aa rahi hain
- Index search chal raha hai
- CSV file ban rahi hai

**Agar sab `[OK]` aaye — scraper ready hai!**

---

## SCRAPING — Kaise Chalayein?

### 1. POORI WEBSITE SCRAPE (Sab journals, 1947-2026)

```bash
python full_scrape.py
```

- 16 journals x 80 saal = **1,280 combinations**
- Lakhon cases
- Beech mein rok sako `Ctrl+C` — **progress save rahegi**
- Dobara chalao — **wahan se shuru hoga jahan ruka tha**
- Result: `saari_website.csv`

---

### 2. SPECIFIC JOURNAL SCRAPE (Sirf ek ya kuch journals)

`full_scrape.py` file kholo aur yeh line change karo:

```python
# Sab journals (default):
journals = None

# Sirf PLD:
journals = ['PLD']

# PLD aur SCMR:
journals = ['PLD', 'SCMR']

# Tax journals:
journals = ['PTD', 'CLD']

# Criminal journals:
journals = ['PCrLJ', 'PCRLJN']
```

**Sab Available Journals:**

| Journal | Kya hai |
|---------|---------|
| `PLD` | Pakistan Legal Decisions |
| `SCMR` | Supreme Court Monthly Review |
| `CLC` | Civil Law Cases |
| `CLD` | Company Law Decisions |
| `YLR` | Yearly Law Reporter |
| `PCrLJ` | Pakistan Criminal Law Journal |
| `PLC` | Pakistan Labour Cases |
| `PLC(CS)` | PLC Civil Service |
| `PTD` | Pakistan Tax Decisions |
| `MLD` | Monthly Law Digest |
| `GBLR` | Gilgit-Baltistan Law Reporter |
| `CLCN` | CLC Notes |
| `YLRN` | YLR Notes |
| `PCRLJN` | PCrLJ Notes |
| `PLCN` | PLC Notes |
| `PLC(CS)N` | PLC(CS) Notes |

---

### 3. SPECIFIC YEAR RANGE (Sirf kuch saal)

`full_scrape.py` file mein yeh lines change karo:

```python
# Sirf 2020 se 2026:
year_start = 2020
year_end   = 2026

# Sirf 2010 se 2015:
year_start = 2010
year_end   = 2015

# Sab saal 1947 se (default):
year_start = None   # ya 1947
year_end   = None   # ya 2026
```

---

### 4. QUICK TEST (Sirf 50 cases, 2-3 minute)

```bash
python run_scraper.py
# Menu mein "1" dabao
```

---

### 5. CUSTOM SCRAPE (Apni marzi se settings)

```bash
python run_scraper.py
# Menu mein "3" dabao
# Journal, year, max cases khud likhein
```

---

## RESULT FILE KAHAN MILEGI?

```
Full scrape:    saari_website.csv
Custom scrape:  aap jo naam dein
Test:           test_output.csv
Check tool:     manual_check_output.csv
```

**Excel mein kholne ka tarika:**
1. File pe double-click karo
2. Excel/LibreOffice mein khulegi
3. Har row = ek case
4. Columns mein sara data

---

## BEECH MEIN ROKNA HO?

```
Ctrl + C  dabao
```

Progress **automatically save** hoti hai `scrape_progress.json` mein.
Dobara `python full_scrape.py` chalao — **wahan se resume hoga**.

---

## ERRORS AUR SOLUTIONS

| Error | Matlab | Solution |
|-------|--------|----------|
| `Login failed` | Username/password galat | Credentials check karo |
| `Session expired` | Website ne logout kar diya | Scraper khud dobara login karega |
| `No cases returned` | Internet ya website issue | Thodi der baad dobara try karo |
| `Rate limited (429)` | Bahut fast request | Scraper khud slow ho jayega |

---

## LOG FILE (Kya Ho Raha Hai Dekhna Hai?)

```bash
# Live log dekhne ke liye:
type scraper.log

# Ya PowerShell mein:
Get-Content scraper.log -Wait
```

---

## PROJECT FILES

```
pakistan_law_scraper/
├── check_scraper.py    # CHECK: Sab sahi hai? (pehle yeh chalao)
├── full_scrape.py      # FULL: Poori website scrape
├── run_scraper.py      # MENU: Interactive options
├── scraper.py          # Core scraper engine
├── dashboard.py        # Web dashboard (browser se control)
├── scraper.log         # Log file (kya ho raha hai)
├── saari_website.csv   # Result file (full scrape)
└── scrape_progress.json # Progress tracker (resume ke liye)
```

---

## WEB DASHBOARD (Browser Se Control)

Agar terminal ki jagah browser se control karna ho:

```bash
python dashboard.py
```

Browser mein kholo: **http://localhost:5001**

---

## LOGIN CREDENTIALS

```
Username: LHCBAR8
Password: pakbar8
```

Ya environment variables set karo:
```bash
set PLS_USERNAME=LHCBAR8
set PLS_PASSWORD=pakbar8
```

---

*Created by [Zensbot.com](https://zensbot.com) — Automation Solutions*

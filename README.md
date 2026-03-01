# Lightyear Portfolio AI Analyst

An automated AI-powered investment portfolio analyst for personal [Lightyear](https://lightyear.com) brokerage accounts. Parses PDF statements, fetches multi-source market data, runs structured LLM analysis across three analytical lenses (fundamentals, technicals, news sentiment), computes portfolio-level risk metrics, generates HTML reports, emails a summary, and tracks recommendation performance over time.

Runs every Saturday via GitHub Actions (free).

---

## What It Does

1. **Parses Lightyear PDF statements** — extracts positions, quantities, values, and statement date via `pdfplumber`
2. **Fetches market data** from two sources:
   - **yfinance** — price, valuation metrics, 4 quarters + 4 years of financials, 1-year daily price history
   - **Finnhub** — company news with article summaries (equities); theme-filtered general news (ETFs)
3. **Computes technical indicators** in pure pandas — RSI(14), MACD(12,26,9), SMA(50/200), Bollinger Bands(20,2σ), volume ratio, 52-week range
4. **Runs LLM analysis** per position across three lenses:
   - Fundamentals (business quality, financial health, valuation, bull/bear case)
   - Technical analysis (trend regime, momentum, mean-reversion signals)
   - News sentiment (Finnhub article summaries synthesised into a directional signal)
5. **Computes portfolio-level risk metrics:**
   - Pairwise 1-year return correlation matrix
   - Position sizing vs conviction alignment (flags undersized/oversized positions)
   - Weighted portfolio beta + drawdown scenarios at −10/15/20/30/50%
6. **Generates a self-contained HTML report** with per-position cards and a portfolio-level summary including sizing bars, beta/drawdown table, and correlation heatmap
7. **Emails an Outlook-safe summary** with the full HTML report attached — open the attachment in any browser for the complete interactive report. The report is also archived to Supabase Storage.
8. **Stores everything in Supabase** — snapshots, positions, analyses, and run logs
9. **Tracks sold positions** — detects when a position disappears from the PDF, records exit price from yfinance, and computes post-sale returns at 30d / 90d / 180d to evaluate recommendation quality (premature / correct / neutral)
10. **Updates historical recommendation prices** — tracks price at time of recommendation for ongoing calibration

---

## Analysis Framework

Each position is evaluated independently with separate prompt schemas for equities and ETFs.

**Equities** — fundamentals-first, multi-year trend focus:
- Business quality & moat assessment (score 1–10)
- Financial health: revenue trend, margin trajectory, FCF quality (score 1–10)
- Valuation: implied growth rate vs actual trajectory, forward P/E analysis
- Technical analysis: RSI zone, MACD direction, MA cross, Bollinger position (bullish/neutral/bearish)
- News sentiment: synthesised from Finnhub article summaries (positive/neutral/negative)
- Bull & bear cases with specific numbers
- BUY / HOLD / SELL with low / medium / high conviction

**ETFs** — macro and thematic focus:
- Fund quality: issuer, expense ratio, AUM/liquidity
- Thematic exposure: macro tailwind strength, sector concentration
- Valuation: sector premium or discount to historical range
- Technical analysis: price trend, momentum, volume (same indicator set as equities)
- News sentiment: general financial news filtered by ETF theme keywords

**Portfolio summary** — cross-position synthesis covering concentration risk, top opportunity, top risk, market context, rebalancing suggestions, sizing/beta/correlation data fed to the LLM so it references specific symbols and numbers.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.13 |
| Package management | UV |
| PDF parsing | pdfplumber |
| Market data | yfinance (financials/metrics), Finnhub (news) |
| Technical indicators | pandas (pure, no extra dependency) |
| LLM — production | Anthropic Claude API |
| LLM — development | Groq (Llama) |
| Database & storage | Supabase (PostgreSQL + file storage) |
| Email delivery | Gmail SMTP (stdlib `smtplib`, no extra package) |
| Deployment | GitHub Actions (weekly Saturday cron, free) |

---

## Project Structure

```
main.py                        # Pipeline entry point, sold position tracking
src/
├── ingestion/
│   ├── lightyear.py           # PDF parser → PortfolioSnapshot
│   └── market.py              # yfinance + Finnhub + technical indicators + price history
├── analysis/
│   ├── prompts.py             # Equity and ETF prompt builders + portfolio summary prompt
│   └── analyst.py            # LLM orchestration, portfolio-level risk computations
├── database/
│   └── supabase_client.py     # All DB + Storage operations (snapshots, analyses, tracking, archival)
└── reporting/
    ├── report.py              # Self-contained HTML report generator
    └── email.py               # Outlook-safe summary email via Gmail SMTP
.github/
└── workflows/
    └── analyst.yml            # GitHub Actions schedule + manual trigger
```

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/stenraak/lightyear-portfolio-ai-analyst
cd lightyear-portfolio-ai-analyst
uv sync
```

### 2. Environment variables

Copy `.env.example` to `.env` and fill in:

```env
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_anon_key
ANTHROPIC_API_KEY=your_anthropic_key
GROQ_API_KEY=your_groq_api_key
FINNHUB_API_KEY=your_finnhub_api_key
LLM_PROVIDER=groq              # or: anthropic

# Email delivery (optional — skip to disable)
GMAIL_ADDRESS=your.address@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
REPORT_EMAIL_TO=recipient@example.com
```

Get a free Finnhub API key at [finnhub.io](https://finnhub.io) (60 req/min, no daily cap).

For `GMAIL_APP_PASSWORD`: enable 2-Step Verification on your Google account, then go to **myaccount.google.com → Security → App Passwords** and generate a password for "Mail". Copy the 16-character code.

### 3. Supabase schema

Run the following in the Supabase SQL editor:

```sql
-- Portfolio snapshots
create table portfolio_snapshots (
  id uuid primary key default gen_random_uuid(),
  statement_date date not null,
  total_investments_eur numeric,
  account_reference text,
  created_at timestamptz default now()
);

-- Individual positions per snapshot
create table positions (
  id uuid primary key default gen_random_uuid(),
  snapshot_id uuid references portfolio_snapshots(id),
  symbol text not null,
  quantity numeric,
  value_eur numeric,
  value_original text,
  currency text,
  created_at timestamptz default now()
);

-- LLM analyses per position
create table analyses (
  id uuid primary key default gen_random_uuid(),
  snapshot_id uuid references portfolio_snapshots(id),
  symbol text not null,
  recommendation text,
  conviction text,
  valuation_assessment text,
  asset_type text,
  raw jsonb,
  price_at_recommendation numeric,
  created_at timestamptz default now()
);

-- Sold position tracking
create table sold_positions (
  id uuid primary key default gen_random_uuid(),
  symbol text not null,
  sold_at date,
  exit_price_eur numeric,
  exit_currency text,
  quantity numeric,
  last_recommendation text,
  last_conviction text,
  last_analysis_id uuid,
  price_30d_after_sale numeric,
  return_30d numeric,
  price_90d_after_sale numeric,
  return_90d numeric,
  price_180d_after_sale numeric,
  return_180d numeric,
  verdict text,
  created_at timestamptz default now()
);

-- Pipeline run log
create table run_logs (
  id uuid primary key default gen_random_uuid(),
  used_new_pdf boolean,
  tickers text[],
  status text,
  error_message text,
  created_at timestamptz default now()
);
```

### 4. Supabase Storage buckets

Create two buckets in **Supabase → Storage**:

| Bucket name | Public |
|---|---|
| `portfolio-pdfs` | No — private, PDFs are sensitive |
| `reports` | **Yes** — public, used for report archival |

### 5. Run locally

```bash
# Place a Lightyear PDF in data/exports/ or upload to Supabase Storage portfolio-pdfs bucket

# Normal run (skips if < 5 days since last run)
uv run python main.py

# Force run regardless of schedule
uv run python main.py --force
```

The report is saved to `reports/report_<date>_<time>.html`, uploaded to Supabase Storage, and emailed if `GMAIL_*` env vars are set.

---

## Deployment (GitHub Actions)

The workflow in `.github/workflows/analyst.yml` runs automatically every Saturday at 08:00 UTC and can be triggered manually from the GitHub Actions UI.

### Add secrets to GitHub

Go to your repo → **Settings → Secrets and variables → Actions** and add:

| Secret | Where to get it |
|---|---|
| `SUPABASE_URL` | Supabase → Project Settings → API |
| `SUPABASE_KEY` | Supabase → Project Settings → API (anon key) |
| `SUPABASE_SERVICE_KEY` | Supabase → Project Settings → API (service_role key) |
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `LLM_PROVIDER` | `groq` or `anthropic` |
| `FINNHUB_API_KEY` | [finnhub.io](https://finnhub.io) |
| `GMAIL_ADDRESS` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | 16-char app password (see Setup step 2) |
| `REPORT_EMAIL_TO` | Where to send the report |

### PDF workflow

Upload your Lightyear statement PDF to the `portfolio-pdfs` Supabase Storage bucket before (or at the time of) each run. The pipeline downloads it automatically — no local files needed on the runner.

### Manual trigger

GitHub → Actions → Portfolio Analysis → **Run workflow**

---

## Email

The pipeline sends two things per run:

1. **Email body** — an Outlook-safe summary (table-based layout, light theme, inline styles). Renders correctly in Outlook for Windows, Outlook on the web, Gmail, and Apple Mail. Contains: position table, portfolio stats, top opportunity/risk, and portfolio action.
2. **Attachment** — the full `report.html` file. Open in any browser for the complete dark-theme report with sizing bars, beta/drawdown table, and correlation heatmap.

Email is skipped silently if `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, and `REPORT_EMAIL_TO` are not set.

---

## Configuration

### LLM provider

Switch between providers in `.env`:

```env
LLM_PROVIDER=groq        # Fast, free — good for development
LLM_PROVIDER=anthropic   # Claude — best analysis quality
```

### Adding new tickers

Tickers are read directly from the PDF. No configuration needed.

ETFs listed on non-US exchanges need an exchange suffix for yfinance. Add overrides to `TICKER_OVERRIDES` in `src/ingestion/market.py`:

```python
TICKER_OVERRIDES = {
    "EXX1": "EXX1.DE",   # XETRA
    "EXH1": "EXH1.DE",
}
```

For ETF news, add theme keywords to `ETF_NEWS_THEMES` in the same file so Finnhub general news is filtered correctly:

```python
ETF_NEWS_THEMES = {
    "EXX1": ["european bank", "ecb", "european financial"],
    "EXH1": ["european oil", "opec", "crude oil", "energy sector"],
}
```

---

## Recommendation Tracking

Every recommendation is stored with the price at the time of analysis. Prices are updated on each pipeline run to compute unrealised returns.

When a position disappears from the PDF, it is recorded in `sold_positions` with:
- Exit price (per-share, native currency) fetched from yfinance at detection time
- Post-sale prices at 30d / 90d / 180d updated automatically on subsequent runs
- **Verdict**: `premature` (price rose >5% after sale), `correct` (price fell >5%), or `neutral`

This creates a ground-truth dataset for evaluating recommendation quality over time.

---

## Tests

```bash
uv run pytest tests/ -v
```

Unit tests in `test_*.py`, integration tests in `test_*_integration.py`.

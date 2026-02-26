# Lightyear Portfolio AI Analyst

An automated AI-powered investment portfolio analyst for personal [Lightyear](https://lightyear.com) brokerage accounts. Parses PDF statements, fetches multi-source market data, runs structured LLM analysis across three analytical lenses (fundamentals, technicals, news sentiment), generates HTML reports, and tracks recommendation performance over time.

Runs on a 5-day schedule via Railway cron.

---

## What It Does

1. **Parses Lightyear PDF statements** — extracts positions, quantities, values, and statement date via `pdfplumber`
2. **Fetches market data** from two sources:
   - **yfinance** — price, valuation metrics, 4 quarters + 4 years of financials
   - **Finnhub** — company news with article summaries (equities); theme-filtered general news (ETFs)
3. **Computes technical indicators** in pure pandas — RSI(14), MACD(12,26,9), SMA(50/200), Bollinger Bands(20,2σ), volume ratio, 52-week range
4. **Runs LLM analysis** per position across three lenses:
   - Fundamentals (business quality, financial health, valuation, bull/bear case)
   - Technical analysis (trend regime, momentum, mean-reversion signals)
   - News sentiment (Finnhub article summaries synthesised into a directional signal)
5. **Generates a self-contained HTML report** with per-position cards and a portfolio-level summary
6. **Stores everything in Supabase** — snapshots, positions, analyses, and run logs
7. **Tracks sold positions** — detects when a position disappears from the PDF, records exit price from yfinance, and computes post-sale returns at 30d / 90d / 180d to evaluate recommendation quality (premature / correct / neutral)
8. **Updates historical recommendation prices** — tracks price at time of recommendation for ongoing calibration

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

**Portfolio summary** — cross-position synthesis covering concentration risk, top opportunity, top risk, market context, and rebalancing suggestions.

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
| Deployment | Railway (5-day cron) |

---

## Project Structure

```
main.py                        # Pipeline entry point, sold position tracking
src/
├── ingestion/
│   ├── lightyear.py           # PDF parser → PortfolioSnapshot
│   └── market.py              # yfinance + Finnhub + technical indicators
├── analysis/
│   ├── prompts.py             # Equity and ETF prompt builders + JSON schemas
│   └── analyst.py            # LLM orchestration, provider abstraction
├── database/
│   └── supabase_client.py     # All DB operations (snapshots, analyses, tracking)
└── reporting/
    └── report.py              # Self-contained HTML report generator
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
SUPABASE_URL=
SUPABASE_KEY=
ANTHROPIC_API_KEY=
GROQ_API_KEY=
FINNHUB_API_KEY=
LLM_PROVIDER=groq          # or: anthropic
```

Get a free Finnhub API key at [finnhub.io](https://finnhub.io) (60 req/min, no daily cap).

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

### 4. Upload a PDF

Upload your Lightyear account statement PDF to a Supabase Storage bucket named `portfolio-pdfs`, or place it in `data/exports/`.

### 5. Run

```bash
# Normal run (skips if < 5 days since last run)
uv run python main.py

# Force run regardless of schedule
uv run python main.py --force
```

The report is saved to `reports/report_<date>_<time>.html`.

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

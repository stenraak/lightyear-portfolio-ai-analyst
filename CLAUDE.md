# lightyear-portfolio-ai-analyst

## Project Overview
AI-powered investment portfolio analyst for personal Lightyear brokerage account.
Parses PDF statements, fetches market data, runs LLM analysis, stores in Supabase,
generates HTML reports. Runs on a 5-day cron schedule via Railway.

## Tech Stack
- Python 3.13, UV for package management (not pip)
- Anthropic Claude API (production) / Groq Llama (development)
- Supabase for storage (PostgreSQL + file storage)
- yfinance for market data
- pdfplumber for PDF parsing
- FastAPI, Railway for deployment

## Project Structure
src/ingestion/   — PDF parser (lightyear.py) and market data (market.py)
src/analysis/    — LLM prompts (prompts.py) and analyst orchestration (analyst.py)
src/database/    — Supabase client (supabase_client.py)
src/reporting/   — HTML report generator (report.py)
main.py          — Entry point with 5-day trigger logic

## Key Conventions
- Always use UV: `uv add <package>` not pip install
- All DB operations go through src/database/supabase_client.py
- LLM provider is swappable via LLM_PROVIDER in .env (groq or anthropic)
- ETFs (EXX1, EXH1) need .DE suffix for yfinance — handled in TICKER_OVERRIDES
- Quarterly financials only fetched for EQUITY type, not ETFs
- Tests: unit tests in test_*.py, integration tests in test_*_integration.py
- Run all tests: `uv run pytest tests/ -v`
- Run with force: `uv run python main.py --force`
- Always run with force end to end after doing changes

## Environment Variables Required
SUPABASE_URL, SUPABASE_KEY, GROQ_API_KEY, ANTHROPIC_API_KEY, LLM_PROVIDER

## What To Avoid
- Never hardcode API keys
- Never use pip directly — always UV
- Don't fetch quarterly financials for ETFs
- Don't break the _parse_json_response robustness — LLMs return messy JSON
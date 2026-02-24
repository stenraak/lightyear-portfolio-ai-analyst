# Lightyear Portfolio AI Analyst

An automated AI-powered investment portfolio analyst that evaluates 
holdings from Lightyear exports, generates structured buy/hold/sell 
recommendations, and tracks recommendation performance over time.

## What It Does

- Parses Lightyear portfolio CSV exports
- Fetches financial statements, valuation metrics, and recent news 
  for each holding via yfinance
- Analyses each position using Claude (Anthropic) with a structured 
  credit-risk-inspired evaluation framework
- Generates an HTML report with per-stock analysis and 
  portfolio-level summary
- Stores recommendations in Supabase and tracks performance 
  over time for calibration
- Runs automatically every 5 days via Railway cron, 
  using the latest portfolio export or the most recent stored snapshot

## Architecture

Lightyear CSV → Data Ingestion → Market Data Fetch → 
LLM Analysis → Report Generation → Supabase Storage → 
Recommendation Tracking

## Stack

- Python 3.11
- Anthropic Claude API
- yfinance for market data
- Supabase for storage and recommendation tracking
- FastAPI
- Railway for deployment
- Docker

## Setup

1. Clone the repo
2. Copy .env.example to .env and fill in credentials
3. pip install -r requirements.txt
4. Run: python main.py

## Project Structure

src/
├── ingestion/     # Lightyear CSV parser and market data fetching
├── analysis/      # LLM prompts and analysis logic
├── database/      # Supabase operations
└── reporting/     # HTML report generation

## Evaluation

Recommendations are stored with the price at time of recommendation. 
Performance is tracked at 30 and 90 day intervals to assess 
calibration and systematic biases in the model.
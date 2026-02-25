"""
Main pipeline entry point.

Flow:
1. Check if 5 days have passed since last run
2. Check if new PDF has been uploaded to Supabase Storage
3. Parse PDF (new or latest stored snapshot)
4. Fetch market data
5. Run LLM analysis
6. Store results in Supabase
7. Generate HTML report
8. Update historical recommendation prices
"""

import os
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from src.ingestion.lightyear import parse_lightyear_pdf, PortfolioSnapshot
from src.analysis.analyst import analyze_portfolio
from src.reporting.report import generate_report
from src.database.supabase_client import (
    get_client,
    store_snapshot,
    store_analysis,
    should_run,
    log_run,
    update_recommendation_prices,
)

load_dotenv()

PDF_BUCKET = "portfolio-pdfs"
REPORTS_DIR = Path("reports")


# ---------------------------------------------------------------------------
# PDF resolution — new upload or fallback to local
# ---------------------------------------------------------------------------

def get_pdf_from_storage() -> tuple[Path | None, bool]:
    """
    Check Supabase Storage for a new PDF upload.

    Returns:
        (local_path, is_new) — is_new=True if fetched from storage
    """
    client = get_client()

    try:
        files = client.storage.from_(PDF_BUCKET).list(
            options={"sortBy": {"column": "created_at", "order": "desc"}}
        )

        if not files:
            return None, False

        latest_file = files[0]
        latest_filename = latest_file["name"]

        # Download to local temp path
        local_path = Path("data/exports") / latest_filename
        local_path.parent.mkdir(parents=True, exist_ok=True)

        if not local_path.exists():
            print(f"Downloading new PDF: {latest_filename}")
            data = client.storage.from_(PDF_BUCKET).download(latest_filename)
            local_path.write_bytes(data)
            return local_path, True

        return local_path, False

    except Exception as e:
        print(f"Could not fetch from Supabase Storage: {e}")
        return None, False


def resolve_pdf() -> tuple[Path | None, bool]:
    """
    Resolve which PDF to use for this run.

    Priority:
    1. New PDF from Supabase Storage
    2. Most recent local PDF in data/exports/
    3. None (abort)

    Returns:
        (pdf_path, is_new)
    """
    # Try storage first
    storage_path, is_new = get_pdf_from_storage()
    if storage_path:
        return storage_path, is_new

    # Fall back to most recent local file
    exports_dir = Path("data/exports")
    exports_dir.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(exports_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime)

    if pdfs:
        print(f"Using local PDF: {pdfs[-1].name}")
        return pdfs[-1], False

    return None, False

def detect_sold_positions(
    current_symbols: set[str],
) -> list[str]:
    """
    Compare current portfolio against previous snapshot.
    Returns symbols that were held before but are gone now.
    """
    client = get_client()
    
    # Get previous snapshot positions
    prev_snapshot = client.table("portfolio_snapshots")\
        .select("id")\
        .order("created_at", desc=True)\
        .offset(1) \
        .limit(1)\
        .execute()
    
    if not prev_snapshot.data:
        return []
    
    prev_id = prev_snapshot.data[0]["id"]
    prev_positions = client.table("positions")\
        .select("symbol")\
        .eq("snapshot_id", prev_id)\
        .execute()
    
    prev_symbols = {r["symbol"] for r in prev_positions.data}
    sold = prev_symbols - current_symbols
    
    return list(sold)

def record_sold_position(symbol: str, statement_date: date) -> None:
    """Store exit data for a position that just disappeared."""
    import yfinance as yf
    from src.ingestion.market import _resolve_symbol

    client = get_client()

    # Idempotency: skip if already recorded for this symbol + date
    existing = client.table("sold_positions")\
        .select("id")\
        .eq("symbol", symbol)\
        .eq("sold_at", str(statement_date))\
        .limit(1)\
        .execute()
    if existing.data:
        print(f"  {symbol}: already recorded as sold, skipping.")
        return

    # Get last analysis for this symbol
    last_analysis = client.table("analyses")\
        .select("*")\
        .eq("symbol", symbol)\
        .order("created_at", desc=True)\
        .limit(1)\
        .execute()

    if not last_analysis.data:
        return

    analysis = last_analysis.data[0]

    # Get last known position data
    last_position = client.table("positions")\
        .select("*")\
        .eq("symbol", symbol)\
        .order("created_at", desc=True)\
        .limit(1)\
        .execute()

    pos = last_position.data[0] if last_position.data else {}

    # Fetch per-share exit price in native currency from yfinance
    exit_price = None
    exit_currency = None
    try:
        info = yf.Ticker(_resolve_symbol(symbol)).info
        exit_price = info.get("currentPrice") or info.get("regularMarketPrice")
        exit_currency = info.get("currency")
    except Exception as e:
        print(f"  Warning: could not fetch exit price for {symbol}: {e}")

    client.table("sold_positions").insert({
        "symbol": symbol,
        "sold_at": str(statement_date),
        "exit_price_eur": exit_price,        # per-share, native currency
        "exit_currency": exit_currency,
        "quantity": pos.get("quantity"),
        "last_recommendation": analysis.get("recommendation"),
        "last_conviction": analysis.get("conviction"),
        "last_analysis_id": analysis.get("id"),
    }).execute()

    print(f"Recorded exit for {symbol}")


def evaluate_sold_positions() -> None:
    """Fill in post-sale prices and compute verdict."""
    import yfinance as yf
    from src.ingestion.market import _resolve_symbol

    client = get_client()

    # Fetch rows where any time-bucket is still unfilled
    result = client.table("sold_positions")\
        .select("*")\
        .or_("price_30d_after_sale.is.null,price_90d_after_sale.is.null,price_180d_after_sale.is.null")\
        .execute()

    for row in result.data:
        sold_date = date.fromisoformat(row["sold_at"])
        days_elapsed = (date.today() - sold_date).days

        if days_elapsed < 30:
            continue

        info = yf.Ticker(_resolve_symbol(row["symbol"])).info
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")

        if not current_price:
            continue

        exit_price = row["exit_price_eur"]   # now per-share in native currency
        if not exit_price:
            continue

        return_pct = (current_price - exit_price) / exit_price * 100

        # Verdict logic
        # If price went up after you sold — premature
        # If price went down after you sold — correct
        # Threshold: 5% to avoid noise
        verdict = None
        if days_elapsed >= 90:
            if return_pct > 5:
                verdict = "premature"   # should have held
            elif return_pct < -5:
                verdict = "correct"     # good exit
            else:
                verdict = "neutral"     # didn't matter much

        update_data = {}
        if row["price_30d_after_sale"] is None and days_elapsed >= 30:
            update_data["price_30d_after_sale"] = current_price
            update_data["return_30d"] = return_pct
        if row["price_90d_after_sale"] is None and days_elapsed >= 90:
            update_data["price_90d_after_sale"] = current_price
            update_data["return_90d"] = return_pct
            update_data["verdict"] = verdict
        if row["price_180d_after_sale"] is None and days_elapsed >= 180:
            update_data["price_180d_after_sale"] = current_price
            update_data["return_180d"] = return_pct

        if not update_data:
            continue

        client.table("sold_positions")\
            .update(update_data)\
            .eq("id", row["id"])\
            .execute()

        print(f"{row['symbol']}: {verdict} — "
              f"{return_pct:+.1f}% since sale")

# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(force: bool = False) -> bool:
    """
    Run the full analysis pipeline.

    Args:
        force: Skip the 5-day interval check and run regardless

    Returns:
        True if pipeline ran successfully
    """
    print(f"\n{'='*50}")
    print(f"Portfolio Analyst — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")

    # --- 5-day gate ---
    if not force and not should_run(interval_days=5):
        print("Less than 5 days since last run. Skipping.")
        print("Use --force to run anyway.")
        return False

    # --- Resolve PDF ---
    pdf_path, is_new = resolve_pdf()
    if not pdf_path:
        msg = "No PDF found. Upload a Lightyear statement to Supabase Storage or data/exports/."
        print(msg)
        log_run(
            used_new_pdf=False,
            tickers=[],
            status="error",
            error_message=msg,
        )
        return False

    print(f"PDF: {pdf_path} ({'new' if is_new else 'existing'})\n")

    # --- Update historical prices before new run ---
    print("Updating historical recommendation prices...")
    update_recommendation_prices()

    try:
        # --- Parse PDF ---
        print("Parsing portfolio statement...")
        snapshot = parse_lightyear_pdf(pdf_path)
        print(f"Found {len(snapshot.positions)} positions — "
              f"total €{snapshot.total_investments_eur:,.2f}\n")

        tickers = [p.symbol for p in snapshot.positions]

        # In update step alongside recommendation prices
        evaluate_sold_positions()

        # --- Store snapshot ---
        snapshot_id = store_snapshot(snapshot)

        sold = detect_sold_positions(set(tickers))

        for symbol in sold:
            print(f"Detected sale: {symbol}")
            record_sold_position(symbol, snapshot.statement_date)

        # --- Run analysis ---
        portfolio_analysis = analyze_portfolio(snapshot)

        # --- Store analysis ---
        store_analysis(portfolio_analysis, snapshot_id)

        # --- Generate report ---
        REPORTS_DIR.mkdir(exist_ok=True)
        report_filename = (
            f"report_{snapshot.statement_date}_"
            f"{datetime.now().strftime('%H%M')}.html"
        )
        report_path = generate_report(
            portfolio_analysis,
            output_path=REPORTS_DIR / report_filename,
        )

        # --- Log success ---
        log_run(
            used_new_pdf=is_new,
            tickers=tickers,
            status="success",
        )

        print(f"\n{'='*50}")
        print(f"Done. Report: {report_path}")
        print(f"{'='*50}\n")
        return True

    except Exception as e:
        error_msg = str(e)
        print(f"\nPipeline failed: {error_msg}")
        log_run(
            used_new_pdf=is_new,
            tickers=[],
            status="error",
            error_message=error_msg,
        )
        return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    force = "--force" in sys.argv
    success = run_pipeline(force=force)
    sys.exit(0 if success else 1)
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
from datetime import datetime
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

        # --- Store snapshot ---
        snapshot_id = store_snapshot(snapshot)

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
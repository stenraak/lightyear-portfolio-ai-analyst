"""
Supabase client for storing portfolio snapshots, analyses and run logs.
"""

import os
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from supabase import create_client, Client

from src.ingestion.lightyear import PortfolioSnapshot
from src.analysis.analyst import PortfolioAnalysis

load_dotenv()

_client: Optional[Client] = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        # Service role key bypasses RLS and is required for Storage access.
        # Falls back to anon key so existing local setups without the service
        # key continue to work for DB-only operations.
        key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY (or SUPABASE_KEY) must be set"
            )
        _client = create_client(url, key)
    return _client


# ---------------------------------------------------------------------------
# Snapshot storage
# ---------------------------------------------------------------------------

def store_snapshot(snapshot: PortfolioSnapshot) -> str:
    """
    Store a portfolio snapshot and its positions.
    Returns the snapshot UUID. If a snapshot for the same statement_date
    and account already exists, returns the existing id without re-inserting.
    """
    client = get_client()

    existing = client.table("portfolio_snapshots") \
        .select("id") \
        .eq("statement_date", str(snapshot.statement_date)) \
        .eq("account_reference", snapshot.account_reference) \
        .limit(1) \
        .execute()

    if existing.data:
        existing_id = str(existing.data[0]["id"])  # type: ignore
        print(f"Snapshot for {snapshot.statement_date} already exists "
              f"({existing_id}), skipping insert.")
        return existing_id

    # Insert snapshot header
    result = client.table("portfolio_snapshots").insert({
        "statement_date": str(snapshot.statement_date),
        "account_reference": snapshot.account_reference,
        "total_investments_eur": snapshot.total_investments_eur,
        "total_portfolio_eur": snapshot.total_portfolio_eur,
        "cash_eur": snapshot.cash_eur,
    }).execute()

    snapshot_id = str(result.data[0]["id"]) # type: ignore

    # Insert all positions
    positions_data = [
        {
            "snapshot_id": snapshot_id,
            "symbol": p.symbol,
            "name": p.name,
            "isin": p.isin,
            "quantity": p.quantity,
            "value_original": p.value_original,
            "value_eur": p.value_eur,
            "currency": p.currency,
        }
        for p in snapshot.positions
    ]
    client.table("positions").insert(positions_data).execute()

    print(f"Stored snapshot {snapshot_id} "
          f"({len(snapshot.positions)} positions)")
    return snapshot_id


# ---------------------------------------------------------------------------
# Analysis storage
# ---------------------------------------------------------------------------

def store_analysis(
    portfolio_analysis: PortfolioAnalysis,
    snapshot_id: str,
) -> list[str]:
    """
    Store all position analyses and portfolio summary.
    Returns list of analysis UUIDs.
    """
    client = get_client()
    analysis_ids = []

    for pos_analysis in portfolio_analysis.positions:
        # model_dump() gives all fields except raw (stored as raw_analysis) and
        # asset_type (not a DB column). Both are handled explicitly below.
        analysis_row = pos_analysis.model_dump(exclude={"raw", "asset_type"})
        analysis_row.update({
            "snapshot_id": snapshot_id,
            "raw_analysis": pos_analysis.raw,
        })
        result = client.table("analyses").insert(analysis_row).execute()

        analysis_id = result.data[0]["id"] # type: ignore
        analysis_ids.append(analysis_id)

        # Seed recommendation tracking row — prices filled in later
        if not pos_analysis.fetch_error:
            current_price = None
            if pos_analysis.symbol in portfolio_analysis.market_data:
                md = portfolio_analysis.market_data[pos_analysis.symbol]
                current_price = md.metrics.current_price

            client.table("recommendation_tracking").insert({
                "analysis_id": analysis_id,
                "symbol": pos_analysis.symbol,
                "recommendation": pos_analysis.recommendation,
                "price_at_recommendation": current_price,
            }).execute()

    # Store portfolio summary once on the snapshot row rather than duplicating
    # it across every analysis row.
    # Requires: ALTER TABLE portfolio_snapshots ADD COLUMN portfolio_summary JSONB;
    if portfolio_analysis.portfolio_summary:
        try:
            client.table("portfolio_snapshots") \
                .update({"portfolio_summary": portfolio_analysis.portfolio_summary}) \
                .eq("id", snapshot_id).execute()
        except Exception as e:
            print(f"Warning: Could not store portfolio summary on snapshot: {e}")

    print(f"Stored {len(analysis_ids)} analyses")
    return analysis_ids


# ---------------------------------------------------------------------------
# Run trigger logic
# ---------------------------------------------------------------------------

def get_last_run_date() -> Optional[datetime]:
    """Return datetime of last successful analysis run, or None."""
    client = get_client()
    try:
        result = client.table("run_log") \
            .select("ran_at") \
            .eq("status", "success") \
            .order("ran_at", desc=True) \
            .limit(1) \
            .execute()

        if result.data:
            return datetime.fromisoformat(result.data[0]["ran_at"]) # type: ignore
        return None
    except Exception:
        return None


def should_run(interval_days: int = 5) -> bool:
    """Return True if enough time has passed since last run."""
    last_run = get_last_run_date()
    if last_run is None:
        return True
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)
    delta = datetime.now(tz=timezone.utc) - last_run
    return delta.days >= interval_days



# ---------------------------------------------------------------------------
# Run logging
# ---------------------------------------------------------------------------

def log_run(
    used_new_pdf: bool,
    tickers: list[str],
    status: str,
    error_message: Optional[str] = None,
) -> None:
    """Log each pipeline execution for debugging."""
    client = get_client()
    try:
        client.table("run_log").insert({
            "used_new_pdf": used_new_pdf,
            "tickers_analyzed": tickers,
            "status": status,
            "error_message": error_message,
        }).execute()
    except Exception as e:
        print(f"Warning: Could not write run log: {e}")


# ---------------------------------------------------------------------------
# Recommendation tracking updater
# ---------------------------------------------------------------------------

def update_recommendation_prices() -> None:
    """
    Fill in 30d and 90d prices for tracked recommendations.
    Called at the start of each run to update historical records.

    Two passes:
    - Pass 1: rows where price_30d_later IS NULL and >= 30 days elapsed.
              Also fills price_90d_later if >= 90 days (first run after 90d).
    - Pass 2: rows where price_90d_later IS NULL but price_30d_later is already
              filled (30d was captured at day 30; 90d needs a separate update).
    """
    import yfinance as yf

    client = get_client()

    def _fetch_price(symbol: str) -> Optional[float]:
        hist = yf.Ticker(symbol).history(period="3mo")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])

    def _return_pct(current: float, entry: Optional[float]) -> Optional[float]:
        if entry:
            return (current - entry) / entry * 100
        return None

    try:
        # --- Pass 1: fill 30d price (and 90d if >= 90 days elapsed) ---
        rows_30 = client.table("recommendation_tracking") \
            .select("*") \
            .is_("price_30d_later", "null") \
            .execute()

        for row in rows_30.data:  # type: ignore
            tracked_at = datetime.fromisoformat(
                row["tracked_at"].replace("Z", "+00:00")
            )
            days_elapsed = (datetime.now(tz=timezone.utc) - tracked_at).days
            if days_elapsed < 30:
                continue

            price = _fetch_price(row["symbol"])
            if price is None:
                continue

            price_at = row["price_at_recommendation"]
            update_data: dict = {
                "price_30d_later": price,
                "return_30d": _return_pct(price, price_at),
            }
            if days_elapsed >= 90:
                update_data["price_90d_later"] = price
                update_data["return_90d"] = _return_pct(price, price_at)

            client.table("recommendation_tracking") \
                .update(update_data).eq("id", row["id"]).execute()  # type: ignore

        # --- Pass 2: fill 90d price for rows where 30d was already captured ---
        # These rows were missed by Pass 1 because price_30d_later is no longer NULL.
        rows_90 = client.table("recommendation_tracking") \
            .select("*") \
            .is_("price_90d_later", "null") \
            .not_.is_("price_30d_later", "null") \
            .execute()

        for row in rows_90.data:  # type: ignore
            tracked_at = datetime.fromisoformat(
                row["tracked_at"].replace("Z", "+00:00")
            )
            days_elapsed = (datetime.now(tz=timezone.utc) - tracked_at).days
            if days_elapsed < 90:
                continue

            price = _fetch_price(row["symbol"])
            if price is None:
                continue

            price_at = row["price_at_recommendation"]
            client.table("recommendation_tracking") \
                .update({
                    "price_90d_later": price,
                    "return_90d": _return_pct(price, price_at),
                }).eq("id", row["id"]).execute()  # type: ignore

        print("Recommendation tracking updated")

    except Exception as e:
        print(f"Warning: Could not update recommendation prices: {e}")
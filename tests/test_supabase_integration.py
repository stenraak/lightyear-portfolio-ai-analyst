# tests/test_supabase_integration.py
"""
Integration tests against real Supabase connection.
Require SUPABASE_URL and SUPABASE_KEY in .env.
Run with: uv run pytest tests/test_supabase_integration.py -v
"""

import pytest
from datetime import date, datetime, timedelta
from src.database.supabase_client import (
    get_client,
    store_snapshot,
    should_run,
    log_run,
)
from src.ingestion.lightyear import PortfolioSnapshot, Position


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """Verify connection works before each test."""
    c = get_client()
    assert c is not None
    return c


@pytest.fixture
def test_snapshot():
    """Minimal fake snapshot for testing storage."""
    return PortfolioSnapshot(
        statement_date=date(2026, 2, 21),
        account_reference="LY-TEST-001",
        positions=[
            Position(
                symbol="NVDA",
                name="NVIDIA",
                isin="US67066G1040",
                quantity=6.619193,
                value_original="$1,256.46",
                value_eur=1066.51,
                currency="USD",
            ),
            Position(
                symbol="AMD",
                name="AMD",
                isin="US0079031078",
                quantity=0.961816,
                value_original="$192.51",
                value_eur=163.41,
                currency="USD",
            ),
        ],
        total_investments_eur=1229.92,
        total_portfolio_eur=1229.93,
        cash_eur=0.01,
    )


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------

def test_supabase_connection(client):
    """Verify we can reach Supabase and query a table."""
    result = client.table("run_log").select("id").limit(1).execute()
    assert result is not None


# ---------------------------------------------------------------------------
# Snapshot tests
# ---------------------------------------------------------------------------

def test_store_snapshot_creates_record(test_snapshot, client):
    snapshot_id = store_snapshot(test_snapshot)

    # Verify snapshot row exists
    result = client.table("portfolio_snapshots") \
        .select("*") \
        .eq("id", snapshot_id) \
        .execute()

    assert len(result.data) == 1
    row = result.data[0]
    assert row["account_reference"] == "LY-TEST-001"
    assert float(row["total_investments_eur"]) == 1229.92
    assert row["statement_date"] == "2026-02-21"

    # Cleanup
    client.table("portfolio_snapshots").delete() \
        .eq("id", snapshot_id).execute()


def test_store_snapshot_creates_positions(test_snapshot, client):
    snapshot_id = store_snapshot(test_snapshot)

    result = client.table("positions") \
        .select("*") \
        .eq("snapshot_id", snapshot_id) \
        .execute()

    assert len(result.data) == 2
    symbols = {r["symbol"] for r in result.data}
    assert symbols == {"NVDA", "AMD"}

    nvda = next(r for r in result.data if r["symbol"] == "NVDA")
    assert nvda["currency"] == "USD"
    assert nvda["isin"] == "US67066G1040"

    # Cleanup — positions cascade delete with snapshot
    client.table("portfolio_snapshots").delete() \
        .eq("id", snapshot_id).execute()


def test_store_snapshot_dedup_returns_existing_id(test_snapshot, client):
    snapshot_id = store_snapshot(test_snapshot)

    # Second call with same statement_date + account_reference must not insert
    snapshot_id_2 = store_snapshot(test_snapshot)
    assert snapshot_id == snapshot_id_2

    # Cleanup
    client.table("portfolio_snapshots").delete() \
        .eq("id", snapshot_id).execute()


# ---------------------------------------------------------------------------
# Run log tests
# ---------------------------------------------------------------------------

def test_log_run_success(client):
    log_run(
        used_new_pdf=True,
        tickers=["NVDA", "AMD"],
        status="success",
    )
    result = client.table("run_log") \
        .select("*") \
        .eq("status", "success") \
        .order("ran_at", desc=True) \
        .limit(1) \
        .execute()

    assert len(result.data) == 1
    row = result.data[0]
    assert row["used_new_pdf"] is True
    assert "NVDA" in row["tickers_analyzed"]

    # Cleanup
    client.table("run_log").delete() \
        .eq("id", row["id"]).execute()


def test_log_run_failure(client):
    log_run(
        used_new_pdf=False,
        tickers=[],
        status="error",
        error_message="Test error message",
    )
    result = client.table("run_log") \
        .select("*") \
        .eq("status", "error") \
        .order("ran_at", desc=True) \
        .limit(1) \
        .execute()

    assert len(result.data) == 1
    assert result.data[0]["error_message"] == "Test error message"

    # Cleanup
    client.table("run_log").delete() \
        .eq("id", result.data[0]["id"]).execute()


# ---------------------------------------------------------------------------
# should_run logic tests (against real DB)
# ---------------------------------------------------------------------------

def test_should_run_after_logging_recent_success(client):
    """After a recent success log, should_run should return False."""
    log_run(
        used_new_pdf=True,
        tickers=["NVDA"],
        status="success",
    )
    assert should_run(interval_days=5) is False

    # Cleanup
    client.table("run_log") \
        .delete() \
        .eq("status", "success") \
        .execute()


def test_should_run_with_no_logs(client):
    """With no success logs, should_run returns True."""
    # Clear all success logs temporarily
    client.table("run_log").delete().eq("status", "success").execute()
    assert should_run(interval_days=5) is True
# tests/test_supabase_client.py
from unittest.mock import patch, MagicMock, call
from src.database.supabase_client import should_run, update_recommendation_prices, store_snapshot
from src.ingestion.lightyear import PortfolioSnapshot, Position
from datetime import datetime, timedelta, timezone, date


def test_should_run_no_previous_runs():
    with patch("src.database.supabase_client._get_last_run_date",
               return_value=None):
        assert should_run() is True


def test_should_run_recent_run():
    recent = datetime.now(tz=timezone.utc) - timedelta(days=2)
    with patch("src.database.supabase_client._get_last_run_date",
               return_value=recent):
        assert should_run(interval_days=5) is False


def test_should_run_old_enough():
    old = datetime.now(tz=timezone.utc) - timedelta(days=6)
    with patch("src.database.supabase_client._get_last_run_date",
               return_value=old):
        assert should_run(interval_days=5) is True


# ---------------------------------------------------------------------------
# store_snapshot — deduplication guard
# ---------------------------------------------------------------------------

def _make_snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        statement_date=date(2026, 2, 21),
        account_reference="LY-TEST",
        positions=[],
        total_investments_eur=1000.0,
        total_portfolio_eur=1050.0,
        cash_eur=50.0,
    )


def test_store_snapshot_returns_existing_id_when_duplicate():
    """store_snapshot skips insert and returns existing id for duplicate statement_date."""
    existing_id = "existing-uuid-123"
    mock_client = MagicMock()
    # Dedup query returns an existing row
    mock_client.table.return_value.select.return_value \
        .eq.return_value.eq.return_value \
        .limit.return_value.execute.return_value.data = [{"id": existing_id}]

    with patch("src.database.supabase_client.get_client", return_value=mock_client):
        result = store_snapshot(_make_snapshot())

    assert result == existing_id
    # insert() should never have been called
    mock_client.table.return_value.insert.assert_not_called()


def test_store_snapshot_inserts_when_no_duplicate():
    """store_snapshot performs a full insert when no existing snapshot exists."""
    new_id = "new-uuid-456"
    mock_client = MagicMock()
    table = mock_client.table.return_value

    # Dedup query returns empty
    table.select.return_value.eq.return_value.eq.return_value \
        .limit.return_value.execute.return_value.data = []
    # Insert returns new id
    table.insert.return_value.execute.return_value.data = [{"id": new_id}]

    with patch("src.database.supabase_client.get_client", return_value=mock_client):
        result = store_snapshot(_make_snapshot())

    assert result == new_id
    mock_client.table.return_value.insert.assert_called()


# ---------------------------------------------------------------------------
# update_recommendation_prices — two-pass logic
# ---------------------------------------------------------------------------

def _make_row(symbol, days_ago, price_at=100.0,
              price_30d=None, price_90d=None, row_id="uuid-1"):
    """Build a fake recommendation_tracking row."""
    tracked_at = (
        datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return {
        "id": row_id,
        "symbol": symbol,
        "tracked_at": tracked_at,
        "price_at_recommendation": price_at,
        "price_30d_later": price_30d,
        "price_90d_later": price_90d,
    }


def _mock_client(rows_30, rows_90):
    """Return a mock Supabase client that returns given rows for each pass."""
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table

    # Chain: .select().is_().execute() → Pass 1
    # Chain: .select().is_().not_.is_().execute() → Pass 2
    pass1_result = MagicMock()
    pass1_result.data = rows_30

    pass2_result = MagicMock()
    pass2_result.data = rows_90

    # Both passes go through the same table mock; we differentiate via call count
    execute_mock = MagicMock(side_effect=[pass1_result, pass2_result])
    table.select.return_value = table
    table.is_.return_value = table
    table.not_ = table                # .not_.is_() chains back to same mock
    table.execute = execute_mock
    table.update.return_value = table
    table.eq.return_value = table

    return client, execute_mock


def test_pass1_fills_30d_price():
    """Pass 1 fills price_30d_later for a row that is 35 days old."""
    row = _make_row("NVDA", days_ago=35, price_at=100.0)

    client, _ = _mock_client(rows_30=[row], rows_90=[])

    with patch("src.database.supabase_client.get_client", return_value=client), \
         patch("yfinance.Ticker") as mock_yf:
        hist = MagicMock()
        hist.empty = False
        hist.__getitem__ = lambda self, key: MagicMock(iloc=[None, None, 120.0])
        mock_yf.return_value.history.return_value = hist
        # Simpler: patch _fetch_price directly
        with patch("src.database.supabase_client.update_recommendation_prices.__wrapped__",
                   create=True):
            pass

    # Use direct _fetch_price patch approach
    with patch("src.database.supabase_client.get_client", return_value=client):
        with patch("yfinance.Ticker") as mock_yf:
            mock_hist = MagicMock()
            mock_hist.empty = False
            mock_hist.__getitem__ = MagicMock(
                return_value=MagicMock(iloc=MagicMock(__getitem__=lambda s, i: 120.0))
            )
            mock_yf.return_value.history.return_value = mock_hist

            update_recommendation_prices()

    # update() should have been called once (for the 30d row)
    client.table.return_value.update.assert_called_once()
    update_args = client.table.return_value.update.call_args[0][0]
    assert "price_30d_later" in update_args
    assert "price_90d_later" not in update_args


def test_pass1_fills_both_when_90d_elapsed():
    """Pass 1 fills BOTH 30d and 90d when a row is >= 90 days old."""
    row = _make_row("NVDA", days_ago=95, price_at=100.0)
    client, _ = _mock_client(rows_30=[row], rows_90=[])

    with patch("src.database.supabase_client.get_client", return_value=client):
        with patch("yfinance.Ticker") as mock_yf:
            mock_hist = MagicMock()
            mock_hist.empty = False
            mock_hist.__getitem__ = MagicMock(
                return_value=MagicMock(iloc=MagicMock(__getitem__=lambda s, i: 150.0))
            )
            mock_yf.return_value.history.return_value = mock_hist

            update_recommendation_prices()

    update_args = client.table.return_value.update.call_args[0][0]
    assert "price_30d_later" in update_args
    assert "price_90d_later" in update_args


def test_pass2_fills_90d_for_rows_already_having_30d():
    """
    Pass 2 fills price_90d_later for a row whose 30d price was filled earlier
    (Pass 1 would skip it because price_30d_later is no longer NULL).
    """
    # price_30d_later already filled; 90d still missing; 92 days elapsed
    row = _make_row("AMZN", days_ago=92, price_at=100.0, price_30d=110.0)
    client, _ = _mock_client(rows_30=[], rows_90=[row])

    with patch("src.database.supabase_client.get_client", return_value=client):
        with patch("yfinance.Ticker") as mock_yf:
            mock_hist = MagicMock()
            mock_hist.empty = False
            mock_hist.__getitem__ = MagicMock(
                return_value=MagicMock(iloc=MagicMock(__getitem__=lambda s, i: 130.0))
            )
            mock_yf.return_value.history.return_value = mock_hist

            update_recommendation_prices()

    update_args = client.table.return_value.update.call_args[0][0]
    assert "price_90d_later" in update_args
    assert "price_30d_later" not in update_args


def test_pass2_skips_rows_under_90_days():
    """Pass 2 does not update a row that has a 30d price but is only 60 days old."""
    row = _make_row("AMD", days_ago=60, price_at=100.0, price_30d=105.0)
    client, _ = _mock_client(rows_30=[], rows_90=[row])

    with patch("src.database.supabase_client.get_client", return_value=client):
        with patch("yfinance.Ticker"):
            update_recommendation_prices()

    client.table.return_value.update.assert_not_called()
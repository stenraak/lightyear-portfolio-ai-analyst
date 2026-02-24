# tests/test_market.py
from unittest.mock import patch
from src.ingestion.market import (
    fetch_market_data,
    _resolve_symbol,
    _detect_asset_type,
    _extract_metrics,
)


def test_resolve_symbol_etf_override():
    assert _resolve_symbol("EXX1") == "EXX1.DE"
    assert _resolve_symbol("EXH1") == "EXH1.DE"

def test_resolve_symbol_no_override():
    assert _resolve_symbol("NVDA") == "NVDA"
    assert _resolve_symbol("AMZN") == "AMZN"

def test_detect_asset_type_etf():
    assert _detect_asset_type({"quoteType": "ETF"}) == "ETF"

def test_detect_asset_type_equity():
    assert _detect_asset_type({"quoteType": "EQUITY"}) == "EQUITY"

def test_detect_asset_type_unknown_empty():
    assert _detect_asset_type({}) == "UNKNOWN"

def test_detect_asset_type_unknown_mutualfund():
    assert _detect_asset_type({"quoteType": "MUTUALFUND"}) == "UNKNOWN"

def test_detect_asset_type_unknown_crypto():
    assert _detect_asset_type({"quoteType": "CRYPTOCURRENCY"}) == "UNKNOWN"

def test_extract_metrics_partial():
    info = {"trailingPE": 35.2, "forwardPE": 28.1, "beta": 1.5}
    metrics = _extract_metrics(info)
    assert metrics.pe_trailing == 35.2
    assert metrics.pe_forward == 28.1
    assert metrics.beta == 1.5
    assert metrics.debt_to_equity is None

def test_fetch_market_data_handles_error():
    with patch("yfinance.Ticker") as mock_ticker:
        mock_ticker.return_value.info = {}
        result = fetch_market_data("FAKE")
        assert result.fetch_error is not None
        assert result.symbol == "FAKE"

def test_quarterly_snapshot_fields():
    from src.ingestion.market import QuarterlySnapshot
    q = QuarterlySnapshot(
        period="2024-Q3",
        revenue=10e9,
        gross_profit=6e9,
        operating_income=3e9,
        net_income=2e9,
        free_cash_flow=2.5e9,
        gross_margin=0.60,
        operating_margin=0.30,
    )
    assert q.gross_margin == 0.60
    assert q.period == "2024-Q3"


def test_trend_arrow_up():
    from src.analysis.prompts import _trend_arrow
    assert _trend_arrow([100, 110, 125, 140]) == "↑"

def test_trend_arrow_down():
    from src.analysis.prompts import _trend_arrow
    assert _trend_arrow([140, 125, 110, 100]) == "↓"

def test_trend_arrow_flat():
    from src.analysis.prompts import _trend_arrow
    assert _trend_arrow([100, 101, 99, 100]) == "→"

def test_trend_arrow_with_nones():
    from src.analysis.prompts import _trend_arrow
    assert _trend_arrow([None, 100, None, 140]) == "↑"
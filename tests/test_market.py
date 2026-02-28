# tests/test_market.py
from unittest.mock import patch, MagicMock
from src.ingestion.market import (
    fetch_market_data,
    _resolve_symbol,
    _detect_asset_type,
    _extract_metrics,
    _normalize_title,
    _deduplicate_news,
    _extract_news,
    _fetch_news_finnhub,
    NewsItem,
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


def test_deduplicate_news_removes_near_duplicates():
    items = [
        NewsItem(title="NVDA Beats Earnings!", publisher="Reuters", link="", published_at="2026-02-01"),
        NewsItem(title="NVDA Beats Earnings!", publisher="Bloomberg", link="", published_at="2026-02-01"),
        NewsItem(title="NVDA beats earnings.", publisher="CNBC", link="", published_at="2026-02-01"),
        NewsItem(title="AMD Reports Results", publisher="Reuters", link="", published_at="2026-02-02"),
    ]
    result = _deduplicate_news(items)
    titles = [r.title for r in result]
    # Exact duplicates collapsed by normalized key
    assert len([t for t in titles if "NVDA" in t]) == 1
    assert any("AMD" in t for t in titles)


def test_extract_news_handles_content_wrapper():
    """New yfinance format wraps items in a 'content' dict."""
    mock_ticker = MagicMock()
    mock_ticker.news = [
        {
            "content": {
                "title": "NVDA Smashes Estimates",
                "pubDate": "2026-02-20T10:00:00Z",
                "provider": {"displayName": "Reuters"},
                "canonicalUrl": {"url": "https://reuters.com/nvda"},
                "summary": "NVDA beat Q4 expectations.",
            }
        }
    ]
    items = _extract_news(mock_ticker, max_items=5)
    assert len(items) == 1
    assert items[0].title == "NVDA Smashes Estimates"
    assert items[0].publisher == "Reuters"
    assert items[0].link == "https://reuters.com/nvda"
    assert items[0].published_at == "2026-02-20"


def test_extract_news_handles_flat_format():
    """Old yfinance format — flat dict without 'content' wrapper."""
    mock_ticker = MagicMock()
    mock_ticker.news = [
        {
            "title": "NVDA Old Format News",
            "publisher": "Bloomberg",
            "link": "https://bloomberg.com/nvda",
            "providerPublishTime": 1708344000,  # 2024-02-19
            "summary": "Old format summary.",
        }
    ]
    items = _extract_news(mock_ticker, max_items=5)
    assert len(items) == 1
    assert items[0].title == "NVDA Old Format News"
    assert items[0].publisher == "Bloomberg"
    assert items[0].link == "https://bloomberg.com/nvda"
    assert items[0].summary == "Old format summary."


def test_fetch_news_falls_back_to_yfinance_when_finnhub_sparse():
    """When Finnhub returns < 3 items, yfinance fallback is used for EQUITY."""
    mock_ticker = MagicMock()
    mock_ticker.news = [
        {
            "title": "YF Fallback Headline",
            "publisher": "YFinance",
            "link": "https://example.com",
            "providerPublishTime": 1708344000,
            "summary": "Fallback news.",
        }
    ]

    sparse_finnhub_response = [
        {
            "headline": "Sparse Finnhub Item",
            "source": "Finnhub",
            "url": "https://finnhub.com/1",
            "datetime": 1708344000,
            "summary": "Only one item.",
        }
    ]

    with patch("src.ingestion.market._get_finnhub_client") as mock_client:
        mock_client.return_value.company_news.return_value = sparse_finnhub_response
        result = _fetch_news_finnhub("NVDA", "EQUITY", ticker=mock_ticker)

    # Should include both Finnhub item and yfinance fallback item
    titles = [r.title for r in result]
    assert any("Sparse Finnhub" in t for t in titles)
    assert any("YF Fallback" in t for t in titles)
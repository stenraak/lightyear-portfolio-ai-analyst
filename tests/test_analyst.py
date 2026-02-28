# tests/test_analyst.py
import pytest
from unittest.mock import patch, MagicMock
from src.analysis.analyst import (
    _parse_json_response,
    PositionAnalysis,
    analyze_position,
    _compute_correlation_matrix,
    _compute_sizing_alignment,
    _compute_portfolio_beta_and_drawdowns,
)
from src.ingestion.lightyear import Position, PortfolioSnapshot
from src.ingestion.market import MarketData, ValuationMetrics


def test_parse_json_clean():
    raw = '{"symbol": "NVDA", "recommendation": {"action": "buy"}}'
    result = _parse_json_response(raw)
    assert result["symbol"] == "NVDA"


def test_parse_json_strips_markdown():
    raw = '```json\n{"symbol": "NVDA"}\n```'
    result = _parse_json_response(raw)
    assert result["symbol"] == "NVDA"


def test_parse_json_with_preamble():
    raw = 'Here is the analysis:\n{"symbol": "AMD"}'
    result = _parse_json_response(raw)
    assert result["symbol"] == "AMD"


def test_parse_json_invalid_raises():
    with pytest.raises(ValueError):
        _parse_json_response("this is not json at all")


# ---------------------------------------------------------------------------
# Issue 2 — "very_expensive" accepted by PositionAnalysis
# ---------------------------------------------------------------------------

def test_valuation_assessment_accepts_very_expensive():
    pa = PositionAnalysis(
        symbol="NVDA",
        raw={},
        recommendation="hold",
        conviction="medium",
        valuation_assessment="very_expensive",
        business_quality_score=8,
        financial_health_score=7,
        risk_score=6,
        asset_type="EQUITY",
    )
    assert pa.valuation_assessment == "very_expensive"


def test_valuation_assessment_all_valid_values():
    for value in ("cheap", "fair", "expensive", "very_expensive", "unknown"):
        pa = PositionAnalysis(
            symbol="X",
            raw={},
            recommendation="hold",
            conviction="low",
            valuation_assessment=value,  # type: ignore[arg-type]
            business_quality_score=0,
            financial_health_score=0,
            risk_score=0,
        )
        assert pa.valuation_assessment == value


# ---------------------------------------------------------------------------
# Issue 7 — parse failure logs a truncation hint and returns a fetch_error
# ---------------------------------------------------------------------------

def _make_market_data(symbol: str = "NVDA") -> MarketData:
    return MarketData(
        symbol=symbol,
        yf_symbol=symbol,
        short_name=symbol,
        long_name=symbol,
        currency="USD",
        asset_type="EQUITY",
        metrics=ValuationMetrics(),
    )


def _make_position(symbol: str = "NVDA") -> Position:
    return Position(
        symbol=symbol,
        name="NVIDIA",
        isin="US67066G1040",
        quantity=1.0,
        value_original="$100",
        value_eur=100.0,
        currency="USD",
    )


def test_unknown_asset_type_sets_fetch_error_without_llm_call():
    """UNKNOWN asset type short-circuits before calling the LLM."""
    md = _make_market_data()
    md = md.model_copy(update={"asset_type": "UNKNOWN"})

    with patch("src.analysis.analyst._call_llm") as mock_llm:
        result = analyze_position(_make_position(), md)

    mock_llm.assert_not_called()
    assert result.fetch_error is not None
    assert "Unsupported asset type" in result.fetch_error
    assert result.asset_type == "UNKNOWN"


def test_truncated_response_sets_fetch_error_with_hint():
    """When _parse_json_response raises ValueError the error message mentions truncation."""
    with patch("src.analysis.analyst._call_llm", return_value="truncated output {"):
        result = analyze_position(_make_position(), _make_market_data())

    assert result.fetch_error is not None
    assert "truncation" in result.fetch_error
    assert result.recommendation == "hold"
    assert result.valuation_assessment == "unknown"


def test_very_expensive_llm_response_accepted():
    """LLM returning very_expensive for valuation no longer triggers a fetch_error."""
    llm_json = """{
        "symbol": "NVDA",
        "business_quality": {"score": 9, "moat_assessment": "wide", "summary": "Strong moat."},
        "financial_health": {"score": 8, "revenue_trend": "accelerating",
                             "margin_trend": "expanding", "fcf_quality": "strong",
                             "summary": "Healthy."},
        "valuation": {"score": 2, "assessment": "very_expensive", "summary": "Stretched."},
        "risks": {"score": 7, "key_risks": ["Risk A", "Risk B", "Risk C"]},
        "news_sentiment": {"sentiment": "positive", "summary": "Good news."},
        "growth_opportunities": ["Catalyst A"],
        "bull_case": {"thesis": "Bull.", "catalysts": ["C1", "C2", "C3"]},
        "bear_case": {"thesis": "Bear.", "risks": ["R1", "R2", "R3"]},
        "recommendation": {
            "action": "hold", "conviction": "medium", "time_horizon": "long_term",
            "rationale": "Expensive but quality.",
            "key_upsides": ["U1", "U2", "U3"],
            "key_downsides": ["D1", "D2", "D3"],
            "price_target_comment": "Fair value lower."
        }
    }"""

    with patch("src.analysis.analyst._call_llm", return_value=llm_json):
        result = analyze_position(_make_position(), _make_market_data())

    assert result.fetch_error is None
    assert result.valuation_assessment == "very_expensive"
    assert result.recommendation == "hold"


def test_key_headlines_field_parsed_into_raw():
    """key_headlines array in news_sentiment is preserved in the parsed raw dict."""
    llm_json = """{
        "symbol": "NVDA",
        "business_quality": {"score": 9, "moat_assessment": "wide", "summary": "Strong moat."},
        "financial_health": {
            "score": 8, "revenue_trend": "accelerating", "margin_trend": "expanding",
            "fcf_quality": "strong", "summary": "Healthy.",
            "news_crossref": "NVDA Beats Q4 Estimates confirms revenue acceleration."
        },
        "valuation": {
            "score": 4, "assessment": "expensive", "summary": "Premium multiple.",
            "catalyst_headline": "NVDA Announces New Blackwell Architecture"
        },
        "risks": {
            "score": 6, "key_risks": ["Risk A", "Risk B", "Risk C"],
            "headline_risks": ["Export Ban Tightening Could Hurt NVDA China Sales"]
        },
        "news_sentiment": {
            "sentiment": "positive",
            "summary": "Broadly positive news flow.",
            "key_headlines": [
                {"title": "NVDA Beats Q4 Estimates", "relevance": "Confirms demand strength."},
                {"title": "AI Spending Accelerates in 2026", "relevance": "Tailwind for data centre."}
            ]
        },
        "bull_case": {
            "thesis": "Bull scenario.", "catalysts": ["C1", "C2", "C3"],
            "supporting_headlines": ["NVDA Beats Q4 Estimates"]
        },
        "bear_case": {
            "thesis": "Bear scenario.", "risks": ["R1", "R2", "R3"],
            "threatening_headlines": ["Export Ban Tightening Could Hurt NVDA China Sales"]
        },
        "recommendation": {
            "action": "hold", "conviction": "high", "time_horizon": "long_term",
            "rationale": "Quality at a price.",
            "key_upsides": ["U1", "U2", "U3"],
            "key_downsides": ["D1", "D2", "D3"]
        }
    }"""

    with patch("src.analysis.analyst._call_llm", return_value=llm_json):
        result = analyze_position(_make_position(), _make_market_data())

    assert result.fetch_error is None
    key_headlines = result.raw["news_sentiment"]["key_headlines"]
    assert isinstance(key_headlines, list)
    assert len(key_headlines) == 2
    assert key_headlines[0]["title"] == "NVDA Beats Q4 Estimates"
    assert "Confirms demand" in key_headlines[0]["relevance"]
    # Extended fields preserved
    assert result.raw["financial_health"]["news_crossref"] is not None
    assert result.raw["valuation"]["catalyst_headline"] is not None
    assert result.raw["risks"]["headline_risks"] == ["Export Ban Tightening Could Hurt NVDA China Sales"]
    assert result.raw["bull_case"]["supporting_headlines"] == ["NVDA Beats Q4 Estimates"]
    assert result.raw["bear_case"]["threatening_headlines"] == ["Export Ban Tightening Could Hurt NVDA China Sales"]


# ---------------------------------------------------------------------------
# Portfolio-level compute helpers
# ---------------------------------------------------------------------------

def test_compute_correlation_matrix_basic():
    """Two perfectly correlated price series should yield r ≈ 1.0."""
    md1 = _make_market_data("NVDA")
    md1 = md1.model_copy(update={"price_history": [100 + i for i in range(50)]})
    md2 = _make_market_data("AMD")
    md2 = md2.model_copy(update={"price_history": [50 + i * 0.5 for i in range(50)]})
    result = _compute_correlation_matrix({"NVDA": md1, "AMD": md2})
    assert "NVDA" in result and "AMD" in result
    assert result["NVDA"]["NVDA"] == 1.0
    assert result["NVDA"]["AMD"] > 0.9


def test_compute_sizing_alignment_flags_mismatch():
    """High-conviction BUY at 8% weight should trigger undersized flag."""
    from datetime import date
    snapshot = PortfolioSnapshot(
        statement_date=date(2026, 2, 21),
        account_reference="TEST",
        positions=[
            Position(
                symbol="NVDA",
                name="NVIDIA",
                isin="US67066G1040",
                quantity=1.0,
                value_original="$800",
                value_eur=800.0,
                currency="USD",
            )
        ],
        total_investments_eur=10000.0,
        total_portfolio_eur=10000.0,
        cash_eur=9200.0,
    )
    pa = PositionAnalysis(
        symbol="NVDA",
        raw={},
        recommendation="buy",
        conviction="high",
        valuation_assessment="fair",
        business_quality_score=9,
        financial_health_score=8,
        risk_score=5,
        asset_type="EQUITY",
    )
    result = _compute_sizing_alignment(snapshot, [pa])
    assert len(result) == 1
    assert result[0]["flag"] == "undersized_high_conviction_buy"
    assert result[0]["weight_pct"] == pytest.approx(8.0)


def test_compute_portfolio_beta_weighted():
    """Weighted beta: NVDA 60% @ 1.8 + AMD 40% @ 1.6 = 1.72."""
    from datetime import date
    snapshot = PortfolioSnapshot(
        statement_date=date(2026, 2, 21),
        account_reference="TEST",
        positions=[
            Position(
                symbol="NVDA",
                name="NVIDIA",
                isin="US67066G1040",
                quantity=1.0,
                value_original="$600",
                value_eur=600.0,
                currency="USD",
            ),
            Position(
                symbol="AMD",
                name="AMD",
                isin="US0079031078",
                quantity=1.0,
                value_original="$400",
                value_eur=400.0,
                currency="USD",
            ),
        ],
        total_investments_eur=1000.0,
        total_portfolio_eur=1000.0,
        cash_eur=0.0,
    )
    nvda_md = _make_market_data("NVDA")
    nvda_md = nvda_md.model_copy(update={"metrics": ValuationMetrics(beta=1.8)})
    amd_md = _make_market_data("AMD")
    amd_md = amd_md.model_copy(update={"metrics": ValuationMetrics(beta=1.6)})
    market_data = {"NVDA": nvda_md, "AMD": amd_md}

    beta, drawdowns = _compute_portfolio_beta_and_drawdowns(snapshot, market_data)
    assert beta is not None
    assert abs(beta - 1.72) < 0.01
    assert len(drawdowns) == 5
    assert drawdowns[0]["market_pct"] == -10
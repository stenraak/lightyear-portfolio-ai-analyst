# tests/test_analyst.py
import pytest
from unittest.mock import patch, MagicMock
from src.analysis.analyst import _parse_json_response, PositionAnalysis, analyze_position
from src.ingestion.lightyear import Position
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
# tests/test_report.py
from src.reporting.report import generate_report
from src.reporting.email import build_email_html
from src.analysis.analyst import PortfolioAnalysis, PositionAnalysis
from src.ingestion.market import MarketData, ValuationMetrics, NewsItem


def _fake_analysis() -> PortfolioAnalysis:
    pos = PositionAnalysis(
        symbol="NVDA",
        raw={
            "symbol": "NVDA",
            "business_quality": {
                "score": 9,
                "summary": "Dominant AI chip maker with strong moat."
            },
            "financial_health": {
                "score": 8,
                "summary": "Strong balance sheet, high margins."
            },
            "valuation": {
                "score": 5,
                "assessment": "expensive",
                "summary": "Trading at premium to peers."
            },
            "risks": {
                "score": 6,
                "key_risks": [
                    "Valuation compression",
                    "China export restrictions",
                    "Competition from AMD and custom silicon"
                ]
            },
            "news_sentiment": {
                "sentiment": "positive",
                "summary": "Strong earnings beat, guidance raised.",
                "key_headlines": [
                    {"title": "NVDA Beats Q4 Estimates by Wide Margin", "relevance": "Confirms strong demand trajectory."}
                ]
            },
            "growth_opportunities": [
                "Blackwell GPU ramp in FY2025 is tracking ahead of Hopper cycle — data centre revenue up +154% YoY in Q4 FY2024.",
                "Sovereign AI spending (governments building national AI infra) is a new TAM not present 2 years ago.",
                "NIM software platform could add a high-margin recurring revenue stream on top of hardware sales."
            ],
            "bull_case": {
                "thesis": "NVDA data centre revenue grew +154% YoY in FY2024 on Hopper demand. Blackwell ramp in FY2025 could sustain 80%+ YoY growth if hyperscaler capex holds. Gross margins expanding toward 78-80% as software attach rates rise.",
                "catalysts": [
                    "Blackwell GPU shipments tracking ahead of Hopper cycle — $10B+ in FY2025 Q1 orders already.",
                    "Sovereign AI spending (national AI infra programmes) adds a multi-billion TAM not present two years ago.",
                    "NIM software platform converting hardware customers to recurring software revenue at 90%+ gross margin."
                ]
            },
            "bear_case": {
                "thesis": "At 35x forward P/E, NVDA is priced for perfection. Any deceleration in hyperscaler AI capex — or a single disappointing guidance cut — could compress multiples by 40-50%. Export restrictions already cap ~25% of addressable market.",
                "risks": [
                    "China export restrictions constrain ~25% of addressable market with no near-term resolution.",
                    "AMD MI300X gaining traction at hyperscalers, threatening NVDA's 80%+ data centre GPU share.",
                    "Custom silicon (Google TPU, Amazon Trainium) could displace 15-20% of inference workloads by 2026."
                ]
            },
            "recommendation": {
                "action": "hold",
                "conviction": "medium",
                "time_horizon": "long_term",
                "rationale": "Strong business but priced for perfection.",
                "key_upsides": [
                    "Revenue grew +122% YoY in FY2024 driven by data centre AI demand.",
                    "Gross margin expanded to 75% in FY2024, up from 57% in FY2022.",
                    "FCF of $60.85B in FY2024 provides significant reinvestment capacity."
                ],
                "key_downsides": [
                    "P/E of 35x assumes continued hypergrowth — any slowdown risks multiple compression.",
                    "China export restrictions already constrain ~25% of addressable market.",
                    "AMD and custom silicon from hyperscalers threaten long-term market share."
                ]
            }
        },
        recommendation="hold",
        conviction="medium",
        valuation_assessment="expensive",
        business_quality_score=9,
        financial_health_score=8,
        risk_score=6,
        asset_type="EQUITY",
    )

    nvda_market_data = MarketData(
        symbol="NVDA",
        yf_symbol="NVDA",
        short_name="NVIDIA",
        long_name="NVIDIA Corporation",
        currency="USD",
        asset_type="EQUITY",
        metrics=ValuationMetrics(current_price=875.0, beta=1.8),
        price_history=[100 + i for i in range(252)],
        news=[
            NewsItem(
                title="NVDA Beats Q4 Estimates by Wide Margin",
                publisher="Reuters",
                link="https://reuters.com/nvda-q4",
                published_at="2026-02-20",
                summary="NVIDIA reported Q4 results far exceeding analyst expectations.",
            ),
            NewsItem(
                title="AI Chip Demand Remains Robust Into 2026",
                publisher="Bloomberg",
                link="https://bloomberg.com/ai-chips",
                published_at="2026-02-18",
                summary="Hyperscaler capex continues to drive chip demand.",
            ),
        ],
    )

    return PortfolioAnalysis(
        snapshot_date="2026-02-21",
        account_reference="LY-WUSK6R3",
        total_value_eur=3050.32,
        positions=[pos],
        portfolio_summary={
            "overall_assessment": "Portfolio is tech-heavy with solid quality.",
            "concentration_risk": "NVDA represents 35% of portfolio.",
            "top_opportunity": {"symbol": "AMD", "reason": "Undervalued relative to AI exposure."},
            "top_risk": {"symbol": "NVDA", "reason": "Valuation leaves no margin of safety."},
            "portfolio_action": "Consider trimming NVDA, adding diversification.",
            "market_context": "AI spend remains strong but valuations elevated.",
            "rebalance_suggestion": "Trim NVDA by 10%, add AMD.",
            "cross_portfolio_news_themes": [
                "AI capex cycle benefiting NVDA and AMD simultaneously.",
                "Regulatory pressure on chip exports affecting sector."
            ],
        },
        market_data={"NVDA": nvda_market_data},
        sizing_alignment=[
            {
                "symbol": "NVDA",
                "weight_pct": 100.0,
                "value_eur": 3050.32,
                "action": "hold",
                "conviction": "medium",
                "flag": None,
            }
        ],
        portfolio_beta=1.45,
        drawdown_scenarios=[
            {"market_pct": -10, "portfolio_pct": -14.5, "eur_impact": -442.3},
            {"market_pct": -15, "portfolio_pct": -21.75, "eur_impact": -663.4},
            {"market_pct": -20, "portfolio_pct": -29.0, "eur_impact": -884.6},
            {"market_pct": -30, "portfolio_pct": -43.5, "eur_impact": -1326.9},
            {"market_pct": -50, "portfolio_pct": -72.5, "eur_impact": -2211.5},
        ],
        correlation_matrix={
            "NVDA": {"NVDA": 1.0, "AMD": 0.82},
            "AMD": {"NVDA": 0.82, "AMD": 1.0},
        },
    )


def test_report_generates_file(tmp_path):
    analysis = _fake_analysis()
    output = tmp_path / "test_report.html"
    result = generate_report(analysis, output_path=output)

    assert result.exists()
    content = result.read_text(encoding="utf-8")
    assert "NVDA" in content
    assert "HOLD" in content
    assert "Portfolio Analysis" in content
    assert "€3,050.32" in content


def test_report_contains_all_sections(tmp_path):
    analysis = _fake_analysis()
    output = tmp_path / "test_report.html"
    generate_report(analysis, output_path=output)
    content = output.read_text(encoding="utf-8")

    assert "Business Quality" in content
    assert "Financial Health" in content
    assert "Valuation" in content
    assert "Key Upside" in content
    assert "Key Downside" in content
    assert "Portfolio Summary" in content
    assert "Growth Opportunities" in content
    assert "Bull Case" in content
    assert "Bear Case" in content
    assert "Rebalance Suggestion" in content
    assert "Cross-Portfolio News Themes" in content
    assert "Position Sizing" in content
    assert "Portfolio Beta" in content
    assert "Return Correlation" in content


def test_email_html_contains_key_sections():
    """build_email_html returns valid Outlook-safe HTML with all expected sections."""
    analysis = _fake_analysis()
    html = build_email_html(
        analysis,
        report_date="2026-02-21",
        report_url="https://example.com/report.html",
    )

    # Structure
    assert "<!DOCTYPE html>" in html
    assert "Portfolio Analysis" in html
    assert "2026-02-21" in html

    # Position data
    assert "NVDA" in html
    assert "HOLD" in html

    # Portfolio summary sections
    assert "Top Opportunity" in html
    assert "Top Risk" in html
    assert "Portfolio Action" in html

    # CTA link
    assert "View Full Report" in html
    assert "https://example.com/report.html" in html

    # No CSS Grid or Flexbox — Outlook-unsafe properties must be absent
    assert "display:grid" not in html
    assert "display: grid" not in html
    assert "display:flex" not in html
    assert "display: flex" not in html


def test_email_html_without_url_omits_cta():
    """When no report_url is provided the CTA button is omitted."""
    analysis = _fake_analysis()
    html = build_email_html(analysis, report_date="2026-02-21", report_url=None)
    assert "View Full Report" not in html
    assert "View in browser" not in html

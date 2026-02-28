"""
Portfolio analyst — calls LLM API and parses structured responses.
Supports Groq (free) and Anthropic (production) via config.
Swap provider by changing LLM_PROVIDER in .env.
"""

import json
import os
import re
from typing import Any, Literal, Optional

import pandas as pd

from pydantic import BaseModel, Field

from dotenv import load_dotenv

from src.ingestion.lightyear import Position, PortfolioSnapshot
from src.ingestion.market import MarketData, fetch_all_market_data
from src.analysis.prompts import (
    build_analysis_prompt,
    build_etf_analysis_prompt,
    build_portfolio_summary_prompt,
)

load_dotenv()

ANALYST_SYSTEM_PROMPT = (
    "You are an experienced investment analyst. "
    "Respond ONLY with valid JSON — no markdown, no preamble, nothing outside the JSON object."
)


# ---------------------------------------------------------------------------
# LLM client — swap provider here, everything else stays the same
# ---------------------------------------------------------------------------

def _call_llm(prompt: str, system: str = ANALYST_SYSTEM_PROMPT) -> str:
    """
    Call the configured LLM provider and return raw text response.
    Change LLM_PROVIDER in .env to switch between groq and anthropic.
    """
    provider = os.getenv("LLM_PROVIDER", "groq")
    if provider == "groq":
        return _call_groq(prompt, system)
    elif provider == "anthropic":
        return _call_anthropic(prompt, system)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")


def _call_groq(prompt: str, system: str) -> str:
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=5000
    )
    final_output = response.choices[0].message.content
    if final_output:
        return final_output
    print(f"Warning: Groq returned empty response for prompt starting: {prompt[:50]}")
    return ""


def _call_anthropic(prompt: str, system: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=5000,
        temperature=0.3,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text # type: ignore


# ---------------------------------------------------------------------------
# JSON parsing — robust against common LLM formatting issues
# ---------------------------------------------------------------------------

def _parse_json_response(raw: str) -> dict:
    """
    Parse JSON from LLM response robustly.
    Handles markdown code blocks and leading/trailing text.
    """
    # Strip markdown code blocks if present
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Find first { and last } and try to parse between them
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start:end])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from LLM response:\n{raw[:300]}")


# ---------------------------------------------------------------------------
# Analysis models
# ---------------------------------------------------------------------------

class PositionAnalysis(BaseModel):
    symbol: str
    raw: dict                    # full parsed JSON from LLM
    recommendation: Literal["buy", "hold", "sell"]
    conviction: Literal["low", "medium", "high"]
    valuation_assessment: Literal["cheap", "fair", "expensive", "very_expensive", "unknown"]
    business_quality_score: int  # fund_quality score for ETFs
    financial_health_score: int  # thematic_exposure score for ETFs
    risk_score: int
    asset_type: Literal["EQUITY", "ETF", "UNKNOWN"] = "EQUITY"
    fetch_error: Optional[str] = None


class PortfolioAnalysis(BaseModel):
    snapshot_date: str
    account_reference: str
    total_value_eur: float
    positions: list[PositionAnalysis]
    portfolio_summary: dict      # parsed JSON from portfolio summary prompt
    market_data: dict[str, Any]  # dict[str, MarketData] — Any avoids cross-module forward ref
    correlation_matrix: dict[str, dict[str, float]] = Field(default_factory=dict)
    sizing_alignment: list[dict] = Field(default_factory=list)
    portfolio_beta: Optional[float] = None
    drawdown_scenarios: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Portfolio-level compute helpers
# ---------------------------------------------------------------------------

def _compute_correlation_matrix(market_data: dict[str, Any]) -> dict[str, dict[str, float]]:
    returns: dict[str, Any] = {}
    for sym, md in market_data.items():
        if hasattr(md, "price_history") and len(md.price_history) > 20:
            prices = pd.Series(md.price_history)
            ret = prices.pct_change().dropna()
            if len(ret) > 10:
                returns[sym] = ret
    if len(returns) < 2:
        return {}
    min_len = min(len(r) for r in returns.values())
    df = pd.DataFrame({sym: ret.iloc[-min_len:].values for sym, ret in returns.items()})
    corr = df.corr()
    return {
        sym: {other: round(float(corr.loc[sym, other]), 2) for other in corr.columns}
        for sym in corr.index
    }


def _compute_sizing_alignment(
    snapshot: Any,
    position_analyses: list[PositionAnalysis],
) -> list[dict]:
    total = snapshot.total_investments_eur
    if total <= 0:
        return []
    analysis_map = {a.symbol: a for a in position_analyses}
    result = []
    for pos in snapshot.positions:
        a = analysis_map.get(pos.symbol)
        if not a:
            continue
        weight = pos.value_eur / total
        flag = None
        if a.recommendation == "buy" and a.conviction == "high" and weight < 0.10:
            flag = "undersized_high_conviction_buy"
        elif a.recommendation == "sell" and weight > 0.20:
            flag = "oversized_sell"
        elif a.recommendation == "buy" and a.conviction == "low" and weight > 0.25:
            flag = "oversized_low_conviction"
        result.append({
            "symbol": pos.symbol,
            "weight_pct": round(weight * 100, 1),
            "value_eur": round(pos.value_eur, 2),
            "action": a.recommendation,
            "conviction": a.conviction,
            "flag": flag,
        })
    return sorted(result, key=lambda x: x["weight_pct"], reverse=True)


def _compute_portfolio_beta_and_drawdowns(
    snapshot: Any,
    market_data: dict[str, Any],
) -> tuple[Optional[float], list[dict]]:
    total = snapshot.total_investments_eur
    if total <= 0:
        return None, []
    weighted_beta = 0.0
    covered_eur = 0.0
    for pos in snapshot.positions:
        md = market_data.get(pos.symbol)
        if md and hasattr(md, "metrics") and md.metrics.beta is not None:
            w = pos.value_eur / total
            weighted_beta += w * md.metrics.beta
            covered_eur += pos.value_eur
    covered_fraction = covered_eur / total
    if covered_fraction < 0.3:
        return None, []
    if covered_fraction < 1.0:
        weighted_beta /= covered_fraction  # normalise for missing betas
    beta = round(weighted_beta, 2)
    drawdowns = [
        {
            "market_pct": int(d * 100),
            "portfolio_pct": round(d * beta * 100, 1),
            "eur_impact": round(total * d * beta, 0),
        }
        for d in [-0.10, -0.15, -0.20, -0.30, -0.50]
    ]
    return beta, drawdowns


# ---------------------------------------------------------------------------
# Core analysis functions
# ---------------------------------------------------------------------------

def analyze_position(
    position: Position,
    market_data: MarketData,
) -> PositionAnalysis:
    """Run LLM analysis for a single portfolio position."""

    asset_type = market_data.asset_type

    if market_data.fetch_error:
        return PositionAnalysis(
            symbol=position.symbol,
            raw={},
            recommendation="hold",
            conviction="low",
            valuation_assessment="unknown",
            business_quality_score=0,
            financial_health_score=0,
            risk_score=0,
            asset_type=asset_type,
            fetch_error=market_data.fetch_error,
        )

    if asset_type == "UNKNOWN":
        qt = market_data.yf_symbol
        return PositionAnalysis(
            symbol=position.symbol,
            raw={},
            recommendation="hold",
            conviction="low",
            valuation_assessment="unknown",
            business_quality_score=0,
            financial_health_score=0,
            risk_score=0,
            asset_type=asset_type,
            fetch_error=(
                f"Unsupported asset type for {qt} — "
                "only EQUITY and ETF are analysable"
            ),
        )

    # Use separate prompt and schema for ETFs vs equities
    if asset_type == "ETF":
        prompt = build_etf_analysis_prompt(position, market_data)
    else:
        prompt = build_analysis_prompt(position, market_data)

    try:
        raw_response = _call_llm(prompt)
        parsed = _parse_json_response(raw_response)

        if asset_type == "ETF":
            bq_score = parsed["fund_quality"]["score"]
            fh_score = parsed["thematic_exposure"]["score"]
        else:
            bq_score = parsed["business_quality"]["score"]
            fh_score = parsed["financial_health"]["score"]

        return PositionAnalysis(
            symbol=position.symbol,
            raw=parsed,
            recommendation=parsed["recommendation"]["action"],
            conviction=parsed["recommendation"]["conviction"],
            valuation_assessment=parsed["valuation"]["assessment"],
            business_quality_score=bq_score,
            financial_health_score=fh_score,
            risk_score=parsed["risks"]["score"],
            asset_type=asset_type,
        )

    except ValueError as e:
        # ValueError covers both _parse_json_response failures (bad/truncated JSON)
        # and Pydantic validation errors (unexpected field values from the LLM).
        # Log a specific hint so truncation is visible in the run output.
        error_msg = f"Response parse/validation failed (possible max_tokens truncation): {e}"
        print(f"    Warning: {error_msg}")
        return PositionAnalysis(
            symbol=position.symbol,
            raw={},
            recommendation="hold",
            conviction="low",
            valuation_assessment="unknown",
            business_quality_score=0,
            financial_health_score=0,
            risk_score=0,
            asset_type=asset_type,
            fetch_error=error_msg,
        )
    except Exception as e:
        return PositionAnalysis(
            symbol=position.symbol,
            raw={},
            recommendation="hold",
            conviction="low",
            valuation_assessment="unknown",
            business_quality_score=0,
            financial_health_score=0,
            risk_score=0,
            asset_type=asset_type,
            fetch_error=str(e),
        )


def analyze_portfolio(snapshot: PortfolioSnapshot) -> PortfolioAnalysis:
    """
    Run full portfolio analysis:
    1. Fetch market data for all positions
    2. Analyze each position individually
    3. Generate portfolio-level summary
    """
    symbols = [p.symbol for p in snapshot.positions]

    print("Fetching market data...")
    market_data = fetch_all_market_data(symbols)

    print("\nAnalyzing positions...")
    position_analyses = []
    for position in snapshot.positions:
        print(f"  Analyzing {position.symbol}...")
        analysis = analyze_position(position, market_data[position.symbol])
        position_analyses.append(analysis)

        if analysis.fetch_error:
            print(f"    Warning: {analysis.fetch_error}")
        else:
            print(f"    {analysis.recommendation.upper()} "
                  f"({analysis.conviction} conviction) — "
                  f"{analysis.valuation_assessment}")

    # Portfolio-level computations
    correlation_matrix = _compute_correlation_matrix(market_data)
    sizing_alignment = _compute_sizing_alignment(snapshot, position_analyses)
    portfolio_beta, drawdown_scenarios = _compute_portfolio_beta_and_drawdowns(
        snapshot, market_data
    )

    # Portfolio summary — only use successful analyses
    successful = [a.raw for a in position_analyses if a.raw]
    portfolio_summary = {}

    if successful:
        print("\nGenerating portfolio summary...")
        summary_prompt = build_portfolio_summary_prompt(
            successful,
            snapshot.total_investments_eur,
            market_data=market_data,
            sizing_alignment=sizing_alignment,
            portfolio_beta=portfolio_beta,
            drawdown_scenarios=drawdown_scenarios,
            correlation_matrix=correlation_matrix,
        )
        try:
            raw_summary = _call_llm(summary_prompt)
            portfolio_summary = _parse_json_response(raw_summary)
        except Exception as e:
            print(f"  Warning: Portfolio summary failed: {e}")

    return PortfolioAnalysis(
        snapshot_date=str(snapshot.statement_date),
        account_reference=snapshot.account_reference,
        total_value_eur=snapshot.total_investments_eur,
        positions=position_analyses,
        portfolio_summary=portfolio_summary,
        market_data=market_data,
        correlation_matrix=correlation_matrix,
        sizing_alignment=sizing_alignment,
        portfolio_beta=portfolio_beta,
        drawdown_scenarios=drawdown_scenarios,
    )
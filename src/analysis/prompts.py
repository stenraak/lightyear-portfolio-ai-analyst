"""
Prompt templates for LLM-based portfolio analysis.
Conservative framing — fundamentals and trend over point-in-time metrics.
Separate prompt schemas for equities vs ETFs.
"""

from src.ingestion.lightyear import Position
from src.ingestion.market import MarketData, QuarterlySnapshot, AnnualSnapshot
from typing import Optional


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt(val, suffix="", pct=False) -> str:
    if val is None:
        return "N/A"
    if pct:
        return f"{val * 100:.1f}%"
    return f"{val:.2f}{suffix}"


def _fmt_large(val) -> str:
    """Format large numbers in B/M for readability."""
    if val is None:
        return "N/A"
    if abs(val) >= 1e9:
        return f"{val/1e9:.2f}B"
    if abs(val) >= 1e6:
        return f"{val/1e6:.1f}M"
    return f"{val:.0f}"


def _fmt_signed(val) -> str:
    """Format large numbers with explicit +/- sign."""
    if val is None:
        return "N/A"
    if abs(val) >= 1e9:
        return f"{val/1e9:+.2f}B"
    if abs(val) >= 1e6:
        return f"{val/1e6:+.1f}M"
    return f"{val:+.0f}"


def _trend_arrow(values: list[Optional[float]]) -> str:
    """
    Derive trend direction from a list of values.
    Returns ↑ ↓ → or ~ for noisy/flat.
    """
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return "~"
    first, last = clean[0], clean[-1]
    if first == 0:
        return "~"
    change = (last - first) / abs(first)
    if change > 0.05:
        return "↑"
    if change < -0.05:
        return "↓"
    return "→"


def _fmt_optional(val) -> str:
    if val is None:
        return "N/A"
    return str(val)


def _build_quarterly_section(quarterly: list[QuarterlySnapshot]) -> str:
    """Format quarterly financials as a readable trend table."""
    if not quarterly:
        return "No quarterly financial data available (ETF or data unavailable)."

    lines = ["Period       Revenue      GrossMargin  OpMargin     FCF"]
    lines.append("-" * 65)

    for q in quarterly:
        rev = _fmt_large(q.revenue)
        gm = _fmt(q.gross_margin, pct=True)
        om = _fmt(q.operating_margin, pct=True)
        fcf = _fmt_large(q.free_cash_flow)
        lines.append(
            f"{q.period:<12} {rev:<12} {gm:<12} {om:<12} {fcf}"
        )

    # Trend summary line
    revenues = [q.revenue for q in quarterly]
    margins = [q.gross_margin for q in quarterly]
    op_margins = [q.operating_margin for q in quarterly]
    fcfs = [q.free_cash_flow for q in quarterly]

    lines.append("-" * 65)
    lines.append(
        f"Trend        {_trend_arrow(revenues):<12} "
        f"{_trend_arrow(margins):<12} "
        f"{_trend_arrow(op_margins):<12} "
        f"{_trend_arrow(fcfs)}"
    )

    return "\n".join(lines)


def _build_annual_section(annual: list[AnnualSnapshot]) -> str:
    """
    Format annual financials as a multi-year trend table with YoY growth,
    margins, net debt and interest coverage. Ordered oldest → newest.
    """
    if not annual:
        return "No annual financial data available (ETF or data unavailable)."

    header = (
        f"{'Year':<6} {'Revenue':>10} {'YoY':>8} "
        f"{'GrsMgn':>8} {'OpMgn':>8} {'NetMgn':>8} "
        f"{'FCF':>10} {'NetDebt':>11} {'IntCov':>8}"
    )
    sep = "-" * 82
    lines = [header, sep]

    for a in annual:
        rev = _fmt_large(a.revenue)
        yoy = (f"{a.revenue_growth_yoy*100:+.1f}%"
               if a.revenue_growth_yoy is not None else "   --")
        gm = f"{a.gross_margin*100:.1f}%" if a.gross_margin is not None else "N/A"
        om = (f"{a.operating_margin*100:.1f}%"
              if a.operating_margin is not None else "N/A")
        nm = f"{a.net_margin*100:.1f}%" if a.net_margin is not None else "N/A"
        fcf = _fmt_large(a.free_cash_flow)
        nd = _fmt_signed(a.net_debt) if a.net_debt is not None else "N/A"
        ic = (f"{a.interest_coverage:.1f}x"
              if a.interest_coverage is not None else "N/A")
        lines.append(
            f"{a.year:<6} {rev:>10} {yoy:>8} "
            f"{gm:>8} {om:>8} {nm:>8} "
            f"{fcf:>10} {nd:>11} {ic:>8}"
        )

    # Trend summary
    lines.append(sep)
    lines.append(
        f"{'Trend':<6} {'':>10} {_trend_arrow([a.revenue for a in annual]):>8} "
        f"{_trend_arrow([a.gross_margin for a in annual]):>8} "
        f"{_trend_arrow([a.operating_margin for a in annual]):>8} "
        f"{_trend_arrow([a.net_margin for a in annual]):>8} "
        f"{_trend_arrow([a.free_cash_flow for a in annual]):>10}"
    )

    lines.append("")
    lines.append(
        "NetDebt: positive = net debt, negative = net cash position. "
        "IntCov: operating income / interest expense (higher = safer)."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Equity analysis prompt
# ---------------------------------------------------------------------------

def build_analysis_prompt(position: Position, market_data: MarketData) -> str:
    """
    Build conservative analysis prompt for a single equity position.
    Emphasises multi-year fundamental trends, debt trajectory,
    and margin quality over point-in-time valuation multiples.
    """
    m = market_data.metrics

    news_text = "\n".join(
        f"- [{n.published_at}] {n.title} ({n.publisher})"
        for n in market_data.news
    ) or "No recent news available."

    net_debt_ttm = None
    if m.total_debt is not None and m.total_cash is not None:
        net_debt_ttm = m.total_debt - m.total_cash

    annual_section = _build_annual_section(market_data.annual)
    quarterly_section = _build_quarterly_section(market_data.quarterly)

    prompt = f"""## Position
Symbol:       {position.symbol}
Name:         {market_data.long_name}
Asset type:   EQUITY
Sector:       {market_data.sector or 'N/A'}
Industry:     {market_data.industry or 'N/A'}
Currency:     {position.currency}

## Portfolio Context
Quantity held:  {position.quantity:.6f}
Current value:  {position.value_original}
Value in EUR:   €{position.value_eur:.2f}

## Current Valuation & Balance Sheet
Price:            {_fmt(m.current_price)} {market_data.currency}
Market cap:       {_fmt_large(m.market_cap)}
Beta:             {_fmt(m.beta)}

P/E trailing:     {_fmt(m.pe_trailing)}
P/E forward:      {_fmt(m.pe_forward)}
P/B ratio:        {_fmt(m.pb_ratio)}
EV/EBITDA:        {_fmt(m.ev_to_ebitda)}
Price/Sales:      {_fmt(m.price_to_sales)}

ROE:              {_fmt(m.return_on_equity, pct=True)}
ROA:              {_fmt(m.return_on_assets, pct=True)}
Profit margin:    {_fmt(m.profit_margin, pct=True)}
Debt/Equity:      {_fmt(m.debt_to_equity)}
Current ratio:    {_fmt(m.current_ratio)}
Dividend yield:   {_fmt(m.dividend_yield, pct=True)}
FCF (TTM):        {_fmt_large(m.free_cash_flow_ttm)}
Total Debt:       {_fmt_large(m.total_debt)}
Total Cash:       {_fmt_large(m.total_cash)}
Net Debt (TTM):   {_fmt_signed(net_debt_ttm)}

## Annual Financial Trend (oldest → newest, FY data)
{annual_section}

## Recent Quarterly Trend (oldest → newest)
{quarterly_section}

## Business Description
{(market_data.description or 'N/A')[:800]}

## Recent News Headlines
{news_text}

## Instructions
Analyze this equity position using the multi-year/quarter data above. You must generate
BOTH a bull case and a bear case before synthesising a recommendation.

- Business quality: Is the moat real and durable? Reference specific margin
  levels and trends from the annual data. What threatens the competitive position?
- Financial health: Assess the TRAJECTORY — is revenue growth accelerating or
  decelerating? Are margins expanding or compressing? Is FCF growing
  proportionally to revenue? Is debt rising or falling relative to earnings?
  Reference anything relevant from the quarterly data to explain recent inflections or momentum.
- Valuation: What growth rate does the current multiple imply? Is it realistic
  given the actual trajectory from the data?
  Specifically: if forward P/E is available, compute the implied EPS CAGR needed
  to justify it at a 15x exit multiple over 3 years and state whether the actual
  revenue/earnings trajectory supports that rate.
- Risks & news: Specific risks with actual numbers. What are the headlines
  signalling about near-term momentum or potential risks/opportunities?
- Bull case: The most optimistic scenario the data can plausibly support over
  3 years. What goes right? Cite the current baseline and the upside scenario.
- Bear case: The realistic downside. What goes wrong, and what would the
  financials look like if it does? Cite specific numbers.
- Recommendation: Synthesise the bull and bear cases and make a clear call.
  BUY if the valuation is cheap or fair AND the fundamental trajectory supports the bull case.
  SELL if the multiple prices in near-perfect execution AND the bear case is more probable
    (e.g. decelerating growth, margin compression, or stretched valuation vs realistic growth rate).
  HOLD if the risk/reward is genuinely balanced and neither direction is clearly dominant.
  Conviction: high = multiple confirming data points; medium = reasonable case but uncertainty present;
    low = limited data or conflicting signals.
  Rationale must cite specific numbers from the data above — do not give a generic assessment.


CRITICAL — all array fields MUST contain specific numbers or data citations.
Generic statements like "strong growth" or "faces competition" are NOT acceptable.

Return ONLY this JSON structure:

{{
  "symbol": "{position.symbol}",
  "business_quality": {{
    "score": <1-10>,
    "moat_assessment": "<none|narrow|wide>",
    "summary": "<cite specific margin levels and moat dynamics>"
  }},
  "financial_health": {{
    "score": <1-10>,
    "revenue_trend": "<accelerating|stable|decelerating|declining>",
    "margin_trend": "<expanding|stable|compressing>",
    "fcf_quality": "<strong|adequate|weak|negative>",
    "summary": "<explain revenue/margin/FCF trajectory with YoY numbers>"
  }},
  "valuation": {{
    "score": <1-10>,
    "assessment": "<cheap|fair|expensive|very_expensive>",
    "summary": "<what growth rate does the current multiple imply? Is it realistic?>"
  }},
  "risks": {{
    "score": <1-10, where 10 = extreme risk>,
    "key_risks": [
      "<specific risk with data>",
      "<specific risk with data>",
      "<specific risk with data>"
    ]
  }},
  "news_sentiment": {{
    "sentiment": "<positive|neutral|negative>",
    "summary": "<1-3 sentences — what story are the headlines telling?>"
  }},
  "bull_case": {{
    "thesis": "<optimistic 3yr scenario with baseline and upside numbers>",
    "catalysts": [
      "<catalyst with data>",
      "<catalyst with data>",
      "<catalyst with data>"
    ]
  }},
  "bear_case": {{
    "thesis": "<realistic downside scenario with specific numbers e.g. margin X%→Y%, debt Z×>",
    "risks": [
      "<bear risk with data>",
      "<bear risk with data>",
      "<bear risk with data>"
    ]
  }},
  "recommendation": {{
    "action": "<buy|hold|sell>",
    "conviction": "<low|medium|high>",
    "time_horizon": "<near_term|long_term|both>",
    "rationale": "<synthesise bull/bear with specific numbers and asymmetry>",
    "key_upsides": [
      "<specific upside with data>",
      "<specific upside with data>",
      "<specific upside with data>"
    ],
    "key_downsides": [
      "<specific downside with data>",
      "<specific downside with data>",
      "<specific downside with data>"
    ],
    "implied_growth_assumption": "<what annual EPS/revenue CAGR does the current multiple price in, and is the historical trend consistent with that rate?>"
  }}
}}"""

    return prompt


# ---------------------------------------------------------------------------
# ETF analysis prompt (separate schema — macro/thematic focus)
# ---------------------------------------------------------------------------

def build_etf_analysis_prompt(position: Position, market_data: MarketData) -> str:
    """
    Build analysis prompt for an ETF position.
    Focuses on thematic exposure, expense ratio, macro tailwinds and
    fund quality rather than single-stock fundamentals.
    """
    m = market_data.metrics

    news_text = "\n".join(
        f"- [{n.published_at}] {n.title} ({n.publisher})"
        for n in market_data.news
    ) or "No recent news available."

    # ETF performance metrics
    expense_pct = f"{m.expense_ratio * 100:.2f}%" if m.expense_ratio else "N/A"
    aum = _fmt_large(m.total_assets) if m.total_assets else "N/A"
    ytd = f"{m.ytd_return * 100:+.1f}%" if m.ytd_return is not None else "N/A"
    ret_3y = (f"{m.three_year_avg_return * 100:+.1f}%/yr"
              if m.three_year_avg_return is not None else "N/A")
    ret_5y = (f"{m.five_year_avg_return * 100:+.1f}%/yr"
              if m.five_year_avg_return is not None else "N/A")

    prompt = f"""## Position
Symbol:       {position.symbol}
Name:         {market_data.long_name}
Asset type:   ETF
Sector/Theme: {market_data.sector or 'N/A'}
Category:     {market_data.industry or 'N/A'}
Currency:     {position.currency}

## Portfolio Context
Quantity held:  {position.quantity:.6f}
Current value:  {position.value_original}
Value in EUR:   €{position.value_eur:.2f}

## Fund Metrics
Price:            {_fmt(m.current_price)} {market_data.currency}
AUM:              {aum}
Expense ratio:    {expense_pct}
Beta:             {_fmt(m.beta)}
Dividend yield:   {_fmt(m.dividend_yield, pct=True)}

## Performance
YTD return:       {ytd}
3-year avg/yr:    {ret_3y}
5-year avg/yr:    {ret_5y}

## Fund Description
{(market_data.description or 'N/A')[:800]}

## Recent News Headlines
{news_text}

## Instructions
Evaluate this ETF on the following dimensions. Generate BOTH a bull and bear
case before synthesising a recommendation.

- Fund quality: Reputable issuer? Competitive expense ratio? AUM sufficient
  for liquidity and closure safety?
- Thematic exposure: What macro or structural trend does this track? Are
  tailwinds structural or cyclical? Is the theme already priced in?
- Valuation: Is the sector at a premium or discount to historical range?
- Risks: Concentration, rate sensitivity, currency, regulatory, fee drag.
- News: What do headlines signal about the theme or sector right now?
- Bull case: Describe the scenario where macro tailwinds materialise, AUM
  grows, and the theme delivers above-market returns. What specific conditions
  would need to be true?
- Bear case: Describe the scenario where the macro backdrop reverses, the
  theme de-rates, or structural risks materialise. Be specific.
- Recommendation: Synthesise both cases and make a clear call.
  BUY if macro tailwinds are structural and the sector is at a fair or cheap valuation.
  SELL if the theme is clearly deteriorating or the sector is significantly overvalued
    relative to its historical range or growth prospects.
  HOLD if the outlook is genuinely balanced or the evidence is mixed.
  Conviction: high = strong directional macro or structural signal; medium = probable but uncertain;
    low = mixed or limited signals.

CRITICAL — all array fields MUST cite specific facts about the fund, theme,
or macro context. No generic statements.

Return ONLY this JSON structure:

{{
  "symbol": "{position.symbol}",
  "fund_quality": {{
    "score": <1-10>,
    "summary": "<issuer quality, expense ratio vs category, AUM and liquidity>"
  }},
  "thematic_exposure": {{
    "score": <1-10>,
    "theme_strength": "<strong|moderate|weak>",
    "summary": "<sector/theme, macro tailwinds or headwinds, concentration>"
  }},
  "valuation": {{
    "score": <1-10>,
    "assessment": "<cheap|fair|expensive|very_expensive>",
    "summary": "<whether sector valuation is attractive or stretched>"
  }},
  "risks": {{
    "score": <1-10, where 10 = extreme risk>,
    "key_risks": [
      "<specific risk with data>",
      "<specific risk with data>",
      "<specific risk with data>"
    ]
  }},
  "news_sentiment": {{
    "sentiment": "<positive|neutral|negative>",
    "summary": "<1-3 sentences — what story are the headlines telling about the theme?>"
  }},
  "bull_case": {{
    "thesis": "<optimistic scenario: macro/structural tailwind that materialises, with upside numbers>",
    "catalysts": [
      "<catalyst with macro/policy/sector data>",
      "<catalyst with macro/policy/sector data>",
      "<catalyst with macro/policy/sector data>"
    ]
  }},
  "bear_case": {{
    "thesis": "<downside scenario: macro reversal or structural risk, with specific conditions>",
    "risks": [
      "<bear risk with macro/regulation/sector data>",
      "<bear risk with macro/regulation/sector data>",
      "<bear risk with macro/regulation/sector data>"
    ]
  }},
  "recommendation": {{
    "action": "<buy|hold|sell>",
    "conviction": "<low|medium|high>",
    "time_horizon": "<near_term|long_term|both>",
    "rationale": "<synthesise bull/bear referencing fund specifics and macro context>",
    "key_upsides": [
      "<upside with fund data or macro>",
      "<upside with fund data or macro>",
      "<upside with fund data or macro>"
    ],
    "key_downsides": [
      "<downside with fund data or macro>",
      "<downside with fund data or macro>",
      "<downside with fund data or macro>"
    ]
  }}
}}"""

    return prompt


# ---------------------------------------------------------------------------
# Portfolio summary prompt
# ---------------------------------------------------------------------------

def build_portfolio_summary_prompt(
    positions_analyses: list[dict],
    total_value_eur: float,
) -> str:
    """Portfolio-level summary after individual analyses."""

    def _fmt_list(val) -> str:
        """Flatten list or string for the summary prompt."""
        if isinstance(val, list):
            return "; ".join(val)
        return str(val) if val else "N/A"

    def _get_financial_health(a: dict) -> tuple[str, str]:
        """Return (revenue_trend, margin_trend) for equity or ETF."""
        if "financial_health" in a:
            fh = a["financial_health"]
            return fh.get("revenue_trend", "N/A"), fh.get("margin_trend", "N/A")
        if "thematic_exposure" in a:
            te = a["thematic_exposure"]
            return te.get("theme_strength", "N/A"), "N/A (ETF)"
        return "N/A", "N/A"

    def _position_summary(a: dict) -> str:
        rev_trend, margin_trend = _get_financial_health(a)
        return (
            f"Symbol: {a['symbol']}\n"
            f"Action: {a['recommendation']['action']} "
            f"({a['recommendation']['conviction']} conviction)\n"
            f"Valuation: {a['valuation']['assessment']}\n"
            f"Revenue/theme trend: {rev_trend}\n"
            f"Margin trend: {margin_trend}\n"
            f"Risk score: {a['risks']['score']}/10\n"
            f"Key upsides: {_fmt_list(a['recommendation'].get('key_upsides'))}\n"
            f"Key downsides: {_fmt_list(a['recommendation'].get('key_downsides'))}"
        )

    analyses_text = "\n\n".join(_position_summary(a) for a in positions_analyses)

    return f"""## Portfolio Total Value: €{total_value_eur:,.2f}

## Individual Position Summaries
{analyses_text}

Return ONLY this JSON structure:
{{
  "overall_assessment": "<2 sentences on portfolio health and quality>",
  "concentration_risk": "<comment on diversification — sector, geography, currency>",
  "fundamental_trend": "<improving|mixed|deteriorating — across the portfolio>",
  "top_opportunity": {{
    "symbol": "<symbol>",
    "reason": "<one sentence>"
  }},
  "top_risk": {{
    "symbol": "<symbol>",
    "reason": "<one sentence>"
  }},
  "portfolio_action": "<2 sentences — what should the investor actually do?>",
  "market_context": "<3-5 sentences. Cover: (1) Current macro regime — where are interest rates, growth trajectory, and inflation headed right now? (2) How do these conditions specifically affect the SECTORS held in this portfolio — name each major sector and explain WHY it is favored or disfavored under the current regime (e.g. 'Semiconductors benefit from AI capex because... however rate sensitivity means...'). (3) The single biggest macro risk that could materially hurt this specific portfolio and the mechanism by which it would do so.>",
  "rebalance_suggestion": "<1-2 sentences — should any position be trimmed or sized up given current valuations and conviction levels? Reference specific symbols>"
}}"""

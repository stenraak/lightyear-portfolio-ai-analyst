"""
HTML report generator for portfolio analysis results.
Produces a clean, self-contained HTML file with all analyses.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
from src.analysis.analyst import PortfolioAnalysis, PositionAnalysis
from src.ingestion.market import MarketData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recommendation_color(action: str) -> str:
    match action.lower():
        case "buy":
            return "#22c55e"   # green
        case "sell":
            return "#ef4444"   # red
        case _:
            return "#f59e0b"   # amber for hold


def _sentiment_color(sentiment: str) -> str:
    match sentiment.lower():
        case "positive":
            return "#22c55e"
        case "negative":
            return "#ef4444"
        case _:
            return "#94a3b8"


def _score_bar(score: int, invert: bool = False) -> str:
    """Render a simple score bar. invert=True for risk (high = bad)."""
    if score == 0:
        return "<span style='color:#94a3b8'>N/A</span>"
    color = "#ef4444" if invert else "#22c55e"
    if invert and score <= 4:
        color = "#22c55e"
    elif invert and score <= 7:
        color = "#f59e0b"
    width = score * 10
    return f"""
        <div style="display:flex;align-items:center;gap:8px;">
            <div style="width:100px;background:#0f172a;border-radius:4px;height:8px;">
                <div style="width:{width}%;background:{color};
                     border-radius:4px;height:8px;"></div>
            </div>
            <span style="color:#94a3b8;font-size:12px;">{score}/10</span>
        </div>"""


def _render_bullet_list(items) -> str:
    """Render key_upsides / key_downsides as a bullet list.
    Accepts either a list of strings (new schema) or a plain string (fallback).
    """
    if not items:
        return ""
    if isinstance(items, list):
        bullets = "".join(f"<li>{item}</li>" for item in items)
        return f"<ul class='risk-list'>{bullets}</ul>"
    return f"<p class='metric-summary'>{items}</p>"


def _fmt_val(val) -> str:
    """Format a financial value for table display."""
    if val is None:
        return "—"
    if abs(val) >= 1e9:
        return f"{val/1e9:.1f}B"
    if abs(val) >= 1e6:
        return f"{val/1e6:.0f}M"
    return f"{val:.0f}"


def _fmt_pct(val) -> str:
    if val is None:
        return "—"
    return f"{val*100:.1f}%"


def _fmt_pct_signed(val) -> str:
    if val is None:
        return "—"
    return f"{val*100:+.1f}%"


def _fmt_val_signed(val) -> str:
    """Format a signed financial value (e.g. net debt: positive = debt, negative = cash)."""
    if val is None:
        return "—"
    if abs(val) >= 1e9:
        return f"{val/1e9:+.1f}B"
    if abs(val) >= 1e6:
        return f"{val/1e6:+.0f}M"
    return f"{val:+.0f}"


def _fmt_coverage(val) -> str:
    """Format interest coverage ratio as e.g. '12.3x'."""
    if val is None:
        return "—"
    return f"{val:.1f}x"


def _signal_color(signal: str) -> str:
    match signal.lower():
        case "bullish":
            return "#22c55e"
        case "bearish":
            return "#ef4444"
        case _:
            return "#94a3b8"


def _render_technicals_panel(market_data: Optional[MarketData]) -> str:
    """
    Render a visual technical indicators panel:
    - Signal pills row (RSI zone, MACD, MA cross, Bollinger, Volume)
    - 52-week range bar with price marker
    - Two-column metrics table
    """
    if not market_data or not market_data.technicals:
        return ""
    t = market_data.technicals
    if all(v is None for v in [t.rsi_14, t.macd, t.sma_50, t.bb_pct]):
        return ""

    # --- Signal pills ---
    pills = []

    if t.rsi_14 is not None:
        if t.rsi_14 < 30:
            c, label = "#22c55e", f"RSI {t.rsi_14:.0f} — Oversold"
        elif t.rsi_14 > 70:
            c, label = "#ef4444", f"RSI {t.rsi_14:.0f} — Overbought"
        else:
            c, label = "#64748b", f"RSI {t.rsi_14:.0f} — Neutral"
        pills.append((c, label))

    if t.macd is not None and t.macd_signal is not None:
        if t.macd > t.macd_signal:
            pills.append(("#22c55e", "MACD ↑ Bullish"))
        else:
            pills.append(("#ef4444", "MACD ↓ Bearish"))

    if t.golden_cross is not None:
        if t.golden_cross:
            pills.append(("#22c55e", "Golden Cross"))
        else:
            pills.append(("#ef4444", "Death Cross"))

    if t.bb_pct is not None:
        if t.bb_pct > 0.8:
            pills.append(("#ef4444", f"BB Upper {t.bb_pct:.0%}"))
        elif t.bb_pct < 0.2:
            pills.append(("#22c55e", f"BB Lower {t.bb_pct:.0%}"))
        else:
            pills.append(("#64748b", f"BB Mid {t.bb_pct:.0%}"))

    if t.volume_ratio is not None:
        if t.volume_ratio > 1.3:
            pills.append(("#818cf8", f"Vol {t.volume_ratio:.1f}x ↑ High"))
        elif t.volume_ratio < 0.7:
            pills.append(("#475569", f"Vol {t.volume_ratio:.1f}x ↓ Low"))
        else:
            pills.append(("#64748b", f"Vol {t.volume_ratio:.1f}x Normal"))

    pills_html = " ".join(
        f"<span style='font-size:11px;padding:3px 10px;border-radius:12px;"
        f"background:{c}22;color:{c};border:1px solid {c}55;white-space:nowrap;'>"
        f"{label}</span>"
        for c, label in pills
    )

    # --- 52-week range bar ---
    range_bar = ""
    price = market_data.metrics.current_price
    if t.price_52w_high and t.price_52w_low and price:
        rng = t.price_52w_high - t.price_52w_low
        pos_pct = max(0.0, min(100.0, (price - t.price_52w_low) / rng * 100)) if rng > 0 else 50.0
        from_high = f"{t.pct_from_52w_high * 100:.1f}%" if t.pct_from_52w_high is not None else ""
        range_bar = f"""
        <div style="margin-top:14px;">
            <div style="display:flex;justify-content:space-between;
                        font-size:11px;color:#475569;margin-bottom:6px;">
                <span>{t.price_52w_low:.2f} <span style="color:#64748b;">52w Low</span></span>
                <span style="color:#94a3b8;font-size:10px;">— 52-Week Range —</span>
                <span><span style="color:#64748b;">52w High</span> {t.price_52w_high:.2f}</span>
            </div>
            <div style="position:relative;height:6px;background:#0f172a;border-radius:3px;">
                <div style="position:absolute;left:{pos_pct:.1f}%;transform:translateX(-50%);
                            width:14px;height:14px;background:#6366f1;border-radius:50%;
                            top:-4px;box-shadow:0 0 0 3px #6366f133;"></div>
            </div>
            <div style="text-align:center;font-size:11px;color:#64748b;margin-top:8px;">
                {price:.2f}
                <span style="color:#475569;margin-left:4px;">
                    ({from_high} from high &nbsp;·&nbsp; {pos_pct:.0f}% of range)
                </span>
            </div>
        </div>"""

    # --- Metrics table (two columns) ---
    def _metric_val(val, fmt=".2f", color_fn=None) -> str:
        if val is None:
            return "<span style='color:#334155'>—</span>"
        txt = f"{val:{fmt}}"
        color = color_fn(val) if color_fn else "#94a3b8"
        return f"<span style='color:{color}'>{txt}</span>"

    def _pct_color(v): return "#22c55e" if v > 0 else "#ef4444"
    def _rsi_color(v): return "#22c55e" if v < 30 else "#ef4444" if v > 70 else "#94a3b8"
    def _hist_color(v): return "#22c55e" if v > 0 else "#ef4444"

    rows = [
        ("RSI (14)",      _metric_val(t.rsi_14,           ".1f",  _rsi_color)),
        ("MACD",          _metric_val(t.macd,              ".3f")),
        ("MACD Signal",   _metric_val(t.macd_signal,       ".3f")),
        ("MACD Hist",     _metric_val(t.macd_hist,         "+.3f", _hist_color)),
        ("SMA (50)",      _metric_val(t.sma_50,            ".2f")),
        ("vs SMA50",      _metric_val(t.price_vs_sma50,    "+.1%", _pct_color)),
        ("SMA (200)",     _metric_val(t.sma_200,           ".2f")),
        ("vs SMA200",     _metric_val(t.price_vs_sma200,   "+.1%", _pct_color)),
        ("Bollinger %B",  _metric_val(t.bb_pct,            ".2f")),
        ("Volume ratio",  _metric_val(t.volume_ratio,      ".2f")),
    ]

    def _tr(label, val_html):
        return (
            f"<tr>"
            f"<td style='color:#475569;padding:3px 8px;font-size:11px;"
            f"white-space:nowrap;'>{label}</td>"
            f"<td style='padding:3px 8px;font-size:12px;text-align:right;"
            f"font-variant-numeric:tabular-nums;'>{val_html}</td>"
            f"</tr>"
        )

    mid = len(rows) // 2
    left_html = "".join(_tr(l, v) for l, v in rows[:mid])
    right_html = "".join(_tr(l, v) for l, v in rows[mid:])

    metrics_html = f"""
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-top:12px;">
            <table style="border-collapse:collapse;width:100%;
                          background:#0f172a;border-radius:6px;">{left_html}</table>
            <table style="border-collapse:collapse;width:100%;
                          background:#0f172a;border-radius:6px;">{right_html}</table>
        </div>"""

    return f"""
    <div style="margin-top:16px;padding-top:16px;border-top:1px solid #334155;">
        <div class="metric-label" style="margin-bottom:10px;">Technical Indicators</div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;">{pills_html}</div>
        {range_bar}
        {metrics_html}
    </div>"""


def _render_quarterly_table(market_data: Optional[MarketData]) -> str:
    """Render quarterly financial snapshot table from real MarketData."""
    if not market_data or not market_data.quarterly:
        return ""

    rows = ""
    for q in market_data.quarterly:
        rows += f"""
        <tr>
            <td>{q.period}</td>
            <td>{_fmt_val(q.revenue)}</td>
            <td>{_fmt_pct(q.gross_margin)}</td>
            <td>{_fmt_pct(q.operating_margin)}</td>
            <td>{_fmt_val(q.free_cash_flow)}</td>
        </tr>"""

    return f"""
    <div style="margin-top:16px;padding-top:16px;border-top:1px solid #334155;">
        <div class="metric-label">Recent Quarters</div>
        <div style="overflow-x:auto;margin-top:8px;">
            <table class="fin-table">
                <thead>
                    <tr>
                        <th>Quarter</th>
                        <th>Revenue</th>
                        <th>Gross Mgn</th>
                        <th>Op Mgn</th>
                        <th>FCF</th>
                    </tr>
                </thead>
                <tbody>{rows}
                </tbody>
            </table>
        </div>
    </div>"""


def _render_financial_table(market_data: Optional[MarketData]) -> str:
    """Render annual financial snapshot table from real MarketData."""
    if not market_data or not market_data.annual:
        return ""

    rows = ""
    for a in market_data.annual:
        yoy_color = ""
        if a.revenue_growth_yoy is not None:
            yoy_color = "#22c55e" if a.revenue_growth_yoy >= 0 else "#ef4444"

        net_debt_color = ""
        if a.net_debt is not None:
            net_debt_color = "#ef4444" if a.net_debt > 0 else "#22c55e"

        rows += f"""
        <tr>
            <td>{a.year}</td>
            <td>{_fmt_val(a.revenue)}</td>
            <td style="color:{yoy_color or '#94a3b8'}">{_fmt_pct_signed(a.revenue_growth_yoy)}</td>
            <td>{_fmt_pct(a.gross_margin)}</td>
            <td>{_fmt_pct(a.operating_margin)}</td>
            <td>{_fmt_pct(a.net_margin)}</td>
            <td>{_fmt_val(a.free_cash_flow)}</td>
            <td style="color:{net_debt_color or '#94a3b8'}">{_fmt_val_signed(a.net_debt)}</td>
            <td>{_fmt_coverage(a.interest_coverage)}</td>
        </tr>"""

    return f"""
    <div style="margin-top:8px;">
        <div class="metric-label">Annual Trend</div>
        <div style="overflow-x:auto;margin-top:8px;">
            <table class="fin-table">
                <thead>
                    <tr>
                        <th>Year</th>
                        <th>Revenue</th>
                        <th>YoY</th>
                        <th>Gross Mgn</th>
                        <th>Op Mgn</th>
                        <th>Net Mgn</th>
                        <th>FCF</th>
                        <th>Net Debt</th>
                        <th>Int Cov</th>
                    </tr>
                </thead>
                <tbody>{rows}
                </tbody>
            </table>
        </div>
    </div>"""


# ---------------------------------------------------------------------------
# Position card — shared header + recommendation
# ---------------------------------------------------------------------------

def _render_card_header(analysis: PositionAnalysis) -> str:
    rec_color = _recommendation_color(analysis.recommendation)
    type_badge = (
        "<span style='font-size:10px;padding:1px 6px;border-radius:8px;"
        "background:#0f172a;color:#64748b;margin-left:8px;'>ETF</span>"
        if analysis.asset_type == "ETF" else ""
    )
    return f"""
        <div class="card-header">
            <h2>{analysis.symbol}{type_badge}</h2>
            <div style="display:flex;gap:8px;align-items:center;">
                <span class="badge" style="background:{rec_color}">
                    {analysis.recommendation.upper()}
                </span>
                <span style="color:#94a3b8;font-size:13px;">
                    {analysis.conviction} conviction
                </span>
            </div>
        </div>"""


def _render_recommendation_block(raw: dict) -> str:
    rec = raw.get("recommendation", {})
    return f"""
        <div class="rationale-block">
            <div class="metric-label">Rationale</div>
            <p class="metric-summary">{rec.get("rationale", "")}</p>
            <div class="updown-grid">
                <div>
                    <span style="color:#22c55e">▲ Key Upsides</span>
                    {_render_bullet_list(rec.get("key_upsides", []))}
                </div>
                <div>
                    <span style="color:#ef4444">▼ Key Downsides</span>
                    {_render_bullet_list(rec.get("key_downsides", []))}
                </div>
            </div>
        </div>"""


def _render_bull_bear_section(raw: dict) -> str:
    bull = raw.get("bull_case", {})
    bear = raw.get("bear_case", {})
    if not bull and not bear:
        return ""
    bull_catalysts = "".join(f"<li>{c}</li>" for c in bull.get("catalysts", []))
    bear_risks = "".join(f"<li>{r}</li>" for r in bear.get("risks", []))
    return f"""
        <div style="margin-top:16px;padding-top:16px;border-top:1px solid #334155;">
            <div class="updown-grid">
                <div>
                    <div class="metric-label" style="color:#22c55e;">▲ Bull Case</div>
                    <p class="metric-summary" style="margin-bottom:6px;">{bull.get("thesis", "")}</p>
                    <ul class="risk-list">{bull_catalysts}</ul>
                </div>
                <div>
                    <div class="metric-label" style="color:#ef4444;">▼ Bear Case</div>
                    <p class="metric-summary" style="margin-bottom:6px;">{bear.get("thesis", "")}</p>
                    <ul class="risk-list">{bear_risks}</ul>
                </div>
            </div>
        </div>"""


# ---------------------------------------------------------------------------
# Equity card body
# ---------------------------------------------------------------------------

def _render_equity_body(analysis: PositionAnalysis,
                        market_data: Optional[MarketData]) -> str:
    raw = analysis.raw
    bq = raw.get("business_quality", {})
    fh = raw.get("financial_health", {})
    val = raw.get("valuation", {})
    risks = raw.get("risks", {})
    news = raw.get("news_sentiment", {})
    growth = raw.get("growth_opportunities", [])

    rev_trend = fh.get("revenue_trend", "")
    margin_trend = fh.get("margin_trend", "")
    fcf_quality = fh.get("fcf_quality", "")
    sentiment_color = _sentiment_color(news.get("sentiment", "neutral"))

    risks_list = "".join(f"<li>{r}</li>" for r in risks.get("key_risks", []))

    quarterly_table = _render_quarterly_table(market_data)
    annual_table = _render_financial_table(market_data)
    technicals_panel = _render_technicals_panel(market_data)

    tech = raw.get("technical_analysis", {})
    tech_signal_color = _signal_color(tech.get("signal", "neutral"))

    bull_bear_section = _render_bull_bear_section(raw)

    growth_section = ""
    if growth:
        growth_section = f"""
        <div style="margin-top:16px;padding-top:16px;border-top:1px solid #334155;">
            <div class="metric-label" style="color:#818cf8;">
                ↗ Growth Opportunities
            </div>
            {_render_bullet_list(growth)}
        </div>"""

    return f"""
        <div style="display:flex;gap:6px;margin:6px 0;flex-wrap:wrap;">
            <span style="font-size:11px;padding:2px 8px;border-radius:10px;
                        background:#0f172a;color:#94a3b8;">
                Rev: {rev_trend}
            </span>
            <span style="font-size:11px;padding:2px 8px;border-radius:10px;
                        background:#0f172a;color:#94a3b8;">
                Margins: {margin_trend}
            </span>
            <span style="font-size:11px;padding:2px 8px;border-radius:10px;
                        background:#0f172a;color:#94a3b8;">
                FCF: {fcf_quality}
            </span>
        </div>

        <div class="grid-3">
            <div class="metric-block">
                <div class="metric-label">Business Quality</div>
                {_score_bar(bq.get("score", 0))}
                <p class="metric-summary">{bq.get("summary", "")}</p>
            </div>
            <div class="metric-block">
                <div class="metric-label">Financial Health</div>
                {_score_bar(fh.get("score", 0))}
                <p class="metric-summary">{fh.get("summary", "")}</p>
            </div>
            <div class="metric-block">
                <div class="metric-label">Risk</div>
                {_score_bar(risks.get("score", 0), invert=True)}
                <ul class="risk-list">{risks_list}</ul>
            </div>
        </div>

        {quarterly_table}
        {annual_table}

        {technicals_panel}

        <div class="grid-3" style="margin-top:16px;">
            <div class="metric-block">
                <div class="metric-label">Valuation —
                    <span style="color:#f59e0b">
                        {val.get("assessment", "").upper()}
                    </span>
                </div>
                <p class="metric-summary">{val.get("summary", "")}</p>
            </div>
            <div class="metric-block">
                <div class="metric-label">Technicals —
                    <span style="color:{tech_signal_color}">
                        {tech.get("signal", "").upper()}
                    </span>
                </div>
                <p class="metric-summary">{tech.get("summary", "")}</p>
            </div>
            <div class="metric-block">
                <div class="metric-label">News Sentiment —
                    <span style="color:{sentiment_color}">
                        {news.get("sentiment", "").upper()}
                    </span>
                </div>
                <p class="metric-summary">{news.get("summary", "")}</p>
            </div>
        </div>

        {growth_section}

        {bull_bear_section}

        {_render_recommendation_block(raw)}"""


# ---------------------------------------------------------------------------
# ETF card body
# ---------------------------------------------------------------------------

def _render_etf_body(analysis: PositionAnalysis,
                     market_data: Optional[MarketData] = None) -> str:
    raw = analysis.raw
    fq = raw.get("fund_quality", {})
    te = raw.get("thematic_exposure", {})
    val = raw.get("valuation", {})
    risks = raw.get("risks", {})
    news = raw.get("news_sentiment", {})

    theme_strength = te.get("theme_strength", "")
    theme_color = {"strong": "#22c55e", "moderate": "#f59e0b", "weak": "#ef4444"}.get(
        theme_strength.lower(), "#94a3b8"
    )
    sentiment_color = _sentiment_color(news.get("sentiment", "neutral"))
    risks_list = "".join(f"<li>{r}</li>" for r in risks.get("key_risks", []))

    tech = raw.get("technical_analysis", {})
    tech_signal_color = _signal_color(tech.get("signal", "neutral"))

    return f"""
        <div style="display:flex;gap:6px;margin:6px 0;flex-wrap:wrap;">
            <span style="font-size:11px;padding:2px 8px;border-radius:10px;
                        background:#0f172a;color:{theme_color};">
                Theme: {theme_strength.upper() if theme_strength else "N/A"}
            </span>
        </div>

        <div class="grid-3">
            <div class="metric-block">
                <div class="metric-label">Fund Quality</div>
                {_score_bar(fq.get("score", 0))}
                <p class="metric-summary">{fq.get("summary", "")}</p>
            </div>
            <div class="metric-block">
                <div class="metric-label">Thematic Exposure</div>
                {_score_bar(te.get("score", 0))}
                <p class="metric-summary">{te.get("summary", "")}</p>
            </div>
            <div class="metric-block">
                <div class="metric-label">Risk</div>
                {_score_bar(risks.get("score", 0), invert=True)}
                <ul class="risk-list">{risks_list}</ul>
            </div>
        </div>

        {_render_technicals_panel(market_data)}

        <div class="grid-3" style="margin-top:16px;">
            <div class="metric-block">
                <div class="metric-label">Valuation —
                    <span style="color:#f59e0b">
                        {val.get("assessment", "").upper()}
                    </span>
                </div>
                <p class="metric-summary">{val.get("summary", "")}</p>
            </div>
            <div class="metric-block">
                <div class="metric-label">Technicals —
                    <span style="color:{tech_signal_color}">
                        {tech.get("signal", "").upper()}
                    </span>
                </div>
                <p class="metric-summary">{tech.get("summary", "")}</p>
            </div>
            <div class="metric-block">
                <div class="metric-label">News Sentiment —
                    <span style="color:{sentiment_color}">
                        {news.get("sentiment", "").upper()}
                    </span>
                </div>
                <p class="metric-summary">{news.get("summary", "")}</p>
            </div>
        </div>

        {_render_bull_bear_section(raw)}

        {_render_recommendation_block(raw)}"""


# ---------------------------------------------------------------------------
# Position card dispatcher
# ---------------------------------------------------------------------------

def _render_position_card(analysis: PositionAnalysis,
                          market_data: Optional[MarketData] = None) -> str:
    if analysis.fetch_error and not analysis.raw:
        return f"""
        <div class="card">
            <div class="card-header">
                <h2>{analysis.symbol}</h2>
                <span class="badge" style="background:#ef4444">ERROR</span>
            </div>
            <p style="color:#ef4444">{analysis.fetch_error}</p>
        </div>"""

    if analysis.asset_type == "ETF":
        body = _render_etf_body(analysis, market_data)
    else:
        body = _render_equity_body(analysis, market_data)

    return f"""
    <div class="card">
        {_render_card_header(analysis)}
        {body}
    </div>"""


# ---------------------------------------------------------------------------
# Portfolio summary section
# ---------------------------------------------------------------------------

def _render_sizing_bars(sizing_alignment: list[dict]) -> str:
    if not sizing_alignment:
        return ""
    ACTION_COLOR = {"buy": "#22c55e", "hold": "#f59e0b", "sell": "#ef4444"}
    FLAG_LABELS = {
        "undersized_high_conviction_buy": "Undersized",
        "oversized_sell": "Oversized SELL",
        "oversized_low_conviction": "Oversized low conv.",
    }
    rows = ""
    for s in sizing_alignment:
        color = ACTION_COLOR.get(s["action"], "#94a3b8")
        bar_width = min(s["weight_pct"] * 2, 100)
        flag_html = ""
        if s.get("flag"):
            label = FLAG_LABELS.get(s["flag"], s["flag"])
            flag_html = (
                f'<span style="font-size:10px;padding:1px 6px;border-radius:8px;'
                f'background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b55;'
                f'margin-left:8px;">{label}</span>'
            )
        rows += f"""
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
            <span style="width:44px;font-size:12px;color:#94a3b8;flex-shrink:0;">{s['symbol']}</span>
            <div style="flex:1;background:#0f172a;border-radius:3px;height:10px;">
                <div style="width:{bar_width:.1f}%;background:{color};border-radius:3px;height:10px;"></div>
            </div>
            <span style="width:40px;text-align:right;font-size:12px;color:#64748b;">{s['weight_pct']:.1f}%</span>
            <span style="font-size:10px;padding:1px 6px;border-radius:8px;
                background:{color}22;color:{color};border:1px solid {color}55;width:36px;text-align:center;">
                {s['action'].upper()[:1]}
            </span>
            {flag_html}
        </div>"""
    return f"""
    <div style="margin-top:16px;padding-top:16px;border-top:1px solid #334155;">
        <div class="metric-label" style="margin-bottom:10px;">Position Sizing</div>
        {rows}
    </div>"""


def _render_beta_drawdown(portfolio_beta: Optional[float], drawdown_scenarios: list[dict]) -> str:
    if portfolio_beta is None:
        return ""
    rows = ""
    intensities = ["#1e293b", "#1c2333", "#1a1f2e", "#1e1520", "#2d0f0f"]
    for i, d in enumerate(drawdown_scenarios):
        bg = intensities[min(i, len(intensities) - 1)]
        impact_color = "#ef4444" if d["eur_impact"] < 0 else "#22c55e"
        rows += f"""
        <tr style="background:{bg};">
            <td style="padding:5px 10px;font-size:12px;color:#94a3b8;">Market {d['market_pct']:+d}%</td>
            <td style="padding:5px 10px;font-size:12px;color:{impact_color};text-align:right;">{d['portfolio_pct']:+.1f}%</td>
            <td style="padding:5px 10px;font-size:12px;color:{impact_color};text-align:right;">€{d['eur_impact']:,.0f}</td>
        </tr>"""
    return f"""
    <div style="margin-top:16px;padding-top:16px;border-top:1px solid #334155;">
        <div class="metric-label" style="margin-bottom:10px;">
            Portfolio Beta: <span style="color:#818cf8;">{portfolio_beta:.2f}</span>
        </div>
        <table style="width:100%;border-collapse:collapse;border-radius:6px;overflow:hidden;">
            <thead>
                <tr style="background:#0f172a;">
                    <th style="padding:4px 10px;font-size:11px;color:#475569;text-align:left;font-weight:600;">Market Drop</th>
                    <th style="padding:4px 10px;font-size:11px;color:#475569;text-align:right;font-weight:600;">Portfolio</th>
                    <th style="padding:4px 10px;font-size:11px;color:#475569;text-align:right;font-weight:600;">EUR Impact</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
    </div>"""


def _render_correlation_heatmap(correlation_matrix: dict) -> str:
    if not correlation_matrix or len(correlation_matrix) < 2:
        return ""
    syms = list(correlation_matrix.keys())

    def _cell_color(r: float, is_diag: bool) -> str:
        if is_diag:
            return "#6366f1"
        if abs(r) > 0.75:
            return "#ef4444"
        if abs(r) > 0.50:
            return "#f59e0b"
        if abs(r) > 0.25:
            return "#64748b"
        return "#22c55e"

    n = len(syms)
    cell_size = max(52, min(80, 400 // n))

    header_cells = f'<div style="width:{cell_size}px;height:{cell_size}px;"></div>'
    for s in syms:
        header_cells += (
            f'<div style="width:{cell_size}px;height:{cell_size}px;display:flex;'
            f'align-items:center;justify-content:center;font-size:11px;color:#64748b;">{s}</div>'
        )

    data_rows = ""
    for s1 in syms:
        row = (
            f'<div style="width:{cell_size}px;height:{cell_size}px;display:flex;'
            f'align-items:center;justify-content:center;font-size:11px;color:#64748b;">{s1}</div>'
        )
        for s2 in syms:
            r = correlation_matrix[s1][s2]
            is_diag = s1 == s2
            color = _cell_color(r, is_diag)
            val_str = "1.0" if is_diag else f"{r:+.2f}"
            row += (
                f'<div style="width:{cell_size}px;height:{cell_size}px;background:{color}33;'
                f'border:1px solid {color}55;display:flex;align-items:center;justify-content:center;'
                f'font-size:11px;color:{color};font-weight:600;border-radius:4px;">{val_str}</div>'
            )
        data_rows += f'<div style="display:flex;gap:3px;margin-bottom:3px;">{row}</div>'

    return f"""
    <div style="margin-top:16px;padding-top:16px;border-top:1px solid #334155;">
        <div class="metric-label" style="margin-bottom:10px;">Return Correlation (1yr daily)</div>
        <div style="overflow-x:auto;">
            <div style="display:flex;gap:3px;margin-bottom:3px;">{header_cells}</div>
            {data_rows}
        </div>
        <p style="font-size:11px;color:#334155;margin-top:8px;">
            <span style="color:#ef4444">&#9632;</span> High (&gt;0.75) &nbsp;
            <span style="color:#f59e0b">&#9632;</span> Mod (0.50&ndash;0.75) &nbsp;
            <span style="color:#64748b">&#9632;</span> Low (0.25&ndash;0.50) &nbsp;
            <span style="color:#22c55e">&#9632;</span> Uncorrelated (&lt;0.25)
        </p>
    </div>"""


def _render_cross_portfolio_news_themes(themes) -> str:
    """Render cross-portfolio news themes as a bullet list."""
    if not themes or not isinstance(themes, list):
        return ""
    items = "".join(f"<li>{theme}</li>" for theme in themes if theme)
    if not items:
        return ""
    return f"""
        <div class="metric-label" style="margin-top:12px;">
            Cross-Portfolio News Themes
        </div>
        <ul class="risk-list">{items}</ul>"""


def _render_portfolio_summary(
    analysis: PortfolioAnalysis,
) -> str:
    summary = analysis.portfolio_summary
    if not summary:
        return ""

    buy_count = sum(
        1 for p in analysis.positions if p.recommendation == "buy"
    )
    hold_count = sum(
        1 for p in analysis.positions if p.recommendation == "hold"
    )
    sell_count = sum(
        1 for p in analysis.positions if p.recommendation == "sell"
    )

    return f"""
    <div class="card" style="border-left: 4px solid #6366f1;">
        <h2 style="color:#6366f1;">Portfolio Summary</h2>

        <div class="grid-3" style="margin-bottom:16px;">
            <div class="summary-stat" style="color:#22c55e">
                <div class="stat-number">{buy_count}</div>
                <div class="stat-label">BUY</div>
            </div>
            <div class="summary-stat" style="color:#f59e0b">
                <div class="stat-number">{hold_count}</div>
                <div class="stat-label">HOLD</div>
            </div>
            <div class="summary-stat" style="color:#ef4444">
                <div class="stat-number">{sell_count}</div>
                <div class="stat-label">SELL</div>
            </div>
        </div>

        <div class="grid-2">
            <div>
                <div class="metric-label">Overall Assessment</div>
                <p class="metric-summary">
                    {summary.get("overall_assessment", "")}
                </p>
            </div>
            <div>
                <div class="metric-label">Concentration Risk</div>
                <p class="metric-summary">
                    {summary.get("concentration_risk", "")}
                </p>
            </div>
            <div>
                <div class="metric-label">Top Opportunity</div>
                <p class="metric-summary">
                    <strong>{summary.get("top_opportunity", {}).get("symbol", "")}</strong>
                    — {summary.get("top_opportunity", {}).get("reason", "")}
                </p>
            </div>
            <div>
                <div class="metric-label">Top Risk</div>
                <p class="metric-summary">
                    <strong>{summary.get("top_risk", {}).get("symbol", "")}</strong>
                    — {summary.get("top_risk", {}).get("reason", "")}
                </p>
            </div>
            <div>
                <div class="metric-label">Rebalance Suggestion</div>
                <p class="metric-summary">
                    {summary.get("rebalance_suggestion", "")}
                </p>
            </div>
        </div>

        <div class="rationale-block">
            <div class="metric-label">Portfolio Action</div>
            <p class="metric-summary">
                {summary.get("portfolio_action", "")}
            </p>
            <div class="metric-label" style="margin-top:12px;">
                Market Context
            </div>
            <p class="metric-summary">
                {summary.get("market_context", "")}
            </p>
            {_render_cross_portfolio_news_themes(summary.get("cross_portfolio_news_themes", []))}
        </div>

        {_render_sizing_bars(analysis.sizing_alignment)}
        {_render_beta_drawdown(analysis.portfolio_beta, analysis.drawdown_scenarios)}
        {_render_correlation_heatmap(analysis.correlation_matrix)}
    </div>"""


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
                 sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    padding: 32px 16px;
}
.container { max-width: 1100px; margin: 0 auto; }
.header {
    margin-bottom: 32px;
    padding-bottom: 16px;
    border-bottom: 1px solid #1e293b;
}
.header h1 { font-size: 24px; color: #f8fafc; }
.header p  { color: #64748b; font-size: 14px; margin-top: 4px; }
.card {
    background: #1e293b;
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 20px;
}
.card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
}
.card-header h2 { font-size: 20px; color: #f8fafc; }
.badge {
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 600;
    color: white;
}
.grid-3 {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
}
.grid-2 {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 16px;
}
.metric-block { padding: 4px 0; }
.metric-label {
    font-size: 12px;
    font-weight: 600;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 6px;
}
.metric-summary {
    font-size: 13px;
    color: #94a3b8;
    line-height: 1.6;
    margin-top: 6px;
}
.risk-list {
    font-size: 13px;
    color: #94a3b8;
    padding-left: 16px;
    margin-top: 6px;
    line-height: 1.8;
}
.rationale-block {
    margin-top: 16px;
    padding-top: 16px;
    border-top: 1px solid #334155;
}
.updown-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-top: 12px;
}
.summary-stat {
    text-align: center;
    padding: 12px;
    background: #0f172a;
    border-radius: 8px;
}
.stat-number { font-size: 32px; font-weight: 700; }
.stat-label  { font-size: 12px; color: #64748b; margin-top: 4px; }
.fin-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
}
.fin-table th {
    text-align: right;
    color: #475569;
    font-weight: 600;
    padding: 4px 8px;
    border-bottom: 1px solid #334155;
    white-space: nowrap;
}
.fin-table th:first-child { text-align: left; }
.fin-table td {
    text-align: right;
    color: #94a3b8;
    padding: 5px 8px;
    border-bottom: 1px solid #1e293b;
}
.fin-table td:first-child { text-align: left; color: #cbd5e1; }
.fin-table tr:last-child td { border-bottom: none; }
@media (max-width: 700px) {
    .grid-3, .grid-2, .updown-grid {
        grid-template-columns: 1fr;
    }
}
"""


def generate_report(
    analysis: PortfolioAnalysis,
    output_path: str | Path = "report.html",
) -> Path:
    """
    Generate HTML report from portfolio analysis.

    Args:
        analysis: Completed PortfolioAnalysis object
        output_path: Where to save the HTML file

    Returns:
        Path to the generated report
    """
    output_path = Path(output_path)
    generated_at = datetime.now().strftime("%d %B %Y %H:%M")

    position_cards = "\n".join(
        _render_position_card(p, analysis.market_data.get(p.symbol))
        for p in analysis.positions
    )
    portfolio_summary = _render_portfolio_summary(analysis)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Portfolio Analysis — {analysis.snapshot_date}</title>
    <style>{CSS}</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Portfolio Analysis</h1>
            <p>
                {analysis.account_reference} &nbsp;·&nbsp;
                Statement date: {analysis.snapshot_date} &nbsp;·&nbsp;
                Generated: {generated_at} &nbsp;·&nbsp;
                Total value: €{analysis.total_value_eur:,.2f}
            </p>
        </div>

        {portfolio_summary}

        <h2 style="margin: 24px 0 16px; color:#64748b;
                   font-size:14px; text-transform:uppercase;
                   letter-spacing:0.05em;">
            Individual Positions
        </h2>

        {position_cards}
    </div>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    print(f"Report saved to {output_path}")
    return output_path

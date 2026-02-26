"""
Market data fetcher using yfinance (financials) and Finnhub (news).
Fetches financial statements, valuation metrics, news and
quarterly/annual financial trends for each position in the portfolio.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Optional

import finnhub
import pandas as pd
import yfinance as yf
from pydantic import BaseModel, Field, field_validator


# Some ETFs listed on European exchanges need exchange suffix for yfinance
TICKER_OVERRIDES = {
    "EXX1": "EXX1.DE",
    "EXH1": "EXH1.DE",
}

# Thematic keywords for general news search (used for ETFs without direct Finnhub coverage)
ETF_NEWS_THEMES: dict[str, list[str]] = {
    "EXX1": ["european bank", "euro bank", "stoxx banks", "ecb", "european financial"],
    "EXH1": ["european oil", "oil gas", "opec", "crude oil", "natural gas", "energy sector"],
}


class ValuationMetrics(BaseModel):
    pe_trailing: Optional[float] = None
    pe_forward: Optional[float] = None
    pb_ratio: Optional[float] = None
    ev_to_ebitda: Optional[float] = None
    price_to_sales: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    return_on_equity: Optional[float] = None
    return_on_assets: Optional[float] = None
    profit_margin: Optional[float] = None
    revenue_growth: Optional[float] = None
    earnings_growth: Optional[float] = None
    dividend_yield: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    current_price: Optional[float] = None
    market_cap: Optional[float] = None
    beta: Optional[float] = None
    free_cash_flow_ttm: Optional[float] = None
    total_debt: Optional[float] = None
    total_cash: Optional[float] = None
    # ETF-specific (None for equities)
    expense_ratio: Optional[float] = None
    total_assets: Optional[float] = None       # AUM
    ytd_return: Optional[float] = None
    three_year_avg_return: Optional[float] = None
    five_year_avg_return: Optional[float] = None

    @field_validator("ytd_return", "three_year_avg_return", "five_year_avg_return", mode="before")
    @classmethod
    def validate_return(cls, v: object) -> Optional[float]:
        """Reject corrupted ETF return data from yfinance (e.g. 3.5 meaning 350%)."""
        if v is None:
            return None
        try:
            v = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return None if abs(v) > 2.0 else v  # type: ignore[return-value]


class QuarterlySnapshot(BaseModel):
    """One quarter of financial data."""
    period: str                           # e.g. "2024-Q3"
    revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None
    free_cash_flow: Optional[float] = None
    gross_margin: Optional[float] = None  # derived
    operating_margin: Optional[float] = None  # derived

    @field_validator("gross_margin", "operating_margin", mode="before")
    @classmethod
    def validate_margin(cls, v: object) -> Optional[float]:
        """Coerce out-of-range margin values to None."""
        if v is None:
            return None
        try:
            v = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return None if abs(v) > 2.0 else v  # type: ignore[return-value]


class AnnualSnapshot(BaseModel):
    """One fiscal year of financial data with YoY growth rates."""
    year: str                               # e.g. "2024"
    revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None
    free_cash_flow: Optional[float] = None
    total_debt: Optional[float] = None
    net_debt: Optional[float] = None        # total_debt - cash (positive = net debt)
    gross_margin: Optional[float] = None    # derived
    operating_margin: Optional[float] = None  # derived
    net_margin: Optional[float] = None      # derived
    revenue_growth_yoy: Optional[float] = None  # derived vs prior year
    interest_coverage: Optional[float] = None   # operating_income / |interest_expense|

    @field_validator("gross_margin", "operating_margin", "net_margin", mode="before")
    @classmethod
    def validate_margin(cls, v: object) -> Optional[float]:
        if v is None:
            return None
        try:
            v = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return None if abs(v) > 2.0 else v  # type: ignore[return-value]

    @field_validator("revenue_growth_yoy", mode="before")
    @classmethod
    def validate_growth(cls, v: object) -> Optional[float]:
        if v is None:
            return None
        try:
            v = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return None if abs(v) > 10.0 else v  # type: ignore[return-value]


@dataclass
class NewsItem:
    title: str
    publisher: str
    link: str
    published_at: str
    summary: str = field(default="")


class TechnicalIndicators(BaseModel):
    """Price-based technical indicators computed from 1-year daily history."""
    rsi_14: Optional[float] = None              # 0–100
    macd: Optional[float] = None                # MACD line (12,26)
    macd_signal: Optional[float] = None         # Signal line (9)
    macd_hist: Optional[float] = None           # Histogram = macd − signal
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    price_vs_sma50: Optional[float] = None      # (price − sma50) / sma50
    price_vs_sma200: Optional[float] = None
    golden_cross: Optional[bool] = None         # sma50 > sma200
    bb_upper: Optional[float] = None            # Bollinger upper (20, 2σ)
    bb_lower: Optional[float] = None
    bb_pct: Optional[float] = None              # %B — 0 = lower, 1 = upper
    volume_ratio: Optional[float] = None        # 10d avg / 90d avg
    price_52w_high: Optional[float] = None
    price_52w_low: Optional[float] = None
    pct_from_52w_high: Optional[float] = None   # negative = below high
    pct_from_52w_low: Optional[float] = None    # positive = above low


class MarketData(BaseModel):
    symbol: str
    yf_symbol: str
    short_name: str
    long_name: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    currency: str
    asset_type: Literal["EQUITY", "ETF", "UNKNOWN"]
    description: Optional[str] = None
    metrics: ValuationMetrics
    quarterly: list[QuarterlySnapshot] = Field(default_factory=list)
    annual: list[AnnualSnapshot] = Field(default_factory=list)
    news: list[NewsItem] = Field(default_factory=list)
    technicals: Optional[TechnicalIndicators] = None
    fetch_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_symbol(symbol: str) -> str:
    """Apply exchange suffix overrides for European ETFs."""
    return TICKER_OVERRIDES.get(symbol, symbol)


def _extract_metrics(info: dict) -> ValuationMetrics:
    """Extract valuation metrics from yfinance info dict."""
    return ValuationMetrics(
        pe_trailing=info.get("trailingPE"),
        pe_forward=info.get("forwardPE"),
        pb_ratio=info.get("priceToBook"),
        ev_to_ebitda=info.get("enterpriseToEbitda"),
        price_to_sales=info.get("priceToSalesTrailing12Months"),
        debt_to_equity=info.get("debtToEquity"),
        current_ratio=info.get("currentRatio"),
        return_on_equity=info.get("returnOnEquity"),
        return_on_assets=info.get("returnOnAssets"),
        profit_margin=info.get("profitMargins"),
        revenue_growth=info.get("revenueGrowth"),
        earnings_growth=info.get("earningsGrowth"),
        dividend_yield=info.get("dividendYield"),
        fifty_two_week_high=info.get("fiftyTwoWeekHigh"),
        fifty_two_week_low=info.get("fiftyTwoWeekLow"),
        current_price=info.get("currentPrice") or info.get(
            "regularMarketPrice"),
        market_cap=info.get("marketCap"),
        beta=info.get("beta"),
        free_cash_flow_ttm=info.get("freeCashflow"),
        total_debt=info.get("totalDebt"),
        total_cash=info.get("totalCash"),
        # ETF-specific fields (None for equities)
        expense_ratio=info.get("annualReportExpenseRatio"),
        total_assets=info.get("totalAssets"),
        ytd_return=info.get("ytdReturn"),
        three_year_avg_return=info.get("threeYearAverageReturn"),
        five_year_avg_return=info.get("fiveYearAverageReturn"),
    )


def _safe_value(
    df: pd.DataFrame,
    row_name: str,
    col: pd.Timestamp,
) -> Optional[float]:
    """Safely extract a single cell from a yfinance financial DataFrame."""
    try:
        if row_name in df.index:
            val = df.loc[row_name, col]
            if pd.notna(val):
                return float(val)
    except Exception:
        pass
    return None


def _safe_value_multi(
    df: pd.DataFrame,
    row_names: list[str],
    col: pd.Timestamp,
) -> Optional[float]:
    """Try multiple row names and return the first hit."""
    for name in row_names:
        val = _safe_value(df, name, col)
        if val is not None:
            return val
    return None


def _extract_quarterly_financials(
    ticker: yf.Ticker,
) -> list[QuarterlySnapshot]:
    """
    Extract last 4 quarters of key financial metrics.
    Returns list ordered oldest → newest for trend reading.
    ETFs return empty list gracefully.
    """
    try:
        income = ticker.quarterly_income_stmt
        cashflow = ticker.quarterly_cashflow

        if income is None or income.empty:
            return []

        # yfinance returns columns as Timestamps, newest first
        # Take up to 4 most recent quarters
        quarters = income.columns[:4]
        snapshots = []

        for col in reversed(quarters):  # reverse so oldest first
            period = col.strftime("%Y-Q") + str((col.month - 1) // 3 + 1)

            revenue = _safe_value(income, "Total Revenue", col)
            gross_profit = _safe_value(income, "Gross Profit", col)
            operating_income = _safe_value(income, "Operating Income", col)
            net_income = _safe_value(income, "Net Income", col)

            # Free cash flow = operating CF - capex (capex is negative)
            free_cash_flow = None
            if cashflow is not None and not cashflow.empty \
                    and col in cashflow.columns:
                op_cf = _safe_value(cashflow, "Operating Cash Flow", col)
                capex = _safe_value(cashflow, "Capital Expenditure", col)
                if op_cf is not None and capex is not None:
                    free_cash_flow = op_cf + capex

            # Derived margins
            gross_margin = None
            operating_margin = None
            if revenue and revenue != 0:
                if gross_profit is not None:
                    gross_margin = gross_profit / revenue
                if operating_income is not None:
                    operating_margin = operating_income / revenue

            snapshots.append(QuarterlySnapshot(
                period=period,
                revenue=revenue,
                gross_profit=gross_profit,
                operating_income=operating_income,
                net_income=net_income,
                free_cash_flow=free_cash_flow,
                gross_margin=gross_margin,
                operating_margin=operating_margin,
            ))

        return snapshots

    except Exception as e:
        print(f"  Warning: Could not fetch quarterly financials: {e}")
        return []


def _extract_annual_financials(
    ticker: yf.Ticker,
) -> list[AnnualSnapshot]:
    """
    Extract last 4 fiscal years of key financial metrics with YoY growth.
    Returns list ordered oldest → newest for trend r eading.
    ETFs return empty list gracefully.
    """
    try:
        income = ticker.income_stmt
        cashflow = ticker.cashflow
        balance = ticker.balance_sheet

        if income is None or income.empty:
            return []

        # yfinance returns columns as Timestamps, newest first
        # Take up to 4 most recent fiscal years
        years = income.columns[:4]
        snapshots = []
        prev_revenue = None

        for col in reversed(years):  # oldest first for YoY calculation
            year = col.strftime("%Y")

            revenue = _safe_value(income, "Total Revenue", col)
            gross_profit = _safe_value(income, "Gross Profit", col)
            operating_income = _safe_value(income, "Operating Income", col)
            net_income = _safe_value(income, "Net Income", col)
            interest_expense = _safe_value(income, "Interest Expense", col)

            # Free cash flow = operating CF + capex (capex stored as negative)
            free_cash_flow = None
            if cashflow is not None and not cashflow.empty \
                    and col in cashflow.columns:
                op_cf = _safe_value(cashflow, "Operating Cash Flow", col)
                capex = _safe_value(cashflow, "Capital Expenditure", col)
                if op_cf is not None and capex is not None:
                    free_cash_flow = op_cf + capex

            # Debt and net debt from balance sheet
            total_debt = None
            net_debt = None
            if balance is not None and not balance.empty \
                    and col in balance.columns:
                total_debt = _safe_value(balance, "Total Debt", col)
                cash = _safe_value_multi(balance, [
                    "Cash And Cash Equivalents",
                    "Cash Cash Equivalents And Short Term Investments",
                ], col)
                if total_debt is not None and cash is not None:
                    net_debt = total_debt - cash

            # Derived margins
            gross_margin = operating_margin = net_margin = None
            if revenue and revenue != 0:
                if gross_profit is not None:
                    gross_margin = gross_profit / revenue
                if operating_income is not None:
                    operating_margin = operating_income / revenue
                if net_income is not None:
                    net_margin = net_income / revenue

            # YoY revenue growth
            revenue_growth_yoy = None
            if prev_revenue and prev_revenue != 0 and revenue is not None:
                revenue_growth_yoy = (revenue - prev_revenue) / abs(prev_revenue)
            prev_revenue = revenue

            # Interest coverage = operating income / |interest expense|
            interest_coverage = None
            if operating_income is not None and interest_expense is not None \
                    and interest_expense != 0:
                interest_coverage = operating_income / abs(interest_expense)

            snapshots.append(AnnualSnapshot(
                year=year,
                revenue=revenue,
                gross_profit=gross_profit,
                operating_income=operating_income,
                net_income=net_income,
                free_cash_flow=free_cash_flow,
                total_debt=total_debt,
                net_debt=net_debt,
                gross_margin=gross_margin,
                operating_margin=operating_margin,
                net_margin=net_margin,
                revenue_growth_yoy=revenue_growth_yoy,
                interest_coverage=interest_coverage,
            ))

        return snapshots

    except Exception as e:
        print(f"  Warning: Could not fetch annual financials: {e}")
        return []


def _get_finnhub_client() -> finnhub.Client:
    return finnhub.Client(api_key=os.environ.get("FINNHUB_API_KEY", ""))


def _parse_finnhub_news(items: list) -> list[NewsItem]:
    """Parse raw Finnhub news items into NewsItem objects."""
    news_items = []
    for item in items:
        try:
            ts = item.get("datetime")
            published_at = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
            title = item.get("headline", "")
            if title:
                news_items.append(NewsItem(
                    title=title,
                    publisher=item.get("source", ""),
                    link=item.get("url", ""),
                    published_at=published_at,
                    summary=item.get("summary", ""),
                ))
        except Exception:
            continue
    return news_items


def _fetch_news_finnhub(symbol: str, asset_type: str) -> list[NewsItem]:
    """
    Fetch news via Finnhub API.
    - Equities: company-specific news for the past 14 days.
    - ETFs: general financial news filtered by thematic keywords.
    """
    try:
        client = _get_finnhub_client()
        today = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")

        if asset_type == "EQUITY":
            raw = client.company_news(symbol, _from=from_date, to=today)
            return _parse_finnhub_news(raw[:10])
        else:
            # ETF: filter general financial news by thematic keywords
            keywords = ETF_NEWS_THEMES.get(symbol, [])
            if not keywords:
                return []
            raw = client.general_news("general")
            filtered = [
                item for item in raw
                if any(
                    kw.lower() in (
                        item.get("headline", "") + " " + item.get("summary", "")
                    ).lower()
                    for kw in keywords
                )
            ]
            return _parse_finnhub_news(filtered[:10])

    except Exception as e:
        print(f"  Warning: Finnhub news fetch failed for {symbol}: {e}")
        return []


def _compute_technicals(ticker: yf.Ticker, symbol: str) -> TechnicalIndicators:
    """
    Compute RSI(14), MACD(12,26,9), SMA(50/200), Bollinger Bands(20,2),
    volume ratio and 52-week range from 1 year of daily price history.
    Uses pure pandas — no extra dependencies.
    """
    try:
        hist = ticker.history(period="1y")
        if hist.empty or len(hist) < 30:
            return TechnicalIndicators()

        close = hist["Close"].dropna()
        volume = hist["Volume"].dropna()

        if len(close) < 30:
            return TechnicalIndicators()

        current = float(close.iloc[-1])

        def _safe(series) -> Optional[float]:
            val = series.iloc[-1]
            return float(val) if not pd.isna(val) else None

        # --- RSI (14) ---
        delta = close.diff()
        avg_gain = delta.clip(lower=0).ewm(com=13, min_periods=14).mean()
        avg_loss = (-delta.clip(upper=0)).ewm(com=13, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        rsi_series = 100 - 100 / (1 + rs)
        rsi_val = _safe(rsi_series)

        # --- MACD (12, 26, 9) ---
        ema12 = close.ewm(span=12, min_periods=12).mean()
        ema26 = close.ewm(span=26, min_periods=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, min_periods=9).mean()
        macd_hist_series = macd_line - signal_line
        macd_val = _safe(macd_line)
        signal_val = _safe(signal_line)
        hist_val = _safe(macd_hist_series)

        # --- SMAs ---
        sma50 = _safe(close.rolling(50).mean()) if len(close) >= 50 else None
        sma200 = _safe(close.rolling(200).mean()) if len(close) >= 200 else None
        price_vs_sma50 = (current - sma50) / sma50 if sma50 else None
        price_vs_sma200 = (current - sma200) / sma200 if sma200 else None
        golden_cross = (sma50 > sma200) if (sma50 is not None and sma200 is not None) else None

        # --- Bollinger Bands (20, 2σ) ---
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_u = _safe(bb_mid + 2 * bb_std)
        bb_l = _safe(bb_mid - 2 * bb_std)
        bb_pct = (current - bb_l) / (bb_u - bb_l) if (bb_u and bb_l and bb_u != bb_l) else None

        # --- Volume ratio (10d avg vs 90d avg) ---
        vol_ratio = None
        if len(volume) >= 10:
            v10 = float(volume.tail(10).mean())
            v90 = float(volume.tail(90).mean()) if len(volume) >= 30 else None
            if v90 and v90 > 0:
                vol_ratio = v10 / v90

        # --- 52-week range ---
        w52 = close.tail(252)
        high_52w = float(w52.max())
        low_52w = float(w52.min())
        pct_from_high = (current - high_52w) / high_52w
        pct_from_low = (current - low_52w) / low_52w if low_52w != 0 else None

        return TechnicalIndicators(
            rsi_14=rsi_val,
            macd=macd_val,
            macd_signal=signal_val,
            macd_hist=hist_val,
            sma_50=sma50,
            sma_200=sma200,
            price_vs_sma50=price_vs_sma50,
            price_vs_sma200=price_vs_sma200,
            golden_cross=golden_cross,
            bb_upper=bb_u,
            bb_lower=bb_l,
            bb_pct=bb_pct,
            volume_ratio=vol_ratio,
            price_52w_high=high_52w,
            price_52w_low=low_52w,
            pct_from_52w_high=pct_from_high,
            pct_from_52w_low=pct_from_low,
        )

    except Exception as e:
        print(f"  Warning: Could not compute technicals for {symbol}: {e}")
        return TechnicalIndicators()


def _detect_asset_type(info: dict) -> Literal["EQUITY", "ETF", "UNKNOWN"]:
    """Detect whether ticker is ETF, equity, or an unsupported type.

    yfinance quoteType values: EQUITY, ETF, MUTUALFUND, CRYPTOCURRENCY,
    INDEX, FUTURE, OPTION. Only EQUITY and ETF are analysable; everything
    else is returned as UNKNOWN so callers can skip or flag gracefully.
    """
    qt = info.get("quoteType", "").upper()
    if qt == "ETF":
        return "ETF"
    if qt == "EQUITY":
        return "EQUITY"
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_market_data(symbol: str) -> MarketData:
    """
    Fetch market data for a single ticker symbol.
    Includes valuation metrics, quarterly/annual financials and news.
    """
    yf_symbol = _resolve_symbol(symbol)

    try:
        ticker = yf.Ticker(yf_symbol)
        info = ticker.info

        if not info or (
            info.get("regularMarketPrice") is None
            and info.get("currentPrice") is None
            and info.get("navPrice") is None
        ):
            return MarketData(
                symbol=symbol,
                yf_symbol=yf_symbol,
                short_name=symbol,
                long_name=symbol,
                sector=None,
                industry=None,
                currency=info.get("currency", "UNKNOWN"),
                asset_type="UNKNOWN",
                description=None,
                metrics=ValuationMetrics(),
                quarterly=[],
                annual=[],
                news=[],
                fetch_error=f"No price data found for {yf_symbol}",
            )

        asset_type = _detect_asset_type(info)

        # Only fetch financials for equities — ETFs don't report earnings
        quarterly = []
        annual = []
        if asset_type == "EQUITY":
            quarterly = _extract_quarterly_financials(ticker)
            annual = _extract_annual_financials(ticker)

        news = _fetch_news_finnhub(symbol, asset_type)
        technicals = _compute_technicals(ticker, symbol)

        return MarketData(
            symbol=symbol,
            yf_symbol=yf_symbol,
            short_name=info.get("shortName", symbol),
            long_name=info.get("longName", symbol),
            sector=info.get("sector"),
            industry=info.get("industry"),
            currency=info.get("currency", "UNKNOWN"),
            asset_type=asset_type,
            description=info.get("longBusinessSummary"),
            metrics=_extract_metrics(info),
            quarterly=quarterly,
            annual=annual,
            news=news,
            technicals=technicals,
        )

    except Exception as e:
        return MarketData(
            symbol=symbol,
            yf_symbol=yf_symbol,
            short_name=symbol,
            long_name=symbol,
            sector=None,
            industry=None,
            currency="UNKNOWN",
            asset_type="UNKNOWN",
            description=None,
            metrics=ValuationMetrics(),
            quarterly=[],
            annual=[],
            news=[],
            fetch_error=str(e),
        )


def fetch_all_market_data(symbols: list[str]) -> dict[str, MarketData]:
    """Fetch market data for all portfolio symbols concurrently."""
    results: dict[str, MarketData] = {}

    with ThreadPoolExecutor(max_workers=min(len(symbols), 6)) as executor:
        future_to_symbol = {
            executor.submit(fetch_market_data, symbol): symbol
            for symbol in symbols
        }
        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            data = future.result()
            results[symbol] = data

            if data.fetch_error:
                print(f"  {symbol}: Warning: {data.fetch_error}")
            else:
                q_count = len(data.quarterly)
                a_count = len(data.annual)
                print(f"  Fetching {symbol}..."
                      f"\n    OK — {data.short_name} "
                      f"({data.asset_type}"
                      f"{f', {q_count}Q / {a_count}Y financials' if q_count else ''})")

    return results


if __name__ == "__main__":
    symbols = ["NVDA", "AMZN", "AMD", "LX", "EXX1", "EXH1"]
    market_data = fetch_all_market_data(symbols)

    for symbol, data in market_data.items():
        if data.fetch_error:
            print(f"\n{symbol}: ERROR — {data.fetch_error}")
            continue

        print(f"\n{symbol} — {data.short_name} ({data.asset_type})")

        if data.annual:
            print("  Annual trend (oldest → newest):")
            for a in data.annual:
                rev = f"{a.revenue/1e9:.2f}B" if a.revenue else "N/A"
                yoy = (f"{a.revenue_growth_yoy*100:+.1f}%"
                       if a.revenue_growth_yoy is not None else "--")
                gm = f"{a.gross_margin*100:.1f}%" if a.gross_margin else "N/A"
                om = (f"{a.operating_margin*100:.1f}%"
                      if a.operating_margin else "N/A")
                nd = (f"{a.net_debt/1e9:+.2f}B"
                      if a.net_debt is not None else "N/A")
                print(f"    {a.year}: Rev={rev} ({yoy})  "
                      f"GrsMgn={gm}  OpMgn={om}  NetDebt={nd}")
        else:
            print("  No annual financials (ETF or unavailable)")

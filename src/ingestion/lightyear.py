"""
Lightyear PDF portfolio statement parser.
Extracts portfolio holdings from the Portfolio breakdown section.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pdfplumber

@dataclass
class Position:
    symbol: str
    name: str
    isin: str
    quantity: float
    value_original: str
    value_eur: float
    currency: str

@dataclass
class PortfolioSnapshot:
    statement_date: date
    account_reference: str
    positions: list[Position]
    total_investments_eur: float
    total_portfolio_eur: float
    cash_eur: float

def _parse_eur_value(value_str: str) -> float:
    cleaned = value_str.replace("€", "").replace(",", "").strip()
    return float(cleaned)

def _detect_currency(value_str: str) -> str:
    if not value_str:
        return "EUR"
    currency_mark = value_str[0]
    match currency_mark:
        case "$":
            return "USD"
        case "£":
            return "GBP"
        case _:
            return "EUR"

def _parse_statement_date(text: str) -> date:
    # Matches: "For the period of 20 February 2026 - 21 February 2026"
    pattern = r"For the period of .+ - (\d{1,2} \w+ \d{4})"
    match = re.search(pattern, text)
    if match:
        return datetime.strptime(match.group(1), "%d %B %Y").date()
    raise ValueError("Could not parse statement date from PDF")

def _parse_account_reference(text: str) -> str:
    """Extract account reference like LY-WUSK6R3."""
    match = re.search(r"Account reference:\s*(LY-\w+)", text)
    if match:
        return match.group(1)
    return "UNKNOWN"

def _parse_portfolio_breakdown(text: str) -> list[Position]:
    """
    Parse the Portfolio breakdown section.

    Expected line format:
    SYMBOL  Name  ISIN  Quantity  Value  Value in EUR

    Examples:
    EXX1 iShares EURO STOXX Banks 30-15 DE0006289309 46.000000000 €1,214.08 €1,214.08
    NVDA NVIDIA US67066G1040 6.619193696 $1,256.46 €1,066.51
    """
    positions = []

    # Find the portfolio breakdown section
    breakdown_start = text.find("Symbol Name ISIN Quantity Value Value in EUR")
    if breakdown_start == -1:
        raise ValueError("Could not find Portfolio breakdown section in PDF")

    breakdown_end = text.find("Investments total", breakdown_start)
    if breakdown_end == -1:
        raise ValueError("Could not find end of Portfolio breakdown section")

    breakdown_text = text[breakdown_start:breakdown_end]
    lines = breakdown_text.strip().split("\n")

    # Skip header line
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue

        # ISIN is always 12 chars alphanumeric — use it as anchor
        isin_match = re.search(r"\b([A-Z]{2}[A-Z0-9]{10})\b", line)
        if not isin_match:
            continue

        isin = isin_match.group(1)
        isin_pos = line.index(isin)

        # Everything before ISIN: "SYMBOL Name"
        before_isin = line[:isin_pos].strip()
        # Everything after ISIN: "Quantity Value ValueEUR"
        after_isin = line[isin_pos + len(isin):].strip()

        # Split symbol from name — symbol is first word, no spaces
        parts_before = before_isin.split(" ", 1)
        symbol = parts_before[0]
        name = parts_before[1] if len(parts_before) > 1 else symbol

        # Parse after_isin: "46.000000000 €1,214.08 €1,214.08"
        # Quantity is the float, then two currency values
        after_parts = after_isin.split()

        if len(after_parts) < 3:
            continue

        quantity = float(after_parts[0])
        value_original = after_parts[1]
        value_eur_str = after_parts[2]

        currency = _detect_currency(value_original)
        value_eur = _parse_eur_value(value_eur_str)

        positions.append(Position(
            symbol=symbol,
            name=name,
            isin=isin,
            quantity=quantity,
            value_original=value_original,
            value_eur=value_eur,
            currency=currency,
        ))

    return positions

def _parse_totals(text: str) -> tuple[float, float, float]:
    """Parse investments total, cash EUR, portfolio total."""
    investments_match = re.search(r"Investments total\s+(€[\d,\.]+)", text)
    portfolio_match = re.search(r"Portfolio total\s+(€[\d,\.]+)", text)
    cash_match = re.search(r"Cash - EUR\s+Euro\s+(€[\d,\.]+)", text)

    investments_total = _parse_eur_value(
        investments_match.group(1)) if investments_match else 0.0
    portfolio_total = _parse_eur_value(
        portfolio_match.group(1)) if portfolio_match else 0.0
    cash_eur = _parse_eur_value(
        cash_match.group(1)) if cash_match else 0.0

    return investments_total, portfolio_total, cash_eur

def parse_lightyear_pdf(pdf_path: str | Path) -> PortfolioSnapshot:
    """
    Parse a Lightyear statement PDF and return a PortfolioSnapshot.

    Args:
        pdf_path: Path to the Lightyear PDF statement

    Returns:
        PortfolioSnapshot with all positions and summary values
    """
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

    statement_date = _parse_statement_date(full_text)
    account_reference = _parse_account_reference(full_text)
    positions = _parse_portfolio_breakdown(full_text)
    investments_total, portfolio_total, cash_eur = _parse_totals(full_text)

    return PortfolioSnapshot(
        statement_date=statement_date,
        account_reference=account_reference,
        positions=positions,
        total_investments_eur=investments_total,
        total_portfolio_eur=portfolio_total,
        cash_eur=cash_eur,
    )

if __name__ == "__main__":
    import sys

    pdf_path = sys.argv[1] if len(sys.argv) > 1 else \
        "AccountStatement-LY-WUSK6R3-2026-02-20_2026-02-21_en.pdf"

    snapshot = parse_lightyear_pdf(pdf_path)

    print(f"Statement date:      {snapshot.statement_date}")
    print(f"Account reference:   {snapshot.account_reference}")
    print(f"Investments total:   €{snapshot.total_investments_eur:,.2f}")
    print(f"Portfolio total:     €{snapshot.total_portfolio_eur:,.2f}")
    print(f"Cash EUR:            €{snapshot.cash_eur:,.2f}")
    print(f"\nPositions ({len(snapshot.positions)}):")
    print(f"{'Symbol':<8} {'Name':<35} {'ISIN':<14} "
          f"{'Quantity':>14} {'Value':>12} {'EUR Value':>12} {'CCY'}")
    print("-" * 105)
    for p in snapshot.positions:
        print(f"{p.symbol:<8} {p.name:<35} {p.isin:<14} "
              f"{p.quantity:>14.6f} {p.value_original:>12} "
              f"€{p.value_eur:>10,.2f}  {p.currency}")
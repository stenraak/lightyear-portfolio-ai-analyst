import pytest

from src.ingestion.lightyear import (
    parse_lightyear_pdf,
    _detect_currency,
    _parse_eur_value,
)


# --- Unit tests for helper functions ---

def test_detect_currency_usd():
    assert _detect_currency("$1,256.46") == "USD"

def test_detect_currency_eur():
    assert _detect_currency("€290.88") == "EUR"

def test_detect_currency_gbp():
    assert _detect_currency("£100.00") == "GBP"

def test_parse_eur_value():
    assert _parse_eur_value("€1,214.08") == 1214.08

def test_parse_eur_value_small():
    assert _parse_eur_value("€0.01") == 0.01


# --- Integration test against real PDF ---

@pytest.fixture
def snapshot():
    return parse_lightyear_pdf(
        "data/exports/AccountStatement-LY-WUSK6R3-2026-02-20_2026-02-21_en.pdf"
    )

def test_snapshot_date(snapshot):
    from datetime import date
    assert snapshot.statement_date == date(2026, 2, 21)

def test_snapshot_account_ref(snapshot):
    assert snapshot.account_reference == "LY-WUSK6R3"

def test_snapshot_position_count(snapshot):
    assert len(snapshot.positions) == 6

def test_snapshot_total(snapshot):
    assert snapshot.total_investments_eur == 3050.32

def test_nvda_parsed_correctly(snapshot):
    nvda = next(p for p in snapshot.positions if p.symbol == "NVDA")
    assert nvda.currency == "USD"
    assert nvda.isin == "US67066G1040"
    assert round(nvda.value_eur, 2) == 1066.51

def test_etf_parsed_correctly(snapshot):
    exx1 = next(p for p in snapshot.positions if p.symbol == "EXX1")
    assert exx1.currency == "EUR"
    assert exx1.quantity == 46.0
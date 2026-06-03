from datetime import date
from decimal import Decimal

from ofin.parsers.common import (
    approx_eq,
    deterministic_doc_id,
    deterministic_tx_id,
    fingerprint_text,
    normalize_spaces,
    parse_brl,
    parse_date_full,
    parse_date_short,
    strip_accents,
)


def test_parse_brl_thousands_and_decimal():
    assert parse_brl("1.234,56") == Decimal("1234.56")


def test_parse_brl_with_currency_prefix():
    assert parse_brl("R$ 99,90") == Decimal("99.90")


def test_parse_brl_negative_prefix():
    assert parse_brl("-100,00") == Decimal("-100.00")


def test_parse_brl_negative_suffix():
    assert parse_brl("100,00-") == Decimal("-100.00")


def test_parse_brl_none_returns_none():
    assert parse_brl(None) is None


def test_parse_brl_garbage_no_comma():
    assert parse_brl("hello") is None


def test_parse_brl_empty():
    assert parse_brl("") is None
    assert parse_brl("   ") is None


def test_strip_accents_basic():
    assert strip_accents("Açaí com não") == "Acai com nao"


def test_strip_accents_no_change():
    assert strip_accents("hello") == "hello"


def test_normalize_spaces():
    assert normalize_spaces("  hello   world  ") == "hello world"


def test_parse_date_short_dd_mm():
    assert parse_date_short("15/03", 2026) == date(2026, 3, 15)


def test_parse_date_short_invalid():
    assert parse_date_short("hello", 2026) is None


def test_parse_date_full_ddmmyyyy():
    assert parse_date_full("15/03/2026") == date(2026, 3, 15)


def test_parse_date_full_yy_century_2000s():
    assert parse_date_full("15/03/26") == date(2026, 3, 15)


def test_parse_date_full_yy_century_1900s():
    assert parse_date_full("15/03/85") == date(1985, 3, 15)


def test_parse_date_full_none():
    assert parse_date_full("nope") is None


def test_approx_eq_within_tolerance():
    assert approx_eq(Decimal("100.00"), Decimal("100.005"), "0.01") is True


def test_approx_eq_outside_tolerance():
    assert approx_eq(Decimal("100.00"), Decimal("100.50"), "0.01") is False


def test_approx_eq_none_returns_false():
    assert approx_eq(None, Decimal("100"), "0.01") is False


def test_deterministic_tx_id_stable():
    a = deterministic_tx_id("doc1", date(2026, 1, 1), "RAW", Decimal("10.00"), 0)
    b = deterministic_tx_id("doc1", date(2026, 1, 1), "RAW", Decimal("10.00"), 0)
    assert a == b
    assert len(a) == 32


def test_deterministic_tx_id_changes_with_amount():
    a = deterministic_tx_id("doc1", date(2026, 1, 1), "RAW", Decimal("10.00"))
    b = deterministic_tx_id("doc1", date(2026, 1, 1), "RAW", Decimal("10.01"))
    assert a != b


def test_deterministic_doc_id_stable():
    a = deterministic_doc_id(b"hello")
    b = deterministic_doc_id(b"hello")
    assert a == b
    assert len(a) == 32


def test_fingerprint_text_stable():
    a = fingerprint_text("Line 1\nLine 2\n", lines=2)
    b = fingerprint_text("Line 1\nLine 2\n", lines=2)
    assert a == b

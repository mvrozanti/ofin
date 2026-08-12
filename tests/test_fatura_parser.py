from datetime import date
from decimal import Decimal

from ofin.parsers.fatura_itau_v1 import (
    _extract_candidates_in_row,
    _in_any_zone,
    _infer_year,
    _looks_like_payment,
    _parcelados_zones,
    _parse_summary,
)


def w(text: str, x0: float, page: int = 0, width: float | None = None) -> dict:
    if width is None:
        width = max(3.0, len(text) * 4.5)
    return {
        "text": text,
        "x0": x0,
        "x1": x0 + width,
        "top": 100.0,
        "bottom": 108.0,
        "page": page,
    }


def test_candidate_glued_minus_is_negative():
    row = [w("21/07", 149), w("ESTORNO", 176), w("-9,26", 317)]
    cands = _extract_candidates_in_row(row, 0)
    assert len(cands) == 1
    assert cands[0].value == Decimal("-9.26")


def test_candidate_standalone_minus_token_makes_value_negative():
    row = [
        w("21/07", 149),
        w("PAGAMENTO", 176),
        w("EFETUADO", 218),
        w("2030", 253),
        w("-", 314, width=3),
        w("897,13", 317.5),
    ]
    cands = _extract_candidates_in_row(row, 0)
    assert len(cands) == 1
    assert cands[0].value == Decimal("-897.13")
    assert cands[0].description == "PAGAMENTO EFETUADO 2030"


def test_candidate_distant_dash_is_not_a_sign():
    row = [w("21/07", 149), w("LOJA", 176), w("-", 200, width=3), w("50,00", 317)]
    cands = _extract_candidates_in_row(row, 0)
    assert len(cands) == 1
    assert cands[0].value == Decimal("50.00")
    assert "-" in cands[0].description


def test_candidate_positive_without_sign():
    row = [w("12/08", 149), w("DEVOLUCAO", 176), w("SALDO", 217), w("CREDOR", 240), w("897,13", 317)]
    cands = _extract_candidates_in_row(row, 0)
    assert len(cands) == 1
    assert cands[0].value == Decimal("897.13")


def test_two_column_row_yields_two_candidates():
    row = [
        w("08/08", 149),
        w("MERCADO", 176),
        w("30,00", 317),
        w("21/07", 365),
        w("IFOOD", 392),
        w("42,40", 537),
    ]
    cands = _extract_candidates_in_row(row, 0)
    assert [c.value for c in cands] == [Decimal("30.00"), Decimal("42.40")]


def test_looks_like_payment_requires_negative():
    assert _looks_like_payment("PAGAMENTO EFETUADO", Decimal("-897.13"))
    assert not _looks_like_payment("PAGAMENTO EFETUADO", Decimal("897.13"))
    assert not _looks_like_payment("MERCADO", Decimal("-10.00"))


def test_summary_payment_standalone_minus_sign_captured():
    rows = [
        [
            w("Pagamento", 365),
            w("efetuado", 397),
            w("em", 423),
            w("23/06/2025", 433),
            w("-", 524, width=3),
            w("1.445,40", 528),
        ],
    ]
    s = _parse_summary(rows)
    assert s.payment_amount == Decimal("-1445.40")
    assert s.payment_date == date(2025, 6, 23)


def test_summary_multicard_domestic_subtotal_sums_all_cards():
    rows = [
        [w("Lançamentos", 149), w("no", 193), w("cartão", 203), w("(final", 224), w("9132)", 242), w("897,13", 314)],
        [w("Lançamentos", 149), w("no", 193), w("cartão", 203), w("(final", 224), w("6079)", 242), w("1.801,22", 308)],
    ]
    s = _parse_summary(rows)
    assert s.domestic_subtotal == Decimal("2698.35")


def test_summary_international_credits():
    rows = [
        [w("Crédito", 149), w("cartão", 174), w("final", 196), w("(6079)", 212), w("em", 234), w("R$", 245), w("999,12", 314)],
    ]
    s = _parse_summary(rows)
    assert s.international_credits == Decimal("999.12")


def test_infer_year_previous_year_rollover():
    anchor = date(2026, 1, 14)
    assert _infer_year(12, anchor) == 2025
    assert _infer_year(1, anchor) == 2026
    assert _infer_year(2, anchor) == 2026


def test_zone_anchor_ignores_transaction_column():
    header_row = [
        w("22/01", 149, page=1),
        w("CONTINENTAL", 176, page=1),
        w("2,00", 321, page=1),
        w("Compras", 392, page=1),
        w("parceladas", 430, page=1),
        w("-", 470, page=1, width=3),
        w("próximas", 475, page=1),
        w("faturas", 510, page=1),
    ]
    installment_row = [w("10/02", 392, page=1), w("LOJA", 420, page=1), w("3/10", 460, page=1), w("99,00", 540, page=1)]
    next_page_row = [w("15/02", 392, page=2), w("MERCADO", 420, page=2), w("50,00", 540, page=2)]
    rows = [header_row, installment_row, next_page_row]
    zones = _parcelados_zones(rows)
    assert len(zones) == 1
    start_ri, end_ri, x_lo, x_hi = zones[0]
    assert start_ri == 0
    assert end_ri == 1
    assert x_lo <= 392 <= x_hi
    assert not (x_lo <= 149 <= x_hi)

    tx_same_row = _extract_candidates_in_row(header_row, 0)[0]
    assert not _in_any_zone(tx_same_row, zones)

    installment = _extract_candidates_in_row(installment_row, 1)[0]
    assert _in_any_zone(installment, zones)

    next_page_tx = _extract_candidates_in_row(next_page_row, 2)[0]
    assert not _in_any_zone(next_page_tx, zones)

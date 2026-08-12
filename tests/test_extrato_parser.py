from datetime import date
from decimal import Decimal

from ofin.parsers.extrato_itau_v1 import parse_extrato

HEADER = """\
extrato mensal ag 0428 cc 17236-5 abr 2026
saldo em 31/03/26   saldo em 30/04/26
R$ 100,00   R$ 150,00
"""


def _doc(movement: str) -> str:
    return HEADER + "Conta Corrente | Movimentação\n" + movement


def test_description_containing_data_is_not_dropped():
    result = parse_extrato(_doc("01/04   SISPAG DATAPREV        50,00\n"))
    assert len(result.transactions) == 1
    tx = result.transactions[0]
    assert tx.description == "SISPAG DATAPREV"
    assert tx.amount == Decimal("50.00")
    assert tx.when == date(2026, 4, 1)


def test_debit_trailing_minus():
    result = parse_extrato(_doc("02/04   PIX TRANSF FULANO02/04        1.234,56-\n"))
    assert len(result.transactions) == 1
    assert result.transactions[0].amount == Decimal("-1234.56")


def test_invalid_date_does_not_crash():
    result = parse_extrato(_doc("31/02   LOJA XYZ        10,00\n"))
    assert all(t.when is not None for t in result.transactions)


def test_sweep_lines_flagged_internal():
    result = parse_extrato(
        _doc(
            "03/04   Res Aplic Aut Mais        200,00\n"
            "        Rend Pago Aplic Aut Mais          0,73\n"
        )
    )
    by_desc = {t.description: t for t in result.transactions}
    assert by_desc["Res Aplic Aut Mais"].is_sweep
    assert by_desc["Rend Pago Aplic Aut Mais"].is_interest


def test_summary_labels_not_transactions():
    result = parse_extrato(
        _doc(
            "05/04   SALDO ANTERIOR        99,00\n"
            "05/04   COMPRA LOJA        30,00-\n"
            "        SALDO FINAL DISPONIVEL        69,00\n"
        )
    )
    assert [t.description for t in result.transactions] == ["COMPRA LOJA"]
    assert result.summary.saldo_anterior_ledger == Decimal("99.00")
    assert result.summary.saldo_final_ledger == Decimal("69.00")

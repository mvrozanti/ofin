from decimal import Decimal

from ofin.analytics import Mover


def test_mover_pct_zero_prev_positive_current_returns_inf():
    m = Mover(mega="renda", category="salario", current=Decimal("100"), previous=Decimal("0"))
    assert m.pct == float("inf")


def test_mover_pct_zero_prev_zero_current_returns_zero():
    m = Mover(mega="renda", category="bonus", current=Decimal("0"), previous=Decimal("0"))
    assert m.pct == 0.0


def test_mover_pct_normal_growth():
    m = Mover(mega="alimentacao", category="ifood", current=Decimal("150"), previous=Decimal("100"))
    assert m.pct == 0.5


def test_mover_pct_decline():
    m = Mover(mega="transporte", category="uber", current=Decimal("50"), previous=Decimal("100"))
    assert m.pct == -0.5


def test_mover_delta():
    m = Mover(mega="x", category=None, current=Decimal("200"), previous=Decimal("80"))
    assert m.delta == Decimal("120")

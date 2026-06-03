from decimal import Decimal

from ofin.analytics import BudgetProgress, Mover, _norm_key


def test_norm_key_uppercases_and_strips_special():
    assert _norm_key("on uber*moves") == "ONUBERMOVES"


def test_norm_key_truncates_to_24():
    long = "a" * 50
    assert len(_norm_key(long)) == 24


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


def test_budget_progress_status_ok():
    bp = BudgetProgress(
        budget_id=1, mega="m", category=None,
        target=Decimal("100"), spent=Decimal("50"), remaining=Decimal("50"),
        pct=0.5, currency="BRL", status="ok",
    )
    assert bp.status == "ok"
    assert bp.pct < 0.85


def test_budget_progress_status_warn():
    bp = BudgetProgress(
        budget_id=1, mega="m", category=None,
        target=Decimal("100"), spent=Decimal("90"), remaining=Decimal("10"),
        pct=0.9, currency="BRL", status="warn",
    )
    assert bp.status == "warn"
    assert 0.85 <= bp.pct < 1.0


def test_budget_progress_status_over():
    bp = BudgetProgress(
        budget_id=1, mega="m", category=None,
        target=Decimal("100"), spent=Decimal("120"), remaining=Decimal("-20"),
        pct=1.2, currency="BRL", status="over",
    )
    assert bp.status == "over"
    assert bp.pct >= 1.0

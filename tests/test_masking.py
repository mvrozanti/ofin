from decimal import Decimal

import pytest

from ofin.masking import (
    MASK_STR,
    fmt_money,
    mask_money,
    mask_series,
    mask_total,
    mask_value,
    proportions,
    scale_to_max,
)


def test_fmt_money_none_returns_dash():
    assert fmt_money(None) == "—"


def test_fmt_money_zero():
    assert fmt_money(0) == "R$ 0,00"


def test_fmt_money_pt_br_locale():
    assert fmt_money(Decimal("1234.5")) == "R$ 1.234,50"


def test_fmt_money_negative():
    assert fmt_money(Decimal("-1234.5")) == "R$ -1.234,50"


def test_fmt_money_garbage_returns_str_repr():
    assert fmt_money("notanumber") == "notanumber"


def test_mask_money_anon():
    assert mask_money(100, False) == MASK_STR


def test_mask_money_authed():
    assert mask_money(100, True) == "R$ 100,00"


def test_mask_money_none_anon_still_masked():
    assert mask_money(None, False) == MASK_STR


def test_mask_value_authed():
    assert mask_value(Decimal("42.5"), True) == 42.5


def test_mask_value_anon_is_none():
    assert mask_value(42, False) is None


def test_mask_value_garbage_authed_is_none():
    assert mask_value("nope", True) is None


def test_proportions_empty():
    assert proportions([]) == []


def test_proportions_all_zero():
    assert proportions([0, 0, 0]) == [0.0, 0.0, 0.0]


def test_proportions_sum_close_to_total():
    out = proportions([1, 2, 3, 4])
    assert sum(out) == pytest.approx(100.0, abs=0.01)


def test_proportions_mixed_signs_use_abs():
    out = proportions([-1, 2])
    assert out == pytest.approx([100 / 3, 200 / 3], abs=0.01)


def test_proportions_handles_none():
    out = proportions([None, 10])
    assert out[0] == 0.0
    assert out[1] == pytest.approx(100.0, abs=0.01)


def test_scale_to_max_empty():
    assert scale_to_max([]) == []


def test_scale_to_max_all_none():
    assert scale_to_max([None, None]) == [None, None]


def test_scale_to_max_peaks_at_100():
    out = scale_to_max([10, 20, 40])
    assert max(out) == pytest.approx(100.0)
    assert out[0] == pytest.approx(25.0)


def test_scale_to_max_zero_max_returns_zeros():
    assert scale_to_max([0, 0]) == [0.0, 0.0]


def test_mask_series_authed_passthrough_floats():
    assert mask_series([Decimal("1"), Decimal("2")], True) == [1.0, 2.0]


def test_mask_series_anon_max_mode():
    out = mask_series([5, 10], False, mode="max")
    assert max(out) == pytest.approx(100.0)


def test_mask_series_anon_sum_mode():
    out = mask_series([1, 1, 2], False, mode="sum")
    assert sum(out) == pytest.approx(100.0, abs=0.01)


def test_mask_total_authed():
    assert mask_total(Decimal("3.5"), True) == 3.5


def test_mask_total_anon():
    assert mask_total(Decimal("3.5"), False) is None

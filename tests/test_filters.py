from datetime import date, timedelta

import pytest

from ofin.filters import Filter, _parse_date, _resolve_preset, _split

from .conftest import FakeRequest


def test_split_basic():
    assert _split("a,b,c") == ["a", "b", "c"]


def test_split_handles_whitespace_and_empty():
    assert _split(" a , , b ") == ["a", "b"]


def test_split_empty_returns_empty_list():
    assert _split(None) == []
    assert _split("") == []


def test_parse_date_valid():
    assert _parse_date("2026-01-15") == date(2026, 1, 15)


def test_parse_date_garbage_returns_none():
    assert _parse_date("nope") is None
    assert _parse_date("") is None
    assert _parse_date(None) is None


def test_resolve_preset_all_returns_none_to_today():
    today = date(2026, 6, 2)
    d_from, d_to = _resolve_preset("all", today)
    assert d_from is None
    assert d_to == today


def test_resolve_preset_30d():
    today = date(2026, 6, 2)
    d_from, d_to = _resolve_preset("30d", today)
    assert d_from == today - timedelta(days=30)
    assert d_to == today


def test_resolve_preset_mtd():
    today = date(2026, 6, 17)
    d_from, d_to = _resolve_preset("mtd", today)
    assert d_from == date(2026, 6, 1)
    assert d_to == today


def test_resolve_preset_ytd():
    today = date(2026, 6, 17)
    d_from, d_to = _resolve_preset("ytd", today)
    assert d_from == date(2026, 1, 1)
    assert d_to == today


def test_resolve_preset_unknown_returns_none():
    today = date(2026, 6, 2)
    d_from, d_to = _resolve_preset("garbage", today)
    assert d_from is None
    assert d_to == today


def test_filter_from_request_defaults():
    req = FakeRequest({})
    f = Filter.from_request(req)
    assert f.preset == "90d"
    assert f.display_currency == "BRL"
    assert f.compare == "none"
    assert f.megas == []


def test_filter_from_request_preset_custom_with_dates():
    req = FakeRequest({"from": "2026-01-01", "to": "2026-03-31"})
    f = Filter.from_request(req)
    assert f.date_from == date(2026, 1, 1)
    assert f.date_to == date(2026, 3, 31)


def test_filter_from_request_multi_values():
    req = FakeRequest({"megas": "renda,pessoas", "accounts": "a1,a2"})
    f = Filter.from_request(req)
    assert f.megas == ["renda", "pessoas"]
    assert f.accounts == ["a1", "a2"]


def test_filter_from_request_rule_ids_parsed_as_ints():
    req = FakeRequest({"rule": "12"})
    f = Filter.from_request(req)
    assert f.rule_ids == [12]


def test_filter_from_request_rules_csv():
    req = FakeRequest({"rules": "1,2,bad,4"})
    f = Filter.from_request(req)
    assert f.rule_ids == [1, 2, 4]


def test_filter_from_request_include_flags():
    req = FakeRequest({"internal": "1", "sweep": "1"})
    f = Filter.from_request(req)
    assert f.include_internal is True
    assert f.include_sweep is True


def test_filter_comparison_range_yoy():
    f = Filter(
        date_from=date(2026, 3, 1), date_to=date(2026, 5, 31),
        preset="custom", compare="yoy",
    )
    p_from, p_to = f.comparison_range()
    assert p_from == date(2025, 3, 1)
    assert p_to == date(2025, 5, 31)


def test_filter_comparison_range_prev():
    f = Filter(
        date_from=date(2026, 5, 1), date_to=date(2026, 5, 31),
        preset="custom", compare="prev",
    )
    p_from, p_to = f.comparison_range()
    assert p_to == date(2026, 4, 30)
    span_days = (p_to - p_from).days
    cur_span = (f.date_to - f.date_from).days
    assert span_days == cur_span


def test_filter_comparison_range_none():
    f = Filter(date_from=date(2026, 1, 1), date_to=date(2026, 1, 31), compare="none")
    assert f.comparison_range() == (None, None)


def test_filter_to_query_string_roundtrip():
    req = FakeRequest({
        "preset": "30d",
        "megas": "renda,pessoas",
        "compare": "yoy",
        "q": "uber",
    })
    f = Filter.from_request(req)
    qs = f.to_query_string()
    req2 = FakeRequest(dict(p.split("=", 1) for p in qs.split("&")))
    f2 = Filter.from_request(req2)
    assert f2.preset == f.preset
    assert f2.megas == f.megas
    assert f2.compare == f.compare
    assert f2.search == f.search


def test_filter_label_known_presets():
    f = Filter(preset="30d")
    assert "30 dias" in f.label()
    assert Filter(preset="all").label() == "tudo"

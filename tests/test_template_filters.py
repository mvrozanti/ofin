from types import SimpleNamespace

from jinja2 import Environment

from ofin.masking import MASK_STR
from ofin.template_filters import register


def _make_env():
    env = Environment()
    register(SimpleNamespace(env=env))
    return env


class _Auth:
    def __init__(self, authed: bool):
        self.authed = authed


class _State:
    def __init__(self, authed: bool):
        self.auth = _Auth(authed)


class _Req:
    def __init__(self, authed: bool):
        self.state = _State(authed)


def test_money_filter_masks_anon():
    env = _make_env()
    t = env.from_string("{{ 100 | money }}")
    assert t.render(request=_Req(False)) == MASK_STR


def test_money_filter_authed_renders_brl():
    env = _make_env()
    t = env.from_string("{{ 100 | money }}")
    assert t.render(request=_Req(True)) == "R$ 100,00"


def test_money_filter_no_request_treats_as_anon():
    env = _make_env()
    t = env.from_string("{{ 100 | money }}")
    assert t.render() == MASK_STR


def test_authed_global_callable_true():
    env = _make_env()
    t = env.from_string("{% if authed() %}yes{% else %}no{% endif %}")
    assert t.render(request=_Req(True)) == "yes"


def test_authed_global_callable_false():
    env = _make_env()
    t = env.from_string("{% if authed() %}yes{% else %}no{% endif %}")
    assert t.render(request=_Req(False)) == "no"


def test_pct_normal():
    env = _make_env()
    t = env.from_string("{{ 25 | pct(100) }}")
    assert t.render() == "25.0%"


def test_pct_zero_total_returns_dash():
    env = _make_env()
    t = env.from_string("{{ 5 | pct(0) }}")
    assert t.render() == "—"


def test_delta_class_up():
    env = _make_env()
    t = env.from_string("{{ 10 | delta_class(5) }}")
    assert t.render() == "up"


def test_delta_class_down():
    env = _make_env()
    t = env.from_string("{{ 5 | delta_class(10) }}")
    assert t.render() == "down"


def test_delta_class_equal_returns_flat():
    env = _make_env()
    t = env.from_string("{{ 5 | delta_class(5) }}")
    assert t.render() == "flat"


def test_delta_class_handles_bad_inputs_without_raising():
    env = _make_env()
    t = env.from_string("{{ None | delta_class(5) }}")
    assert t.render() == "flat"


def test_mask_pessoa_anon_masks_pessoas():
    env = _make_env()
    t = env.from_string("{{ mask_pessoa('pessoas', 'Roberta') }}")
    assert t.render(request=_Req(False)) == MASK_STR


def test_mask_pessoa_anon_other_mega_passes():
    env = _make_env()
    t = env.from_string("{{ mask_pessoa('alimentacao', 'mercado') }}")
    assert t.render(request=_Req(False)) == "mercado"


def test_mask_pessoa_authed_passes_pessoas():
    env = _make_env()
    t = env.from_string("{{ mask_pessoa('pessoas', 'Roberta') }}")
    assert t.render(request=_Req(True)) == "Roberta"


def test_mask_text_empty_returns_dash():
    env = _make_env()
    t = env.from_string("{{ '' | mask_text }}")
    assert t.render(request=_Req(True)) == "—"


def test_mask_text_anon_masks():
    env = _make_env()
    t = env.from_string("{{ 'PAG ROBERTA' | mask_text }}")
    assert t.render(request=_Req(False)) == MASK_STR


def test_mask_text_authed_passthrough():
    env = _make_env()
    t = env.from_string("{{ 'IFOOD' | mask_text }}")
    assert t.render(request=_Req(True)) == "IFOOD"

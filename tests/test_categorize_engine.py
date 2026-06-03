from ofin.parsers.categorize_engine import CompiledRule, _compile, _matches, _norm


class _RuleStub:
    def __init__(self, **kw):
        self.id = kw.get("id", 1)
        self.pattern_type = kw["pattern_type"]
        self.pattern = kw["pattern"]
        self.account_type = kw.get("account_type")
        self.sign = kw.get("sign")
        self.mega = kw.get("mega", "outros")
        self.category = kw.get("category", "outros")
        self.is_internal = kw.get("is_internal", False)
        self.priority = kw.get("priority", 100)


def test_norm_strips_accents_and_lowercases():
    assert _norm("AÇAÍ Maçã") == "acai maca"


def test_norm_handles_none():
    assert _norm(None) == ""


def test_norm_empty_string():
    assert _norm("") == ""


def test_compile_regex_stores_pattern():
    r = _compile(_RuleStub(pattern_type="regex", pattern=r"^uber.*$"))
    assert r.compiled_re is not None


def test_compile_bad_regex_returns_none_re():
    r = _compile(_RuleStub(pattern_type="regex", pattern="[invalid"))
    assert r.compiled_re is None


def test_compile_lowercases_pattern():
    r = _compile(_RuleStub(pattern_type="contains", pattern="UBER*MOVES"))
    assert r.pattern == "uber*moves"


def _cr(**kw) -> CompiledRule:
    return _compile(_RuleStub(**kw))


def test_matches_exact_positive():
    r = _cr(pattern_type="exact", pattern="uber")
    assert _matches(r, "uber") is True


def test_matches_exact_negative():
    r = _cr(pattern_type="exact", pattern="uber")
    assert _matches(r, "uber eats") is False


def test_matches_startswith_positive():
    r = _cr(pattern_type="startswith", pattern="pix")
    assert _matches(r, "pix recebido roberta") is True


def test_matches_startswith_negative():
    r = _cr(pattern_type="startswith", pattern="pix")
    assert _matches(r, "ted pix") is False


def test_matches_contains_positive():
    r = _cr(pattern_type="contains", pattern="ifood")
    assert _matches(r, "pag deb ifood comida") is True


def test_matches_contains_negative():
    r = _cr(pattern_type="contains", pattern="ifood")
    assert _matches(r, "ubereats") is False


def test_matches_regex_positive():
    r = _cr(pattern_type="regex", pattern=r"^pag\s+\w+\s+ifood")
    assert _matches(r, "pag deb ifood comida") is True


def test_matches_regex_negative():
    r = _cr(pattern_type="regex", pattern=r"^pag\s+\w+\s+ifood")
    assert _matches(r, "ifood pag") is False


def test_matches_regex_bad_pattern_returns_false():
    r = _cr(pattern_type="regex", pattern="[bad")
    assert _matches(r, "anything") is False


def test_matches_default_fallback_is_contains():
    r = _cr(pattern_type="unknown", pattern="abc")
    assert _matches(r, "xxabcyy") is True

from decimal import Decimal

from ofin.sankey import build_sankey, pretty_cat, pretty_mega


def _income():
    return [("renda", "salario", Decimal("10000"))]


def _spend():
    return [("moradia", "aluguel", Decimal("2000")), ("alimentacao", "mercado", Decimal("1500"))]


def test_pretty_labels_are_human_and_accented():
    assert pretty_mega("alimentacao") == "alimentação"
    assert pretty_mega("pix_out") == "pix (sem categoria)"
    assert pretty_cat("renda", "salario") == "salário"
    # unknown category falls back to underscores-as-spaces
    assert pretty_cat("moradia", "taxa_condominio") == "taxa condominio"
    # generic 'outros' category shows the mega instead
    assert pretty_cat("transporte", "outros") == "transporte"


def test_build_sankey_structure_and_savings_flow():
    d = build_sankey(_income(), _spend(), authed=True)
    names = {n["name"] for n in d["nodes"]}
    assert "TOTAL" in names
    assert "inc:renda/salario" in names
    assert "exp:moradia" in names and "sub:moradia/aluguel" in names
    # net positive -> a savings node exists
    assert "SAVINGS" in names
    assert d["totals"] == {"income": 10000.0, "spend": 3500.0, "net": 6500.0}
    # every node carries a human display label
    assert all(n.get("display") for n in d["nodes"])
    # income flows into TOTAL, expenses flow out of it
    kinds = {(lk["source"], lk["target"]): lk["kind"] for lk in d["links"]}
    assert kinds[("inc:renda/salario", "TOTAL")] == "income"
    assert kinds[("TOTAL", "exp:moradia")] == "expense"
    assert kinds[("TOTAL", "SAVINGS")] == "savings"


def test_build_sankey_deficit_when_overspending():
    d = build_sankey(_income(), [("moradia", "aluguel", Decimal("15000"))], authed=True)
    names = {n["name"] for n in d["nodes"]}
    assert "DEFICIT" in names
    assert "SAVINGS" not in names
    assert d["totals"]["net"] == -5000.0


def test_build_sankey_anon_masks_totals_and_scales_links():
    d = build_sankey(_income(), _spend(), authed=False)
    assert d["totals"] == {"income": None, "spend": None, "net": None}
    assert abs(sum(lk["value"] for lk in d["links"]) - 100.0) < 0.5


def test_build_sankey_empty():
    d = build_sankey([], [], authed=True)
    assert d["nodes"] == [] and d["links"] == []

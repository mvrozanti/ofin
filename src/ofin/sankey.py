"""Shared cashflow-sankey builder.

Flow model (left → right), the layout personal-finance Sankeys converge on:

    income categories ─┐
                       ├─▶  Renda (total)  ─┬─▶ expense mega ─▶ subcategory
    (uso de reservas) ─┘                    └─▶ Sobrou (net saved)

No account-name hub nodes: bank and card spend are merged per category, because
"how much on food" is the question, not "how much on food via which card". Node
ids stay machine-readable (inc:/exp:/sub:) for links + drill; the human label and
value live on each node so the chart reads in Portuguese with amounts inline.
"""
from __future__ import annotations

from decimal import Decimal

from .masking import proportions

MEGA_PT = {
    "renda": "renda",
    "moradia": "moradia",
    "alimentacao": "alimentação",
    "transporte": "transporte",
    "saude": "saúde",
    "assinatura": "assinaturas",
    "compra_loja": "compras (loja)",
    "compra_online": "compras (online)",
    "utilidades": "utilidades",
    "pessoas": "pessoas",
    "doacao": "doações",
    "transferencia": "transferências",
    "pix_out": "pix (sem categoria)",
    "saque": "saque",
    "financeiro": "financeiro",
    "internal": "interno",
    "outros": "outros",
}

CAT_PT = {
    "salario": "salário",
    "rendimento_cdb": "rendimento CDB",
    "credito_cartao": "crédito cartão",
    "devolucao_pix": "devolução pix",
    "devolucao": "devolução",
    "ressarcimento": "ressarcimento",
    "alimentacao": "alimentação",
    "saude": "saúde",
    "farmacia": "farmácia",
    "condominio": "condomínio",
    "transferencia_propria": "transf. própria",
    "gateway_qr": "pagamento (QR)",
    "eletronicos": "eletrônicos",
    "restaurante": "restaurante",
    "delivery_ifood": "delivery",
    "fundo_investimento": "fundo invest.",
    "pagamento_cartao": "pagamento cartão",
}


def pretty_mega(m: str | None) -> str:
    m = m or "outros"
    return MEGA_PT.get(m, m.replace("_", " "))


def pretty_cat(mega: str | None, cat: str | None) -> str:
    if not cat or cat in ("outros", "uncategorized"):
        return pretty_mega(mega)
    return CAT_PT.get(cat, cat.replace("_", " "))


_COLORS = {
    "income": "#4ade80",
    "total": "#94a3b8",
    "mega": "#fb923c",
    "sub": "#fdba74",
    "savings": "#38bdf8",
    "deficit": "#f87171",
}


def build_sankey(
    income_data: list[tuple[str, str, Decimal]],
    spend_data: list[tuple[str, str, Decimal]],
    *,
    authed: bool,
) -> dict:
    """income_data / spend_data: (mega, category, positive_value) rows.

    Returns {nodes, links, totals}. Nodes carry `display` + `itemStyle.color`;
    links carry `mega`/`category`/`kind` for drill-down. Anonymous callers get
    link values rescaled to proportions (summing ~100) and totals=None.
    """
    income_data = sorted(income_data, key=lambda r: r[2], reverse=True)
    spend_data = sorted(spend_data, key=lambda r: r[2], reverse=True)

    if not income_data and not spend_data:
        return {"nodes": [], "links": [], "totals": {"income": 0, "spend": 0, "net": 0} if authed else {"income": None, "spend": None, "net": None}}

    total_income = sum((v for _, _, v in income_data), Decimal(0))
    total_spend = sum((v for _, _, v in spend_data), Decimal(0))
    net = total_income - total_spend

    nodes: list[dict] = []
    seen: set[str] = set()

    def add(name: str, display: str, kind: str, depth: int) -> None:
        if name in seen:
            return
        seen.add(name)
        nodes.append({
            "name": name,
            "display": display,
            "depth": depth,
            "itemStyle": {"color": _COLORS[kind]},
        })

    links: list[dict] = []

    # income leaves → Renda (total)
    for mega, cat, v in income_data:
        if v <= 0:
            continue
        nid = f"inc:{mega}/{cat}"
        add(nid, pretty_cat(mega, cat), "income", 0)
        links.append({"source": nid, "target": "TOTAL", "value": float(v),
                      "mega": mega, "category": cat, "kind": "income"})

    add("TOTAL", "renda", "total", 1)

    # deficit inflow (spent more than earned) balances the diagram
    if net < 0:
        add("DEFICIT", "uso de reservas", "deficit", 0)
        links.append({"source": "DEFICIT", "target": "TOTAL", "value": float(-net), "kind": "deficit"})

    # Renda → expense mega → subcategory
    mega_totals: dict[str, Decimal] = {}
    for mega, cat, v in spend_data:
        mega_totals[mega] = mega_totals.get(mega, Decimal(0)) + v
    for mega, mtotal in sorted(mega_totals.items(), key=lambda kv: kv[1], reverse=True):
        if mtotal <= 0:
            continue
        add(f"exp:{mega}", pretty_mega(mega), "mega", 2)
        links.append({"source": "TOTAL", "target": f"exp:{mega}", "value": float(mtotal),
                      "mega": mega, "kind": "expense"})
    for mega, cat, v in spend_data:
        if v <= 0:
            continue
        nid = f"sub:{mega}/{cat}"
        add(nid, pretty_cat(mega, cat), "sub", 3)
        links.append({"source": f"exp:{mega}", "target": nid, "value": float(v),
                      "mega": mega, "category": cat, "kind": "expense"})

    # Renda → Sobrou (what stayed)
    if net > 0:
        add("SAVINGS", "sobrou", "savings", 2)
        links.append({"source": "TOTAL", "target": "SAVINGS", "value": float(net), "kind": "savings"})

    if authed:
        totals = {"income": float(total_income), "spend": float(total_spend), "net": float(net)}
    else:
        scaled = proportions([lk["value"] for lk in links], total=100.0)
        for lk, v in zip(links, scaled):
            lk["value"] = v
        totals = {"income": None, "spend": None, "net": None}

    return {"nodes": nodes, "links": links, "totals": totals}

from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CategoryRule, SeedState


# (pattern_type, pattern, account_type, sign, mega, category, is_internal, priority)
DEFAULT_RULES: list[tuple] = [
    # Internal — own money movement
    ("contains", "res aplic aut mais", "BANK", None, "internal", "sweep_resgate", True, 10),
    ("contains", "apl aplic aut mais", "BANK", None, "internal", "sweep_aplicacao", True, 10),
    ("contains", "rend pago aplic aut mais", "BANK", "credit", "renda", "rendimento_cdb", False, 10),
    ("contains", "saldo aplic aut mais", "BANK", None, "internal", "saldo_cdb", True, 10),
    ("startswith", "est pix", "BANK", "credit", "internal", "estorno", True, 15),
    ("startswith", "est on", "BANK", "credit", "internal", "estorno", True, 15),
    ("startswith", "estorno", "BANK", None, "internal", "estorno", True, 15),
    ("contains", "fatura itau", "BANK", "debit", "internal", "pagamento_cartao", True, 15),
    ("contains", "faturaitau", "BANK", "debit", "internal", "pagamento_cartao", True, 15),
    ("contains", "fatura paga itau", "BANK", "debit", "internal", "pagamento_cartao", True, 15),
    ("startswith", "itau visa", "BANK", "debit", "internal", "pagamento_cartao", True, 15),
    ("startswith", "tbi", "BANK", None, "internal", "transferencia_propria", True, 15),
    ("startswith", "int itau click", "BANK", None, "internal", "transferencia_propria", True, 15),
    # "PIX TRANSF MARCELO": SAÍDA = transferência própria (BTG); ENTRADA = renda.
    ("contains", "pix transf marcelo", "BANK", "debit", "internal", "transferencia_propria", True, 12),
    ("contains", "pix transf marcelo", "BANK", "credit", "renda", "salario", False, 12),

    # User-confirmed: rent
    ("startswith", "pag boleto lucia maria", "BANK", "debit", "moradia", "aluguel", False, 20),

    # User-confirmed: investment (asset move, not consumption)
    ("startswith", "int ted d ", "BANK", "debit", "internal", "investimento", True, 20),

    # User-confirmed: salary TED (employer)
    ("startswith", "ted 033.4635.marcelo", "BANK", "credit", "renda", "salario", False, 50),

    # User-confirmed: crypto / financial services (asset moves; stock tracked via snapshots)
    ("contains", "bifinity", "BANK", "debit", "internal", "bifinity", True, 25),
    ("contains", "gowd instit", "BANK", "debit", "internal", "gowd", True, 25),
    ("contains", "latam gatew", "BANK", "debit", "internal", "latam", True, 25),
    ("contains", "latam tecno", "BANK", "debit", "internal", "latam", True, 25),
    ("contains", "pagali", "BANK", "debit", "internal", "pagali", True, 25),
    ("contains", "aibr instit", "BANK", "debit", "internal", "aibr", True, 25),
    ("contains", "fundo g d c", "BANK", None, "internal", "fundo_investimento", True, 25),

    # User-confirmed: electronics shop
    ("contains", "terabyte", "BANK", "debit", "compra_online", "eletronicos", False, 30),
    ("contains", "terabytesh", "CREDIT", "debit", "compra_online", "eletronicos", False, 30),

    # Online shopping
    ("contains", "magalupay", "BANK", "debit", "pix_out", "gateway_qr", False, 30),
    ("contains", "magazin", None, "debit", "compra_online", "magalu", False, 30),
    ("contains", "alipay", "BANK", "debit", "compra_online", "alipay", False, 30),
    ("contains", "alipay", "CREDIT", "debit", "compra_online", "alipay", False, 30),
    ("contains", "aliexpress", None, "debit", "compra_online", "aliexpress", False, 30),
    ("contains", "amazon mktpl", None, "debit", "compra_online", "amazon", False, 30),
    ("contains", "amzn.com", None, "debit", "compra_online", "amazon", False, 30),
    ("contains", "mercado livre", None, "debit", "compra_online", "mercado_livre", False, 30),
    ("contains", "shopee", None, "debit", "compra_online", "shopee", False, 30),
    ("contains", "pix marketp", "BANK", "debit", "compra_online", "marketp", False, 30),
    ("contains", "shein", None, "debit", "compra_online", "shein", False, 30),

    # Pharmacy
    ("contains", "raia drogas", "BANK", "debit", "saude", "farmacia", False, 35),
    ("contains", "rshop-raia", "BANK", "debit", "saude", "farmacia", False, 35),
    ("contains", "rshop- raia", "BANK", "debit", "saude", "farmacia", False, 35),
    ("contains", "drogaria", None, "debit", "saude", "farmacia", False, 35),
    ("contains", "pague menos", None, "debit", "saude", "farmacia", False, 35),
    ("contains", "drogasi", None, "debit", "saude", "farmacia", False, 35),
    ("contains", "drogasil", None, "debit", "saude", "farmacia", False, 35),

    # People (PIX TRANSF)
    ("contains", "roberta", "BANK", None, "pessoas", "Roberta", False, 40),
    ("contains", "caique", "BANK", None, "pessoas", "Caique", False, 40),
    ("contains", "lucas j", "BANK", None, "pessoas", "Lucas", False, 40),
    ("contains", "leds in", "BANK", None, "pessoas", "Leds", False, 40),
    ("contains", "edgard scha", "BANK", None, "pessoas", "Edgard", False, 40),
    ("contains", "leticia fig", "BANK", None, "pessoas", "Leticia", False, 40),
    ("contains", "marco tulio", "BANK", "debit", "pessoas", "Marco_Tulio", False, 40),
    ("contains", "edisail", "BANK", None, "pessoas", "Edisail", False, 40),

    # Transport
    ("contains", "uber", None, None, "transporte", "uber", False, 45),
    ("contains", "99tax", None, "debit", "transporte", "99taxi", False, 45),
    ("contains", "99app", None, "debit", "transporte", "99taxi", False, 45),
    ("contains", "auto posto", "BANK", "debit", "transporte", "gasolina", False, 45),
    ("contains", "posto ipiranga", None, "debit", "transporte", "gasolina", False, 45),
    ("contains", "posto sho", None, "debit", "transporte", "gasolina", False, 45),
    ("contains", "metro", None, "debit", "transporte", "metro", False, 45),
    ("contains", "estacion", None, "debit", "transporte", "estacionamento", False, 45),

    # Subscriptions (CREDIT card)
    # IA — tudo junto sob assinatura/ia, sign-agnostic (cartão lança como "credit").
    ("contains", "chatgp", None, None, "assinatura", "ia", False, 48),
    ("contains", "openai", None, None, "assinatura", "ia", False, 48),
    ("contains", "anthrop", None, None, "assinatura", "ia", False, 48),
    ("contains", "claude.ai", None, None, "assinatura", "ia", False, 48),
    ("contains", "cursor", None, None, "assinatura", "ia", False, 48),
    ("contains", "perplexity", None, None, "assinatura", "ia", False, 48),
    # lojas de eletrônicos + farmácia + barbearia (chegam como PIX QRS, sem sinal fixo)
    ("contains", "kabum", None, None, "compra_online", "eletronicos", False, 40),
    ("contains", "fast shop", None, None, "compra_online", "eletronicos", False, 40),
    ("contains", "pharma", None, None, "saude", "farmacia", False, 40),
    ("contains", "black zone", None, None, "compra_loja", "barbearia", False, 40),
    ("contains", "posto", None, None, "transporte", "gasolina", False, 40),
    # transferência pra minha própria conta (BTG)
    ("contains", "marcelo vir", None, None, "internal", "transferencia_propria", True, 12),
    ("contains", "youtub", None, "debit", "assinatura", "youtube", False, 50),
    ("contains", "namecheap", "CREDIT", "debit", "assinatura", "namecheap", False, 50),
    ("contains", "name-cheap", "CREDIT", "debit", "assinatura", "namecheap", False, 50),
    ("contains", "tinder", None, "debit", "assinatura", "tinder", False, 50),
    ("contains", "duolingo", None, "debit", "assinatura", "duolingo", False, 50),
    ("contains", "spotif", None, "debit", "assinatura", "spotify", False, 50),
    ("contains", "netflix", None, "debit", "assinatura", "netflix", False, 50),
    ("contains", "ifd*ifood club", "CREDIT", "debit", "assinatura", "ifood_club", False, 50),
    ("contains", "ifood club", None, "debit", "assinatura", "ifood_club", False, 50),
    ("contains", "github", None, "debit", "assinatura", "github", False, 50),
    ("contains", "cloudflare", None, "debit", "assinatura", "cloudflare", False, 50),
    ("contains", "vercel", None, "debit", "assinatura", "vercel", False, 50),
    ("contains", "digitalocean", None, "debit", "assinatura", "digitalocean", False, 50),

    # Donation
    ("contains", "gofundme", None, "debit", "doacao", "gofundme", False, 55),
    ("contains", "vakinha", None, "debit", "doacao", "vakinha", False, 55),

    # Utilities
    ("contains", "comgas", "BANK", "debit", "utilidades", "gas", False, 60),
    ("contains", "enel", None, "debit", "utilidades", "luz", False, 60),
    ("contains", "eletropaulo", None, "debit", "utilidades", "luz", False, 60),
    ("contains", "sabesp", None, "debit", "utilidades", "agua", False, 60),
    ("regex", "\\bvivo\\b", None, "debit", "utilidades", "telefone", False, 60),
    ("regex", "\\bclaro\\b", None, "debit", "utilidades", "telefone", False, 60),
    ("regex", "\\btim\\b", None, "debit", "utilidades", "telefone", False, 60),
    ("contains", "da net servicos", "BANK", "debit", "utilidades", "internet", False, 60),
    ("contains", "net servicos", None, "debit", "utilidades", "internet", False, 60),
    ("contains", "mobilepag tit banco", "BANK", "debit", "utilidades", "boleto_app", False, 60),
    ("contains", "pag tit banco", "BANK", "debit", "utilidades", "boleto_app", False, 60),
    ("contains", "pag boleto", "BANK", "debit", "moradia", "boleto", False, 65),

    # Food — grocery
    ("contains", "pao de acuc", "BANK", "debit", "alimentacao", "mercado", False, 70),
    ("contains", "carrefour", None, "debit", "alimentacao", "mercado", False, 70),
    ("contains", "daki", None, "debit", "alimentacao", "mercado", False, 70),
    ("contains", "sacolao", "BANK", "debit", "alimentacao", "mercado", False, 70),
    ("contains", "oxxo", "BANK", "debit", "alimentacao", "mercado", False, 70),
    ("contains", "vr higienop", "BANK", "debit", "alimentacao", "mercado", False, 70),
    ("contains", "food to sav", "BANK", "debit", "alimentacao", "mercado", False, 70),
    ("contains", "shopper", None, "debit", "alimentacao", "mercado", False, 70),
    ("contains", "rappi", None, "debit", "alimentacao", "delivery", False, 70),

    # Food — delivery / restaurant
    ("contains", "ifood club", None, "debit", "assinatura", "ifood_club", False, 50),
    ("regex", "(^|[^a-z])(ifd|ifood)", None, None, "alimentacao", "delivery_ifood", False, 55),
    ("contains", "rshop-pag restaur", "BANK", "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "rscss-pag restaur", "BANK", "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "rsccs-pag", "BANK", "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "lanchon", None, "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "boulevard", "BANK", "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "banca", None, None, "compra_loja", "cigarro", False, 42),
    ("contains", "dejailton", None, None, "alimentacao", "restaurante", False, 40),
    ("contains", "cantinho do", None, None, "alimentacao", "restaurante", False, 40),
    ("contains", "victor noth", None, None, "alimentacao", "restaurante", False, 40),
    ("contains", "highlander", None, None, "outros", "outros", False, 40),
    ("contains", "pizzar", None, "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "burger", None, "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "burguer", None, "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "padaria", None, "debit", "alimentacao", "padaria", False, 80),
    ("contains", "casting bar", None, "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "bar e lan", None, "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "boteco", None, "debit", "alimentacao", "restaurante", False, 80),

    # Shopping mall / generic
    ("contains", "rshop-shopping", "BANK", "debit", "compra_loja", "shopping", False, 85),
    ("contains", "shopping co", "BANK", "debit", "compra_loja", "shopping", False, 85),

    # Income
    ("contains", "remunera", "BANK", "credit", "renda", "salario", False, 90),
    ("contains", "pagto salario", "BANK", "credit", "renda", "salario", False, 90),
    ("contains", "credito cartao", "BANK", "credit", "renda", "credito_cartao", False, 90),
    ("contains", "ressarcimento", "BANK", "credit", "renda", "ressarcimento", False, 90),
    ("startswith", "dev pix", "BANK", "credit", "renda", "devolucao_pix", False, 90),
    ("startswith", "dev ", "BANK", "credit", "renda", "devolucao", False, 95),

    # Generic PIX/TED fallbacks
    ("startswith", "pix transf", "BANK", "credit", "renda", "pix_recebido", False, 100),
    ("startswith", "pix qrs", "BANK", "debit", "pix_out", "pix_pagamento", False, 100),
    ("startswith", "pix transf", "BANK", "debit", "pix_out", "pix_pessoa", False, 100),
    ("startswith", "sispag", "BANK", "credit", "renda", "sispag", False, 100),
    ("contains", "saque", "BANK", "debit", "saque", "saque", False, 100),
    ("startswith", "ted", "BANK", None, "transferencia", "ted", False, 100),
    ("startswith", "doc", "BANK", None, "transferencia", "doc", False, 100),

    # Fatura fallbacks
    ("contains", "pagamento deb auto", "CREDIT", "credit", "internal", "pagamento_fatura", True, 10),
    ("contains", "google", "CREDIT", "debit", "assinatura", "google", False, 105),
    ("contains", "dl*", "CREDIT", "debit", "compra_online", "dlocal", False, 110),
]


SEED_VERSION = 8

SEED_MIGRATIONS: dict[int, list[tuple]] = {
    2: [
        ("add", ("contains", "pix transf marcelo", "BANK", None, "internal", "transferencia_propria", True, 12)),
        ("update", {"pattern": "magalupay"}, {"mega": "pix_out", "category": "gateway_qr"}),
        ("update", {"category": "estorno_pix"}, {"category": "estorno"}),
        ("update", {"category": "estorno_compra"}, {"category": "estorno"}),
        ("update", {"category": "assinatura_tech"}, {"category": "assinatura"}),
    ],
    3: [
        ("update", {"mega": "financeiro"}, {"mega": "internal", "is_internal": True}),
    ],
    4: [
        ("update", {"pattern": "vivo "}, {"pattern_type": "regex", "pattern": "\\bvivo\\b"}),
        ("update", {"pattern": "claro "}, {"pattern_type": "regex", "pattern": "\\bclaro\\b"}),
        ("update", {"pattern": "tim "}, {"pattern_type": "regex", "pattern": "\\btim\\b"}),
    ],
    5: [
        # se tem ifood/ifd no nome, é ifood — uma regra só no lugar dos fragmentos.
        # (iFood Club continua assinatura via a regra de prioridade 50, que ganha.)
        ("delete", {"pattern": "ifd*"}),
        ("delete", {"pattern": "ifood *ifd"}),
        ("delete", {"pattern": "ifood *ifood"}),
        ("delete", {"pattern": "on ifd"}),
        ("delete", {"pattern": "pay -ifood"}),
        ("add", ("regex", "(^|[^a-z])(ifd|ifood)", None, "debit", "alimentacao", "delivery_ifood", False, 55)),
        # uber é uber (transporte) — pega PIX/QR e variações que os fragmentos perdiam.
        ("delete", {"pattern": "uberrides"}),
        ("delete", {"pattern": "uber *trip"}),
        ("delete", {"pattern": "on uber"}),
        ("add", ("contains", "uber", None, "debit", "transporte", "uber", False, 45)),
        # saque é saque — amplia de startswith p/ contains (pega "saque 24h", "atm saque"…).
        ("update", {"pattern": "saque"}, {"pattern_type": "contains"}),
    ],
    6: [
        # Merchant rules must be sign-agnostic: on a credit card, spend is a
        # POSITIVE amount so its sign is "credit", not "debit" — a sign="debit"
        # rule silently skips every card charge. This is why card iFood/uber
        # still fell through to the hardcoded "restaurante"/"transporte".
        ("update", {"pattern": "(^|[^a-z])(ifd|ifood)"}, {"sign": None}),
        ("update", {"pattern": "uber"}, {"sign": None}),
    ],
    7: [
        # IA: unifica claude/anthropic/chatgpt/cursor sob assinatura/ia e torna
        # sign-agnostic (os antigos com sign=debit+CREDIT nunca disparavam no cartão).
        ("delete", {"pattern": "claude.ai"}),
        ("delete", {"pattern": "anthrop"}),
        ("delete", {"pattern": "cursor,"}),
        ("delete", {"pattern": "cursor usage"}),
        ("delete", {"pattern": "chatgpt"}),
        ("delete", {"pattern": "chatgp"}),
        ("add", ("contains", "chatgp", None, None, "assinatura", "ia", False, 48)),
        ("add", ("contains", "openai", None, None, "assinatura", "ia", False, 48)),
        ("add", ("contains", "anthrop", None, None, "assinatura", "ia", False, 48)),
        ("add", ("contains", "claude.ai", None, None, "assinatura", "ia", False, 48)),
        ("add", ("contains", "cursor", None, None, "assinatura", "ia", False, 48)),
        ("add", ("contains", "perplexity", None, None, "assinatura", "ia", False, 48)),
        # merchants confirmados pelo usuário
        ("add", ("contains", "kabum", None, None, "compra_online", "eletronicos", False, 40)),
        ("add", ("contains", "fast shop", None, None, "compra_online", "eletronicos", False, 40)),
        ("add", ("contains", "pharma", None, None, "saude", "farmacia", False, 40)),
        ("add", ("contains", "black zone", None, None, "compra_loja", "barbearia", False, 40)),
        ("add", ("contains", "posto", None, None, "transporte", "gasolina", False, 40)),
        ("add", ("contains", "marcelo vir", None, None, "internal", "transferencia_propria", True, 12)),
        # banca (jornaleiro) = cigarro — reclassifica os antigos + genérico
        ("update", {"pattern": "banca macke"}, {"mega": "compra_loja", "category": "cigarro"}),
        ("update", {"pattern": "bancadobent"}, {"mega": "compra_loja", "category": "cigarro"}),
        ("update", {"pattern": "banca conso"}, {"mega": "compra_loja", "category": "cigarro"}),
        ("add", ("contains", "banca", None, None, "compra_loja", "cigarro", False, 42)),
        # comida
        ("add", ("contains", "dejailton", None, None, "alimentacao", "restaurante", False, 40)),
        ("add", ("contains", "cantinho do", None, None, "alimentacao", "restaurante", False, 40)),
        ("add", ("contains", "victor noth", None, None, "alimentacao", "restaurante", False, 40)),
        # highlander: não é comida e o usuário não sabe o que é -> volta pro balde de triagem
        ("add", ("contains", "highlander", None, None, "outros", "outros", False, 40)),
    ],
    8: [
        # "PIX TRANSF MARCELO" era internal nas DUAS direções (regra sign=None),
        # comendo ~R$124k de renda que ENTRA. Separa: saída interna, entrada renda.
        ("update", {"pattern": "pix transf marcelo"}, {"sign": "debit"}),
        ("add", ("contains", "pix transf marcelo", "BANK", "credit", "renda", "salario", False, 12)),
    ],
}


def _rule_from_tuple(t: tuple) -> CategoryRule:
    pat_type, pat, acct, sign, mega, cat, internal, priority = t
    return CategoryRule(
        pattern_type=pat_type,
        pattern=pat,
        account_type=acct,
        sign=sign,
        mega=mega,
        category=cat,
        is_internal=internal,
        priority=priority,
        enabled=True,
    )


async def seed_default_rules(s: AsyncSession) -> int:
    n = (await s.execute(select(func.count()).select_from(CategoryRule))).scalar_one()
    if n > 0:
        return 0
    inserted = 0
    for t in DEFAULT_RULES:
        s.add(_rule_from_tuple(t))
        inserted += 1
    if await s.get(SeedState, 1) is None:
        s.add(SeedState(id=1, version=SEED_VERSION))
    await s.commit()
    return inserted


async def migrate_seed_rules(s: AsyncSession) -> bool:
    state = await s.get(SeedState, 1)
    if state is None:
        state = SeedState(id=1, version=1)
        s.add(state)
    if state.version >= SEED_VERSION:
        await s.commit()
        return False
    changed = False
    for version in range(state.version + 1, SEED_VERSION + 1):
        for op in SEED_MIGRATIONS.get(version, []):
            kind = op[0]
            if kind == "add":
                t = op[1]
                exists_q = select(func.count()).select_from(CategoryRule).where(
                    CategoryRule.pattern == t[1],
                    CategoryRule.mega == t[4],
                    CategoryRule.category == t[5],
                )
                if (await s.execute(exists_q)).scalar_one() == 0:
                    s.add(_rule_from_tuple(t))
                    changed = True
            elif kind == "update":
                match, sets = op[1], op[2]
                q = select(CategoryRule)
                for k, v in match.items():
                    q = q.where(getattr(CategoryRule, k) == v)
                for rule in (await s.execute(q)).scalars().all():
                    for k, v in sets.items():
                        setattr(rule, k, v)
                    changed = True
            elif kind == "delete":
                match = op[1]
                q = select(CategoryRule)
                for k, v in match.items():
                    q = q.where(getattr(CategoryRule, k) == v)
                for rule in (await s.execute(q)).scalars().all():
                    await s.delete(rule)
                    changed = True
        state.version = version
    await s.commit()
    return changed

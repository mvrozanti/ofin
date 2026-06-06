from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CategoryRule


# (pattern_type, pattern, account_type, sign, mega, category, is_internal, priority)
DEFAULT_RULES: list[tuple] = [
    # Internal — own money movement
    ("contains", "res aplic aut mais", "BANK", None, "internal", "sweep_resgate", True, 10),
    ("contains", "apl aplic aut mais", "BANK", None, "internal", "sweep_aplicacao", True, 10),
    ("contains", "rend pago aplic aut mais", "BANK", "credit", "renda", "rendimento_cdb", False, 10),
    ("contains", "saldo aplic aut mais", "BANK", None, "internal", "saldo_cdb", True, 10),
    ("startswith", "est pix", "BANK", "credit", "internal", "estorno_pix", True, 15),
    ("startswith", "est on", "BANK", "credit", "internal", "estorno_compra", True, 15),
    ("startswith", "estorno", "BANK", None, "internal", "estorno", True, 15),
    ("contains", "fatura itau", "BANK", "debit", "internal", "pagamento_cartao", True, 15),
    ("contains", "faturaitau", "BANK", "debit", "internal", "pagamento_cartao", True, 15),
    ("contains", "fatura paga itau", "BANK", "debit", "internal", "pagamento_cartao", True, 15),
    ("startswith", "itau visa", "BANK", "debit", "internal", "pagamento_cartao", True, 15),
    ("startswith", "tbi", "BANK", None, "internal", "transferencia_propria", True, 15),
    ("startswith", "int itau click", "BANK", None, "internal", "transferencia_propria", True, 15),

    # User-confirmed: rent
    ("startswith", "pag boleto lucia maria", "BANK", "debit", "moradia", "aluguel", False, 20),

    # User-confirmed: investment
    ("startswith", "int ted d ", "BANK", "debit", "financeiro", "investimento", False, 20),

    # User-confirmed: salary TED (employer)
    ("startswith", "ted 033.4635.marcelo", "BANK", "credit", "renda", "salario", False, 50),

    # User-confirmed: crypto / financial services
    ("contains", "bifinity", "BANK", "debit", "financeiro", "bifinity", False, 25),
    ("contains", "gowd instit", "BANK", "debit", "financeiro", "gowd", False, 25),
    ("contains", "latam gatew", "BANK", "debit", "financeiro", "latam", False, 25),
    ("contains", "latam tecno", "BANK", "debit", "financeiro", "latam", False, 25),
    ("contains", "pagali", "BANK", "debit", "financeiro", "pagali", False, 25),
    ("contains", "aibr instit", "BANK", "debit", "financeiro", "aibr", False, 25),
    ("contains", "fundo g d c", "BANK", None, "financeiro", "fundo_investimento", False, 25),

    # User-confirmed: electronics shop
    ("contains", "terabyte", "BANK", "debit", "compra_online", "eletronicos", False, 30),
    ("contains", "terabytesh", "CREDIT", "debit", "compra_online", "eletronicos", False, 30),

    # Online shopping
    ("contains", "magalupay", "BANK", "debit", "compra_online", "magalu", False, 30),
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
    ("contains", "uberrides", None, "debit", "transporte", "uber", False, 45),
    ("contains", "uber *trip", "BANK", "debit", "transporte", "uber", False, 45),
    ("contains", "on uber", "BANK", "debit", "transporte", "uber", False, 45),
    ("contains", "99tax", None, "debit", "transporte", "99taxi", False, 45),
    ("contains", "99app", None, "debit", "transporte", "99taxi", False, 45),
    ("contains", "auto posto", "BANK", "debit", "transporte", "gasolina", False, 45),
    ("contains", "posto ipiranga", None, "debit", "transporte", "gasolina", False, 45),
    ("contains", "posto sho", None, "debit", "transporte", "gasolina", False, 45),
    ("contains", "metro", None, "debit", "transporte", "metro", False, 45),
    ("contains", "estacion", None, "debit", "transporte", "estacionamento", False, 45),

    # Subscriptions (CREDIT card)
    ("contains", "claude.ai", "CREDIT", "debit", "assinatura", "claude_ai", False, 50),
    ("contains", "anthrop", "CREDIT", "debit", "assinatura", "anthropic", False, 50),
    ("contains", "cursor,", "CREDIT", "debit", "assinatura", "cursor", False, 50),
    ("contains", "cursor usage", "CREDIT", "debit", "assinatura", "cursor", False, 50),
    ("contains", "chatgpt", "CREDIT", "debit", "assinatura", "chatgpt", False, 50),
    ("contains", "chatgp", "CREDIT", "debit", "assinatura", "chatgpt", False, 50),
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
    ("contains", "vivo ", None, "debit", "utilidades", "telefone", False, 60),
    ("contains", "claro ", None, "debit", "utilidades", "telefone", False, 60),
    ("contains", "tim ", None, "debit", "utilidades", "telefone", False, 60),
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
    ("contains", "ifd*", None, "debit", "alimentacao", "delivery_ifood", False, 75),
    ("contains", "ifood *ifd", None, "debit", "alimentacao", "delivery_ifood", False, 75),
    ("contains", "ifood *ifood", None, "debit", "alimentacao", "delivery_ifood", False, 75),
    ("contains", "ifood club", None, "debit", "assinatura", "ifood_club", False, 50),
    ("contains", "on ifd", "BANK", "debit", "alimentacao", "delivery_ifood", False, 75),
    ("contains", "pay -ifood", "BANK", "debit", "alimentacao", "delivery_ifood", False, 75),
    ("contains", "rshop-pag restaur", "BANK", "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "rscss-pag restaur", "BANK", "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "rsccs-pag", "BANK", "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "lanchon", None, "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "boulevard", "BANK", "debit", "alimentacao", "restaurante", False, 80),
    ("contains", "banca macke", "BANK", "debit", "alimentacao", "banca", False, 80),
    ("contains", "bancadobent", "BANK", "debit", "alimentacao", "banca", False, 80),
    ("contains", "banca conso", "BANK", "debit", "alimentacao", "banca", False, 80),
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
    ("startswith", "saque", "BANK", "debit", "saque", "saque", False, 100),
    ("startswith", "ted", "BANK", None, "transferencia", "ted", False, 100),
    ("startswith", "doc", "BANK", None, "transferencia", "doc", False, 100),

    # Fatura fallbacks
    ("contains", "pagamento deb auto", "CREDIT", "credit", "internal", "pagamento_fatura", True, 10),
    ("contains", "google", "CREDIT", "debit", "assinatura", "google", False, 105),
    ("contains", "dl*", "CREDIT", "debit", "compra_online", "dlocal", False, 110),
]


async def seed_default_rules(s: AsyncSession) -> int:
    n = (await s.execute(select(func.count()).select_from(CategoryRule))).scalar_one()
    if n > 0:
        return 0
    inserted = 0
    for pat_type, pat, acct, sign, mega, cat, internal, priority in DEFAULT_RULES:
        rule = CategoryRule(
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
        s.add(rule)
        inserted += 1
    await s.commit()
    return inserted

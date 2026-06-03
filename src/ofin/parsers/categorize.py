from __future__ import annotations

import re

from .common import strip_accents


def _norm(s: str | None) -> str:
    return strip_accents(s or "").strip().lower()


SELF_NAME_TOKENS = ("marcelo", "vironda", "rozanti")


def is_self(desc_norm: str) -> bool:
    return any(tok in desc_norm for tok in SELF_NAME_TOKENS)


SWEEP_CREDIT_DESC = {"res aplic aut mais", "resgate aplic aut mais"}
SWEEP_DEBIT_DESC = {"apl aplic aut mais", "aplicacao aplic aut mais"}
INTEREST_DESC = {"rend pago aplic aut mais"}

PHARMACIES = (
    "raia", "droga", "pague menos", "pacheco", "venancio", "saojoao",
    "panvel", "ultrafarma",
)
GROCERIES = (
    "pao de acuc", "pao de açúc", "sacolao", "sacolão", "oxxo", "pao acuc",
    "carrefour", "extra", "dia", "mercado", "kanga", "supermer", "atacad",
    "daki", "shopper", "rappi-merc", "minuto", "festval", "rede compras",
    "verdemar", "muffato", "tonin",
)
RESTAURANTS = (
    "restaur", "lanchon", "bar e lan", "burguer", "burger", "pizza", "pizzar",
    "padaria", "doceria", "sorvet", "cervej", "boteco", "bistro", "churrasc",
    "casting bar", "bibibij", "consolacao", "consolação", "cardeal",
    "highlander", "patio", "patior", "ifd", "ifood",
)
TRANSPORTATION = (
    "uberrides", "uber*", "uber bv", "99tax", "99app", "99pop",
    "auto posto", "posto sho", "posto ipiranga", "gasol",
    "metro", "metropasso", "easytax", "linktax", "bilhete unico",
    "estacion", "estap", "parkclick",
)
SUBSCRIPTIONS_TECH = (
    "claude.ai", "anthrop", "cursor", "openai", "github", "vercel",
    "digitalocean", "name-cheap", "namecheap", "cloudflare", "aws",
    "amazon web", "google cloud", "googl cloud", "fly.io", "linode",
)
SUBSCRIPTIONS_CONSUMER = (
    "netflix", "spotif", "amazon prime", "youtub", "youtube prem",
    "globoplay", "appletv", "apple.com/bill", "hbo", "disney", "paramount",
    "tinder", "duolingo", "audible",
)
DONATIONS = ("gofundme", "vakinha", "abrigo", "ong ")
INSURANCE = ("apolice", "seguros", "porto seguro", "bradesco seguros", "azul seguros")
HEALTHCARE = ("amilcard", "amil", "drconsult", "consul medico", "consulta")
HOUSING = ("aluguel", "condominio", "condomi", "rent ", "imobiliaria")
UTILITIES = ("net servicos", "enel", "eletropaulo", "comgas", "sabesp", "vivo", "tim ", "claro ", "vivocelu", "claro nx", "tim cel")
SHOPPING_ONLINE = (
    "amazon mktpl", "amzn.com", "magazin", "magalu", "mercado livre",
    "shopee", "aliexpress", "alipay", "shein", "americanas", "submarino",
    "centauro", "kabum", "terabytesh",
)
ENTERTAINMENT = ("cinemark", "ingresso", "ingresse", "kinoplex")


def _matches(desc_norm: str, tokens) -> bool:
    return any(t in desc_norm for t in tokens)


def classify_extrato(desc: str, desc_norm: str | None = None) -> tuple[str, bool, bool, bool]:
    """Returns (category, is_sweep, is_interest, is_internal). Extrato CC tx."""
    if desc_norm is None:
        desc_norm = _norm(desc)

    if desc_norm in SWEEP_CREDIT_DESC:
        return "sweep_resgate", True, False, True
    if desc_norm in SWEEP_DEBIT_DESC:
        return "sweep_aplicacao", True, False, True
    if desc_norm in INTEREST_DESC:
        return "rendimento_cdb", False, True, False

    if desc_norm.startswith("est pix") or desc_norm.startswith("est tef") or desc_norm.startswith("est ted") or desc_norm.startswith("est on"):
        return "estorno", False, False, True
    if desc_norm.startswith("estorno"):
        return "estorno", False, False, True
    if "fatura" in desc_norm and ("itau" in desc_norm or "platinu" in desc_norm or "visa" in desc_norm):
        return "pagamento_cartao", False, False, True
    if desc_norm.startswith("itau visa"):
        return "pagamento_cartao", False, False, True
    if desc_norm.startswith("tbi") or desc_norm.startswith("int itau click"):
        return "transferencia_propria", False, False, True
    if desc_norm.startswith("dev pix"):
        return "devolucao_pix", False, False, False
    if desc_norm.startswith("dev "):
        return "devolucao", False, False, False
    if desc_norm.startswith("remuneracao") or "salario" in desc_norm[:20]:
        return "salario", False, False, False
    if desc_norm.startswith("credito cartao") or desc_norm.startswith("credito de cartao"):
        return "credito_cartao", False, False, False
    if desc_norm.startswith("ressarcimento"):
        return "ressarcimento", False, False, False

    if desc_norm.startswith("pix") and is_self(desc_norm):
        return "pix_proprio", False, False, True
    if desc_norm.startswith("transf") and is_self(desc_norm):
        return "transferencia_propria", False, False, True

    if "pag tit banco" in desc_norm or desc_norm.startswith("mobilepag") or desc_norm.startswith("pag boleto"):
        return "boleto", False, False, False

    if desc_norm.startswith("da ") or desc_norm.startswith("debito automatico") or desc_norm.startswith("db cob"):
        if _matches(desc_norm, UTILITIES):
            return "utilidades", False, False, False
        return "debito_automatico", False, False, False

    if desc_norm.startswith("sispag"):
        return "sispag", False, False, False
    if desc_norm.startswith("saque"):
        return "saque", False, False, False

    if (desc_norm.startswith("ted") or desc_norm.startswith("doc") or desc_norm.startswith("int ted")) and is_self(desc_norm):
        return "transferencia_propria", False, False, True
    if desc_norm.startswith("ted") or desc_norm.startswith("doc") or desc_norm.startswith("int ted"):
        return "transferencia", False, False, False

    if desc_norm.startswith("rshop") or desc_norm.startswith("rscss") or desc_norm.startswith("rsccs"):
        cat = _classify_merchant(desc_norm)
        return cat, False, False, False

    if desc_norm.startswith("on ifd") or "ifd*" in desc_norm or "ifood" in desc_norm:
        return "restaurante", False, False, False

    if desc_norm.startswith("pix"):
        return "pix", False, False, False

    return "outros", False, False, False


def classify_fatura(merchant: str, category_hint: str | None = None, is_international: bool = False) -> str:
    """Returns category for a credit card transaction."""
    m = _norm(merchant)
    if not m:
        return "outros"

    if _matches(m, SUBSCRIPTIONS_TECH):
        return "assinatura_tech"
    if _matches(m, SUBSCRIPTIONS_CONSUMER):
        return "assinatura"
    if _matches(m, DONATIONS):
        return "doacao"
    if _matches(m, TRANSPORTATION):
        return "transporte"
    if _matches(m, RESTAURANTS):
        return "restaurante"
    if _matches(m, GROCERIES):
        return "mercado"
    if _matches(m, PHARMACIES):
        return "farmacia"
    if _matches(m, SHOPPING_ONLINE):
        return "compra_online"
    if _matches(m, HEALTHCARE):
        return "saude"
    if _matches(m, HOUSING):
        return "moradia"
    if _matches(m, UTILITIES):
        return "utilidades"
    if _matches(m, ENTERTAINMENT):
        return "entretenimento"
    if _matches(m, INSURANCE):
        return "seguros"

    if category_hint:
        hint = _norm(category_hint)
        if hint.startswith("restaur"):
            return "restaurante"
        if hint.startswith("transport"):
            return "transporte"
        if hint.startswith("supermer"):
            return "mercado"
        if hint.startswith("saude"):
            return "saude"
        if hint.startswith("educ"):
            return "educacao"
        if hint.startswith("vestu"):
            return "vestuario"
        if hint.startswith("lazer"):
            return "lazer"
        if hint.startswith("eletron"):
            return "compra_online"

    if is_international:
        return "internacional_outros"
    return "outros"


def _classify_merchant(desc_norm: str) -> str:
    if _matches(desc_norm, PHARMACIES):
        return "farmacia"
    if _matches(desc_norm, GROCERIES):
        return "mercado"
    if _matches(desc_norm, RESTAURANTS):
        return "restaurante"
    if _matches(desc_norm, TRANSPORTATION):
        return "transporte"
    if _matches(desc_norm, SUBSCRIPTIONS_TECH):
        return "assinatura_tech"
    if _matches(desc_norm, SUBSCRIPTIONS_CONSUMER):
        return "assinatura"
    if _matches(desc_norm, SHOPPING_ONLINE):
        return "compra_online"
    if _matches(desc_norm, HEALTHCARE):
        return "saude"
    if _matches(desc_norm, UTILITIES):
        return "utilidades"
    return "compra_debito"

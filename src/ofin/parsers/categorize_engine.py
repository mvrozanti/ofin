from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Account, CategoryRule, Transaction, TransactionOverride
from .categorize import classify_extrato, classify_fatura
from .common import strip_accents


@dataclass(slots=True)
class CompiledRule:
    id: int
    pattern_type: str
    pattern: str
    account_type: str | None
    sign: str | None
    mega: str
    category: str
    is_internal: bool
    priority: int
    compiled_re: re.Pattern | None = None


_cache: list[CompiledRule] = []
_cache_version: int = 0
_loaded_version: int = -1


def bump_cache() -> None:
    global _cache_version
    _cache_version += 1


def _norm(s: str) -> str:
    return strip_accents(s or "").lower()


def _compile(rule: CategoryRule) -> CompiledRule:
    pat = rule.pattern.lower()
    compiled = None
    if rule.pattern_type == "regex":
        try:
            compiled = re.compile(pat, re.IGNORECASE)
        except re.error:
            compiled = None
    return CompiledRule(
        id=rule.id,
        pattern_type=rule.pattern_type,
        pattern=pat,
        account_type=rule.account_type,
        sign=rule.sign,
        mega=rule.mega,
        category=rule.category,
        is_internal=rule.is_internal,
        priority=rule.priority,
        compiled_re=compiled,
    )


async def _reload(s: AsyncSession) -> None:
    global _cache, _loaded_version
    rows = (
        await s.execute(
            select(CategoryRule)
            .where(CategoryRule.enabled == True)  # noqa: E712
            .order_by(CategoryRule.priority, CategoryRule.id)
        )
    ).scalars().all()
    _cache = [_compile(r) for r in rows]
    _loaded_version = _cache_version


def _matches(rule: CompiledRule, desc_norm: str) -> bool:
    if rule.pattern_type == "exact":
        return desc_norm == rule.pattern
    if rule.pattern_type == "startswith":
        return desc_norm.startswith(rule.pattern)
    if rule.pattern_type == "regex":
        return rule.compiled_re is not None and rule.compiled_re.search(desc_norm) is not None
    return rule.pattern in desc_norm


async def classify_tx(
    s: AsyncSession,
    *,
    description: str | None,
    account_type: str,
    sign: str,
    is_international: bool = False,
    fatura_category_hint: str | None = None,
    tx_id: str | None = None,
) -> tuple[str, str, bool, int | None]:
    """Returns (mega, category, is_internal, rule_id). tx_id triggers override lookup."""
    if _loaded_version != _cache_version:
        await _reload(s)

    if tx_id:
        ov = await s.get(TransactionOverride, tx_id)
        if ov and (ov.mega or ov.category):
            return (
                ov.mega or "outros",
                ov.category or "outros",
                bool(ov.is_internal) if ov.is_internal is not None else False,
                None,
            )

    desc = description or ""
    desc_norm = _norm(desc)

    for rule in _cache:
        if rule.account_type and rule.account_type != account_type:
            continue
        if rule.sign and rule.sign != sign:
            continue
        if _matches(rule, desc_norm):
            return rule.mega, rule.category, rule.is_internal, rule.id

    # Fallback to hardcoded classifier
    if account_type == "BANK":
        cat, is_sweep, is_int, is_internal = classify_extrato(desc, desc_norm)
        mega = _legacy_mega_for(cat, is_sweep, is_int, is_internal)
        return mega, cat, is_internal or is_sweep, None
    elif account_type == "CREDIT":
        cat = classify_fatura(desc, category_hint=fatura_category_hint, is_international=is_international)
        mega = _legacy_mega_for(cat, False, False, False)
        return mega, cat, False, None
    return "outros", "outros", False, None


async def apply_rules_to_all(
    s: AsyncSession,
    *,
    only_rule_id: int | None = None,
    force: bool = False,
) -> tuple[int, int]:
    """Reclassify transactions with current rules. Returns (updated, skipped_overrides).

    only_rule_id: only apply where the winning rule is that id (rule-mode path).
    force: also overwrite override-protected transactions. Caller commits.
    """
    bump_cache()
    overrides: set[str] = set()
    if not force:
        overrides = set((await s.execute(select(TransactionOverride.tx_id))).scalars().all())
    rows = (
        await s.execute(
            select(Transaction, Account.type).join(Account, Transaction.account_id == Account.id)
        )
    ).all()
    updated = 0
    skipped = 0
    for tx, acct_type in rows:
        if tx.id in overrides:
            skipped += 1
            continue
        cc_meta = tx.credit_card_metadata or {}
        sign = "credit" if (tx.amount or 0) > 0 else "debit"
        mega, cat, is_internal_eng, rule_id = await classify_tx(
            s,
            description=tx.description or tx.description_raw,
            account_type=acct_type or "BANK",
            sign=sign,
            is_international=bool(cc_meta.get("is_international")),
            fatura_category_hint=cc_meta.get("category_label"),
            tx_id=tx.id if only_rule_id is not None else None,
        )
        if only_rule_id is not None and rule_id != only_rule_id:
            continue
        tx.mega = mega
        tx.category = cat
        tx.rule_id = rule_id
        raw = dict(tx.raw or {})
        if not raw.get("is_sweep"):
            raw["is_internal"] = is_internal_eng
        tx.raw = raw
        updated += 1
    return updated, skipped


_LEGACY_MEGA_MAP = {
    "sweep_resgate": "internal",
    "sweep_aplicacao": "internal",
    "pix_proprio": "internal",
    "transferencia_propria": "internal",
    "pagamento_cartao": "internal",
    "estorno": "internal",
    "salario": "renda",
    "remuneracao": "renda",
    "rendimento_cdb": "renda",
    "credito_cartao": "renda",
    "devolucao_pix": "renda",
    "devolucao": "renda",
    "ressarcimento": "renda",
    "pix": "pix_out",
    "boleto": "moradia",
    "ifood": "alimentacao",
    "restaurante": "alimentacao",
    "mercado": "alimentacao",
    "farmacia": "saude",
    "saude": "saude",
    "transporte": "transporte",
    "utilidades": "utilidades",
    "compra_debito": "compra_loja",
    "compra_online": "compra_online",
    "assinatura": "assinatura",
    "doacao": "doacao",
    "moradia": "moradia",
    "saque": "saque",
    "sispag": "outros",
    "transferencia": "transferencia",
    "debito_automatico": "utilidades",
    "internacional_outros": "compra_online",
    "pagamento_fatura": "internal",
    "outros": "outros",
}


def _legacy_mega_for(category: str, is_sweep: bool, is_interest: bool, is_internal: bool) -> str:
    if is_sweep or is_internal:
        return "internal"
    if is_interest:
        return "renda"
    return _LEGACY_MEGA_MAP.get(category or "outros", "outros")

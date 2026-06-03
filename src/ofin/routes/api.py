from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_dep
from ..models import (
    Account,
    CategoryRule,
    Document,
    ParseWarning,
    Tag,
    Transaction,
    TransactionOverride,
    TransactionTag,
)
from ..parsers.categorize_engine import bump_cache, classify_tx

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/documents")
async def list_documents(request: Request, s: AsyncSession = Depends(session_dep)) -> list[dict]:
    if not request.state.auth.authed:
        raise HTTPException(403, "login required")
    authed = request.state.auth.authed
    rows = (
        await s.execute(
            select(Document).order_by(Document.period_year.desc(), Document.period_month.desc())
        )
    ).scalars().all()
    return [
        {
            "id": d.id,
            "type": d.document_type,
            "period_year": d.period_year,
            "period_month": d.period_month,
            "source_path": d.source_path if authed else None,
            "parser_version": d.parser_version,
            "summary": d.summary if authed else None,
        }
        for d in rows
    ]


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, request: Request, s: AsyncSession = Depends(session_dep)) -> dict:
    if not request.state.auth.authed:
        raise HTTPException(403, "login required")
    authed = request.state.auth.authed
    d = await s.get(Document, doc_id)
    if not d:
        return {"error": "not found"}
    warnings = (
        await s.execute(select(ParseWarning).where(ParseWarning.document_id == doc_id))
    ).scalars().all()
    txs = (
        await s.execute(
            select(Transaction).where(Transaction.document_id == doc_id).order_by(Transaction.date)
        )
    ).scalars().all()
    return {
        "id": d.id,
        "type": d.document_type,
        "summary": d.summary if authed else None,
        "warnings": [
            {"severity": w.severity, "code": w.code, "message": w.message, "diff": w.diff if authed else None}
            for w in warnings
        ],
        "transactions": [
            {
                "date": t.date.isoformat(),
                "amount": str(t.amount) if authed else None,
                "description": t.description,
                "category": t.category,
                "type": t.type,
                "is_sweep": (t.raw or {}).get("is_sweep") if t.raw else None,
                "is_international": (t.credit_card_metadata or {}).get("is_international"),
            }
            for t in txs
        ],
    }


@router.get("/accounts")
async def list_accounts(request: Request, s: AsyncSession = Depends(session_dep)) -> list[dict]:
    authed = request.state.auth.authed
    rows = (await s.execute(select(Account))).scalars().all()
    return [
        {
            "id": a.id,
            "type": a.type,
            "subtype": a.subtype,
            "name": a.marketing_name or a.name,
            "balance": (str(a.balance) if a.balance is not None else None) if authed else None,
            "currency": a.currency_code,
        }
        for a in rows
    ]


@router.get("/sankey")
async def api_sankey(request: Request, s: AsyncSession = Depends(session_dep)) -> dict:
    from ..filters import Filter
    from sqlalchemy import func as sqlfunc
    from decimal import Decimal as _D
    f = Filter.from_request(request)
    inc_q = (
        select(Transaction.mega, Transaction.category, sqlfunc.sum(Transaction.amount))
        .where(Transaction.amount > 0)
        .group_by(Transaction.mega, Transaction.category)
    )
    inc_q = f.apply_to_tx(inc_q).where(
        Account.type == "BANK",
        sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False,  # noqa: E712
        sqlfunc.coalesce(Transaction.mega, "") != "internal",
    )
    incomes = (await s.execute(inc_q)).all()
    spend_bank_q = (
        select(Transaction.mega, Transaction.category, sqlfunc.sum(-Transaction.amount))
        .where(Transaction.amount < 0)
        .group_by(Transaction.mega, Transaction.category)
    )
    spend_bank_q = f.apply_to_tx(spend_bank_q).where(
        Account.type == "BANK",
        sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False,  # noqa: E712
        sqlfunc.coalesce(Transaction.mega, "") != "internal",
    )
    spend_bank = (await s.execute(spend_bank_q)).all()
    spend_credit_q = (
        select(Transaction.mega, Transaction.category, sqlfunc.sum(Transaction.amount))
        .where(Transaction.amount > 0)
        .group_by(Transaction.mega, Transaction.category)
    )
    spend_credit_q = f.apply_to_tx(spend_credit_q).where(Account.type == "CREDIT")
    spend_credit = (await s.execute(spend_credit_q)).all()

    authed_api = request.state.auth.authed

    def _anon_cat(mega, category):
        if not authed_api and mega == "pessoas":
            return "•••"
        return category

    def _collapse(rows, default_mega):
        agg: dict[tuple[str, str], _D] = {}
        for mega, cat, total in rows:
            if not total:
                continue
            m = mega or default_mega
            c = _anon_cat(m, cat or "outros")
            agg[(m, c)] = agg.get((m, c), _D(0)) + abs(_D(str(total)))
        return [(m, c, v) for (m, c), v in agg.items()]

    income_data = _collapse(incomes, "renda")
    spend_bank_data = _collapse(spend_bank, "outros")
    spend_credit_data = _collapse(spend_credit, "outros")
    total_income = sum((v for _, _, v in income_data), _D(0))
    total_spend_bank = sum((v for _, _, v in spend_bank_data), _D(0))
    total_spend_credit = sum((v for _, _, v in spend_credit_data), _D(0))
    total_spend = total_spend_bank + total_spend_credit

    nodes: list[dict] = []
    seen: set[str] = set()
    def add(name):
        if name not in seen:
            nodes.append({"name": name}); seen.add(name)

    add("CC Itaú")
    if total_spend_credit > 0:
        add("Cartão Platinum")
    income_megas_sum: dict[str, _D] = {}
    for mega, cat, v in income_data:
        income_megas_sum[mega] = income_megas_sum.get(mega, _D(0)) + v
    for mega in income_megas_sum:
        add(f"renda:{mega}")
    for mega, cat, v in income_data:
        add(f"in:{mega}/{cat}")
    bank_megas_sum: dict[str, _D] = {}
    for mega, cat, v in spend_bank_data:
        bank_megas_sum[mega] = bank_megas_sum.get(mega, _D(0)) + v
    for mega in bank_megas_sum:
        add(f"out:{mega}")
    for mega, cat, v in spend_bank_data:
        add(f"sub:{mega}/{cat}")
    card_megas_sum: dict[str, _D] = {}
    for mega, cat, v in spend_credit_data:
        card_megas_sum[mega] = card_megas_sum.get(mega, _D(0)) + v
    for mega in card_megas_sum:
        add(f"card:{mega}")
    for mega, cat, v in spend_credit_data:
        add(f"csub:{mega}/{cat}")

    links: list[dict] = []
    for mega, cat, v in income_data:
        if v > 0:
            links.append({"source": f"in:{mega}/{cat}", "target": f"renda:{mega}", "value": float(v), "mega": mega, "category": cat, "side": "income"})
    for mega, total in income_megas_sum.items():
        if total > 0:
            links.append({"source": f"renda:{mega}", "target": "CC Itaú", "value": float(total), "mega": mega, "side": "income"})
    for mega, total in bank_megas_sum.items():
        if total > 0:
            links.append({"source": "CC Itaú", "target": f"out:{mega}", "value": float(total), "mega": mega, "side": "bank"})
    for mega, cat, v in spend_bank_data:
        if v > 0:
            links.append({"source": f"out:{mega}", "target": f"sub:{mega}/{cat}", "value": float(v), "mega": mega, "category": cat, "side": "bank"})
    if total_spend_credit > 0:
        links.append({"source": "CC Itaú", "target": "Cartão Platinum", "value": float(total_spend_credit), "side": "card"})
    for mega, total in card_megas_sum.items():
        if total > 0:
            links.append({"source": "Cartão Platinum", "target": f"card:{mega}", "value": float(total), "mega": mega, "side": "card"})
    for mega, cat, v in spend_credit_data:
        if v > 0:
            links.append({"source": f"card:{mega}", "target": f"csub:{mega}/{cat}", "value": float(v), "mega": mega, "category": cat, "side": "card"})

    authed = request.state.auth.authed
    if not authed:
        from ..masking import proportions
        link_vals = [lk["value"] for lk in links]
        scaled = proportions(link_vals, total=100.0)
        for lk, v in zip(links, scaled):
            lk["value"] = v
        totals = {"income": None, "spend": None, "net": None}
    else:
        totals = {
            "income": float(total_income),
            "spend": float(total_spend),
            "net": float(total_income - total_spend),
        }
    return {"nodes": nodes, "links": links, "totals": totals}


@router.get("/megas")
async def list_megas(s: AsyncSession = Depends(session_dep)) -> list[str]:
    rows = (await s.execute(select(CategoryRule.mega).distinct().order_by(CategoryRule.mega))).scalars().all()
    tx_rows = (await s.execute(select(Transaction.mega).distinct())).scalars().all()
    combined = sorted({*rows, *(m for m in tx_rows if m)})
    return combined


@router.get("/categories")
async def list_categories(request: Request, s: AsyncSession = Depends(session_dep), mega: str | None = None) -> list[dict]:
    authed = request.state.auth.authed
    q = select(Transaction.mega, Transaction.category).distinct()
    if mega:
        q = q.where(Transaction.mega == mega)
    rows = (await s.execute(q)).all()
    out = []
    seen = set()
    for m, c in rows:
        if not c:
            continue
        cat = "•••" if (not authed and m == "pessoas") else c
        key = (m, cat)
        if key in seen:
            continue
        seen.add(key)
        out.append({"mega": m, "category": cat})
    return out


def _tx_to_dict(t: Transaction, authed: bool = True) -> dict:
    raw = t.raw or {}
    meta = t.credit_card_metadata or {}
    fx = (meta.get("fx") or {}) if isinstance(meta, dict) else {}
    cat = t.category
    if not authed and t.mega == "pessoas":
        cat = "•••"
    return {
        "id": t.id,
        "date": t.date.isoformat() if t.date else None,
        "amount": (str(t.amount) if t.amount is not None else None) if authed else None,
        "currency": t.currency_code,
        "description": (t.description or t.description_raw) if authed else None,
        "description_raw": t.description_raw if authed else None,
        "mega": t.mega,
        "category": cat,
        "type": t.type,
        "account_id": t.account_id,
        "rule_id": t.rule_id,
        "is_sweep": raw.get("is_sweep"),
        "is_internal": raw.get("is_internal"),
        "is_international": meta.get("is_international") if isinstance(meta, dict) else None,
        "fx": ({
            "currency": fx.get("currency"),
            "original_value": (str(fx.get("original_value")) if fx.get("original_value") is not None else None) if authed else None,
            "rate": (str(fx.get("rate")) if fx.get("rate") is not None else None) if authed else None,
        } if fx else None),
    }


@router.get("/transactions/{tx_id}/explain")
async def explain_tx(tx_id: str, request: Request, s: AsyncSession = Depends(session_dep)) -> dict:
    authed = request.state.auth.authed
    tx = await s.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(404)
    rule = None
    if tx.rule_id:
        r = await s.get(CategoryRule, tx.rule_id)
        if r:
            rule = {
                "id": r.id,
                "pattern_type": r.pattern_type,
                "pattern": r.pattern,
                "mega": r.mega,
                "category": r.category,
                "account_type": r.account_type,
                "sign": r.sign,
                "priority": r.priority,
                "is_internal": r.is_internal,
                "enabled": r.enabled,
            }
    override = await s.get(TransactionOverride, tx_id)
    ov = None
    if override:
        ov = {
            "mega": override.mega,
            "category": override.category,
            "is_internal": override.is_internal,
            "note": override.note,
            "set_at": override.set_at.isoformat() if override.set_at else None,
        }
    tags = (
        await s.execute(
            select(Tag.name, Tag.color)
            .join(TransactionTag, TransactionTag.tag_id == Tag.id)
            .where(TransactionTag.tx_id == tx_id)
        )
    ).all()
    return {
        "transaction": _tx_to_dict(tx, authed=authed),
        "matched_rule": rule,
        "override": ov,
        "tags": [{"name": n, "color": c} for n, c in tags],
    }


@router.post("/transactions/{tx_id}/categorize")
async def categorize_tx(
    tx_id: str,
    payload: dict = Body(...),
    s: AsyncSession = Depends(session_dep),
) -> dict:
    tx = await s.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(404)
    mode = payload.get("mode", "once")
    mega = (payload.get("mega") or "").strip() or None
    category = (payload.get("category") or "").strip() or None
    is_internal = payload.get("is_internal")
    note = payload.get("note")
    if not mega and not category and is_internal is None and mode == "once":
        raise HTTPException(400, "no fields to update")

    if mode == "once":
        ov = await s.get(TransactionOverride, tx_id)
        if not ov:
            ov = TransactionOverride(tx_id=tx_id)
            s.add(ov)
        if mega is not None:
            ov.mega = mega
        if category is not None:
            ov.category = category
        if is_internal is not None:
            ov.is_internal = bool(is_internal)
        if note is not None:
            ov.note = note
        ov.set_at = datetime.now(timezone.utc)
        tx.mega = ov.mega or tx.mega
        tx.category = ov.category or tx.category
        raw = dict(tx.raw or {})
        if is_internal is not None and not raw.get("is_sweep"):
            raw["is_internal"] = bool(is_internal)
            tx.raw = raw
        tx.rule_id = None
        await s.commit()
        return {"ok": True, "mode": "once", "tx": _tx_to_dict(tx)}

    if mode == "rule":
        pattern_type = payload.get("pattern_type", "contains")
        pattern = (payload.get("pattern") or tx.description or tx.description_raw or "").lower().strip()
        if not pattern:
            raise HTTPException(400, "empty pattern")
        if not mega or not category:
            raise HTTPException(400, "mega+category required for rule mode")
        account_type = payload.get("account_type")
        sign = payload.get("sign")
        priority = int(payload.get("priority") or 50)
        rule = CategoryRule(
            pattern_type=pattern_type,
            pattern=pattern,
            mega=mega,
            category=category,
            account_type=account_type or None,
            sign=sign or None,
            is_internal=bool(is_internal) if is_internal is not None else False,
            priority=priority,
            enabled=True,
            notes=note or f"created from tx {tx_id}",
        )
        s.add(rule)
        await s.flush()
        bump_cache()
        affected = 0
        all_rows = (
            await s.execute(
                select(Transaction, Account.type)
                .join(Account, Transaction.account_id == Account.id)
            )
        ).all()
        overrides = set((await s.execute(select(TransactionOverride.tx_id))).scalars().all())
        for t, acct_type in all_rows:
            if t.id in overrides:
                continue
            cc_meta = t.credit_card_metadata or {}
            is_intl = bool(cc_meta.get("is_international"))
            hint = cc_meta.get("category_label")
            sgn = "credit" if (t.amount or 0) > 0 else "debit"
            m, c, internal_eng, rid = await classify_tx(
                s,
                description=t.description or t.description_raw,
                account_type=acct_type or "BANK",
                sign=sgn,
                is_international=is_intl,
                fatura_category_hint=hint,
                tx_id=t.id,
            )
            if rid == rule.id:
                t.mega = m
                t.category = c
                t.rule_id = rid
                raw = dict(t.raw or {})
                if not raw.get("is_sweep"):
                    raw["is_internal"] = internal_eng
                t.raw = raw
                affected += 1
        await s.commit()
        return {"ok": True, "mode": "rule", "rule_id": rule.id, "affected": affected}

    if mode == "clear":
        ov = await s.get(TransactionOverride, tx_id)
        if ov:
            await s.delete(ov)
        await s.commit()
        return {"ok": True, "mode": "clear"}

    raise HTTPException(400, f"unknown mode {mode}")


@router.post("/transactions/bulk_categorize")
async def bulk_categorize(payload: dict = Body(...), s: AsyncSession = Depends(session_dep)) -> dict:
    ids: list[str] = payload.get("ids") or []
    mega = (payload.get("mega") or "").strip() or None
    category = (payload.get("category") or "").strip() or None
    is_internal = payload.get("is_internal")
    if not ids:
        raise HTTPException(400, "no ids")
    if not (mega or category or is_internal is not None):
        raise HTTPException(400, "no fields to update")
    now = datetime.now(timezone.utc)
    for tx_id in ids:
        tx = await s.get(Transaction, tx_id)
        if not tx:
            continue
        ov = await s.get(TransactionOverride, tx_id)
        if not ov:
            ov = TransactionOverride(tx_id=tx_id)
            s.add(ov)
        if mega is not None:
            ov.mega = mega
            tx.mega = mega
        if category is not None:
            ov.category = category
            tx.category = category
        if is_internal is not None:
            ov.is_internal = bool(is_internal)
            raw = dict(tx.raw or {})
            if not raw.get("is_sweep"):
                raw["is_internal"] = bool(is_internal)
                tx.raw = raw
        ov.set_at = now
        tx.rule_id = None
    await s.commit()
    return {"ok": True, "n": len(ids)}


@router.get("/transactions")
async def list_transactions(
    request: Request,
    s: AsyncSession = Depends(session_dep),
    limit: int = 200,
    offset: int = 0,
    mega: str | None = None,
    category: str | None = None,
    q: str | None = None,
    account_id: str | None = None,
) -> list[dict]:
    authed = request.state.auth.authed
    stmt = select(Transaction).order_by(Transaction.date.desc()).limit(limit).offset(offset)
    if mega:
        stmt = stmt.where(Transaction.mega == mega)
    if category:
        stmt = stmt.where(Transaction.category == category)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(Transaction.description.ilike(like))
    if account_id:
        stmt = stmt.where(Transaction.account_id == account_id)
    rows = (await s.execute(stmt)).scalars().all()
    return [_tx_to_dict(t, authed=authed) for t in rows]

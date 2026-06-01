from __future__ import annotations

import json
from datetime import date as _date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db import session
from .models import Account, Document, Item, ParseWarning, Transaction
from .parsers.common import deterministic_tx_id, sha256_bytes
from .parsers.extrato_itau_v1 import (
    ExtratoParseResult,
    parse_extrato as _parse_extrato_text,
)
from .parsers.fatura_itau_v1 import FaturaParseResult, parse_fatura as _parse_fatura_path
from .parsers.pdf import pdftotext_layout
from .parsers.registry import detect_document_type
from .parsers.validators import validate_extrato, validate_fatura

log = structlog.get_logger()

ITEM_ID = "itau-pdf"
ACCOUNT_CHECKING_ID = "itau-checking-17236-5"
ACCOUNT_CREDIT_ID = "itau-credit-platinum-9132"


def _jsonable(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (_date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


async def _ensure_item(s: AsyncSession) -> None:
    stmt = pg_insert(Item).values(
        id=ITEM_ID,
        connector_name="itau-pdf",
        status="active",
    ).on_conflict_do_nothing(index_elements=[Item.id])
    await s.execute(stmt)


async def _ensure_account(
    s: AsyncSession,
    account_id: str,
    *,
    type_: str,
    subtype: str,
    name: str,
    marketing_name: str,
    number: str,
    balance: Decimal | None = None,
) -> None:
    values = {
        "id": account_id,
        "item_id": ITEM_ID,
        "type": type_,
        "subtype": subtype,
        "name": name,
        "marketing_name": marketing_name,
        "number": number,
        "currency_code": "BRL",
    }
    update = dict(values)
    update.pop("id")
    update.pop("item_id")
    if balance is not None:
        values["balance"] = balance
        update["balance"] = balance
    stmt = pg_insert(Account).values(**values).on_conflict_do_update(
        index_elements=[Account.id], set_=update
    )
    await s.execute(stmt)


async def _update_account_balance_from_latest(s: AsyncSession, account_id: str) -> None:
    """Set Account.balance to closing_balance from the latest extrato (by period).

    For credit account, we don't track a 'balance' (it's a card, value changes
    daily); leave NULL so dashboard treats as informational.
    """
    if account_id != ACCOUNT_CHECKING_ID:
        return
    rows = (
        await s.execute(
            select(Document.summary)
            .where(Document.account_id == account_id, Document.document_type == "extrato")
            .order_by(Document.period_year.desc(), Document.period_month.desc())
            .limit(1)
        )
    ).scalars().all()
    if not rows:
        return
    summary = rows[0] or {}
    cb = summary.get("closing_balance")
    if cb is None:
        return
    try:
        balance = Decimal(str(cb))
    except Exception:
        return
    obj = await s.get(Account, account_id)
    if obj is not None:
        obj.balance = balance


async def _upsert_document(
    s: AsyncSession,
    *,
    doc_id: str,
    source_path: str,
    doc_type: str,
    issuer: str,
    parser_version: str,
    period_year: int | None,
    period_month: int | None,
    account_id: str | None,
    summary: dict,
    file_size: int,
    file_sha256: str,
    raw_text: str,
) -> None:
    values = {
        "id": doc_id,
        "source_path": source_path,
        "document_type": doc_type,
        "issuer": issuer,
        "parser_version": parser_version,
        "period_year": period_year,
        "period_month": period_month,
        "account_id": account_id,
        "summary": _jsonable(summary),
        "file_size": file_size,
        "file_sha256": file_sha256,
        "raw_text": raw_text,
        "parsed_at": datetime.now(timezone.utc),
    }
    stmt = pg_insert(Document).values(**values).on_conflict_do_update(
        index_elements=[Document.id],
        set_={k: v for k, v in values.items() if k != "id"},
    )
    await s.execute(stmt)


async def _replace_warnings(s: AsyncSession, doc_id: str, warnings: list) -> None:
    await s.execute(delete(ParseWarning).where(ParseWarning.document_id == doc_id))
    if not warnings:
        return
    rows = [
        {
            "document_id": doc_id,
            "severity": w.severity,
            "code": w.code,
            "message": w.message,
            "raw_line": w.raw_line,
            "diff": _jsonable(w.diff) if w.diff else None,
        }
        for w in warnings
    ]
    await s.execute(pg_insert(ParseWarning).values(rows))


async def _replace_doc_transactions(
    s: AsyncSession,
    doc_id: str,
    rows: list[dict],
) -> int:
    await s.execute(delete(Transaction).where(Transaction.document_id == doc_id))
    if not rows:
        return 0
    stmt = pg_insert(Transaction).values(rows).on_conflict_do_nothing(
        index_elements=[Transaction.id]
    )
    await s.execute(stmt)
    return len(rows)


def _extrato_to_tx_rows(doc_id: str, account_id: str, result: ExtratoParseResult) -> list[dict]:
    rows = []
    s = result.summary
    period = (s.period_year, s.period_month)
    seen: dict[tuple, int] = {}
    for t in result.transactions:
        key = (t.when, t.raw_line, t.amount)
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        tx_id = deterministic_tx_id(doc_id, t.when, t.raw_line, t.amount, occ)
        type_ = "CREDIT" if t.amount > 0 else "DEBIT"
        merchant = None
        payment_data = None
        d_lower = (t.description_norm or t.description).lower()
        if "pix" in d_lower:
            payment_data = {"paymentMethod": "PIX"}
        rows.append(
            {
                "id": tx_id,
                "account_id": account_id,
                "amount": t.amount,
                "balance": t.balance_after,
                "currency_code": "BRL",
                "description": t.description,
                "description_raw": t.raw_line,
                "type": type_,
                "category": t.category,
                "category_id": None,
                "payment_data": payment_data,
                "credit_card_metadata": None,
                "merchant": merchant,
                "date": t.when,
                "raw": {
                    "doc_period": f"{period[0]:04d}-{period[1]:02d}",
                    "is_sweep": t.is_sweep,
                    "is_interest": t.is_interest,
                    "is_internal": t.is_internal,
                },
                "document_id": doc_id,
                "raw_line": t.raw_line,
            }
        )
    return rows


def _fatura_to_tx_rows(doc_id: str, account_id: str, result: FaturaParseResult) -> list[dict]:
    rows = []
    s = result.summary
    seen: dict[tuple, int] = {}
    for pmt in result.payments:
        key = (pmt.when, pmt.raw_line, pmt.amount)
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        tx_id = deterministic_tx_id(doc_id, pmt.when, pmt.raw_line, pmt.amount, occ)
        rows.append(
            {
                "id": tx_id,
                "account_id": account_id,
                "amount": pmt.amount,
                "balance": None,
                "currency_code": "BRL",
                "description": pmt.description,
                "description_raw": pmt.raw_line,
                "type": "CREDIT" if pmt.amount > 0 else "DEBIT",
                "category": "pagamento_fatura",
                "category_id": None,
                "payment_data": None,
                "credit_card_metadata": {"kind": "payment"},
                "merchant": None,
                "date": pmt.when,
                "raw": {"posting_date": s.posting_date.isoformat() if s.posting_date else None},
                "document_id": doc_id,
                "raw_line": pmt.raw_line,
            }
        )
    for t in result.transactions:
        key = (t.when, t.raw_line, t.amount_brl)
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        tx_id = deterministic_tx_id(doc_id, t.when, t.raw_line, t.amount_brl, occ)
        type_ = "CREDIT" if t.amount_brl < 0 else "DEBIT"
        merchant = {"name": t.merchant} if t.merchant else None
        cc_meta = {
            "category_label": t.category,
            "city": t.city,
            "is_international": t.is_international,
        }
        if t.fx_original_value is not None:
            cc_meta["fx"] = {
                "original_value": str(t.fx_original_value),
                "currency": t.fx_currency,
                "rate": str(t.fx_rate) if t.fx_rate is not None else None,
            }
        rows.append(
            {
                "id": tx_id,
                "account_id": account_id,
                "amount": t.amount_brl,
                "balance": None,
                "currency_code": "BRL",
                "description": t.merchant,
                "description_raw": t.raw_line,
                "type": type_,
                "category": t.category,
                "category_id": None,
                "payment_data": None,
                "credit_card_metadata": cc_meta,
                "merchant": merchant,
                "date": t.when,
                "raw": {"posting_date": s.posting_date.isoformat() if s.posting_date else None},
                "document_id": doc_id,
                "raw_line": t.raw_line,
            }
        )
    return rows


def _extrato_summary_dict(result: ExtratoParseResult) -> dict:
    s = result.summary
    return {
        "agency": s.agency,
        "account": s.account,
        "period_year": s.period_year,
        "period_month": s.period_month,
        "opening_balance": s.opening_balance,
        "opening_date": s.opening_date,
        "closing_balance": s.closing_balance,
        "closing_date": s.closing_date,
        "entradas_total": s.entradas_total,
        "saidas_total": s.saidas_total,
        "sweep_credit_total": s.sweep_credit_total,
        "sweep_debit_total": s.sweep_debit_total,
        "saldo_anterior_ledger": s.saldo_anterior_ledger,
        "saldo_final_ledger": s.saldo_final_ledger,
        "saldo_cc_ledger": s.saldo_cc_ledger,
        "cdb_snapshots": [
            {"date": c.when, "cdb_balance": c.cdb_balance} for c in result.cdb_snapshots
        ],
        "n_transactions": len(result.transactions),
    }


def _fatura_summary_dict(result: FaturaParseResult) -> dict:
    s = result.summary
    return {
        "card_brand": s.card_brand,
        "card_last4": s.card_last4,
        "posting_date": s.posting_date,
        "due_date": s.due_date,
        "emission_date": s.emission_date,
        "next_close_estimated": s.next_close_estimated,
        "previous_total": s.previous_total,
        "payment_amount": s.payment_amount,
        "payment_date": s.payment_date,
        "financed_balance": s.financed_balance,
        "current_charges": s.current_charges,
        "total": s.total,
        "limit_total": s.limit_total,
        "limit_available": s.limit_available,
        "limit_used": s.limit_used,
        "domestic_subtotal": s.domestic_subtotal,
        "international_subtotal": s.international_subtotal,
        "iof_repasse": s.iof_repasse,
        "international_total_with_iof": s.international_total_with_iof,
        "n_payments": len(result.payments),
        "n_transactions": len(result.transactions),
    }


async def import_pdf(s: AsyncSession, pdf_path: str | Path) -> dict:
    p = Path(pdf_path)
    data = p.read_bytes()
    file_sha = sha256_bytes(data)
    doc_id = file_sha[:32]
    doc_type, parser_version, _fp = detect_document_type(pdf_path)

    await _ensure_item(s)

    if doc_type == "extrato":
        text = pdftotext_layout(pdf_path)
        result = _parse_extrato_text(text)
        warnings = validate_extrato(result)
        await _ensure_account(
            s,
            ACCOUNT_CHECKING_ID,
            type_="BANK",
            subtype="CHECKING",
            name=f"Itaú {result.summary.account} ag {result.summary.agency}",
            marketing_name=f"Itaú CC {result.summary.account}",
            number=result.summary.account,
        )
        await _upsert_document(
            s,
            doc_id=doc_id,
            source_path=str(p),
            doc_type="extrato",
            issuer="itau",
            parser_version=parser_version,
            period_year=result.summary.period_year,
            period_month=result.summary.period_month,
            account_id=ACCOUNT_CHECKING_ID,
            summary=_extrato_summary_dict(result),
            file_size=len(data),
            file_sha256=file_sha,
            raw_text=text,
        )
        tx_rows = _extrato_to_tx_rows(doc_id, ACCOUNT_CHECKING_ID, result)
        await _replace_warnings(s, doc_id, warnings)
        n = await _replace_doc_transactions(s, doc_id, tx_rows)
        await _update_account_balance_from_latest(s, ACCOUNT_CHECKING_ID)
        return {"doc_id": doc_id, "type": "extrato", "tx": n, "warnings": len(warnings)}

    if doc_type == "fatura":
        result = _parse_fatura_path(pdf_path)
        warnings = validate_fatura(result)
        text = pdftotext_layout(pdf_path)
        await _ensure_account(
            s,
            ACCOUNT_CREDIT_ID,
            type_="CREDIT",
            subtype="CREDIT_CARD",
            name="Itaú Platinum 9132",
            marketing_name=f"Itaú {result.summary.card_brand} {result.summary.card_last4}",
            number=f"4705XXXXXXXX{result.summary.card_last4 or '9132'}",
        )
        py = result.summary.posting_date.year if result.summary.posting_date else None
        pm = result.summary.posting_date.month if result.summary.posting_date else None
        await _upsert_document(
            s,
            doc_id=doc_id,
            source_path=str(p),
            doc_type="fatura",
            issuer="itau",
            parser_version=parser_version,
            period_year=py,
            period_month=pm,
            account_id=ACCOUNT_CREDIT_ID,
            summary=_fatura_summary_dict(result),
            file_size=len(data),
            file_sha256=file_sha,
            raw_text=text,
        )
        tx_rows = _fatura_to_tx_rows(doc_id, ACCOUNT_CREDIT_ID, result)
        await _replace_warnings(s, doc_id, warnings)
        n = await _replace_doc_transactions(s, doc_id, tx_rows)
        return {"doc_id": doc_id, "type": "fatura", "tx": n, "warnings": len(warnings)}

    return {"doc_id": doc_id, "type": "unknown", "tx": 0, "warnings": 1}


async def init_schema() -> None:
    from .db import engine
    from .models import Base
    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def import_directory(directory: str | Path) -> dict:
    await init_schema()
    d = Path(directory)
    pdfs = sorted(d.glob("*.pdf"))
    results = []
    async with session() as s:
        for pdf in pdfs:
            try:
                r = await import_pdf(s, pdf)
                await s.commit()
            except Exception as e:
                await s.rollback()
                log.error("import_failed", path=str(pdf), err=str(e))
                r = {"doc_id": None, "type": "error", "error": str(e), "path": str(pdf)}
            results.append(r)
    counts = {"extrato": 0, "fatura": 0, "unknown": 0, "error": 0}
    for r in results:
        counts[r.get("type", "error")] = counts.get(r.get("type", "error"), 0) + 1
    return {"directory": str(d), "n": len(pdfs), "counts": counts, "results": results}

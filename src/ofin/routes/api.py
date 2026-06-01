from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_dep
from ..models import Account, Document, ParseWarning, Transaction

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/documents")
async def list_documents(s: AsyncSession = Depends(session_dep)) -> list[dict]:
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
            "source_path": d.source_path,
            "parser_version": d.parser_version,
            "summary": d.summary,
        }
        for d in rows
    ]


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, s: AsyncSession = Depends(session_dep)) -> dict:
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
        "summary": d.summary,
        "warnings": [
            {"severity": w.severity, "code": w.code, "message": w.message, "diff": w.diff}
            for w in warnings
        ],
        "transactions": [
            {
                "date": t.date.isoformat(),
                "amount": str(t.amount),
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
async def list_accounts(s: AsyncSession = Depends(session_dep)) -> list[dict]:
    rows = (await s.execute(select(Account))).scalars().all()
    return [
        {
            "id": a.id,
            "type": a.type,
            "subtype": a.subtype,
            "name": a.marketing_name or a.name,
            "balance": str(a.balance) if a.balance is not None else None,
            "currency": a.currency_code,
        }
        for a in rows
    ]

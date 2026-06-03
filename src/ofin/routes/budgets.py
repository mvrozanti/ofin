from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import budget_progress
from ..config import settings
from ..db import session_dep
from ..filters import Filter
from ..models import Budget

router = APIRouter(tags=["budgets"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["read_only"] = settings().read_only

from ..template_filters import register as _register_filters  # noqa: E402

_register_filters(templates)


@router.get("/budgets", response_class=HTMLResponse)
async def budgets_page(request: Request, s: AsyncSession = Depends(session_dep)):
    f = Filter.from_request(request)
    rows = (await s.execute(select(Budget).where(Budget.enabled == True).order_by(Budget.mega, Budget.category))).scalars().all()  # noqa: E712
    progress = await budget_progress(s, rows, f)
    return templates.TemplateResponse(
        "budgets.html",
        {
            "request": request,
            "filter": f,
            "budgets": rows,
            "progress": progress,
        },
    )


@router.post("/budgets")
async def budgets_create(
    mega: str = Form(...),
    category: str = Form(""),
    amount: str = Form(...),
    currency_code: str = Form("BRL"),
    period: str = Form("monthly"),
    notes: str = Form(""),
    s: AsyncSession = Depends(session_dep),
):
    try:
        amt = Decimal(amount.replace(",", "."))
    except Exception:
        raise HTTPException(400, "invalid amount")
    b = Budget(
        mega=mega.strip(),
        category=(category.strip() or None),
        amount=amt,
        currency_code=currency_code.strip().upper() or "BRL",
        period=period.strip() or "monthly",
        notes=notes or None,
        enabled=True,
    )
    s.add(b)
    await s.commit()
    return RedirectResponse("/budgets", status_code=303)


@router.post("/budgets/{budget_id}/edit")
async def budgets_edit(
    budget_id: int,
    mega: str = Form(...),
    category: str = Form(""),
    amount: str = Form(...),
    currency_code: str = Form("BRL"),
    period: str = Form("monthly"),
    notes: str = Form(""),
    s: AsyncSession = Depends(session_dep),
):
    b = await s.get(Budget, budget_id)
    if not b:
        raise HTTPException(404)
    try:
        amt = Decimal(amount.replace(",", "."))
    except Exception:
        raise HTTPException(400, "invalid amount")
    b.mega = mega.strip()
    b.category = (category.strip() or None)
    b.amount = amt
    b.currency_code = currency_code.strip().upper() or "BRL"
    b.period = period.strip() or "monthly"
    b.notes = notes or None
    b.updated_at = datetime.now(timezone.utc)
    await s.commit()
    return RedirectResponse("/budgets", status_code=303)


@router.post("/budgets/{budget_id}/delete")
async def budgets_delete(budget_id: int, s: AsyncSession = Depends(session_dep)):
    b = await s.get(Budget, budget_id)
    if not b:
        raise HTTPException(404)
    await s.delete(b)
    await s.commit()
    return RedirectResponse("/budgets", status_code=303)


@router.post("/budgets/{budget_id}/toggle")
async def budgets_toggle(budget_id: int, s: AsyncSession = Depends(session_dep)):
    b = await s.get(Budget, budget_id)
    if not b:
        raise HTTPException(404)
    b.enabled = not b.enabled
    await s.commit()
    return RedirectResponse("/budgets", status_code=303)

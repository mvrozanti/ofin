from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import (
    loan_outstanding_rows,
    net_worth_series,
    patrimonio_breakdown,
    savings_rate,
)
from ..config import settings
from ..db import session_dep
from ..filters import Filter
from ..models import BalanceSnapshot, Loan, LoanPayment

router = APIRouter(tags=["wealth"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["read_only"] = settings().read_only

from ..template_filters import register as _register_filters  # noqa: E402

_register_filters(templates)


def _parse_amount(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(".", "").replace(",", ".") if "," in raw else raw)
    except Exception:
        raise HTTPException(400, "invalid amount")


def _parse_date(raw: str) -> date:
    if not raw:
        return date.today()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(400, "invalid date")


async def _patrimonio_series(s: AsyncSession) -> dict:
    itau = await net_worth_series(s)
    snaps = (
        await s.execute(
            select(BalanceSnapshot.source, BalanceSnapshot.taken_at, BalanceSnapshot.value_brl)
            .order_by(BalanceSnapshot.taken_at)
        )
    ).all()
    months = {p.month for p in itau}
    per_source_month: dict[str, dict[str, Decimal]] = {}
    for src, taken_at, value in snaps:
        mk = f"{taken_at.year:04d}-{taken_at.month:02d}"
        months.add(mk)
        per_source_month.setdefault(src, {})[mk] = per_source_month.get(src, {}).get(mk, Decimal(0)) + Decimal(str(value))
    axis = sorted(months)
    itau_map = {p.month: p.total for p in itau}
    series: dict[str, list[float | None]] = {"itau": []}
    last_itau: Decimal | None = None
    for mk in axis:
        if itau_map.get(mk) is not None:
            last_itau = itau_map[mk]
        series["itau"].append(float(last_itau) if last_itau is not None else None)
    for src, by_month in sorted(per_source_month.items()):
        vals: list[float | None] = []
        last: Decimal | None = None
        for mk in axis:
            if mk in by_month:
                last = by_month[mk]
            vals.append(float(last) if last is not None else None)
        series[src] = vals
    return {"months": axis, "series": series}


@router.get("/savings", response_class=HTMLResponse)
async def savings_page(request: Request, s: AsyncSession = Depends(session_dep)):
    f = Filter.from_request(request)
    series = await savings_rate(s, f)
    json_data = {
        "months": [p.month for p in series],
        "income": [float(p.income) for p in series],
        "spend": [float(p.spend) for p in series],
        "saved": [float(p.saved) for p in series],
        "rate": [round(p.rate * 100, 2) for p in series],
    }
    avg_rate = (sum(p.rate for p in series) / len(series)) if series else 0.0
    avg_monthly_spend = (sum((p.spend for p in series), Decimal(0)) / len(series)) if series else Decimal(0)

    pat = await patrimonio_breakdown(s)
    loans = await loan_outstanding_rows(s)
    snapshots = (
        await s.execute(
            select(BalanceSnapshot).order_by(BalanceSnapshot.taken_at.desc(), BalanceSnapshot.source)
        )
    ).scalars().all()
    pat_series = await _patrimonio_series(s)
    runway_months = (pat.total / avg_monthly_spend) if avg_monthly_spend else None

    authed = request.state.auth.authed
    if not authed:
        peak = max([abs(v) for v in json_data["income"] + json_data["spend"] if v is not None] or [1.0])
        json_data["income"] = [round((v / peak) * 100, 2) for v in json_data["income"]]
        json_data["spend"] = [round((v / peak) * 100, 2) for v in json_data["spend"]]
        json_data["saved"] = [round((v / peak) * 100, 2) for v in json_data["saved"]]
        flat = [abs(v) for vals in pat_series["series"].values() for v in vals if v is not None]
        peak_p = max(flat or [1.0])
        pat_series["series"] = {
            k: [round((v / peak_p) * 100, 2) if v is not None else None for v in vals]
            for k, vals in pat_series["series"].items()
        }

    loan_prefill = {
        "person": request.query_params.get("loan_person") or "",
        "amount": request.query_params.get("loan_amount") or "",
        "date": request.query_params.get("loan_date") or "",
        "tx_id": request.query_params.get("loan_tx") or "",
    }

    return templates.TemplateResponse(
        "savings.html",
        {
            "request": request,
            "filter": f,
            "series": series,
            "json_data": json.dumps(json_data, default=str),
            "avg_rate": avg_rate,
            "avg_monthly_spend": avg_monthly_spend,
            "runway_months": runway_months,
            "pat": pat,
            "loans": loans,
            "snapshots": snapshots,
            "pat_series_json": json.dumps(pat_series, default=str),
            "loan_prefill": loan_prefill,
            "today": date.today().isoformat(),
        },
    )


@router.post("/snapshots")
async def snapshot_create(
    source: str = Form(...),
    asset: str = Form(""),
    quantity: str = Form(""),
    value_brl: str = Form(...),
    taken_at: str = Form(""),
    note: str = Form(""),
    s: AsyncSession = Depends(session_dep),
):
    qty = None
    if quantity.strip():
        try:
            qty = Decimal(quantity.replace(",", "."))
        except Exception:
            raise HTTPException(400, "invalid quantity")
    snap = BalanceSnapshot(
        source=source.strip().lower(),
        asset=(asset.strip().upper() or None),
        quantity=qty,
        value_brl=_parse_amount(value_brl),
        taken_at=_parse_date(taken_at),
        note=note.strip() or None,
    )
    s.add(snap)
    await s.commit()
    return RedirectResponse("/savings", status_code=303)


@router.post("/snapshots/{snapshot_id}/delete")
async def snapshot_delete(snapshot_id: int, s: AsyncSession = Depends(session_dep)):
    snap = await s.get(BalanceSnapshot, snapshot_id)
    if not snap:
        raise HTTPException(404)
    await s.delete(snap)
    await s.commit()
    return RedirectResponse("/savings", status_code=303)


@router.post("/loans")
async def loan_create(
    person: str = Form(...),
    principal: str = Form(...),
    direction: str = Form("lent"),
    loan_date: str = Form(""),
    note: str = Form(""),
    tx_id: str = Form(""),
    s: AsyncSession = Depends(session_dep),
):
    if direction not in ("lent", "borrowed"):
        raise HTTPException(400, "invalid direction")
    loan = Loan(
        person=person.strip(),
        direction=direction,
        principal=_parse_amount(principal),
        date=_parse_date(loan_date),
        note=note.strip() or None,
        tx_id=tx_id.strip() or None,
        status="open",
    )
    s.add(loan)
    await s.commit()
    return RedirectResponse("/savings", status_code=303)


@router.post("/loans/{loan_id}/payments")
async def loan_payment_create(
    loan_id: int,
    amount: str = Form(...),
    payment_date: str = Form(""),
    note: str = Form(""),
    tx_id: str = Form(""),
    s: AsyncSession = Depends(session_dep),
):
    loan = await s.get(Loan, loan_id)
    if not loan:
        raise HTTPException(404)
    already_paid = sum(
        (p.amount for p in (await s.execute(select(LoanPayment).where(LoanPayment.loan_id == loan_id))).scalars()),
        Decimal(0),
    )
    payment = LoanPayment(
        loan_id=loan_id,
        amount=_parse_amount(amount),
        date=_parse_date(payment_date),
        note=note.strip() or None,
        tx_id=tx_id.strip() or None,
    )
    s.add(payment)
    if already_paid + payment.amount >= loan.principal:
        loan.status = "repaid"
    await s.commit()
    return RedirectResponse("/savings", status_code=303)


@router.post("/loans/{loan_id}/status")
async def loan_status(
    loan_id: int,
    status: str = Form(...),
    s: AsyncSession = Depends(session_dep),
):
    if status not in ("open", "repaid", "written_off"):
        raise HTTPException(400, "invalid status")
    loan = await s.get(Loan, loan_id)
    if not loan:
        raise HTTPException(404)
    loan.status = status
    await s.commit()
    return RedirectResponse("/savings", status_code=303)

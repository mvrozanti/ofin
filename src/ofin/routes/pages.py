from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..analyzer import (
    accounts,
    by_category,
    by_merchant,
    investments,
    monthly_cashflow,
    pix_volumes,
)
from ..config import settings
from ..db import session_dep
from ..import_pdfs import import_pdf
from ..models import Account, Document, ParseWarning, Transaction

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _money(v) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"R$ {n:,.2f}".replace(",", "·").replace(".", ",").replace("·", ".")


templates.env.filters["money"] = _money


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, s: AsyncSession = Depends(session_dep)):
    since = date.today() - timedelta(days=30)
    cashflow = await monthly_cashflow(s, months=12)
    cats = await by_category(s, since=since, top=12)
    merchants = await by_merchant(s, since=since, top=12)
    accs = await accounts(s)
    pix = await pix_volumes(s, since=since)
    cc_balance, cdb_balance = await _latest_balances(s)
    patrimonio = (cc_balance or Decimal(0)) + (cdb_balance or Decimal(0))
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "cashflow": cashflow,
            "categories": cats,
            "merchants": merchants,
            "accounts": accs,
            "pix": pix,
            "since": since,
            "cc_balance": cc_balance,
            "cdb_balance": cdb_balance,
            "patrimonio": patrimonio,
        },
    )


async def _latest_balances(s: AsyncSession) -> tuple[Decimal | None, Decimal | None]:
    row = (
        await s.execute(
            select(Document.summary)
            .where(Document.document_type == "extrato")
            .order_by(Document.period_year.desc(), Document.period_month.desc())
            .limit(1)
        )
    ).scalars().first()
    if not row:
        return None, None
    cc_raw = row.get("saldo_cc_ledger")
    cc = Decimal(str(cc_raw)) if cc_raw is not None else None
    cdb = None
    cdb_list = row.get("cdb_snapshots") or []
    if cdb_list:
        v = cdb_list[-1].get("cdb_balance")
        if v is not None:
            cdb = Decimal(str(v))
    if cc is None:
        cb = row.get("closing_balance")
        if cb is not None:
            total = Decimal(str(cb))
            cc = total - (cdb or Decimal(0))
    return cc, cdb


@router.get("/documents", response_class=HTMLResponse)
async def documents_list(
    request: Request,
    s: AsyncSession = Depends(session_dep),
    type: str | None = Query(default=None, alias="type"),
    status: str | None = None,
):
    q = select(Document).order_by(Document.period_year.desc(), Document.period_month.desc(), Document.document_type)
    if type:
        q = q.where(Document.document_type == type)
    rows = (await s.execute(q)).scalars().all()

    sev_q = select(ParseWarning.document_id, ParseWarning.severity, func.count()).group_by(
        ParseWarning.document_id, ParseWarning.severity
    )
    sev_rows = (await s.execute(sev_q)).all()
    sev_map: dict[str, dict[str, int]] = {}
    for did, severity, n in sev_rows:
        sev_map.setdefault(did, {})[severity] = n

    docs = []
    total_err = total_warn = 0
    for d in rows:
        sm = sev_map.get(d.id, {})
        errs = sm.get("error", 0)
        warns = sm.get("warn", 0)
        total_err += errs
        total_warn += warns
        if status == "error" and errs == 0:
            continue
        if status == "warn" and warns == 0:
            continue
        if status == "ok" and (errs > 0 or warns > 0):
            continue
        totals = _doc_totals(d)
        docs.append(
            {
                "id": d.id,
                "document_type": d.document_type,
                "period_year": d.period_year,
                "period_month": d.period_month,
                "account_id": d.account_id,
                "totals": totals,
                "errors": errs,
                "warnings": warns,
                "filename": Path(d.source_path).name,
            }
        )

    return templates.TemplateResponse(
        "documents.html",
        {
            "request": request,
            "docs": docs,
            "total": len(rows),
            "errors": total_err,
            "warnings": total_warn,
            "type_filter": type,
            "status_filter": status,
        },
    )


def _doc_totals(d: Document) -> str:
    s = d.summary or {}
    if d.document_type == "extrato":
        opening = s.get("opening_balance")
        closing = s.get("closing_balance")
        return f"{_money(opening)} → {_money(closing)}"
    if d.document_type == "fatura":
        return _money(s.get("total"))
    return "—"


@router.get("/documents/{doc_id}", response_class=HTMLResponse)
async def document_detail(
    request: Request,
    doc_id: str,
    s: AsyncSession = Depends(session_dep),
):
    d = await s.get(Document, doc_id)
    if not d:
        raise HTTPException(status_code=404, detail="document not found")
    warnings = (await s.execute(select(ParseWarning).where(ParseWarning.document_id == doc_id))).scalars().all()
    txs = (
        await s.execute(
            select(Transaction)
            .where(Transaction.document_id == doc_id)
            .order_by(Transaction.date, Transaction.id)
        )
    ).scalars().all()

    summary_kv, recon = _summary_and_recon(d, txs)

    return templates.TemplateResponse(
        "document_detail.html",
        {
            "request": request,
            "doc": d,
            "warnings": warnings,
            "transactions": txs,
            "summary_kv": summary_kv,
            "recon": recon,
        },
    )


def _summary_and_recon(d: Document, txs: list[Transaction]) -> tuple[list[tuple[str, str]], list[tuple[str, str, str, bool]]]:
    s = d.summary or {}
    kv: list[tuple[str, str]] = []
    recon: list[tuple[str, str, str, bool]] = []

    if d.document_type == "extrato":
        kv.extend([
            ("agência", s.get("agency") or "—"),
            ("conta", s.get("account") or "—"),
            ("período", f"{s.get('period_year')}-{int(s.get('period_month') or 0):02d}"),
            ("saldo abertura", _money(s.get("opening_balance"))),
            ("saldo fechamento", _money(s.get("closing_balance"))),
            ("entradas (header)", _money(s.get("entradas_total"))),
            ("saídas (header)", _money(s.get("saidas_total"))),
            ("sweep CDB entrada", _money(s.get("sweep_credit_total"))),
            ("sweep CDB saída", _money(s.get("sweep_debit_total"))),
            ("transações", str(s.get("n_transactions") or 0)),
        ])
        real_credit = sum((t.amount for t in txs if t.amount > 0 and not (t.raw or {}).get("is_sweep")), Decimal(0))
        real_debit = sum((-t.amount for t in txs if t.amount < 0 and not (t.raw or {}).get("is_sweep")), Decimal(0))
        sweep_credit = sum((t.amount for t in txs if t.amount > 0 and (t.raw or {}).get("is_sweep")), Decimal(0))
        sweep_debit = sum((-t.amount for t in txs if t.amount < 0 and (t.raw or {}).get("is_sweep")), Decimal(0))
        recon.append(_recon_row("Σ entradas", s.get("entradas_total"), real_credit))
        recon.append(_recon_row("Σ saídas", s.get("saidas_total"), real_debit))
        recon.append(_recon_row("Σ sweep credit", s.get("sweep_credit_total"), sweep_credit))
        recon.append(_recon_row("Σ sweep debit", s.get("sweep_debit_total"), sweep_debit))
        if s.get("opening_balance") is not None and s.get("closing_balance") is not None:
            opening = Decimal(s["opening_balance"])
            computed = opening + real_credit - real_debit
            recon.append(_recon_row("opening + flows", s.get("closing_balance"), computed))

    elif d.document_type == "fatura":
        kv.extend([
            ("cartão", f"{s.get('card_brand')} {s.get('card_last4')}"),
            ("postagem", s.get("posting_date") or "—"),
            ("vencimento", s.get("due_date") or "—"),
            ("emissão", s.get("emission_date") or "—"),
            ("fatura anterior", _money(s.get("previous_total"))),
            ("pagamento", _money(s.get("payment_amount"))),
            ("data pagamento", s.get("payment_date") or "—"),
            ("saldo financiado", _money(s.get("financed_balance"))),
            ("lançamentos atuais", _money(s.get("current_charges"))),
            ("total desta fatura", _money(s.get("total"))),
            ("limite total", _money(s.get("limit_total"))),
            ("limite disponível", _money(s.get("limit_available"))),
            ("limite utilizado", _money(s.get("limit_used"))),
        ])
        sum_dom = sum(
            (t.amount for t in txs if not (t.credit_card_metadata or {}).get("is_international") and (t.credit_card_metadata or {}).get("kind") != "payment"),
            Decimal(0),
        )
        sum_intl = sum(
            (t.amount for t in txs if (t.credit_card_metadata or {}).get("is_international")),
            Decimal(0),
        )
        sum_pay = sum(
            (t.amount for t in txs if (t.credit_card_metadata or {}).get("kind") == "payment"),
            Decimal(0),
        )
        iof = Decimal(s.get("iof_repasse") or 0)
        recon.append(_recon_row("Σ lançamentos nacionais", s.get("domestic_subtotal"), sum_dom))
        recon.append(_recon_row("Σ lançamentos internacionais", s.get("international_subtotal"), sum_intl))
        recon.append(_recon_row("Σ pagamentos", s.get("payment_amount"), sum_pay))
        if s.get("current_charges") is not None:
            recon.append(_recon_row("dom + intl + IOF", s.get("current_charges"), sum_dom + sum_intl + iof))

    return kv, recon


def _recon_row(label: str, expected, computed) -> tuple[str, str, str, bool]:
    if expected is None:
        return (label, "—", _money(computed), True)
    try:
        ok = Decimal(str(expected)) == Decimal(str(computed))
    except Exception:
        ok = False
    return (label, _money(expected), _money(computed), ok)


@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    return templates.TemplateResponse("import.html", {"request": request, "results": None})


@router.post("/import", response_class=HTMLResponse)
async def import_submit(
    request: Request,
    files: list[UploadFile] = File(...),
    s: AsyncSession = Depends(session_dep),
):
    upload_dir = Path(settings().upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for f in files:
        target = upload_dir / f.filename
        with target.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        try:
            r = await import_pdf(s, target)
            await s.commit()
            r["filename"] = f.filename
        except Exception as e:
            await s.rollback()
            r = {"filename": f.filename, "type": "error", "tx": 0, "warnings": 1, "doc_id": "", "error": str(e)}
        results.append(r)
    return templates.TemplateResponse("import.html", {"request": request, "results": results})


@router.get("/breakdown", response_class=HTMLResponse)
async def breakdown_page(request: Request, s: AsyncSession = Depends(session_dep)):
    docs = (
        await s.execute(
            select(Document)
            .where(Document.document_type == "extrato")
            .order_by(Document.period_year.desc(), Document.period_month.desc())
        )
    ).scalars().all()

    from sqlalchemy import case, func as sqlfunc, literal_column

    rows = []
    for d in docs:
        if not d.period_year or not d.period_month:
            continue
        period = f"{d.period_year:04d}-{d.period_month:02d}"
        sums = (
            await s.execute(
                select(
                    sqlfunc.sum(
                        case((Transaction.amount > 0, Transaction.amount), else_=0)
                    ).label("all_in"),
                    sqlfunc.sum(
                        case((Transaction.amount < 0, -Transaction.amount), else_=0)
                    ).label("all_out"),
                    sqlfunc.sum(
                        case(
                            (
                                (Transaction.amount > 0)
                                & (sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False)  # noqa: E712
                                & (sqlfunc.coalesce(Transaction.raw["is_internal"].as_boolean(), False) == False),  # noqa: E712
                                Transaction.amount,
                            ),
                            else_=0,
                        )
                    ).label("real_in"),
                    sqlfunc.sum(
                        case(
                            (
                                (Transaction.amount < 0)
                                & (sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False)  # noqa: E712
                                & (sqlfunc.coalesce(Transaction.raw["is_internal"].as_boolean(), False) == False),  # noqa: E712
                                -Transaction.amount,
                            ),
                            else_=0,
                        )
                    ).label("real_out"),
                    sqlfunc.sum(
                        case(
                            (
                                (Transaction.amount > 0)
                                & (sqlfunc.coalesce(Transaction.raw["is_internal"].as_boolean(), False) == True)  # noqa: E712
                                & (sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False),  # noqa: E712
                                Transaction.amount,
                            ),
                            else_=0,
                        )
                    ).label("internal_in"),
                    sqlfunc.sum(
                        case(
                            (
                                (Transaction.amount < 0)
                                & (sqlfunc.coalesce(Transaction.raw["is_internal"].as_boolean(), False) == True)  # noqa: E712
                                & (sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False),  # noqa: E712
                                -Transaction.amount,
                            ),
                            else_=0,
                        )
                    ).label("internal_out"),
                ).where(
                    Transaction.document_id == d.id,
                    sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False,  # noqa: E712
                )
            )
        ).one()
        sm = d.summary or {}
        h_in = Decimal(str(sm.get("entradas_total") or 0))
        h_out = Decimal(str(sm.get("saidas_total") or 0))
        parser_in = sums.all_in or Decimal(0)
        parser_out = sums.all_out or Decimal(0)
        rows.append(
            {
                "period": period,
                "doc_id": d.id,
                "header_in": h_in,
                "header_out": h_out,
                "parser_in": parser_in,
                "parser_out": parser_out,
                "real_in": sums.real_in or Decimal(0),
                "real_out": sums.real_out or Decimal(0),
                "internal_in": sums.internal_in or Decimal(0),
                "internal_out": sums.internal_out or Decimal(0),
                "in_match": h_in == parser_in,
                "out_match": h_out == parser_out,
            }
        )

    income_cats = await _category_summary(s, sign="credit", internal=False)
    internal_cats = await _category_summary(s, sign="credit", internal=True)
    spend_cats = await _category_summary(s, sign="debit", internal=False)
    return templates.TemplateResponse(
        "breakdown.html",
        {
            "request": request,
            "rows": rows,
            "income_cats": income_cats,
            "internal_cats": internal_cats,
            "spend_cats": spend_cats,
        },
    )


async def _category_summary(s: AsyncSession, *, sign: str, internal: bool) -> list[dict]:
    from sqlalchemy import case, func as sqlfunc
    sign_filter = Transaction.amount > 0 if sign == "credit" else Transaction.amount < 0
    q = (
        select(
            Transaction.category,
            sqlfunc.sum(Transaction.amount).label("total"),
            sqlfunc.count().label("n"),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Account.type == "BANK",
            sign_filter,
            sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False,  # noqa: E712
            Transaction.raw["is_internal"].as_boolean() == (True if internal else False),  # noqa: E712
        )
        .group_by(Transaction.category)
    )
    rows = (await s.execute(q)).all()
    out = [
        {"category": cat or "uncategorized", "total": abs(Decimal(str(total or 0))), "n": int(n)}
        for cat, total, n in rows
    ]
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


@router.get("/sankey", response_class=HTMLResponse)
async def sankey_page(
    request: Request,
    s: AsyncSession = Depends(session_dep),
    months: int = 12,
):
    from sqlalchemy import case, func as sqlfunc
    import json
    today = date.today()
    if months == 0:
        since = date(2000, 1, 1)
    else:
        ms = today.month - months
        year_shift = (-ms) // 12 + 1 if ms <= 0 else 0
        new_month = ms + 12 * year_shift
        since = date(today.year - year_shift, new_month or 12, 1)
    until = today

    income_q = (
        select(
            Transaction.mega,
            Transaction.category,
            sqlfunc.sum(Transaction.amount).label("total"),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Account.type == "BANK",
            Transaction.amount > 0,
            Transaction.date >= since,
            sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False,  # noqa: E712
            sqlfunc.coalesce(Transaction.raw["is_internal"].as_boolean(), False) == False,  # noqa: E712
        )
        .group_by(Transaction.mega, Transaction.category)
    )
    incomes = (await s.execute(income_q)).all()

    spend_bank_q = (
        select(
            Transaction.mega,
            Transaction.category,
            sqlfunc.sum(-Transaction.amount).label("total"),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Account.type == "BANK",
            Transaction.amount < 0,
            Transaction.date >= since,
            sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False,  # noqa: E712
            sqlfunc.coalesce(Transaction.raw["is_internal"].as_boolean(), False) == False,  # noqa: E712
        )
        .group_by(Transaction.mega, Transaction.category)
    )
    spend_bank = (await s.execute(spend_bank_q)).all()

    spend_credit_q = (
        select(
            Transaction.mega,
            Transaction.category,
            sqlfunc.sum(Transaction.amount).label("total"),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Account.type == "CREDIT",
            Transaction.amount > 0,
            Transaction.date >= since,
        )
        .group_by(Transaction.mega, Transaction.category)
    )
    spend_credit = (await s.execute(spend_credit_q)).all()

    income_data = [(mega or "renda", cat or "outros", abs(Decimal(str(total or 0)))) for mega, cat, total in incomes if total]
    income_data.sort(key=lambda x: x[2], reverse=True)
    spend_bank_data = [(mega or "outros", cat or "outros", abs(Decimal(str(total or 0)))) for mega, cat, total in spend_bank if total]
    spend_credit_data = [(mega or "outros", cat or "outros", abs(Decimal(str(total or 0)))) for mega, cat, total in spend_credit if total]

    total_income = sum((v for _, _, v in income_data), Decimal(0))
    total_spend_bank = sum((v for _, _, v in spend_bank_data), Decimal(0))
    total_spend_credit = sum((v for _, _, v in spend_credit_data), Decimal(0))
    total_spend = total_spend_bank + total_spend_credit
    net = total_income - total_spend

    nodes: list[dict] = []
    seen: set[str] = set()

    def add(name):
        if name not in seen:
            nodes.append({"name": name})
            seen.add(name)

    add("CC Itaú")
    if total_spend_credit > 0:
        add("Cartão Platinum")

    income_megas_sum: dict[str, Decimal] = {}
    for mega, cat, v in income_data:
        income_megas_sum[mega] = income_megas_sum.get(mega, Decimal(0)) + v
    for mega in income_megas_sum:
        add(f"renda:{mega}")
    for mega, cat, v in income_data:
        add(f"in:{mega}/{cat}")

    bank_megas_sum: dict[str, Decimal] = {}
    for mega, cat, v in spend_bank_data:
        bank_megas_sum[mega] = bank_megas_sum.get(mega, Decimal(0)) + v
    for mega in bank_megas_sum:
        add(f"out:{mega}")
    for mega, cat, v in spend_bank_data:
        add(f"sub:{mega}/{cat}")

    card_megas_sum: dict[str, Decimal] = {}
    for mega, cat, v in spend_credit_data:
        card_megas_sum[mega] = card_megas_sum.get(mega, Decimal(0)) + v
    for mega in card_megas_sum:
        add(f"card:{mega}")
    for mega, cat, v in spend_credit_data:
        add(f"csub:{mega}/{cat}")

    links: list[dict] = []
    for mega, cat, v in income_data:
        if v > 0:
            links.append({"source": f"in:{mega}/{cat}", "target": f"renda:{mega}", "value": float(v)})
    for mega, total in income_megas_sum.items():
        if total > 0:
            links.append({"source": f"renda:{mega}", "target": "CC Itaú", "value": float(total)})
    for mega, total in bank_megas_sum.items():
        if total > 0:
            links.append({"source": "CC Itaú", "target": f"out:{mega}", "value": float(total)})
    for mega, cat, v in spend_bank_data:
        if v > 0:
            links.append({"source": f"out:{mega}", "target": f"sub:{mega}/{cat}", "value": float(v)})
    if total_spend_credit > 0:
        links.append({"source": "CC Itaú", "target": "Cartão Platinum", "value": float(total_spend_credit)})
    for mega, total in card_megas_sum.items():
        if total > 0:
            links.append({"source": "Cartão Platinum", "target": f"card:{mega}", "value": float(total)})
    for mega, cat, v in spend_credit_data:
        if v > 0:
            links.append({"source": f"card:{mega}", "target": f"csub:{mega}/{cat}", "value": float(v)})

    sankey = {"nodes": nodes, "links": links}

    income_cats_view = []
    for mega, cat, v in income_data:
        income_cats_view.append({"name": f"{mega} / {cat}", "value": v, "pct": (float(v) / float(total_income)) if total_income else 0})
    spend_cats_total = []
    for mega, cat, v in spend_bank_data:
        spend_cats_total.append((f"{mega} / {cat} (cc)", v))
    for mega, cat, v in spend_credit_data:
        spend_cats_total.append((f"{mega} / {cat} (cartão)", v))
    spend_cats_total.sort(key=lambda x: x[1], reverse=True)
    spend_cats_view = [
        {
            "name": cat,
            "value": v,
            "pct_spend": (float(v) / float(total_spend)) if total_spend else 0,
            "pct_income": (float(v) / float(total_income)) if total_income else 0,
        }
        for cat, v in spend_cats_total
    ]

    return templates.TemplateResponse(
        "sankey.html",
        {
            "request": request,
            "months": months,
            "since": since,
            "until": until,
            "sankey_json": json.dumps(sankey, default=str),
            "income_cats": income_cats_view,
            "spend_cats": spend_cats_view,
            "total_income": total_income,
            "total_spend": total_spend,
            "net": net,
        },
    )


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, s: AsyncSession = Depends(session_dep)):
    import json
    from ..analytics import detect_subscriptions, fx_by_currency, fx_by_month, net_worth_series

    subs = await detect_subscriptions(s, min_months=3, tolerance_pct=Decimal("0.20"))
    fx_ccy = await fx_by_currency(s)
    fx_mo = await fx_by_month(s)
    nw = await net_worth_series(s)

    nw_json = {
        "months": [p.month for p in nw],
        "cc": [float(p.cc_balance) if p.cc_balance is not None else None for p in nw],
        "cdb": [float(p.cdb_balance) if p.cdb_balance is not None else None for p in nw],
        "total": [float(p.total) if p.total is not None else None for p in nw],
    }
    is_json = {
        "months": [p.month for p in nw],
        "income": [float(p.real_income) for p in nw],
        "spend": [float(p.real_spend) for p in nw],
        "net": [float(p.net) for p in nw],
    }
    usd_months = sorted({f.month for f in fx_mo if f.currency == "USD"})
    brl_by_mo = {f.month: float(f.brl_total) for f in fx_mo if f.currency == "USD"}
    rate_by_mo = {f.month: float(f.avg_rate) for f in fx_mo if f.currency == "USD"}
    fx_json = {
        "months": usd_months,
        "brl": [brl_by_mo.get(m, 0) for m in usd_months],
        "rate": [rate_by_mo.get(m, None) for m in usd_months],
    }

    return templates.TemplateResponse(
        "analytics.html",
        {
            "request": request,
            "subscriptions": subs,
            "fx_by_ccy": fx_ccy,
            "nw_json": json.dumps(nw_json, default=str),
            "is_json": json.dumps(is_json, default=str),
            "fx_json": json.dumps(fx_json, default=str),
        },
    )


@router.get("/transactions", response_class=HTMLResponse)
async def transactions_page(
    request: Request,
    s: AsyncSession = Depends(session_dep),
    limit: int = 200,
):
    rows = (
        await s.execute(select(Transaction).order_by(desc(Transaction.date)).limit(limit))
    ).scalars().all()
    return templates.TemplateResponse(
        "transactions.html", {"request": request, "transactions": rows, "limit": limit}
    )

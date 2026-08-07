from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, desc, func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import (
    DARK_MEGAS,
    dark_matter,
    latest_tx_date,
    sankey_datasets,
)
from ..sankey import build_sankey, pretty_cat, pretty_mega
from ..analyzer import accounts as accounts_q
from ..config import settings
from ..db import session_dep
from ..filters import Filter
from ..models import Document, ParseWarning, Transaction, TransactionOverride

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


from ..template_filters import register as _register_filters
from ..masking import fmt_money

_register_filters(templates)
templates.env.globals["read_only"] = settings().read_only


def _money(v) -> str:
    return fmt_money(v)


async def _flow_totals(s: AsyncSession, f: Filter) -> tuple[Decimal, Decimal]:
    from ..analytics import income_cond, spend_amount_abs, spend_cond
    inc_q = select(sqlfunc.sum(Transaction.amount))
    inc_q = f.apply_to_tx(inc_q).where(income_cond())
    out_q = select(sqlfunc.sum(spend_amount_abs()))
    out_q = f.apply_to_tx(out_q).where(spend_cond())
    cur_in = Decimal(str((await s.execute(inc_q)).scalar() or 0))
    cur_out = Decimal(str((await s.execute(out_q)).scalar() or 0))
    return cur_in, cur_out


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, s: AsyncSession = Depends(session_dep)):
    f = Filter.from_request(request, await latest_tx_date(s))

    cur_in, cur_out = await _flow_totals(s, f)
    prev_from, prev_to = f.comparison_range()
    if prev_from is None or prev_to is None:
        if f.date_from and f.date_to:
            span = (f.date_to - f.date_from).days
            prev_from = f.date_from - timedelta(days=span + 1)
            prev_to = f.date_from - timedelta(days=1)
    prev_in, prev_out = (Decimal(0), Decimal(0))
    if prev_from and prev_to:
        pf = Filter(date_from=prev_from, date_to=prev_to, preset="custom",
                    accounts=f.accounts, account_types=f.account_types, megas=f.megas)
        prev_in, prev_out = await _flow_totals(s, pf)

    accs = await accounts_q(s)
    dark_n, dark_total = await dark_matter(s, f)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "filter": f,
            "cur_in": cur_in,
            "cur_out": cur_out,
            "prev_in": prev_in,
            "prev_out": prev_out,
            "saved": cur_in - cur_out,
            "saved_prev": prev_in - prev_out,
            "savings_rate": (float((cur_in - cur_out) / cur_in) if cur_in else 0.0),
            "accounts": accs,
            "dark_n": dark_n,
            "dark_total": dark_total,
        },
    )


@router.get("/documents", response_class=HTMLResponse)
async def documents_list(
    request: Request,
    s: AsyncSession = Depends(session_dep),
    type: str | None = Query(default=None, alias="type"),
    status: str | None = None,
):
    if not request.state.auth.authed:
        raise HTTPException(status_code=403, detail="login required")
    q = select(Document).order_by(Document.period_year.desc(), Document.period_month.desc(), Document.document_type)
    if type:
        q = q.where(Document.document_type == type)
    rows = (await s.execute(q)).scalars().all()

    sev_q = select(ParseWarning.document_id, ParseWarning.severity, sqlfunc.count()).group_by(
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
            "filter": Filter.from_request(request),
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
    if not request.state.auth.authed:
        raise HTTPException(status_code=403, detail="login required")
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
            "filter": Filter.from_request(request),
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


@router.get("/sankey", response_class=HTMLResponse)
async def sankey_page(
    request: Request,
    s: AsyncSession = Depends(session_dep),
):
    f = Filter.from_request(request, await latest_tx_date(s))
    authed = request.state.auth.authed

    bounds = (await s.execute(select(sqlfunc.min(Transaction.date), sqlfunc.max(Transaction.date)))).one()
    min_date, max_date = bounds[0], bounds[1]
    months_axis: list[str] = []
    if min_date and max_date:
        y, m = min_date.year, min_date.month
        while (y, m) <= (max_date.year, max_date.month):
            months_axis.append(f"{y:04d}-{m:02d}")
            m += 1
            if m == 13:
                m = 1
                y += 1

    income_data, spend_data = await sankey_datasets(s, f, authed=authed)
    sankey = build_sankey(income_data, spend_data, authed=authed)

    total_income = sum((v for _, _, v in income_data), Decimal(0))
    total_spend = sum((v for _, _, v in spend_data), Decimal(0))
    net = total_income - total_spend

    income_cats_view = [
        {"mega": mega, "category": cat, "name": pretty_cat(mega, cat), "value": v,
         "pct": (float(v) / float(total_income)) if total_income else 0}
        for mega, cat, v in sorted(income_data, key=lambda x: x[2], reverse=True)
    ]
    spend_cats_view = [
        {"mega": mega, "category": cat, "mega_label": pretty_mega(mega), "cat_label": pretty_cat(mega, cat),
         "value": v,
         "pct_spend": (float(v) / float(total_spend)) if total_spend else 0,
         "pct_income": (float(v) / float(total_income)) if total_income else 0}
        for mega, cat, v in sorted(spend_data, key=lambda x: x[2], reverse=True)
    ]

    return templates.TemplateResponse(
        "sankey.html",
        {
            "request": request,
            "filter": f,
            "sankey_json": json.dumps(sankey, default=str),
            "income_cats": income_cats_view,
            "spend_cats": spend_cats_view,
            "total_income": total_income,
            "total_spend": total_spend,
            "net": net,
            "months_axis": months_axis,
            "min_date": min_date.isoformat() if min_date else None,
            "max_date": max_date.isoformat() if max_date else None,
        },
    )


@router.get("/transactions", response_class=HTMLResponse)
async def transactions_page(
    request: Request,
    s: AsyncSession = Depends(session_dep),
    limit: int = 500,
    offset: int = 0,
    sort: str = "date",
):
    f = Filter.from_request(request, await latest_tx_date(s))
    if sort == "amount":
        q = select(Transaction).order_by(desc(sqlfunc.abs(Transaction.amount)), desc(Transaction.date))
    else:
        q = select(Transaction).order_by(desc(Transaction.date), desc(Transaction.id))
    q = f.apply_to_tx(q)
    if not f.include_sweep:
        q = q.where(sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False)  # noqa: E712
    if not f.include_internal:
        q = q.where(sqlfunc.coalesce(Transaction.mega, "") != "internal")
    total = int((await s.execute(q.with_only_columns(sqlfunc.count()).order_by(None))).scalar() or 0)
    rows = (await s.execute(q.limit(limit).offset(offset))).scalars().all()
    overrides = set(
        (await s.execute(select(TransactionOverride.tx_id).where(TransactionOverride.tx_id.in_([r.id for r in rows])))).scalars().all()
    ) if rows else set()

    base_qs = [(k, v) for k, v in request.query_params.multi_items() if k not in ("offset", "limit")]

    def with_offset(o: int) -> str:
        from urllib.parse import urlencode
        parts = base_qs + [("offset", str(o))]
        return urlencode(parts)

    from urllib.parse import urlencode as _urlencode
    sort_toggle_url = _urlencode(
        [(k, v) for k, v in base_qs if k != "sort"]
        + ([("sort", "amount")] if sort != "amount" else [])
    )

    prev_url = with_offset(max(offset - limit, 0)) if offset > 0 else None
    next_url = with_offset(offset + limit) if offset + limit < total else None
    triage = bool(set(f.megas) & set(DARK_MEGAS)) or (not f.megas and any(
        (r.mega or "outros") in DARK_MEGAS for r in rows
    ))
    return templates.TemplateResponse(
        "transactions.html",
        {
            "request": request,
            "filter": f,
            "transactions": rows,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "sort_toggle_url": sort_toggle_url,
            "total": total,
            "overrides": overrides,
            "prev_url": prev_url,
            "next_url": next_url,
            "triage": triage,
            "dark_megas": list(DARK_MEGAS),
        },
    )


@router.get("/transactions.csv")
async def transactions_csv(request: Request, s: AsyncSession = Depends(session_dep)):
    if not request.state.auth.authed:
        raise HTTPException(status_code=403, detail="login required")
    f = Filter.from_request(request, await latest_tx_date(s))
    q = select(Transaction).order_by(desc(Transaction.date))
    q = f.apply_to_tx(q)
    rows = (await s.execute(q)).scalars().all()

    def stream():
        yield "id,date,account,amount,currency,mega,category,description\n"
        for t in rows:
            desc = (t.description or "").replace('"', "''")
            yield f'{t.id},{t.date.isoformat()},{t.account_id},{t.amount},{t.currency_code or ""},{t.mega or ""},{t.category or ""},"{desc}"\n'

    return StreamingResponse(stream(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=ofin-transactions.csv"})



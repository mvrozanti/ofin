from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, desc, func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import (
    category_movers,
    dark_matter,
    latest_tx_date,
)
from ..analyzer import accounts as accounts_q
from ..config import settings
from ..db import session_dep
from ..filters import Filter
from ..import_pdfs import import_pdf
from ..models import Account, Document, ParseWarning, Transaction, TransactionOverride

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


from ..template_filters import register as _register_filters
from ..masking import fmt_money

_register_filters(templates)
templates.env.globals["read_only"] = settings().read_only


def _money(v) -> str:
    return fmt_money(v)


def _is_not_internal_or_sweep():
    return (
        sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False,  # noqa: E712
        sqlfunc.coalesce(Transaction.mega, "") != "internal",
    )


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

    movers = [m for m in await category_movers(s, f, top=10) if abs(m.delta) >= 100][:6]
    authed_dash = request.state.auth.authed
    if not authed_dash:
        for m in movers:
            if m.mega == "pessoas":
                m.category = "•••"
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
            "movers": movers,
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


@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    return templates.TemplateResponse("import.html", {"request": request, "filter": Filter.from_request(request), "results": None})


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
    return templates.TemplateResponse("import.html", {"request": request, "filter": Filter.from_request(request), "results": results})


@router.get("/sankey", response_class=HTMLResponse)
async def sankey_page(
    request: Request,
    s: AsyncSession = Depends(session_dep),
):
    f = Filter.from_request(request, await latest_tx_date(s))
    authed_view = request.state.auth.authed

    def _anon_cat(mega, category):
        if not authed_view and mega == "pessoas":
            return "•••"
        return category

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

    inc_q = (
        select(Transaction.mega, Transaction.category, sqlfunc.sum(Transaction.amount))
        .where(Transaction.amount > 0)
        .group_by(Transaction.mega, Transaction.category)
    )
    inc_q = f.apply_to_tx(inc_q).where(
        Account.type == "BANK",
        *_is_not_internal_or_sweep(),
    )
    incomes = (await s.execute(inc_q)).all()

    spend_bank_q = (
        select(Transaction.mega, Transaction.category, sqlfunc.sum(-Transaction.amount))
        .where(Transaction.amount < 0)
        .group_by(Transaction.mega, Transaction.category)
    )
    spend_bank_q = f.apply_to_tx(spend_bank_q).where(
        Account.type == "BANK",
        *_is_not_internal_or_sweep(),
    )
    spend_bank = (await s.execute(spend_bank_q)).all()

    spend_credit_q = (
        select(Transaction.mega, Transaction.category, sqlfunc.sum(Transaction.amount))
        .where(Transaction.amount > 0)
        .group_by(Transaction.mega, Transaction.category)
    )
    spend_credit_q = f.apply_to_tx(spend_credit_q).where(Account.type == "CREDIT")
    spend_credit = (await s.execute(spend_credit_q)).all()

    def _collapse(rows):
        agg: dict[tuple[str, str], Decimal] = {}
        for mega, cat, total in rows:
            if not total:
                continue
            m = mega or "outros"
            c = _anon_cat(m, cat or "outros")
            agg[(m, c)] = agg.get((m, c), Decimal(0)) + abs(Decimal(str(total)))
        return [(m, c, v) for (m, c), v in agg.items()]

    income_data = _collapse((mega or "renda", cat, total) for mega, cat, total in incomes)
    income_data.sort(key=lambda x: x[2], reverse=True)
    spend_bank_data = _collapse(spend_bank)
    spend_credit_data = _collapse(spend_credit)

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

    sankey = {"nodes": nodes, "links": links}

    income_cats_view = []
    for mega, cat, v in income_data:
        income_cats_view.append({"mega": mega, "category": cat, "name": f"{mega} / {cat}", "value": v, "pct": (float(v) / float(total_income)) if total_income else 0})
    spend_cats_total = []
    for mega, cat, v in spend_bank_data:
        spend_cats_total.append((mega, cat, "cc", v))
    for mega, cat, v in spend_credit_data:
        spend_cats_total.append((mega, cat, "cartão", v))
    spend_cats_total.sort(key=lambda x: x[3], reverse=True)
    spend_cats_view = [
        {
            "mega": mega,
            "category": cat,
            "src": src,
            "name": f"{mega} / {cat} ({src})",
            "value": v,
            "pct_spend": (float(v) / float(total_spend)) if total_spend else 0,
            "pct_income": (float(v) / float(total_income)) if total_income else 0,
        }
        for mega, cat, src, v in spend_cats_total
    ]

    authed = request.state.auth.authed
    if not authed:
        from ..masking import proportions
        link_vals = [lk["value"] for lk in sankey["links"]]
        scaled = proportions(link_vals, total=100.0)
        for lk, v in zip(sankey["links"], scaled):
            lk["value"] = v

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



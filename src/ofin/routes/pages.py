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
    anomalies_by_mega,
    budget_progress,
    cashflow_waterfall,
    category_movers,
    daily_spend_calendar,
    detect_subscriptions,
    fx_by_currency,
    fx_by_month,
    income_mix,
    latest_itau_balances,
    merchant_profiles,
    net_worth_series,
    savings_rate,
)
from ..analyzer import accounts as accounts_q
from ..config import settings
from ..db import session_dep
from ..filters import Filter
from ..import_pdfs import import_pdf
from ..models import Account, Budget, Document, Goal, ParseWarning, Transaction, TransactionOverride

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


async def _uncategorized_count(s: AsyncSession) -> int:
    q = select(sqlfunc.count()).where(
        (Transaction.mega.is_(None)) | (Transaction.mega.in_(["outros", "uncategorized"]))
    )
    return int((await s.execute(q)).scalar() or 0)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, s: AsyncSession = Depends(session_dep)):
    f = Filter.from_request(request)

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

    cc_balance, cdb_balance = await latest_itau_balances(s)
    patrimonio = (cc_balance or Decimal(0)) + (cdb_balance or Decimal(0))

    waterfall = await cashflow_waterfall(s, f)
    movers = await category_movers(s, f, top=10)
    authed_dash = request.state.auth.authed
    if not authed_dash:
        for m in movers:
            if m.mega == "pessoas":
                m.category = "•••"
    accs = await accounts_q(s)
    uncat = await _uncategorized_count(s)

    budgets_q = (await s.execute(select(Budget).where(Budget.enabled == True))).scalars().all()  # noqa: E712
    bprog = await budget_progress(s, list(budgets_q), f)

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
            "cc_balance": cc_balance,
            "cdb_balance": cdb_balance,
            "patrimonio": patrimonio,
            "waterfall": waterfall,
            "wf_max": max((abs(w.delta) for w in waterfall), default=Decimal(1)),
            "movers": movers,
            "accounts": accs,
            "uncategorized": uncat,
            "budget_progress": bprog[:6],
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


@router.get("/breakdown", response_class=HTMLResponse)
async def breakdown_page(request: Request, s: AsyncSession = Depends(session_dep)):
    f = Filter.from_request(request)
    docs = (
        await s.execute(
            select(Document)
            .where(Document.document_type == "extrato")
            .order_by(Document.period_year.desc(), Document.period_month.desc())
        )
    ).scalars().all()

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
                                & (sqlfunc.coalesce(Transaction.mega, "") != "internal"),
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
                                & (sqlfunc.coalesce(Transaction.mega, "") != "internal"),
                                -Transaction.amount,
                            ),
                            else_=0,
                        )
                    ).label("real_out"),
                    sqlfunc.sum(
                        case(
                            (
                                (Transaction.amount > 0)
                                & (sqlfunc.coalesce(Transaction.mega, "") == "internal")
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
                                & (sqlfunc.coalesce(Transaction.mega, "") == "internal")
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

    for i, r in enumerate(rows):
        prev = rows[i + 1] if i + 1 < len(rows) else None
        yoy = next((rr for rr in rows if rr["period"][:4] == str(int(r["period"][:4]) - 1) and rr["period"][5:] == r["period"][5:]), None)
        r["delta_out_prev"] = r["real_out"] - prev["real_out"] if prev else None
        r["delta_out_yoy"] = r["real_out"] - yoy["real_out"] if yoy else None
        r["delta_in_prev"] = r["real_in"] - prev["real_in"] if prev else None
        r["delta_in_yoy"] = r["real_in"] - yoy["real_in"] if yoy else None

    income_cats = await _category_summary(s, sign="credit", internal=False)
    internal_cats = await _category_summary(s, sign="credit", internal=True)
    spend_cats = await _category_summary(s, sign="debit", internal=False)
    if not request.state.auth.authed:
        for lst in (income_cats, internal_cats, spend_cats):
            for c in lst:
                if c.get("mega") == "pessoas":
                    c["category"] = "•••"
    return templates.TemplateResponse(
        "breakdown.html",
        {
            "request": request,
            "filter": f,
            "rows": rows,
            "income_cats": income_cats,
            "internal_cats": internal_cats,
            "spend_cats": spend_cats,
        },
    )


async def _category_summary(s: AsyncSession, *, sign: str, internal: bool) -> list[dict]:
    sign_filter = Transaction.amount > 0 if sign == "credit" else Transaction.amount < 0
    q = (
        select(
            Transaction.mega,
            Transaction.category,
            sqlfunc.sum(Transaction.amount).label("total"),
            sqlfunc.count().label("n"),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Account.type == "BANK",
            sign_filter,
            sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False,  # noqa: E712
            (sqlfunc.coalesce(Transaction.mega, "") == "internal") == (True if internal else False),
        )
        .group_by(Transaction.mega, Transaction.category)
    )
    rows = (await s.execute(q)).all()
    out = [
        {"mega": mega or "—", "category": cat or "uncategorized", "total": abs(Decimal(str(total or 0))), "n": int(n)}
        for mega, cat, total, n in rows
    ]
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


@router.get("/sankey", response_class=HTMLResponse)
async def sankey_page(
    request: Request,
    s: AsyncSession = Depends(session_dep),
):
    f = Filter.from_request(request)
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


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, s: AsyncSession = Depends(session_dep)):
    f = Filter.from_request(request)

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
    sr = await savings_rate(s, f)
    sr_json = {
        "months": [p.month for p in sr],
        "rate": [round(p.rate * 100, 2) for p in sr],
    }
    usd_months = sorted({fm.month for fm in fx_mo if fm.currency == "USD"})
    brl_by_mo = {fm.month: float(fm.brl_total) for fm in fx_mo if fm.currency == "USD"}
    rate_by_mo = {fm.month: float(fm.avg_rate) for fm in fx_mo if fm.currency == "USD"}
    fx_json = {
        "months": usd_months,
        "brl": [brl_by_mo.get(m, 0) for m in usd_months],
        "rate": [rate_by_mo.get(m, None) for m in usd_months],
    }

    authed = request.state.auth.authed
    if not authed:
        from ..masking import scale_to_max
        nw_json["cc"] = scale_to_max(nw_json["cc"])
        nw_json["cdb"] = scale_to_max(nw_json["cdb"])
        nw_json["total"] = scale_to_max(nw_json["total"])
        peak = max([abs(v) for v in is_json["income"] + is_json["spend"] if v is not None] or [1.0])
        is_json["income"] = [round((v / peak) * 100, 2) if v is not None else None for v in is_json["income"]]
        is_json["spend"] = [round((v / peak) * 100, 2) if v is not None else None for v in is_json["spend"]]
        is_json["net"] = [round((v / peak) * 100, 2) if v is not None else None for v in is_json["net"]]
        fx_json["brl"] = scale_to_max(fx_json["brl"])
        fx_json["rate"] = scale_to_max(fx_json["rate"])

    return templates.TemplateResponse(
        "analytics.html",
        {
            "request": request,
            "filter": f,
            "subscriptions": subs,
            "fx_by_ccy": fx_ccy,
            "nw_json": json.dumps(nw_json, default=str),
            "is_json": json.dumps(is_json, default=str),
            "sr_json": json.dumps(sr_json, default=str),
            "fx_json": json.dumps(fx_json, default=str),
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
    f = Filter.from_request(request)
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
    f = Filter.from_request(request)
    q = select(Transaction).order_by(desc(Transaction.date))
    q = f.apply_to_tx(q)
    rows = (await s.execute(q)).scalars().all()

    def stream():
        yield "id,date,account,amount,currency,mega,category,description\n"
        for t in rows:
            desc = (t.description or "").replace('"', "''")
            yield f'{t.id},{t.date.isoformat()},{t.account_id},{t.amount},{t.currency_code or ""},{t.mega or ""},{t.category or ""},"{desc}"\n'

    return StreamingResponse(stream(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=ofin-transactions.csv"})


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, s: AsyncSession = Depends(session_dep)):
    f = Filter.from_request(request)
    days = await daily_spend_calendar(s, f)
    if days:
        max_spend = float(max(d.spend for d in days)) or 1.0
    else:
        max_spend = 1.0
    authed = request.state.auth.authed
    cells = []
    for d in days:
        intensity = min(5, int((float(d.spend) / max_spend) * 5)) if max_spend else 0
        if authed:
            spend_val = float(d.spend)
            income_val = float(d.income)
        else:
            spend_val = round((float(d.spend) / max_spend) * 100.0, 2) if max_spend else 0.0
            income_val = round((float(d.income) / max_spend) * 100.0, 2) if max_spend else 0.0
        cells.append({
            "day": d.day.isoformat(),
            "spend": spend_val,
            "income": income_val,
            "n": d.n,
            "level": intensity,
        })
    top_spend = sorted(cells, key=lambda c: c["spend"], reverse=True)[:15]
    top_income = [c for c in sorted(cells, key=lambda c: c["income"], reverse=True) if c["income"] > 0][:15]
    range_from = cells[0]["day"] if cells else None
    range_to = cells[-1]["day"] if cells else None
    return templates.TemplateResponse(
        "calendar.html",
        {
            "request": request,
            "filter": f,
            "cells": cells,
            "top_spend": top_spend,
            "top_income": top_income,
            "max_spend": max_spend if authed else 100.0,
            "range_from": range_from,
            "range_to": range_to,
        },
    )


@router.get("/merchants", response_class=HTMLResponse)
async def merchants_page(request: Request, s: AsyncSession = Depends(session_dep)):
    f = Filter.from_request(request)
    profiles = await merchant_profiles(s, f, top=200)
    return templates.TemplateResponse(
        "merchants.html",
        {"request": request, "filter": f, "merchants": profiles},
    )


@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request, s: AsyncSession = Depends(session_dep)):
    f = Filter.from_request(request)
    subs = await detect_subscriptions(s, min_months=3, tolerance_pct=Decimal("0.20"))
    today = date.today()
    monthly_burn = sum((s_.avg_amount for s_ in subs), Decimal(0))
    dormant = [s_ for s_ in subs if (today - s_.last_seen).days > 60]
    active = [s_ for s_ in subs if (today - s_.last_seen).days <= 60]
    return templates.TemplateResponse(
        "subscriptions.html",
        {
            "request": request,
            "filter": f,
            "subscriptions": subs,
            "active": active,
            "dormant": dormant,
            "monthly_burn": monthly_burn,
            "annual_burn": monthly_burn * 12,
        },
    )


@router.get("/income", response_class=HTMLResponse)
async def income_page(request: Request, s: AsyncSession = Depends(session_dep)):
    f = Filter.from_request(request)
    mix = await income_mix(s, f)
    if not request.state.auth.authed:
        for m in mix:
            if m.mega == "pessoas":
                m.category = "•••"
    total = sum((m.total for m in mix), Decimal(0))
    return templates.TemplateResponse(
        "income.html",
        {"request": request, "filter": f, "mix": mix, "total": total},
    )


@router.get("/anomalies", response_class=HTMLResponse)
async def anomalies_page(request: Request, s: AsyncSession = Depends(session_dep)):
    f = Filter.from_request(request)
    anomalies = await anomalies_by_mega(s, f, z_threshold=1.6)
    return templates.TemplateResponse(
        "anomalies.html",
        {"request": request, "filter": f, "anomalies": anomalies},
    )


@router.get("/goals", response_class=HTMLResponse)
async def goals_page(request: Request, s: AsyncSession = Depends(session_dep)):
    f = Filter.from_request(request)
    goals = (await s.execute(select(Goal).order_by(Goal.target_date.asc().nullslast()))).scalars().all()
    cc, cdb = await _latest_balances(s)
    nw = (cc or Decimal(0)) + (cdb or Decimal(0))
    nw_series = await net_worth_series(s)
    growth = None
    if len(nw_series) >= 2 and nw_series[-1].total and nw_series[0].total:
        months = len(nw_series)
        growth = (nw_series[-1].total - nw_series[0].total) / Decimal(max(months - 1, 1))
    enriched = []
    for g in goals:
        progress = float(nw / g.target_amount) if g.target_amount else 0.0
        eta = None
        if growth and growth > 0 and g.target_amount > nw:
            months_needed = int((g.target_amount - nw) / growth)
            from datetime import date as _date
            today = _date.today()
            eta_year = today.year + ((today.month - 1 + months_needed) // 12)
            eta_month = ((today.month - 1 + months_needed) % 12) + 1
            eta = f"{eta_year:04d}-{eta_month:02d}"
        enriched.append({"goal": g, "progress": progress, "eta": eta})
    return templates.TemplateResponse(
        "goals.html",
        {
            "request": request,
            "filter": f,
            "goals": enriched,
            "current_nw": nw,
            "monthly_growth": growth,
        },
    )


@router.post("/goals")
async def goal_create(
    request: Request,
    s: AsyncSession = Depends(session_dep),
):
    form = await request.form()
    target_date = form.get("target_date") or None
    td = None
    if target_date:
        try:
            from datetime import date as _date
            td = _date.fromisoformat(target_date)
        except ValueError:
            td = None
    g = Goal(
        name=form.get("name", "meta"),
        target_amount=Decimal(form.get("target_amount", "0").replace(",", ".")),
        currency_code=(form.get("currency_code") or "BRL").upper(),
        target_date=td,
        kind=form.get("kind", "net_worth"),
        notes=form.get("notes") or None,
    )
    s.add(g)
    await s.commit()
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/goals", status_code=303)


@router.post("/goals/{goal_id}/delete")
async def goal_delete(goal_id: int, s: AsyncSession = Depends(session_dep)):
    g = await s.get(Goal, goal_id)
    if not g:
        raise HTTPException(404)
    await s.delete(g)
    await s.commit()
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/goals", status_code=303)

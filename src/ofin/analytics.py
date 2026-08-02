from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, case, func as sqlfunc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .filters import Filter
from .models import Account, BalanceSnapshot, Budget, Document, Loan, LoanPayment, Transaction


def _not_internal():
    return sqlfunc.coalesce(Transaction.mega, "") != "internal"


def _not_sweep():
    return sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False  # noqa: E712


def income_cond():
    return and_(Account.type == "BANK", Transaction.amount > 0, _not_internal(), _not_sweep())


def spend_cond():
    bank = and_(Account.type == "BANK", Transaction.amount < 0, _not_internal(), _not_sweep())
    credit = and_(Account.type == "CREDIT", Transaction.amount > 0, _not_internal())
    return or_(bank, credit)


def spend_amount_abs():
    return case((Account.type == "BANK", -Transaction.amount), else_=Transaction.amount)


@dataclass(slots=True)
class Subscription:
    merchant: str
    avg_amount: Decimal
    last_amount: Decimal
    last_seen: date
    first_seen: date
    months_active: int
    cumulative: Decimal
    sample_count: int


@dataclass(slots=True)
class FxByMonth:
    month: str
    currency: str
    original_total: Decimal
    brl_total: Decimal
    avg_rate: Decimal


@dataclass(slots=True)
class FxByCurrency:
    currency: str
    n: int
    original_total: Decimal
    brl_total: Decimal
    avg_rate: Decimal


@dataclass(slots=True)
class NetWorthPoint:
    month: str
    cc_balance: Decimal | None
    cdb_balance: Decimal | None
    total: Decimal | None
    real_income: Decimal
    real_spend: Decimal
    net: Decimal


def _norm_key(merchant: str) -> str:
    s = merchant.upper().strip()
    s = s.replace(" ", "").replace("*", "").replace(",", "")
    return s[:24]


async def detect_subscriptions(
    s: AsyncSession,
    *,
    min_months: int = 3,
    tolerance_pct: Decimal = Decimal("0.20"),
) -> list[Subscription]:
    rows = (
        await s.execute(
            select(Transaction.date, Transaction.amount, Transaction.description)
            .join(Account, Transaction.account_id == Account.id)
            .where(
                Account.type == "CREDIT",
                Transaction.amount > 0,
                Transaction.description.is_not(None),
            )
        )
    ).all()

    buckets: dict[str, list[tuple[date, Decimal, str]]] = defaultdict(list)
    for d, amt, desc in rows:
        if amt is None or amt <= 0:
            continue
        key = _norm_key(desc or "")
        if not key or len(key) < 4:
            continue
        buckets[key].append((d, Decimal(str(amt)), desc))

    subs: list[Subscription] = []
    for key, entries in buckets.items():
        if len(entries) < min_months:
            continue
        months_seen = {(d.year, d.month) for d, _, _ in entries}
        if len(months_seen) < min_months:
            continue
        entries.sort(key=lambda e: e[0])
        amts = [a for _, a, _ in entries]
        avg = sum(amts, Decimal(0)) / Decimal(len(amts))
        if avg == 0:
            continue
        deltas = [abs(a - avg) / avg for a in amts]
        if max(deltas) > tolerance_pct * Decimal(3):
            continue
        merchant = max((desc for _, _, desc in entries), key=lambda x: len(x or ""))
        subs.append(
            Subscription(
                merchant=merchant,
                avg_amount=avg.quantize(Decimal("0.01")),
                last_amount=entries[-1][1],
                last_seen=entries[-1][0],
                first_seen=entries[0][0],
                months_active=len(months_seen),
                cumulative=sum(amts, Decimal(0)),
                sample_count=len(entries),
            )
        )
    subs.sort(key=lambda x: x.cumulative, reverse=True)
    return subs


async def fx_by_currency(s: AsyncSession, *, since: date | None = None) -> list[FxByCurrency]:
    rows = (
        await s.execute(
            select(Transaction.date, Transaction.amount, Transaction.credit_card_metadata)
            .join(Account, Transaction.account_id == Account.id)
            .where(Account.type == "CREDIT")
        )
    ).all()
    agg: dict[str, dict] = defaultdict(lambda: {"n": 0, "orig": Decimal(0), "brl": Decimal(0), "rate_sum": Decimal(0), "rate_n": 0})
    for d, amt, meta in rows:
        if since and d < since:
            continue
        if not isinstance(meta, dict):
            continue
        fx = meta.get("fx")
        if not fx:
            continue
        ccy = fx.get("currency")
        orig = Decimal(str(fx.get("original_value") or 0))
        rate_raw = fx.get("rate")
        if not ccy or orig <= 0:
            continue
        bucket = agg[ccy]
        bucket["n"] += 1
        bucket["orig"] += orig
        bucket["brl"] += abs(Decimal(str(amt or 0)))
        if rate_raw:
            bucket["rate_sum"] += Decimal(str(rate_raw))
            bucket["rate_n"] += 1
    out = []
    for ccy, v in agg.items():
        avg_rate = (v["rate_sum"] / v["rate_n"]) if v["rate_n"] else Decimal(0)
        out.append(
            FxByCurrency(
                currency=ccy,
                n=v["n"],
                original_total=v["orig"],
                brl_total=v["brl"],
                avg_rate=avg_rate.quantize(Decimal("0.0001")),
            )
        )
    out.sort(key=lambda x: x.brl_total, reverse=True)
    return out


async def fx_by_month(s: AsyncSession, *, since: date | None = None) -> list[FxByMonth]:
    rows = (
        await s.execute(
            select(Transaction.date, Transaction.amount, Transaction.credit_card_metadata)
            .join(Account, Transaction.account_id == Account.id)
            .where(Account.type == "CREDIT")
        )
    ).all()
    agg: dict[tuple[str, str], dict] = defaultdict(lambda: {"orig": Decimal(0), "brl": Decimal(0), "rate_sum": Decimal(0), "rate_n": 0})
    for d, amt, meta in rows:
        if since and d < since:
            continue
        if not isinstance(meta, dict):
            continue
        fx = meta.get("fx")
        if not fx:
            continue
        ccy = fx.get("currency")
        if not ccy:
            continue
        mk = f"{d.year:04d}-{d.month:02d}"
        bucket = agg[(mk, ccy)]
        bucket["orig"] += Decimal(str(fx.get("original_value") or 0))
        bucket["brl"] += abs(Decimal(str(amt or 0)))
        rate_raw = fx.get("rate")
        if rate_raw:
            bucket["rate_sum"] += Decimal(str(rate_raw))
            bucket["rate_n"] += 1
    out = []
    for (mk, ccy), v in agg.items():
        avg_rate = (v["rate_sum"] / v["rate_n"]) if v["rate_n"] else Decimal(0)
        out.append(
            FxByMonth(
                month=mk,
                currency=ccy,
                original_total=v["orig"],
                brl_total=v["brl"],
                avg_rate=avg_rate.quantize(Decimal("0.0001")),
            )
        )
    out.sort(key=lambda x: (x.month, x.currency))
    return out


@dataclass(slots=True)
class WaterfallStep:
    label: str
    delta: Decimal
    kind: str
    mega: str | None = None


@dataclass(slots=True)
class Mover:
    mega: str
    category: str | None
    current: Decimal
    previous: Decimal

    @property
    def delta(self) -> Decimal:
        return self.current - self.previous

    @property
    def pct(self) -> float:
        if self.previous == 0:
            return float("inf") if self.current > 0 else 0.0
        return float((self.current - self.previous) / self.previous)


@dataclass(slots=True)
class DailySpend:
    day: date
    spend: Decimal
    income: Decimal
    n: int


@dataclass(slots=True)
class MerchantProfile:
    merchant: str
    total: Decimal
    n: int
    first_seen: date
    last_seen: date
    avg_amount: Decimal
    monthly_avg: Decimal
    last_30d: Decimal


@dataclass(slots=True)
class Anomaly:
    month: str
    mega: str
    amount: Decimal
    baseline: Decimal
    z_score: float


@dataclass(slots=True)
class SavingsPoint:
    month: str
    income: Decimal
    spend: Decimal
    saved: Decimal
    rate: float


@dataclass(slots=True)
class IncomeMix:
    mega: str
    category: str
    total: Decimal
    pct: float
    n: int


@dataclass(slots=True)
class BudgetProgress:
    budget_id: int
    mega: str
    category: str | None
    target: Decimal
    spent: Decimal
    remaining: Decimal
    pct: float
    currency: str
    status: str


def _apply_filter_tx(stmt, f: Filter, join_account: bool = True):
    return f.apply_to_tx(stmt, join_account=join_account)


def _is_internal_flag():
    return sqlfunc.coalesce(Transaction.raw["is_internal"].as_boolean(), False) == True  # noqa: E712


def _is_sweep_flag():
    return sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == True  # noqa: E712


async def cashflow_waterfall(s: AsyncSession, f: Filter) -> list[WaterfallStep]:
    inc_q = (
        select(Transaction.mega, sqlfunc.sum(Transaction.amount))
        .group_by(Transaction.mega)
    )
    inc_q = _apply_filter_tx(inc_q, f).where(income_cond())
    out_q = (
        select(Transaction.mega, sqlfunc.sum(spend_amount_abs()))
        .group_by(Transaction.mega)
    )
    out_q = _apply_filter_tx(out_q, f).where(spend_cond())
    inc_rows = (await s.execute(inc_q)).all()
    out_rows = (await s.execute(out_q)).all()
    steps: list[WaterfallStep] = []
    inc_sorted = sorted([(m or "renda", Decimal(str(v or 0))) for m, v in inc_rows], key=lambda x: -x[1])
    for mega, v in inc_sorted:
        if v <= 0:
            continue
        steps.append(WaterfallStep(label=mega, delta=v, kind="income", mega=mega))
    out_sorted = sorted([(m or "outros", Decimal(str(v or 0))) for m, v in out_rows], key=lambda x: -x[1])
    for mega, v in out_sorted:
        if v <= 0:
            continue
        steps.append(WaterfallStep(label=mega, delta=-v, kind="spend", mega=mega))
    return steps


async def category_movers(s: AsyncSession, f: Filter, *, top: int = 12) -> list[Mover]:
    prev_from, prev_to = f.comparison_range()
    if prev_from is None or prev_to is None:
        prev_from, prev_to = (None, None)
        if f.date_from and f.date_to:
            span = (f.date_to - f.date_from).days
            prev_from = f.date_from - timedelta(days=span + 1)
            prev_to = f.date_from - timedelta(days=1)
    cur = await _spend_by_mega_category(s, f, f.date_from, f.date_to)
    prev = await _spend_by_mega_category(s, f, prev_from, prev_to)
    keys = set(cur) | set(prev)
    movers = [
        Mover(mega=k[0], category=k[1], current=cur.get(k, Decimal(0)), previous=prev.get(k, Decimal(0)))
        for k in keys
    ]
    movers.sort(key=lambda m: abs(m.delta), reverse=True)
    return movers[:top]


async def _spend_by_mega_category(s: AsyncSession, f: Filter, d_from: date | None, d_to: date | None) -> dict[tuple[str, str], Decimal]:
    f2 = Filter(
        date_from=d_from,
        date_to=d_to,
        preset="custom",
        accounts=f.accounts,
        megas=f.megas,
        categories=f.categories,
        account_types=f.account_types,
        currencies=f.currencies,
    )
    q = (
        select(Transaction.mega, Transaction.category, sqlfunc.sum(spend_amount_abs()))
        .group_by(Transaction.mega, Transaction.category)
    )
    q = _apply_filter_tx(q, f2).where(spend_cond())
    rows = (await s.execute(q)).all()
    return {(m or "outros", c or "outros"): Decimal(str(v or 0)) for m, c, v in rows}


async def daily_spend_calendar(s: AsyncSession, f: Filter) -> list[DailySpend]:
    spend_q = (
        select(Transaction.date, sqlfunc.sum(spend_amount_abs()), sqlfunc.count())
        .group_by(Transaction.date)
    )
    spend_q = _apply_filter_tx(spend_q, f).where(spend_cond())
    income_q = (
        select(Transaction.date, sqlfunc.sum(Transaction.amount))
        .group_by(Transaction.date)
    )
    income_q = _apply_filter_tx(income_q, f).where(income_cond())
    spend_map = {d: (Decimal(str(v or 0)), int(n)) for d, v, n in (await s.execute(spend_q)).all()}
    income_map = {d: Decimal(str(v or 0)) for d, v in (await s.execute(income_q)).all()}
    days = sorted(set(spend_map) | set(income_map))
    return [
        DailySpend(
            day=d,
            spend=spend_map.get(d, (Decimal(0), 0))[0],
            income=income_map.get(d, Decimal(0)),
            n=spend_map.get(d, (Decimal(0), 0))[1],
        )
        for d in days
    ]


async def merchant_profiles(s: AsyncSession, f: Filter, *, top: int = 50) -> list[MerchantProfile]:
    q = select(Transaction.date, spend_amount_abs().label("amt"), Transaction.description, Transaction.merchant)
    q = _apply_filter_tx(q, f).where(spend_cond())
    rows = (await s.execute(q)).all()
    bucket: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    for d, amt, desc, mer in rows:
        name = None
        if isinstance(mer, dict):
            name = mer.get("name")
        if not name:
            name = (desc or "").strip()
        if not name:
            continue
        bucket[name].append((d, Decimal(str(amt or 0))))
    today = date.today()
    profiles = []
    for name, entries in bucket.items():
        entries.sort(key=lambda e: e[0])
        total = sum((a for _, a in entries), Decimal(0))
        n = len(entries)
        first = entries[0][0]
        last = entries[-1][0]
        avg = (total / Decimal(n)).quantize(Decimal("0.01")) if n else Decimal(0)
        months_span = max(1, (last.year - first.year) * 12 + (last.month - first.month) + 1)
        monthly = (total / Decimal(months_span)).quantize(Decimal("0.01"))
        cutoff = today - timedelta(days=30)
        last30 = sum((a for d, a in entries if d >= cutoff), Decimal(0))
        profiles.append(
            MerchantProfile(
                merchant=name,
                total=total,
                n=n,
                first_seen=first,
                last_seen=last,
                avg_amount=avg,
                monthly_avg=monthly,
                last_30d=last30,
            )
        )
    profiles.sort(key=lambda p: p.total, reverse=True)
    return profiles[:top]


async def anomalies_by_mega(s: AsyncSession, f: Filter, *, z_threshold: float = 1.8) -> list[Anomaly]:
    q = (
        select(
            sqlfunc.to_char(Transaction.date, "YYYY-MM").label("mk"),
            Transaction.mega,
            sqlfunc.sum(spend_amount_abs()).label("total"),
        )
        .group_by("mk", Transaction.mega)
    )
    q = _apply_filter_tx(q, f).where(spend_cond())
    rows = (await s.execute(q)).all()
    by_mega: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    for mk, mega, total in rows:
        by_mega[mega or "outros"].append((mk, Decimal(str(total or 0))))
    out: list[Anomaly] = []
    for mega, series in by_mega.items():
        if len(series) < 4:
            continue
        amts = [float(v) for _, v in series]
        mu = statistics.mean(amts)
        sd = statistics.pstdev(amts) or 1.0
        baseline_dec = Decimal(str(mu)).quantize(Decimal("0.01"))
        for mk, v in series:
            z = (float(v) - mu) / sd
            if abs(z) >= z_threshold:
                out.append(Anomaly(month=mk, mega=mega, amount=v, baseline=baseline_dec, z_score=round(z, 2)))
    out.sort(key=lambda a: (a.month, -abs(a.z_score)), reverse=True)
    return out


async def savings_rate(s: AsyncSession, f: Filter) -> list[SavingsPoint]:
    inc_q = (
        select(
            sqlfunc.to_char(Transaction.date, "YYYY-MM").label("mk"),
            sqlfunc.sum(Transaction.amount),
        )
        .group_by("mk")
    )
    inc_q = _apply_filter_tx(inc_q, f).where(income_cond())
    spend_q = (
        select(
            sqlfunc.to_char(Transaction.date, "YYYY-MM").label("mk"),
            sqlfunc.sum(spend_amount_abs()),
        )
        .group_by("mk")
    )
    spend_q = _apply_filter_tx(spend_q, f).where(spend_cond())
    inc_map = {mk: Decimal(str(v or 0)) for mk, v in (await s.execute(inc_q)).all()}
    spend_map = {mk: Decimal(str(v or 0)) for mk, v in (await s.execute(spend_q)).all()}
    months = sorted(set(inc_map) | set(spend_map))
    out = []
    for mk in months:
        income = inc_map.get(mk, Decimal(0))
        spend = spend_map.get(mk, Decimal(0))
        if income <= 0:
            continue
        saved = income - spend
        rate = float(saved / income)
        out.append(SavingsPoint(month=mk, income=income, spend=spend, saved=saved, rate=rate))
    return out


async def income_mix(s: AsyncSession, f: Filter) -> list[IncomeMix]:
    q = (
        select(
            Transaction.mega,
            Transaction.category,
            sqlfunc.sum(Transaction.amount),
            sqlfunc.count(),
        )
        .group_by(Transaction.mega, Transaction.category)
    )
    q = _apply_filter_tx(q, f).where(income_cond())
    rows = (await s.execute(q)).all()
    total = sum((Decimal(str(v or 0)) for _, _, v, _ in rows), Decimal(0))
    out = []
    for mega, cat, v, n in rows:
        amt = Decimal(str(v or 0))
        pct = float(amt / total) if total else 0.0
        out.append(IncomeMix(mega=mega or "renda", category=cat or "outros", total=amt, pct=pct, n=int(n)))
    out.sort(key=lambda x: x.total, reverse=True)
    return out


async def budget_progress(s: AsyncSession, budgets: list[Budget], f: Filter) -> list[BudgetProgress]:
    today = date.today()
    period_from = today.replace(day=1)
    period_to = today
    pf = Filter(
        date_from=period_from,
        date_to=period_to,
        preset="custom",
        accounts=f.accounts,
        account_types=f.account_types,
    )
    q = (
        select(Transaction.mega, Transaction.category, sqlfunc.sum(spend_amount_abs()))
        .group_by(Transaction.mega, Transaction.category)
    )
    q = _apply_filter_tx(q, pf).where(spend_cond())
    rows = (await s.execute(q)).all()
    spend_map: dict[tuple[str, str | None], Decimal] = {}
    mega_total: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for m, c, v in rows:
        amt = Decimal(str(v or 0))
        spend_map[(m or "outros", c)] = spend_map.get((m or "outros", c), Decimal(0)) + amt
        mega_total[m or "outros"] += amt
    progress = []
    for b in budgets:
        target = Decimal(str(b.amount))
        if b.category:
            spent = spend_map.get((b.mega, b.category), Decimal(0))
        else:
            spent = mega_total.get(b.mega, Decimal(0))
        remaining = target - spent
        pct = float(spent / target) if target else 0.0
        if pct >= 1.0:
            status = "over"
        elif pct >= 0.85:
            status = "warn"
        else:
            status = "ok"
        progress.append(
            BudgetProgress(
                budget_id=b.id,
                mega=b.mega,
                category=b.category,
                target=target,
                spent=spent,
                remaining=remaining,
                pct=pct,
                currency=b.currency_code,
                status=status,
            )
        )
    progress.sort(key=lambda p: p.pct, reverse=True)
    return progress


async def net_worth_series(s: AsyncSession) -> list[NetWorthPoint]:
    docs = (
        await s.execute(
            select(Document.period_year, Document.period_month, Document.summary, Document.id)
            .where(Document.document_type == "extrato")
            .order_by(Document.period_year, Document.period_month)
        )
    ).all()
    by_period: dict[str, dict] = {}
    for py, pm, summary, doc_id in docs:
        if not py or not pm:
            continue
        mk = f"{py:04d}-{pm:02d}"
        summary = summary or {}
        cdb_snaps = summary.get("cdb_snapshots") or []
        cdb_last = None
        if cdb_snaps:
            v = cdb_snaps[-1].get("cdb_balance")
            if v is not None:
                cdb_last = Decimal(str(v))
        cc_ledger = summary.get("saldo_cc_ledger")
        cc = Decimal(str(cc_ledger)) if cc_ledger is not None else None
        cb = summary.get("closing_balance")
        total = Decimal(str(cb)) if cb is not None else None
        if cc is None and total is not None:
            cc = total - (cdb_last or Decimal(0))
        by_period[mk] = {"cc": cc, "cdb": cdb_last, "total": total, "doc_id": doc_id}

    inc_rows = (
        await s.execute(
            select(
                sqlfunc.to_char(Transaction.date, "YYYY-MM").label("mk"),
                sqlfunc.sum(Transaction.amount).label("total"),
            )
            .join(Account, Transaction.account_id == Account.id)
            .where(
                Account.type == "BANK",
                Transaction.amount > 0,
                sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False,  # noqa: E712
                sqlfunc.coalesce(Transaction.mega, "") != "internal",
            )
            .group_by("mk")
        )
    ).all()
    spend_rows = (
        await s.execute(
            select(
                sqlfunc.to_char(Transaction.date, "YYYY-MM").label("mk"),
                sqlfunc.sum(-Transaction.amount).label("total"),
            )
            .join(Account, Transaction.account_id == Account.id)
            .where(
                Account.type == "BANK",
                Transaction.amount < 0,
                sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False,  # noqa: E712
                sqlfunc.coalesce(Transaction.mega, "") != "internal",
            )
            .group_by("mk")
        )
    ).all()
    inc_map = {mk: Decimal(str(total or 0)) for mk, total in inc_rows}
    spend_map = {mk: Decimal(str(total or 0)) for mk, total in spend_rows}

    out = []
    for mk in sorted(by_period):
        b = by_period[mk]
        income = inc_map.get(mk, Decimal(0))
        spend = spend_map.get(mk, Decimal(0))
        out.append(
            NetWorthPoint(
                month=mk,
                cc_balance=b["cc"],
                cdb_balance=b["cdb"],
                total=b["total"],
                real_income=income,
                real_spend=spend,
                net=income - spend,
            )
        )
    return out


@dataclass(slots=True)
class SourceSnapshot:
    source: str
    value_brl: Decimal
    taken_at: date


@dataclass(slots=True)
class Patrimonio:
    itau_cc: Decimal | None
    itau_cdb: Decimal | None
    snapshots: list[SourceSnapshot]
    loans_out: Decimal
    loans_in: Decimal
    total: Decimal


async def latest_itau_balances(s: AsyncSession) -> tuple[Decimal | None, Decimal | None]:
    rows = (
        await s.execute(
            select(Document.account_id, Document.summary, Document.period_year, Document.period_month)
            .where(Document.document_type == "extrato")
            .order_by(Document.period_year.desc(), Document.period_month.desc())
        )
    ).all()
    if not rows:
        return None, None
    latest_per_account: dict[str | None, dict] = {}
    for acct, summary, _y, _m in rows:
        if acct in latest_per_account:
            continue
        latest_per_account[acct] = summary or {}
    cc_total = Decimal(0)
    cdb_total = Decimal(0)
    cc_found = False
    cdb_found = False
    for summary in latest_per_account.values():
        cc_raw = summary.get("saldo_cc_ledger")
        cdb_last = None
        cdb_list = summary.get("cdb_snapshots") or []
        if cdb_list:
            v = cdb_list[-1].get("cdb_balance")
            if v is not None:
                cdb_last = Decimal(str(v))
        if cc_raw is not None:
            cc_total += Decimal(str(cc_raw))
            cc_found = True
        else:
            cb = summary.get("closing_balance")
            if cb is not None:
                cc_total += Decimal(str(cb)) - (cdb_last or Decimal(0))
                cc_found = True
        if cdb_last is not None:
            cdb_total += cdb_last
            cdb_found = True
    cc = cc_total if cc_found else None
    cdb = cdb_total if cdb_found else None
    return cc, cdb


async def loan_outstanding_rows(s: AsyncSession) -> list[tuple[Loan, Decimal, Decimal]]:
    paid_sub = (
        select(LoanPayment.loan_id, sqlfunc.sum(LoanPayment.amount).label("paid"))
        .group_by(LoanPayment.loan_id)
        .subquery()
    )
    rows = (
        await s.execute(
            select(Loan, sqlfunc.coalesce(paid_sub.c.paid, 0))
            .join(paid_sub, paid_sub.c.loan_id == Loan.id, isouter=True)
            .order_by(Loan.status, Loan.date.desc())
        )
    ).all()
    out = []
    for loan, paid in rows:
        paid_d = Decimal(str(paid or 0))
        out.append((loan, paid_d, loan.principal - paid_d))
    return out


async def patrimonio_breakdown(s: AsyncSession) -> Patrimonio:
    cc, cdb = await latest_itau_balances(s)
    latest = (
        select(BalanceSnapshot.source, sqlfunc.max(BalanceSnapshot.taken_at).label("d"))
        .group_by(BalanceSnapshot.source)
        .subquery()
    )
    snap_rows = (
        await s.execute(
            select(BalanceSnapshot.source, latest.c.d, sqlfunc.sum(BalanceSnapshot.value_brl))
            .join(latest, and_(BalanceSnapshot.source == latest.c.source, BalanceSnapshot.taken_at == latest.c.d))
            .group_by(BalanceSnapshot.source, latest.c.d)
            .order_by(BalanceSnapshot.source)
        )
    ).all()
    snapshots = [SourceSnapshot(source=src, value_brl=Decimal(str(v)), taken_at=d) for src, d, v in snap_rows]
    loans_out = Decimal(0)
    loans_in = Decimal(0)
    for loan, _paid, outstanding in await loan_outstanding_rows(s):
        if loan.status != "open":
            continue
        if loan.direction == "lent":
            loans_out += outstanding
        else:
            loans_in += outstanding
    total = (
        (cc or Decimal(0))
        + (cdb or Decimal(0))
        + sum((sn.value_brl for sn in snapshots), Decimal(0))
        + loans_out
        - loans_in
    )
    return Patrimonio(
        itau_cc=cc,
        itau_cdb=cdb,
        snapshots=snapshots,
        loans_out=loans_out,
        loans_in=loans_in,
        total=total,
    )


DARK_MEGAS = ("pix_out", "transferencia", "saque", "outros")


async def dark_matter(s: AsyncSession, f: Filter) -> tuple[int, Decimal]:
    q = (
        select(sqlfunc.count(), sqlfunc.coalesce(sqlfunc.sum(spend_amount_abs()), 0))
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(spend_cond())
        .where(or_(Transaction.mega.in_(DARK_MEGAS), Transaction.mega.is_(None)))
    )
    q = f.apply_to_tx(q)
    n, total = (await s.execute(q)).one()
    return int(n or 0), Decimal(str(total or 0))

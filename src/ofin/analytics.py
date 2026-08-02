from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, case, func as sqlfunc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .filters import Filter
from .models import Account, BalanceSnapshot, Document, Loan, LoanPayment, Transaction


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
class NetWorthPoint:
    month: str
    cc_balance: Decimal | None
    cdb_balance: Decimal | None
    total: Decimal | None
    real_income: Decimal
    real_spend: Decimal
    net: Decimal


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
class SavingsPoint:
    month: str
    income: Decimal
    spend: Decimal
    saved: Decimal
    rate: float


def _apply_filter_tx(stmt, f: Filter, join_account: bool = True):
    return f.apply_to_tx(stmt, join_account=join_account)


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

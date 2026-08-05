from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, case, func as sqlfunc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .filters import Filter
from .models import Account, Loan, LoanPayment, Transaction


def _month_end(month: str) -> str:
    y, m = (int(x) for x in month.split("-"))
    return f"{month}-{calendar.monthrange(y, m)[1]:02d}"


async def latest_tx_date(s: AsyncSession) -> date | None:
    return (await s.execute(select(sqlfunc.max(Transaction.date)))).scalar()


def _not_internal():
    return sqlfunc.coalesce(Transaction.mega, "") != "internal"


def _not_sweep():
    return sqlfunc.coalesce(Transaction.raw["is_sweep"].as_boolean(), False) == False  # noqa: E712


def income_cond():
    return and_(Account.type == "BANK", Transaction.amount > 0, _not_internal(), _not_sweep())


def spend_cond():
    # Card refunds/estornos post as NEGATIVE credit amounts; include them so
    # they net against purchases (via spend_amount_abs) instead of inflating spend.
    bank = and_(Account.type == "BANK", Transaction.amount < 0, _not_internal(), _not_sweep())
    credit = and_(Account.type == "CREDIT", Transaction.amount != 0, _not_internal())
    return or_(bank, credit)


def spend_amount_abs():
    return case((Account.type == "BANK", -Transaction.amount), else_=Transaction.amount)


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
    rate: float | None
    month_end: str


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
        saved = income - spend
        rate = float(saved / income) if income > 0 else None
        out.append(SavingsPoint(month=mk, income=income, spend=spend, saved=saved, rate=rate, month_end=_month_end(mk)))
    return out


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


DARK_MEGAS = ("pix_out", "transferencia", "saque", "outros")


async def dark_matter(s: AsyncSession, f: Filter) -> tuple[int, Decimal]:
    q = (
        select(sqlfunc.count(), sqlfunc.coalesce(sqlfunc.sum(spend_amount_abs()), 0))
        .select_from(Transaction)
        .where(spend_cond())
        .where(or_(Transaction.mega.in_(DARK_MEGAS), Transaction.mega.is_(None)))
    )
    q = f.apply_to_tx(q)
    n, total = (await s.execute(q)).one()
    return int(n or 0), Decimal(str(total or 0))

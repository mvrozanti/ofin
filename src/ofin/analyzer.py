from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Account, Investment, Transaction


@dataclass(slots=True)
class MonthCash:
    month: str
    inflow: Decimal
    outflow: Decimal

    @property
    def net(self) -> Decimal:
        return self.inflow - self.outflow


@dataclass(slots=True)
class CategoryAgg:
    category: str
    spend: Decimal
    count: int


@dataclass(slots=True)
class MerchantAgg:
    merchant: str
    spend: Decimal
    count: int


@dataclass(slots=True)
class AccountSummary:
    id: str
    name: str
    type: str | None
    subtype: str | None
    balance: Decimal | None
    currency: str | None


@dataclass(slots=True)
class InvestmentSummary:
    name: str
    type: str | None
    issuer: str | None
    balance: Decimal | None
    invested: Decimal | None
    profit: Decimal | None


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


async def accounts(s: AsyncSession) -> list[AccountSummary]:
    rows = (await s.execute(select(Account))).scalars().all()
    return [
        AccountSummary(
            id=a.id,
            name=a.marketing_name or a.name or a.id,
            type=a.type,
            subtype=a.subtype,
            balance=a.balance,
            currency=a.currency_code,
        )
        for a in rows
    ]


async def monthly_cashflow(s: AsyncSession, *, months: int = 24, include_internal: bool = False) -> list[MonthCash]:
    today = date.today()
    cutoff = date(today.year - (months // 12 + 1), today.month, 1)
    rows = (
        await s.execute(
            select(Transaction.date, Transaction.amount, Transaction.type, Transaction.raw, Account.type)
            .join(Account, Transaction.account_id == Account.id)
            .where(Transaction.date >= cutoff, Account.type == "BANK")
        )
    ).all()
    buckets: dict[str, MonthCash] = {}
    for d, amount, t, raw, _acct_type in rows:
        if isinstance(raw, dict):
            if raw.get("is_sweep"):
                continue
            if not include_internal and raw.get("is_internal"):
                continue
        mk = _month_key(d)
        bucket = buckets.setdefault(mk, MonthCash(mk, Decimal(0), Decimal(0)))
        amt = Decimal(amount or 0)
        is_credit = (t or "").upper() == "CREDIT" or amt > 0
        if is_credit:
            bucket.inflow += abs(amt)
        else:
            bucket.outflow += abs(amt)
    return sorted(buckets.values(), key=lambda m: m.month)


async def by_category(s: AsyncSession, *, since: date | None = None, top: int = 20) -> list[CategoryAgg]:
    q = select(Transaction.category, Transaction.amount, Transaction.type, Transaction.raw)
    if since:
        q = q.where(Transaction.date >= since)
    rows = (await s.execute(q)).all()
    agg: dict[str, list[Decimal | int]] = defaultdict(lambda: [Decimal(0), 0])
    for cat, amount, t, raw in rows:
        if isinstance(raw, dict) and raw.get("is_sweep"):
            continue
        amt = Decimal(amount or 0)
        is_debit = (t or "").upper() == "DEBIT" or amt < 0
        if not is_debit:
            continue
        key = cat or "uncategorized"
        agg[key][0] += abs(amt)
        agg[key][1] += 1
    items = [CategoryAgg(k, v[0], int(v[1])) for k, v in agg.items()]
    items.sort(key=lambda x: x.spend, reverse=True)
    return items[:top]


async def by_merchant(s: AsyncSession, *, since: date | None = None, top: int = 20) -> list[MerchantAgg]:
    q = select(Transaction.merchant, Transaction.description, Transaction.amount, Transaction.type, Transaction.raw)
    if since:
        q = q.where(Transaction.date >= since)
    rows = (await s.execute(q)).all()
    agg: dict[str, list[Decimal | int]] = defaultdict(lambda: [Decimal(0), 0])
    for merchant, descr, amount, t, raw in rows:
        if isinstance(raw, dict) and raw.get("is_sweep"):
            continue
        amt = Decimal(amount or 0)
        is_debit = (t or "").upper() == "DEBIT" or amt < 0
        if not is_debit:
            continue
        name = (merchant or {}).get("name") if isinstance(merchant, dict) else None
        key = name or (descr or "").strip() or "unknown"
        agg[key][0] += abs(amt)
        agg[key][1] += 1
    items = [MerchantAgg(k, v[0], int(v[1])) for k, v in agg.items()]
    items.sort(key=lambda x: x.spend, reverse=True)
    return items[:top]


async def pix_volumes(s: AsyncSession, *, since: date | None = None) -> dict[str, Decimal]:
    q = select(Transaction.payment_data, Transaction.amount, Transaction.type)
    if since:
        q = q.where(Transaction.date >= since)
    rows = (await s.execute(q)).all()
    pix_in = pix_out = Decimal(0)
    for pdata, amount, t in rows:
        if not isinstance(pdata, dict):
            continue
        method = (pdata.get("paymentMethod") or "").upper()
        if method != "PIX":
            continue
        amt = abs(Decimal(amount or 0))
        if (t or "").upper() == "CREDIT" or Decimal(amount or 0) > 0:
            pix_in += amt
        else:
            pix_out += amt
    return {"in": pix_in, "out": pix_out, "net": pix_in - pix_out}


async def investments(s: AsyncSession) -> list[InvestmentSummary]:
    rows = (await s.execute(select(Investment))).scalars().all()
    return [
        InvestmentSummary(
            name=i.name or i.code or i.id,
            type=i.type,
            issuer=i.issuer,
            balance=i.balance,
            invested=i.amount_original,
            profit=i.amount_profit,
        )
        for i in rows
    ]

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Account, Document, Transaction


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
                sqlfunc.coalesce(Transaction.raw["is_internal"].as_boolean(), False) == False,  # noqa: E712
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
                sqlfunc.coalesce(Transaction.raw["is_internal"].as_boolean(), False) == False,  # noqa: E712
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

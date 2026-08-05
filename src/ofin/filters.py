from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import and_, or_
from sqlalchemy.sql import Select

from .models import Account, Transaction


PRESETS = {
    "today": 0,
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "180d": 180,
    "365d": 365,
    "mtd": "mtd",
    "ytd": "ytd",
    "12mo": 365,
    "all": "all",
}


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _resolve_preset(preset: str | None, today: date) -> tuple[date | None, date | None]:
    if not preset or preset == "all":
        return None, today
    if preset == "mtd":
        return today.replace(day=1), today
    if preset == "ytd":
        return today.replace(month=1, day=1), today
    if preset in PRESETS and isinstance(PRESETS[preset], int):
        n = PRESETS[preset]
        return today - timedelta(days=n), today
    return None, today


@dataclass(slots=True)
class Filter:
    date_from: date | None = None
    date_to: date | None = None
    preset: str = "90d"
    accounts: list[str] = field(default_factory=list)
    megas: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    account_types: list[str] = field(default_factory=list)
    currencies: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    rule_ids: list[int] = field(default_factory=list)
    search: str | None = None
    include_internal: bool = False
    include_sweep: bool = False
    compare: str = "none"
    display_currency: str = "BRL"

    @classmethod
    def from_request(cls, request: Request, anchor: date | None = None) -> "Filter":
        q = request.query_params
        today = date.today()
        # Data arrives via monthly statements, so it lags "today" by weeks.
        # Anchor relative windows on the newest transaction, not the wall clock,
        # otherwise the default view slides past the data and shows R$0.
        ref = anchor if (anchor and anchor < today) else today
        preset = q.get("preset") or "90d"
        d_from = _parse_date(q.get("from"))
        d_to = _parse_date(q.get("to"))
        if not d_from and not d_to:
            d_from, d_to = _resolve_preset(preset, ref)
        elif not d_to:
            d_to = ref
        rule_raw = _split(q.get("rules") or q.get("rule"))
        rule_ids = []
        for r in rule_raw:
            try:
                rule_ids.append(int(r))
            except ValueError:
                pass
        return cls(
            date_from=d_from,
            date_to=d_to,
            preset=preset,
            accounts=_split(q.get("accounts")),
            megas=_split(q.get("megas")),
            categories=_split(q.get("categories")),
            account_types=_split(q.get("account_types")),
            currencies=_split(q.get("currencies")),
            tags=_split(q.get("tags")),
            rule_ids=rule_ids,
            search=q.get("q") or None,
            include_internal=q.get("internal") == "1",
            include_sweep=q.get("sweep") == "1",
            compare=q.get("compare") or "none",
            display_currency=q.get("display_currency") or "BRL",
        )

    def comparison_range(self) -> tuple[date | None, date | None]:
        if self.compare == "none" or not self.date_from or not self.date_to:
            return None, None
        span = (self.date_to - self.date_from).days
        if self.compare == "prev":
            return self.date_from - timedelta(days=span + 1), self.date_from - timedelta(days=1)
        if self.compare == "yoy":
            try:
                return self.date_from.replace(year=self.date_from.year - 1), self.date_to.replace(year=self.date_to.year - 1)
            except ValueError:
                return self.date_from - timedelta(days=365), self.date_to - timedelta(days=365)
        return None, None

    def apply_to_tx(self, stmt: Select, *, join_account: bool = True) -> Select:
        if join_account:
            stmt = stmt.join(Account, Transaction.account_id == Account.id)
        if self.date_from:
            stmt = stmt.where(Transaction.date >= self.date_from)
        if self.date_to:
            stmt = stmt.where(Transaction.date <= self.date_to)
        if self.accounts:
            stmt = stmt.where(Transaction.account_id.in_(self.accounts))
        if self.account_types:
            stmt = stmt.where(Account.type.in_(self.account_types))
        if self.megas:
            stmt = stmt.where(Transaction.mega.in_(self.megas))
        if self.categories:
            stmt = stmt.where(Transaction.category.in_(self.categories))
        if self.currencies:
            stmt = stmt.where(Transaction.currency_code.in_(self.currencies))
        if self.rule_ids:
            stmt = stmt.where(Transaction.rule_id.in_(self.rule_ids))
        if self.search:
            like = f"%{self.search.lower()}%"
            stmt = stmt.where(
                or_(
                    Transaction.description.ilike(like),
                    Transaction.description_raw.ilike(like),
                )
            )
        return stmt

    def to_query_string(self, overrides: dict[str, Any] | None = None) -> str:
        parts: list[tuple[str, str]] = []
        data: dict[str, Any] = {
            "preset": self.preset if self.preset != "custom" else None,
            "from": self.date_from.isoformat() if (self.date_from and self.preset == "custom") else None,
            "to": self.date_to.isoformat() if (self.date_to and self.preset == "custom") else None,
            "accounts": ",".join(self.accounts) or None,
            "megas": ",".join(self.megas) or None,
            "categories": ",".join(self.categories) or None,
            "account_types": ",".join(self.account_types) or None,
            "currencies": ",".join(self.currencies) or None,
            "tags": ",".join(self.tags) or None,
            "q": self.search,
            "internal": "1" if self.include_internal else None,
            "sweep": "1" if self.include_sweep else None,
            "compare": self.compare if self.compare != "none" else None,
            "display_currency": self.display_currency if self.display_currency != "BRL" else None,
        }
        if overrides:
            data.update(overrides)
        for k, v in data.items():
            if v is None or v == "":
                continue
            parts.append((k, str(v)))
        return "&".join(f"{k}={v}" for k, v in parts)

    def label(self) -> str:
        if self.preset == "custom" and self.date_from and self.date_to:
            return f"{self.date_from.isoformat()} → {self.date_to.isoformat()}"
        labels = {
            "today": "hoje",
            "7d": "últimos 7 dias",
            "30d": "últimos 30 dias",
            "90d": "últimos 90 dias",
            "180d": "últimos 180 dias",
            "365d": "últimos 365 dias",
            "12mo": "últimos 12 meses",
            "mtd": "mês atual",
            "ytd": "ano atual",
            "all": "tudo",
        }
        return labels.get(self.preset, self.preset)

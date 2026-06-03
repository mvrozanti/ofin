from __future__ import annotations

from decimal import Decimal
from typing import Iterable


MASK_STR = "•••"


def fmt_money(v) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"R$ {n:,.2f}".replace(",", "·").replace(".", ",").replace("·", ".")


def mask_money(v, authed: bool) -> str:
    if not authed:
        return MASK_STR
    return fmt_money(v)


def mask_value(v, authed: bool) -> float | None:
    if not authed:
        return None
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def proportions(values: Iterable, *, total: float = 100.0) -> list[float]:
    nums = []
    for v in values:
        if v is None:
            nums.append(0.0)
            continue
        try:
            nums.append(abs(float(v)))
        except (TypeError, ValueError):
            nums.append(0.0)
    s = sum(nums)
    if s <= 0:
        return [0.0 for _ in nums]
    factor = total / s
    return [round(n * factor, 4) for n in nums]


def scale_to_max(values: Iterable, *, peak: float = 100.0) -> list[float | None]:
    nums = [None if v is None else float(v) for v in values]
    finite = [abs(n) for n in nums if n is not None]
    if not finite or max(finite) == 0:
        return [None if n is None else 0.0 for n in nums]
    m = max(finite)
    return [None if n is None else round((n / m) * peak, 4) for n in nums]


def mask_series(values: Iterable, authed: bool, *, mode: str = "max") -> list[float | None]:
    if authed:
        return [None if v is None else float(v) for v in values]
    if mode == "sum":
        return [v if v is not None else None for v in proportions(values)]
    return scale_to_max(values)


def mask_total(v, authed: bool):
    return float(v) if authed and v is not None else None

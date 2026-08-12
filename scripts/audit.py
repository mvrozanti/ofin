"""DB-level invariant checks for ofin. Run via `python -m scripts.audit`.

Exits 0 if all checks pass; exit code = number of failures otherwise.
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass

import asyncpg


def db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url.replace("postgresql+asyncpg://", "postgresql://")
    return "postgresql://ofin:ofin@ofin-db:5432/ofin"


@dataclass
class Check:
    name: str
    sql: str
    expected: str  # "zero" | "positive"
    description: str


CHECKS: list[Check] = [
    Check(
        name="mega_null",
        sql="SELECT COUNT(*) FROM transactions WHERE mega IS NULL",
        expected="zero",
        description="every tx must have a mega assigned",
    ),
    Check(
        name="dangling_rule_id",
        sql=(
            "SELECT COUNT(*) FROM transactions t "
            "WHERE t.rule_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM category_rules r WHERE r.id = t.rule_id)"
        ),
        expected="zero",
        description="rule_id must reference an existing rule",
    ),
    Check(
        name="sweep_without_internal_mega",
        sql=(
            "SELECT COUNT(*) FROM transactions "
            "WHERE COALESCE((raw->>'is_sweep')::bool, false) = true "
            "AND COALESCE(mega,'') <> 'internal'"
        ),
        expected="zero",
        description="sweep flag implies mega=internal",
    ),
    Check(
        name="raw_internal_mega_drift",
        sql=(
            "SELECT COUNT(*) FROM transactions "
            "WHERE COALESCE((raw->>'is_internal')::bool, false) <> (mega='internal')"
        ),
        expected="zero",
        description="raw.is_internal must match mega='internal'",
    ),
    Check(
        name="override_dangling_tx",
        sql=(
            "SELECT COUNT(*) FROM transaction_overrides ov "
            "WHERE NOT EXISTS (SELECT 1 FROM transactions t WHERE t.id = ov.tx_id)"
        ),
        expected="zero",
        description="override must reference existing tx",
    ),
    Check(
        name="budget_amount_non_positive",
        sql="SELECT COUNT(*) FROM budgets WHERE amount <= 0",
        expected="zero",
        description="budget amounts must be > 0",
    ),
    Check(
        name="any_transactions_exist",
        sql="SELECT COUNT(*) FROM transactions",
        expected="positive",
        description="db should contain ingested transactions",
    ),
    Check(
        name="any_rules_exist",
        sql="SELECT COUNT(*) FROM category_rules",
        expected="positive",
        description="seed rules must be present",
    ),
    Check(
        name="duplicate_tx_ids",
        sql=(
            "SELECT COUNT(*) FROM (SELECT id FROM transactions GROUP BY id HAVING COUNT(*) > 1) d"
        ),
        expected="zero",
        description="tx ids must be unique",
    ),
    Check(
        name="credit_positive_payment_desc",
        sql=(
            "SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id "
            "WHERE a.type = 'CREDIT' AND t.amount > 0 "
            "AND LOWER(t.description) LIKE 'pagamento efetuado%'"
        ),
        expected="zero",
        description="fatura payment lines must be negative payments, never positive charges",
    ),
    Check(
        name="tx_credit_negative_unaccounted",
        sql=(
            "SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id "
            "WHERE a.type = 'CREDIT' AND t.amount < 0 "
            "AND COALESCE((credit_card_metadata->>'kind'), '') NOT IN ('payment', 'refund', 'estorno') "
            "AND mega <> 'internal'"
        ),
        expected="zero",
        description="credit-card negative amounts must have kind=payment|refund|estorno or mega=internal",
    ),
]


@dataclass
class IncomeSpend:
    month: str
    income: float
    spend: float


SAVINGS_SQL = """
WITH income AS (
  SELECT to_char(t.date, 'YYYY-MM') AS mk, SUM(t.amount) AS v
  FROM transactions t JOIN accounts a ON a.id = t.account_id
  WHERE a.type = 'BANK' AND t.amount > 0
    AND COALESCE(t.mega,'') <> 'internal'
    AND COALESCE((t.raw->>'is_sweep')::bool, false) = false
  GROUP BY mk
), spend AS (
  SELECT to_char(t.date, 'YYYY-MM') AS mk,
    SUM(CASE WHEN a.type='BANK' THEN -t.amount ELSE t.amount END) AS v
  FROM transactions t JOIN accounts a ON a.id = t.account_id
  WHERE COALESCE(t.mega,'') <> 'internal'
    AND ((a.type = 'BANK' AND t.amount < 0
          AND COALESCE((t.raw->>'is_sweep')::bool, false) = false)
      OR (a.type = 'CREDIT' AND t.amount > 0))
  GROUP BY mk
)
SELECT i.mk, i.v::float, COALESCE(s.v, 0)::float
FROM income i LEFT JOIN spend s ON s.mk = i.mk
WHERE i.v > 0
ORDER BY i.mk;
"""


async def run() -> int:
    conn = await asyncpg.connect(db_url())
    fails = 0
    print("=== ofin DB audit ===")
    for c in CHECKS:
        n = await conn.fetchval(c.sql)
        ok = (c.expected == "zero" and n == 0) or (c.expected == "positive" and n > 0)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {c.name}: n={n} — {c.description}")
        if not ok:
            fails += 1

    print()
    print("=== income/spend monthly snapshot ===")
    rows = await conn.fetch(SAVINGS_SQL)
    for r in rows[-12:]:
        mk, inc, sp = r[0], r[1], r[2]
        saved = inc - sp
        rate = (saved / inc * 100) if inc else 0
        print(f"  {mk}  income={inc:>11.2f}  spend={sp:>11.2f}  saved={saved:>11.2f}  rate={rate:>+7.1f}%")

    await conn.close()
    print()
    print(f"{fails} failure(s)" if fails else "all checks passed")
    return fails


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())

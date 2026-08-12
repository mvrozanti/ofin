from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .common import approx_eq


SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"


@dataclass(slots=True)
class Warning_:
    severity: str
    code: str
    message: str
    diff: dict | None = None
    raw_line: str | None = None


def validate_extrato(result) -> list[Warning_]:
    s = result.summary
    out: list[Warning_] = []

    real_credit = sum((t.amount for t in result.transactions if t.amount > 0 and not t.is_sweep), Decimal(0))
    real_debit = sum((-t.amount for t in result.transactions if t.amount < 0 and not t.is_sweep), Decimal(0))
    sweep_credit = sum((t.amount for t in result.transactions if t.amount > 0 and t.is_sweep), Decimal(0))
    sweep_debit = sum((-t.amount for t in result.transactions if t.amount < 0 and t.is_sweep), Decimal(0))

    if s.entradas_total is not None and real_credit != s.entradas_total:
        out.append(
            Warning_(
                SEVERITY_ERROR,
                "extrato.totais_creditos",
                f"sum(real credits) = {real_credit} != header.entradas_total = {s.entradas_total}",
                diff={"computed": str(real_credit), "expected": str(s.entradas_total)},
            )
        )

    if s.saidas_total is not None and real_debit != s.saidas_total:
        out.append(
            Warning_(
                SEVERITY_ERROR,
                "extrato.totais_debitos",
                f"sum(real debits) = {real_debit} != header.saidas_total = {s.saidas_total}",
                diff={"computed": str(real_debit), "expected": str(s.saidas_total)},
            )
        )

    if s.sweep_credit_total is not None and sweep_credit != s.sweep_credit_total:
        out.append(
            Warning_(
                SEVERITY_WARN,
                "extrato.sweep_credit",
                f"sum(sweep credits) = {sweep_credit} != totalizador.entrada = {s.sweep_credit_total}",
                diff={"computed": str(sweep_credit), "expected": str(s.sweep_credit_total)},
            )
        )

    if s.sweep_debit_total is not None and sweep_debit != s.sweep_debit_total:
        out.append(
            Warning_(
                SEVERITY_WARN,
                "extrato.sweep_debit",
                f"sum(sweep debits) = {sweep_debit} != totalizador.saida = {s.sweep_debit_total}",
                diff={"computed": str(sweep_debit), "expected": str(s.sweep_debit_total)},
            )
        )

    if s.opening_balance is not None and s.closing_balance is not None:
        computed_closing = s.opening_balance + real_credit - real_debit
        if not approx_eq(computed_closing, s.closing_balance, "0.01"):
            out.append(
                Warning_(
                    SEVERITY_WARN,
                    "extrato.saldo_final",
                    f"opening + real_flows = {computed_closing} != closing_balance = {s.closing_balance}",
                    diff={"computed": str(computed_closing), "expected": str(s.closing_balance)},
                )
            )

    for field in ("entradas_total", "saidas_total", "opening_balance", "closing_balance"):
        if getattr(s, field) is None:
            out.append(
                Warning_(
                    SEVERITY_INFO,
                    "extrato.summary_field_missing",
                    f"summary field '{field}' not parsed; its reconciliation check was skipped",
                )
            )

    return out


def validate_fatura(result) -> list[Warning_]:
    s = result.summary
    out: list[Warning_] = []

    sum_dom = sum((t.amount_brl for t in result.transactions if not t.is_international), Decimal(0))
    sum_intl = sum((t.amount_brl for t in result.transactions if t.is_international), Decimal(0))

    if s.domestic_subtotal is not None and sum_dom != s.domestic_subtotal:
        out.append(
            Warning_(
                SEVERITY_ERROR,
                "fatura.lancamentos_nacional",
                f"sum(domestic) = {sum_dom} != Lançamentos no cartão = {s.domestic_subtotal}",
                diff={"computed": str(sum_dom), "expected": str(s.domestic_subtotal)},
            )
        )

    if s.international_subtotal is not None:
        expected_intl = s.international_subtotal - (s.international_credits or Decimal(0))
        if sum_intl != expected_intl:
            out.append(
                Warning_(
                    SEVERITY_ERROR,
                    "fatura.lancamentos_internacional",
                    f"sum(intl) = {sum_intl} != transações inter − créditos = {expected_intl}",
                    diff={"computed": str(sum_intl), "expected": str(expected_intl)},
                )
            )

    if s.current_charges is not None:
        computed = sum_dom + sum_intl + (s.iof_repasse or Decimal(0))
        if computed != s.current_charges:
            out.append(
                Warning_(
                    SEVERITY_ERROR,
                    "fatura.total_lancamentos",
                    f"sum(dom+intl+iof) = {computed} != current_charges = {s.current_charges}",
                    diff={"computed": str(computed), "expected": str(s.current_charges)},
                )
            )

    sum_pay = sum((p.amount for p in result.payments), Decimal(0))

    if s.payment_amount is not None and result.payments:
        if not approx_eq(sum_pay, s.payment_amount, "0.01"):
            out.append(
                Warning_(
                    SEVERITY_ERROR,
                    "fatura.pagamentos",
                    f"sum(payments) = {sum_pay} != summary payment_amount = {s.payment_amount}",
                    diff={"computed": str(sum_pay), "expected": str(s.payment_amount)},
                )
            )

    if s.previous_total is not None and s.current_charges is not None and s.total is not None:
        computed_total = s.previous_total + sum_pay + s.current_charges
        if not approx_eq(computed_total, s.total, "0.02"):
            out.append(
                Warning_(
                    SEVERITY_ERROR,
                    "fatura.equacao_fatura",
                    f"previous + payments + charges = {computed_total} != total = {s.total}",
                    diff={"computed": str(computed_total), "expected": str(s.total)},
                )
            )

    for field in ("posting_date", "previous_total", "current_charges", "total"):
        if getattr(s, field) is None:
            out.append(
                Warning_(
                    SEVERITY_INFO,
                    "fatura.summary_field_missing",
                    f"summary field '{field}' not parsed; its reconciliation check was skipped",
                )
            )

    return out

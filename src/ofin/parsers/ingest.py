from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .common import deterministic_tx_id, sha256_bytes
from .extrato_itau_v1 import parse_extrato
from .fatura_itau_v1 import parse_fatura
from .pdf import pdftotext_layout
from .registry import detect_document_type
from .validators import (
    Warning_,
    validate_extrato,
    validate_fatura,
)

ACCOUNT_CHECKING_ID = "itau-checking-17236-5"
ACCOUNT_CREDIT_ID = "itau-credit-platinum-9132"
ITEM_CHECKING_ID = "itau-pdf-checking"
ITEM_CREDIT_ID = "itau-pdf-credit"


def _jsonable(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def ingest_path(pdf_path: str | Path) -> dict:
    """Parse a single PDF and return a structured report dict (no DB writes)."""
    p = Path(pdf_path)
    data = p.read_bytes()
    file_sha256 = sha256_bytes(data)
    doc_id = file_sha256[:32]

    doc_type, parser_version, fingerprint = detect_document_type(pdf_path)

    if doc_type == "extrato":
        text = pdftotext_layout(pdf_path)
        parsed = parse_extrato(text)
        warnings = validate_extrato(parsed)
        return _extrato_report(p, parsed, warnings, doc_id, file_sha256, parser_version, fingerprint, len(data), text)

    if doc_type == "fatura":
        parsed = parse_fatura(pdf_path)
        warnings = validate_fatura(parsed)
        text = pdftotext_layout(pdf_path)
        return _fatura_report(p, parsed, warnings, doc_id, file_sha256, parser_version, fingerprint, len(data), text)

    return {
        "source_path": str(p),
        "doc_type": "unknown",
        "parser_version": parser_version,
        "doc_id": doc_id,
        "file_sha256": file_sha256,
        "fingerprint": fingerprint,
        "warnings": [{"severity": "error", "code": "unknown_document", "message": "doc type could not be detected"}],
    }


def _extrato_report(p, parsed, warnings, doc_id, sha, version, fp, size, raw_text):
    s = parsed.summary
    summary = {
        "agency": s.agency,
        "account": s.account,
        "period_year": s.period_year,
        "period_month": s.period_month,
        "opening_balance": str(s.opening_balance) if s.opening_balance is not None else None,
        "opening_date": s.opening_date.isoformat() if s.opening_date else None,
        "closing_balance": str(s.closing_balance) if s.closing_balance is not None else None,
        "closing_date": s.closing_date.isoformat() if s.closing_date else None,
        "entradas_total": str(s.entradas_total) if s.entradas_total is not None else None,
        "saidas_total": str(s.saidas_total) if s.saidas_total is not None else None,
        "sweep_credit_total": str(s.sweep_credit_total) if s.sweep_credit_total is not None else None,
        "sweep_debit_total": str(s.sweep_debit_total) if s.sweep_debit_total is not None else None,
        "saldo_anterior_ledger": str(s.saldo_anterior_ledger) if s.saldo_anterior_ledger is not None else None,
        "saldo_final_ledger": str(s.saldo_final_ledger) if s.saldo_final_ledger is not None else None,
        "saldo_cc_ledger": str(s.saldo_cc_ledger) if s.saldo_cc_ledger is not None else None,
    }
    txs = [
        {
            "date": t.when.isoformat(),
            "description": t.description,
            "amount": str(t.amount),
            "balance_after": str(t.balance_after) if t.balance_after is not None else None,
            "category": t.category,
            "is_sweep": t.is_sweep,
            "is_interest": t.is_interest,
            "raw_line": t.raw_line,
        }
        for t in parsed.transactions
    ]
    return {
        "source_path": str(p),
        "doc_type": "extrato",
        "issuer": "itau",
        "parser_version": version,
        "fingerprint": fp,
        "doc_id": doc_id,
        "file_sha256": sha,
        "file_size": size,
        "account_id": ACCOUNT_CHECKING_ID,
        "summary": summary,
        "transactions": txs,
        "cdb_snapshots": [
            {"date": c.when.isoformat(), "cdb_balance": str(c.cdb_balance)}
            for c in parsed.cdb_snapshots
        ],
        "warnings": [_w_to_dict(w) for w in warnings],
        "raw_text_len": len(raw_text),
    }


def _fatura_report(p, parsed, warnings, doc_id, sha, version, fp, size, raw_text):
    s = parsed.summary
    summary = {
        "card_brand": s.card_brand,
        "card_last4": s.card_last4,
        "posting_date": s.posting_date.isoformat() if s.posting_date else None,
        "due_date": s.due_date.isoformat() if s.due_date else None,
        "emission_date": s.emission_date.isoformat() if s.emission_date else None,
        "next_close_estimated": s.next_close_estimated.isoformat() if s.next_close_estimated else None,
        "previous_total": str(s.previous_total) if s.previous_total is not None else None,
        "payment_amount": str(s.payment_amount) if s.payment_amount is not None else None,
        "payment_date": s.payment_date.isoformat() if s.payment_date else None,
        "financed_balance": str(s.financed_balance) if s.financed_balance is not None else None,
        "current_charges": str(s.current_charges) if s.current_charges is not None else None,
        "total": str(s.total) if s.total is not None else None,
        "limit_total": str(s.limit_total) if s.limit_total is not None else None,
        "limit_available": str(s.limit_available) if s.limit_available is not None else None,
        "limit_used": str(s.limit_used) if s.limit_used is not None else None,
        "domestic_subtotal": str(s.domestic_subtotal) if s.domestic_subtotal is not None else None,
        "international_subtotal": str(s.international_subtotal) if s.international_subtotal is not None else None,
        "iof_repasse": str(s.iof_repasse) if s.iof_repasse is not None else None,
        "international_total_with_iof": str(s.international_total_with_iof) if s.international_total_with_iof is not None else None,
    }
    txs = [
        {
            "date": t.when.isoformat(),
            "merchant": t.merchant,
            "category": t.category,
            "city": t.city,
            "amount_brl": str(t.amount_brl),
            "fx_original_value": str(t.fx_original_value) if t.fx_original_value is not None else None,
            "fx_currency": t.fx_currency,
            "fx_rate": str(t.fx_rate) if t.fx_rate is not None else None,
            "is_international": t.is_international,
            "raw_line": t.raw_line,
        }
        for t in parsed.transactions
    ]
    payments = [
        {
            "date": pmt.when.isoformat(),
            "description": pmt.description,
            "amount": str(pmt.amount),
            "raw_line": pmt.raw_line,
        }
        for pmt in parsed.payments
    ]
    period_year = parsed.summary.posting_date.year if parsed.summary.posting_date else None
    period_month = parsed.summary.posting_date.month if parsed.summary.posting_date else None
    return {
        "source_path": str(p),
        "doc_type": "fatura",
        "issuer": "itau",
        "parser_version": version,
        "fingerprint": fp,
        "doc_id": doc_id,
        "file_sha256": sha,
        "file_size": size,
        "account_id": ACCOUNT_CREDIT_ID,
        "period_year": period_year,
        "period_month": period_month,
        "summary": summary,
        "payments": payments,
        "transactions": txs,
        "warnings": [_w_to_dict(w) for w in warnings],
        "raw_text_len": len(raw_text),
    }


def _w_to_dict(w: Warning_) -> dict:
    return {
        "severity": w.severity,
        "code": w.code,
        "message": w.message,
        "raw_line": w.raw_line,
        "diff": w.diff,
    }


def dry_run_dir(directory: str | Path) -> dict:
    """Parse every PDF in `directory` and return aggregated report (no DB writes)."""
    d = Path(directory)
    pdfs = sorted(d.glob("*.pdf"))
    docs = []
    counts = {"extrato": 0, "fatura": 0, "unknown": 0}
    sev_counts = {"error": 0, "warn": 0, "info": 0}
    for pdf in pdfs:
        try:
            r = ingest_path(pdf)
        except Exception as e:
            r = {
                "source_path": str(pdf),
                "doc_type": "error",
                "warnings": [{"severity": "error", "code": "parse_exception", "message": repr(e)}],
            }
        docs.append(r)
        counts[r.get("doc_type", "unknown")] = counts.get(r.get("doc_type", "unknown"), 0) + 1
        for w in r.get("warnings", []):
            sev = w.get("severity", "info")
            sev_counts[sev] = sev_counts.get(sev, 0) + 1
    return {
        "directory": str(d),
        "total_pdfs": len(pdfs),
        "doc_type_counts": counts,
        "warnings_by_severity": sev_counts,
        "docs": docs,
    }

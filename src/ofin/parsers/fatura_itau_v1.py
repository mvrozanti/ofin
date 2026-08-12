from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from .common import (
    BRL,
    parse_brl,
    parse_date_full,
    strip_accents,
)
from .categorize import classify_fatura
from .pdf import pdf_rows

NUM = r"\d{1,3}(?:\.\d{3})*,\d{2}"
NUM_TOKEN_RE = re.compile(rf"^-?{NUM}-?$")
DATE_TOKEN_RE = re.compile(r"^(\d{2})/(\d{2})$")

SIGN_ADJ_GAP = 8.0


def _sign_token_for(prev: dict | None, num_word: dict) -> dict | None:
    """Itaú prints negatives as a standalone '-' word right before the value
    ('-' x1 and value x0 nearly touch). Returns the sign word, or None."""
    if prev is None or prev["text"] != "-":
        return None
    if num_word["x0"] - prev["x1"] > SIGN_ADJ_GAP:
        return None
    return prev

FX_CCYS = {"USD", "EUR", "BRL", "GBP", "CHF", "ARS", "JPY", "CAD", "AUD"}


@dataclass(slots=True)
class FaturaPayment:
    when: date
    description: str
    amount: Decimal
    raw_line: str


@dataclass(slots=True)
class FaturaTx:
    when: date
    merchant: str
    category: str | None
    city: str | None
    amount_brl: Decimal
    fx_original_value: Decimal | None
    fx_currency: str | None
    fx_rate: Decimal | None
    is_international: bool
    raw_line: str


@dataclass(slots=True)
class FaturaSummary:
    card_brand: str
    card_last4: str
    titular: str | None
    posting_date: date | None
    due_date: date | None
    emission_date: date | None
    next_close_estimated: date | None
    previous_total: Decimal | None
    payment_amount: Decimal | None
    payment_date: date | None
    financed_balance: Decimal | None
    current_charges: Decimal | None
    total: Decimal | None
    limit_total: Decimal | None
    limit_available: Decimal | None
    limit_used: Decimal | None
    domestic_subtotal: Decimal | None
    international_subtotal: Decimal | None
    international_credits: Decimal | None
    iof_repasse: Decimal | None
    international_total_with_iof: Decimal | None


@dataclass(slots=True)
class FaturaParseResult:
    summary: FaturaSummary
    payments: list[FaturaPayment] = field(default_factory=list)
    transactions: list[FaturaTx] = field(default_factory=list)
    warnings: list[tuple[str, str, str, str | None]] = field(default_factory=list)


def _norm(s: str) -> str:
    return strip_accents(s).strip().lower()


def _denoise(s: str) -> str:
    return re.sub(r"\s+", "", strip_accents(s)).lower()


def _row_text(row: list[dict], sep: str = " ") -> str:
    return sep.join(w["text"] for w in row)


def _is_date_token(text: str) -> bool:
    return bool(DATE_TOKEN_RE.match(text))


def _is_num_token(text: str) -> bool:
    return bool(NUM_TOKEN_RE.match(text))


def _flatten(rows: list[list[list[dict]]]) -> list[list[dict]]:
    out: list[list[dict]] = []
    for page in rows:
        for row in page:
            out.append(row)
    return out


@dataclass(slots=True)
class _TxCandidate:
    row_idx: int
    date_token: dict
    words: list[dict]
    value_token: dict | None
    column_x0: float
    sign_token: dict | None = None

    @property
    def description(self) -> str:
        return " ".join(
            w["text"]
            for w in self.words
            if w is not self.date_token and w is not self.value_token and w is not self.sign_token
        ).strip()

    @property
    def value(self) -> Decimal | None:
        if self.value_token is None:
            return None
        v = parse_brl(self.value_token["text"])
        if v is None:
            return None
        if self.sign_token is not None and v > 0:
            v = -v
        return v


COLUMN_SPAN = 200.0


def _extract_candidates_in_row(row: list[dict], row_idx: int) -> list[_TxCandidate]:
    out: list[_TxCandidate] = []
    current: _TxCandidate | None = None
    for w in row:
        if DATE_TOKEN_RE.match(w["text"]):
            if current is not None and current.value_token is not None:
                out.append(current)
                current = _TxCandidate(row_idx=row_idx, date_token=w, words=[w], value_token=None, column_x0=w["x0"])
                continue
            if current is None:
                current = _TxCandidate(row_idx=row_idx, date_token=w, words=[w], value_token=None, column_x0=w["x0"])
                continue
            if w["x0"] > current.column_x0 + COLUMN_SPAN:
                # date in a different column; emit current as incomplete-aborted (we drop it)
                # and start a new candidate for this date
                current = _TxCandidate(row_idx=row_idx, date_token=w, words=[w], value_token=None, column_x0=w["x0"])
                continue
            current.words.append(w)
            continue
        if current is None:
            continue
        if w["x0"] > current.column_x0 + COLUMN_SPAN:
            continue
        prev = current.words[-1] if current.words else None
        current.words.append(w)
        if NUM_TOKEN_RE.match(w["text"]):
            current.value_token = w
            current.sign_token = _sign_token_for(prev, w)
    if current is not None and current.value_token is not None:
        out.append(current)
    return out


def _has_fx_followup(
    rows: list[list[dict]], start: int, x_anchor: float, span: int = 6
) -> tuple[Decimal | None, str | None, Decimal | None]:
    """Look ahead for FX continuation rows in the same column as anchor.

    Returns (original_value, currency, rate) — any may be None.
    """
    orig_val: Decimal | None = None
    ccy: str | None = None
    rate: Decimal | None = None
    for i in range(start + 1, min(start + span + 1, len(rows))):
        row = rows[i]
        toks = [w for w in row if x_anchor - 10 <= w["x0"] <= x_anchor + 200]
        if not toks:
            continue
        texts = [w["text"] for w in toks]
        joined = " ".join(texts)
        if any(t in FX_CCYS for t in texts):
            for j, t in enumerate(texts):
                if t in FX_CCYS and j >= 1 and NUM_TOKEN_RE.match(texts[j - 1]):
                    orig_val = parse_brl(texts[j - 1])
                    ccy = t
                    break
        m = re.search(r"R\$\s*(" + NUM + r")", joined)
        if m and "onvers" in joined.lower():
            rate = parse_brl(m.group(1))
        if orig_val is not None and rate is not None:
            break
        if DATE_TOKEN_RE.match(texts[0]):
            break
    return orig_val, ccy, rate


def _parse_summary(rows: list[list[dict]]) -> FaturaSummary:
    def _value_in_row(row: list[dict], ln: str) -> str | None:
        positions = [(w["x0"], w["x1"], w["text"], _denoise(w["text"])) for w in row]
        glued = "".join(t[3] for t in positions)
        idx = glued.find(ln)
        if idx < 0:
            return None
        running = 0
        label_end_x = None
        for x0, x1, _, dn in positions:
            running_end = running + len(dn)
            if running <= idx + len(ln) - 1 < running_end:
                label_end_x = x1
                break
            running = running_end
        after = [w for w in row if label_end_x is None or w["x0"] >= label_end_x]
        for i, w in enumerate(after):
            if _is_num_token(w["text"]):
                sign = _sign_token_for(after[i - 1] if i > 0 else None, w)
                return ("-" + w["text"]) if sign is not None else w["text"]
        return None

    def find_value_after(label: str) -> str | None:
        ln = _denoise(label)
        for row in rows:
            if ln not in _denoise(_row_text(row)):
                continue
            v = _value_in_row(row, ln)
            if v is not None:
                return v
        return None

    def find_value_sum(label: str) -> Decimal | None:
        """Sum the label's value over every matching row (multi-card faturas
        print one 'Lançamentos no cartão (final NNNN)' subtotal per card)."""
        ln = _denoise(label)
        total: Decimal | None = None
        for row in rows:
            if ln not in _denoise(_row_text(row)):
                continue
            v = _value_in_row(row, ln)
            if v is None:
                continue
            parsed = parse_brl(v)
            if parsed is None:
                continue
            total = parsed if total is None else total + parsed
        return total

    def find_date_in(label: str) -> date | None:
        ln = _denoise(label)
        for row in rows:
            text = _row_text(row)
            if ln in _denoise(text):
                m = re.search(r"(\d{2}/\d{2}/\d{4})", text)
                if m:
                    return parse_date_full(m.group(1))
        return None

    previous_total = parse_brl(find_value_after("Total da fatura anterior") or "")
    payment_amount = parse_brl(find_value_after("Pagamento efetuado em") or "")
    payment_date = find_date_in("Pagamento efetuado em")
    financed = parse_brl(find_value_after("Saldo financiado") or "")
    current_charges = parse_brl(find_value_after("Lançamentos atuais") or "")
    total = parse_brl(find_value_after("Total desta fatura") or "")
    posting = find_date_in("Postagem:")
    due = find_date_in("Vencimento:")
    emission = find_date_in("Emissão:")
    next_close = find_date_in("Previsão próx. Fechamento")
    if next_close is None:
        next_close = find_date_in("Previsão para o próximo fechamento")

    limit_total = parse_brl(find_value_after("Limite total de crédito") or "")
    limit_available = parse_brl(find_value_after("Limite disponível") or "")
    limit_used = parse_brl(find_value_after("Limite total utilizado") or "")

    domestic_subtotal = find_value_sum("Lançamentos no cartão")
    international_subtotal = parse_brl(find_value_after("Total transações inter") or "")
    international_credits = find_value_sum("Crédito cartão final")
    iof_repasse = parse_brl(find_value_after("Repasse de IOF em R$") or "")
    international_total_with_iof = parse_brl(find_value_after("Total lançamentos inter") or "")

    last4 = ""
    for row in rows:
        text = _row_text(row)
        m = re.search(r"(\d{4}\.X+\.X+\.\d{4})", text)
        if m:
            last4 = m.group(1)[-4:]
            break

    return FaturaSummary(
        card_brand="Platinum",
        card_last4=last4,
        titular=None,
        posting_date=posting,
        due_date=due,
        emission_date=emission,
        next_close_estimated=next_close,
        previous_total=previous_total,
        payment_amount=payment_amount,
        payment_date=payment_date,
        financed_balance=financed,
        current_charges=current_charges,
        total=total,
        limit_total=limit_total,
        limit_available=limit_available,
        limit_used=limit_used,
        domestic_subtotal=domestic_subtotal,
        international_subtotal=international_subtotal,
        international_credits=international_credits,
        iof_repasse=iof_repasse,
        international_total_with_iof=international_total_with_iof,
    )


def _infer_year(month: int, anchor: date) -> int:
    yr = anchor.year
    if month > anchor.month + 1:
        yr -= 1
    return yr


def parse_fatura(pdf_path: str | Path) -> FaturaParseResult:
    pages = pdf_rows(pdf_path)
    rows = _flatten(pages)
    summary = _parse_summary(rows)
    result = FaturaParseResult(summary=summary)

    anchor_date = summary.posting_date or summary.emission_date or summary.due_date
    if anchor_date is None:
        result.warnings.append(
            (
                "error",
                "anchor_missing",
                "no posting/emission/due date found; cannot infer transaction years",
                None,
            )
        )
    else:
        _parse_candidates(rows, anchor_date, result)

    if not result.payments and summary.payment_amount is not None:
        _synthesize_payment_from_summary(summary, result)

    return result


def _parse_candidates(rows: list[list[dict]], anchor_date: date, result: FaturaParseResult) -> None:
    excl_zones = _parcelados_zones(rows)

    candidates: list[_TxCandidate] = []
    for ri, row in enumerate(rows):
        for c in _extract_candidates_in_row(row, ri):
            if _in_any_zone(c, excl_zones):
                continue
            candidates.append(c)

    for cand in candidates:
        when_m = DATE_TOKEN_RE.match(cand.date_token["text"])
        if not when_m:
            continue
        mm = int(when_m.group(2))
        dd = int(when_m.group(1))
        year = _infer_year(mm, anchor_date)
        raw_line = _row_text(rows[cand.row_idx])[:240]
        try:
            when = date(year, mm, dd)
        except ValueError:
            result.warnings.append(
                ("warn", "date_parse", f"invalid date token '{cand.date_token['text']}'", raw_line)
            )
            continue
        value = cand.value
        if value is None:
            continue
        desc = cand.description

        if _looks_like_payment(desc, value):
            result.payments.append(
                FaturaPayment(when=when, description=desc or "PAGAMENTO", amount=value, raw_line=raw_line)
            )
            continue

        fx_val, fx_ccy, fx_rate = _has_fx_followup(rows, cand.row_idx, cand.column_x0)
        is_intl = fx_val is not None or fx_rate is not None

        category_hint, city = _extract_category_city(rows, cand.row_idx, cand.column_x0)
        category = classify_fatura(desc, category_hint=category_hint, is_international=is_intl)

        result.transactions.append(
            FaturaTx(
                when=when,
                merchant=_clean_merchant(desc),
                category=category,
                city=city,
                amount_brl=value,
                fx_original_value=fx_val,
                fx_currency=fx_ccy,
                fx_rate=fx_rate,
                is_international=is_intl,
                raw_line=raw_line,
            )
        )


def _synthesize_payment_from_summary(summary: FaturaSummary, result: FaturaParseResult) -> None:
    """Old-layout faturas print the payment only in the summary box, never as
    a lançamento row — synthesize it so every month carries its payment tx."""
    when = summary.payment_date or summary.posting_date
    if when is None:
        return
    amount = summary.payment_amount
    if amount > 0:
        result.warnings.append(
            ("warn", "payment_sign", f"summary payment amount came out positive: {amount}", None)
        )
        amount = -amount
    result.payments.append(
        FaturaPayment(
            when=when,
            description="PAGAMENTO EFETUADO",
            amount=amount,
            raw_line="(summary) pagamento efetuado",
        )
    )


ZONE_HEADER_PHRASES = ("comprasparceladas", "simulacaodecompras")


def _word_at_glued_index(row: list[dict], idx: int) -> dict | None:
    running = 0
    for w in row:
        running_end = running + len(_denoise(w["text"]))
        if running <= idx < running_end:
            return w
        running = running_end
    return None


def _parcelados_zones(rows: list[list[dict]]) -> list[tuple[int, int, float, float]]:
    """Detect 'Compras parceladas' / 'Simulação de Compras' zones.

    Candidates within these zones are billing-future placeholders, not current
    fatura lançamentos, so they must be filtered out before reconciliation.
    A zone is anchored at the x0 of the word where the header phrase itself
    starts (headers can share a row with a real transaction in the other
    column, and some layouts fragment words into sub-tokens) and ends at the
    last row of its page.
    """
    last_row_of_page: dict[int, int] = {}
    for ri, row in enumerate(rows):
        if row:
            last_row_of_page[row[0]["page"]] = ri
    zones: list[tuple[int, int, float, float]] = []
    for ri, row in enumerate(rows):
        glued = "".join(_denoise(w["text"]) for w in row)
        for phrase in ZONE_HEADER_PHRASES:
            idx = glued.find(phrase)
            if idx < 0:
                continue
            anchor = _word_at_glued_index(row, idx)
            if anchor is None:
                continue
            end_ri = last_row_of_page.get(row[0]["page"], len(rows) - 1)
            zones.append((ri, end_ri, anchor["x0"] - 5, anchor["x0"] + COLUMN_SPAN))
            break
    return zones


def _in_any_zone(cand: _TxCandidate, zones: list[tuple[int, int, float, float]]) -> bool:
    for start_ri, end_ri, x_lo, x_hi in zones:
        if start_ri <= cand.row_idx <= end_ri and x_lo <= cand.column_x0 <= x_hi:
            return True
    return False


def _looks_like_payment(desc: str, value: Decimal) -> bool:
    if value >= 0:
        return False
    d = _norm(desc).strip()
    return d.startswith("pagamento")


def _clean_merchant(desc: str) -> str:
    desc = re.sub(r"\s+", " ", desc).strip()
    return desc


def _extract_category_city(
    rows: list[list[dict]], start: int, x_anchor: float, span: int = 3
) -> tuple[str | None, str | None]:
    for i in range(start + 1, min(start + span + 1, len(rows))):
        toks = [w for w in rows[i] if x_anchor - 10 <= w["x0"] <= x_anchor + 200]
        if not toks:
            continue
        if any(DATE_TOKEN_RE.match(w["text"]) for w in toks):
            continue
        if any(NUM_TOKEN_RE.match(w["text"]) for w in toks):
            continue
        texts = [w["text"] for w in toks]
        if any(t in FX_CCYS for t in texts):
            continue
        joined = " ".join(texts).strip()
        if not joined:
            continue
        parts = joined.split(None, 1)
        return parts[0], parts[1] if len(parts) > 1 else None
    return None, None

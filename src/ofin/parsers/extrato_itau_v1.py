from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .common import (
    BRL,
    MONTHS_PT,
    parse_brl,
    parse_date_short,
    strip_accents,
)

NUM = r"\d{1,3}(?:\.\d{3})*,\d{2}"
NUM_RE = re.compile(rf"^-?{NUM}-?$")
HEADER_RE = re.compile(
    r"extrato mensal\s+ag\s+(\d+)\s+cc\s+([\d-]+)\s+(\w{3})\s+(\d{4})",
    re.IGNORECASE,
)
SALDOS_TWO_LINE_RE = re.compile(
    r"saldo em\s+(\d{2}/\d{2}/\d{2})\s+saldo em\s+(\d{2}/\d{2}/\d{2})"
    r"[\s\S]{0,400}?R\$\s*(" + NUM + r")\s+R\$\s*(" + NUM + r")",
    re.IGNORECASE,
)
DATE_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<date>\d{2}/\d{2})\s{2,}(?P<rest>.+)$")
SUBLINE_DATE_RE = re.compile(r"^(?P<indent>\s*)(?P<date>\d{2}/\d{2})\s*$")

LEGEND_PHRASES = (
    "A = agendamento",
    "B = ações movimentadas",
    "pela Bolsa de Valores",
    "C = crédito a compensar",
    "D = débito a compensar",
    "G = aplicação programada",
    "P = poupança automática",
    "Para demais siglas, consulte as Notas",
    "Explicativas no final do extrato",
)

PAGE_FOOTER_RE = re.compile(
    r"^\s*Este material está disponível.*$", re.MULTILINE
)
PAGE_HEADER_RE = re.compile(
    r"^.*extrato mensal\s+ag\s+\d+\s+cc\s+[\d-]+\s+\w{3}\s+\d{4}\s+\d+\s*\|\s*\d+.*$",
    re.MULTILINE | re.IGNORECASE,
)
TABLE_HEADER_RE = re.compile(
    r"^.*data\s+descrição\s+entradas\s+R\$.*$", re.MULTILINE | re.IGNORECASE
)
TABLE_HEADER2_RE = re.compile(
    r"^.*\(créditos\)\s+\(débitos\).*$", re.MULTILINE | re.IGNORECASE
)


def _scrub_movement(text: str) -> str:
    for phrase in LEGEND_PHRASES:
        text = text.replace(phrase, " " * len(phrase))
    text = PAGE_FOOTER_RE.sub("", text)
    text = PAGE_HEADER_RE.sub("", text)
    text = TABLE_HEADER_RE.sub("", text)
    text = TABLE_HEADER2_RE.sub("", text)
    return text

SWEEP_CREDIT_DESC = {
    "res aplic aut mais",
    "resgate aplic aut mais",
}
SWEEP_DEBIT_DESC = {
    "apl aplic aut mais",
    "aplicacao aplic aut mais",
}
INTEREST_DESC = {"rend pago aplic aut mais"}
CDB_BALANCE_DESC = {"saldo aplic aut mais"}
SUMMARY_DESC_PREFIXES = (
    "saldo anterior",
    "saldo em c/c",
    "saldo final",
    "totalizador de aplicacoes",
    "na conta corrente",
    "(1) os valores referentes",
    "data",
    "descricao",
    "(creditos)",
    "(debitos)",
    "a = agendamento",
    "b = acoes movimentadas",
    "pela bolsa de valores",
    "c = credito a compensar",
    "d = debito a compensar",
    "g = aplicacao programada",
    "p = poupanca automatica",
    "para demais siglas",
    "explicativas no final",
)


def _norm(s: str) -> str:
    return strip_accents(s).strip().lower()


def _is_summary_label(desc_norm: str) -> bool:
    if not desc_norm:
        return True
    return any(desc_norm.startswith(p) for p in SUMMARY_DESC_PREFIXES)


@dataclass(slots=True)
class ExtratoTx:
    when: date
    description: str
    description_norm: str
    amount: Decimal
    balance_after: Decimal | None
    category: str
    raw_line: str
    is_sweep: bool = False
    is_interest: bool = False
    is_internal: bool = False


@dataclass(slots=True)
class ExtratoCdbSnapshot:
    when: date
    cdb_balance: Decimal


@dataclass(slots=True)
class ExtratoSummary:
    agency: str
    account: str
    period_year: int
    period_month: int
    opening_balance: Decimal | None
    opening_date: date | None
    closing_balance: Decimal | None
    closing_date: date | None
    entradas_total: Decimal | None
    saidas_total: Decimal | None
    sweep_credit_total: Decimal | None = None
    sweep_debit_total: Decimal | None = None
    saldo_anterior_ledger: Decimal | None = None
    saldo_final_ledger: Decimal | None = None
    saldo_cc_ledger: Decimal | None = None


@dataclass(slots=True)
class ExtratoParseResult:
    summary: ExtratoSummary
    transactions: list[ExtratoTx] = field(default_factory=list)
    cdb_snapshots: list[ExtratoCdbSnapshot] = field(default_factory=list)
    warnings: list[tuple[str, str, str, str | None]] = field(default_factory=list)


def _month_to_int(token: str) -> int | None:
    t = strip_accents(token).lower()[:3]
    return MONTHS_PT.get(t)


def parse_header(text: str) -> tuple[str, str, int, int] | None:
    m = HEADER_RE.search(text)
    if not m:
        return None
    agency = m.group(1)
    account = m.group(2)
    month_tok = m.group(3)
    year = int(m.group(4))
    month = _month_to_int(month_tok)
    if month is None:
        return None
    return agency, account, month, year


def _extract_tokens(line: str) -> tuple[str, list[str]]:
    parts = re.split(r"\s{2,}", line.rstrip())
    parts = [p for p in parts if p != ""]
    if not parts:
        return "", []
    nums: list[str] = []
    desc_parts: list[str] = []
    for p in parts:
        if NUM_RE.match(p.replace(" ", "")):
            nums.append(p.strip())
        else:
            desc_parts.append(p.strip())
    desc = " ".join(desc_parts).strip()
    return desc, nums


SELF_NAME_TOKENS = ("marcelo", "vironda", "rozanti")


def _is_self(desc_norm: str) -> bool:
    return any(tok in desc_norm for tok in SELF_NAME_TOKENS)


def _classify(desc: str, desc_norm: str) -> tuple[str, bool, bool, bool]:
    """returns (category, is_sweep, is_interest, is_internal)."""
    if desc_norm in SWEEP_CREDIT_DESC:
        return "sweep_resgate", True, False, True
    if desc_norm in SWEEP_DEBIT_DESC:
        return "sweep_aplicacao", True, False, True
    if desc_norm in INTEREST_DESC:
        return "rendimento_cdb", False, True, False
    if desc_norm.startswith("est pix") or desc_norm.startswith("est tef") or desc_norm.startswith("est ted") or desc_norm.startswith("est on"):
        return "estorno", False, False, True
    if desc_norm.startswith("estorno"):
        return "estorno", False, False, True
    if "fatura" in desc_norm and ("itau" in desc_norm or "platinu" in desc_norm or "visa" in desc_norm):
        return "pagamento_cartao", False, False, True
    if desc_norm.startswith("tbi") or desc_norm.startswith("int itau click"):
        return "transferencia_propria", False, False, True
    if desc_norm.startswith("itau visa"):
        return "pagamento_cartao", False, False, True
    if desc_norm.startswith("dev pix"):
        return "devolucao_pix", False, False, False
    if desc_norm.startswith("dev "):
        return "devolucao", False, False, False
    if desc_norm.startswith("remuneracao") or "salario" in desc_norm[:20]:
        return "salario", False, False, False
    if desc_norm.startswith("credito cartao") or desc_norm.startswith("credito de cartao"):
        return "credito_cartao", False, False, False
    if desc_norm.startswith("ressarcimento"):
        return "ressarcimento", False, False, False
    if desc_norm.startswith("pix") and _is_self(desc_norm):
        return "pix_proprio", False, False, True
    if desc_norm.startswith("transf") and _is_self(desc_norm):
        return "transferencia_propria", False, False, True
    if desc_norm.startswith("pix"):
        return "pix", False, False, False
    if desc_norm.startswith("rshop") or desc_norm.startswith("rscss") or desc_norm.startswith("rsccs") or desc_norm.startswith("mobilepag"):
        return "compra_debito", False, False, False
    if desc_norm.startswith("on ifd") or "ifood" in desc_norm or "pay -ifood" in desc_norm or "pay-ifood" in desc_norm:
        return "ifood", False, False, False
    if desc_norm.startswith("pag boleto"):
        return "boleto", False, False, False
    if "pag tit banco" in desc_norm or desc_norm.startswith("mobilepag"):
        return "boleto", False, False, False
    if desc_norm.startswith("pagto salario"):
        return "salario", False, False, False
    if desc_norm.startswith("da ") or desc_norm.startswith("debito automatico") or desc_norm.startswith("db cob"):
        return "debito_automatico", False, False, False
    if desc_norm.startswith("sispag"):
        return "sispag", False, False, False
    if desc_norm.startswith("saque"):
        return "saque", False, False, False
    if (desc_norm.startswith("ted") or desc_norm.startswith("doc") or desc_norm.startswith("int ted")) and _is_self(desc_norm):
        return "transferencia_propria", False, False, True
    if desc_norm.startswith("ted") or desc_norm.startswith("doc") or desc_norm.startswith("int ted"):
        return "transferencia", False, False, False
    return "outros", False, False, False


def parse_extrato(text: str) -> ExtratoParseResult:
    header = parse_header(text)
    if not header:
        raise ValueError("extrato header not found")
    agency, account, month, year = header

    opening_balance = closing_balance = None
    opening_date = closing_date = None
    m = SALDOS_TWO_LINE_RE.search(text)
    if m:
        from .common import parse_date_full
        opening_date = parse_date_full(m.group(1))
        closing_date = parse_date_full(m.group(2))
        opening_balance = parse_brl(m.group(3))
        closing_balance = parse_brl(m.group(4))

    entradas_total = _extract_section_total(text, "entradas (créditos)")
    saidas_total = _extract_section_total(text, "saídas (débitos)")

    summary = ExtratoSummary(
        agency=agency,
        account=account,
        period_year=year,
        period_month=month,
        opening_balance=opening_balance,
        opening_date=opening_date,
        closing_balance=closing_balance,
        closing_date=closing_date,
        entradas_total=entradas_total,
        saidas_total=saidas_total,
    )

    result = ExtratoParseResult(summary=summary)

    movement_text = _slice_movement(text)
    if movement_text is None:
        result.warnings.append(
            ("error", "section_missing", "Conta Corrente | Movimentação not found", None)
        )
        return result

    movement_text = _scrub_movement(movement_text)
    _parse_movement(movement_text, summary.period_year, result)
    return result


SECTION_TOTAL_RE_TPL = (
    r"{label}\s*\n(?:.*\n){{0,12}}?\s*total\s+([\d.,]+)"
)


def _extract_section_total(text: str, label: str) -> Decimal | None:
    pat = re.compile(SECTION_TOTAL_RE_TPL.format(label=re.escape(label)), re.IGNORECASE)
    m = pat.search(text)
    if not m:
        return None
    return parse_brl(m.group(1))


def _slice_movement(text: str) -> str | None:
    start = re.search(r"Conta Corrente\s*\|\s*Movimentação", text, re.IGNORECASE)
    if not start:
        return None
    rest = text[start.end():]
    end = re.search(
        r"\n\s*Conta Corrente\s*\|\s*Aplicações Automáticas",
        rest,
        re.IGNORECASE,
    )
    if end:
        return rest[: end.start()]
    return rest


REPEAT_HEADER_TOKENS = (
    "extrato mensal",
    "ag 0428",
    "(créditos)",
    "(débitos)",
    "data",
    "descrição",
    "entradas r$",
    "saídas r$",
    "saldo r$",
    "este material",
)


def _parse_movement(text: str, year: int, result: ExtratoParseResult) -> None:
    current_date: date | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        lnorm = strip_accents(line).lower()
        if any(tok in lnorm for tok in REPEAT_HEADER_TOKENS):
            continue
        if "pela bolsa" in lnorm or "= agendamento" in lnorm:
            continue
        if "para demais siglas" in lnorm or "explicativas no final" in lnorm:
            continue

        m = DATE_LINE_RE.match(line)
        if m:
            current_date = parse_date_short(m.group("date"), year)
            rest = m.group("rest")
            _process_subline(rest, current_date, result)
            continue
        m2 = SUBLINE_DATE_RE.match(line)
        if m2:
            current_date = parse_date_short(m2.group("date"), year)
            continue
        _process_subline(line, current_date, result)


def _process_subline(text: str, when: date | None, result: ExtratoParseResult) -> None:
    if not text.strip():
        return
    desc, nums = _extract_tokens(text)
    if not desc and not nums:
        return
    desc_norm = _norm(desc)
    if not desc_norm:
        return

    if desc_norm.startswith("saldo anterior"):
        if nums:
            result.summary.saldo_anterior_ledger = parse_brl(nums[-1])
        return
    if desc_norm.startswith("saldo em c/c"):
        if nums:
            result.summary.saldo_cc_ledger = parse_brl(nums[-1])
        return
    if desc_norm.startswith("saldo final"):
        if nums:
            result.summary.saldo_final_ledger = parse_brl(nums[-1])
        return
    if desc_norm in CDB_BALANCE_DESC:
        if nums and when is not None:
            cdb = parse_brl(nums[-1])
            if cdb is not None:
                result.cdb_snapshots.append(ExtratoCdbSnapshot(when=when, cdb_balance=cdb))
        return
    if desc_norm.startswith("totalizador de aplicacoes") or desc_norm.startswith(
        "na conta corrente"
    ):
        if desc_norm.startswith("na conta corrente") and len(nums) >= 2:
            result.summary.sweep_credit_total = parse_brl(nums[0])
            debit_raw = parse_brl(nums[1])
            if debit_raw is not None:
                result.summary.sweep_debit_total = abs(debit_raw)
        return
    if _is_summary_label(desc_norm):
        return

    if when is None:
        result.warnings.append(
            ("warn", "orphan_line", f"transaction line before any date: {desc[:40]}", text[:200])
        )
        return
    if not nums:
        return

    amount_token = nums[0]
    amount = parse_brl(amount_token)
    if amount is None:
        result.warnings.append(
            ("warn", "amount_parse", f"could not parse amount '{amount_token}' on '{desc[:40]}'", text[:200])
        )
        return

    balance_after: Decimal | None = None
    if len(nums) >= 2:
        balance_after = parse_brl(nums[-1])

    category, is_sweep, is_interest, is_internal = _classify(desc, desc_norm)

    result.transactions.append(
        ExtratoTx(
            when=when,
            description=desc,
            description_norm=desc_norm,
            amount=amount,
            balance_after=balance_after,
            category=category,
            raw_line=text.strip()[:240],
            is_sweep=is_sweep,
            is_interest=is_interest,
            is_internal=is_internal,
        )
    )

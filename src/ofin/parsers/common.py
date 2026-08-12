from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

BRL_RE = re.compile(r"-?\s*(?:R\$\s*)?(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})\s*-?")
DATE_SHORT_RE = re.compile(r"^(\d{2})/(\d{2})$")
DATE_DDMM_RE = re.compile(r"(\d{2})/(\d{2})")
DATE_DDMMYY_RE = re.compile(r"(\d{2})/(\d{2})/(\d{2})$")
DATE_DDMMYYYY_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

MONTHS_PT = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}


BRL = Decimal


def parse_brl(s: str) -> Decimal | None:
    if s is None:
        return None
    raw = s.strip()
    if not raw:
        return None
    negative = False
    if raw.startswith("-") or raw.endswith("-"):
        negative = True
    cleaned = raw.replace("R$", "").replace(" ", "").replace("\xa0", "")
    cleaned = cleaned.replace("-", "")
    if not cleaned:
        return None
    if "," not in cleaned:
        return None
    int_part, _, dec_part = cleaned.rpartition(",")
    int_part = int_part.replace(".", "")
    if not int_part:
        int_part = "0"
    try:
        val = Decimal(f"{int_part}.{dec_part}")
    except Exception:
        return None
    return -val if negative else val


def parse_date_short(s: str, year: int) -> date | None:
    m = DATE_SHORT_RE.match(s.strip())
    if not m:
        return None
    try:
        return date(year, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def parse_date_full(s: str) -> date | None:
    m = DATE_DDMMYYYY_RE.search(s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = DATE_DDMMYY_RE.search(s)
    if m:
        yy = int(m.group(3))
        year = 2000 + yy if yy < 70 else 1900 + yy
        try:
            return date(year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fingerprint_text(text: str, lines: int = 20) -> str:
    head = []
    for ln in text.splitlines():
        s = normalize_spaces(strip_accents(ln).lower())
        s = re.sub(r"\d", "0", s)
        s = re.sub(r"r\$\s*0", "r$ 0", s)
        if s:
            head.append(s)
        if len(head) >= lines:
            break
    return hashlib.sha1("\n".join(head).encode("utf-8")).hexdigest()[:16]


def deterministic_tx_id(document_id: str, when: date, raw_line: str, amount: Decimal, occurrence: int = 0) -> str:
    payload = f"{document_id}|{when.isoformat()}|{raw_line}|{amount}|{occurrence}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def deterministic_doc_id(file_bytes: bytes) -> str:
    return sha256_bytes(file_bytes)[:32]


@dataclass(slots=True)
class ParsedAmount:
    value: Decimal
    sign: int

    @property
    def signed(self) -> Decimal:
        return self.value if self.sign >= 0 else -self.value


def _approx_eq(a: Decimal | None, b: Decimal | None, tol: Decimal = Decimal("0.01")) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def approx_eq(a: Decimal | None, b: Decimal | None, tol: str | Decimal = "0.01") -> bool:
    if isinstance(tol, str):
        tol = Decimal(tol)
    return _approx_eq(a, b, tol)

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .common import fingerprint_text, strip_accents
from .pdf import pdftotext_layout


_EXTRATO_MARK = re.compile(r"extrato mensal\s+ag\s+\d+\s+cc\s+[\d-]+", re.IGNORECASE)
_FATURA_MARK = re.compile(r"Resumo da fatura em R\$", re.IGNORECASE)


def detect_document_type(pdf_path: str | Path) -> tuple[str, str, str]:
    """Returns (doc_type, parser_version, layout_fingerprint)."""
    text = pdftotext_layout(pdf_path)
    norm = strip_accents(text).lower()
    fp = fingerprint_text(text)
    if "extrato mensal" in norm and "ag" in norm and "cc" in norm:
        return "extrato", "extrato_itau.v1", fp
    if "resumo da fatura" in norm or "lancamentos atuais" in norm or "lançamentos atuais" in norm:
        return "fatura", "fatura_itau.v1", fp
    return "unknown", "none", fp

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class PdftotextNotFound(RuntimeError):
    pass


class PdfExtractionFailed(RuntimeError):
    pass


def _binary() -> str:
    path = shutil.which("pdftotext")
    if not path:
        raise PdftotextNotFound(
            "pdftotext not found in PATH. Install poppler-utils "
            "(NixOS: `nix-shell -p poppler-utils`)."
        )
    return path


def _run(args: list[str], pdf_path: str) -> str:
    bin_ = _binary()
    proc = subprocess.run(
        [bin_, *args, pdf_path, "-"],
        capture_output=True,
        check=False,
        text=False,
    )
    if proc.returncode != 0:
        raise PdfExtractionFailed(
            f"pdftotext exited {proc.returncode} on {pdf_path}: "
            f"{proc.stderr.decode('utf-8', 'replace')[:500]}"
        )
    return proc.stdout.decode("utf-8", "replace")


def pdftotext_layout(pdf_path: str | Path) -> str:
    return _run(["-layout", "-nopgbrk"], str(pdf_path))


def pdftotext_raw(pdf_path: str | Path) -> str:
    return _run(["-raw", "-nopgbrk"], str(pdf_path))


def pdf_rows(pdf_path: str | Path) -> list[list[dict]]:
    """Returns pages of rows of word-dicts with bbox.

    Each word: {text, x0, x1, top, bottom, page}. Rows are sorted by `top`
    then x0. Words on the same logical line share a row.
    """
    import pdfplumber

    from collections import defaultdict

    pages_out: list[list[dict]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pi, page in enumerate(pdf.pages):
            words = page.extract_words(x_tolerance=1, y_tolerance=1, keep_blank_chars=False)
            rows: dict[int, list[dict]] = defaultdict(list)
            for w in words:
                ykey = round(w["top"])
                rows[ykey].append(
                    {
                        "text": w["text"],
                        "x0": float(w["x0"]),
                        "x1": float(w["x1"]),
                        "top": float(w["top"]),
                        "bottom": float(w["bottom"]),
                        "page": pi,
                    }
                )
            ordered = []
            for ykey in sorted(rows):
                row = sorted(rows[ykey], key=lambda w: w["x0"])
                ordered.append(row)
            pages_out.append(ordered)
    return pages_out

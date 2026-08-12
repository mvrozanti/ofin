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


ROW_Y_TOLERANCE = 1.5


def pdf_rows(pdf_path: str | Path) -> list[list[dict]]:
    """Returns pages of rows of word-dicts with bbox.

    Each word: {text, x0, x1, top, bottom, page}. Rows are sorted by `top`
    then x0. Words whose `top` is within ROW_Y_TOLERANCE of the row's first
    word share a row; hard round() buckets would split words that straddle
    a .5 boundary.
    """
    import pdfplumber

    pages_out: list[list[dict]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pi, page in enumerate(pdf.pages):
            words = page.extract_words(x_tolerance=1, y_tolerance=1, keep_blank_chars=False)
            words.sort(key=lambda w: (float(w["top"]), float(w["x0"])))
            rows: list[list[dict]] = []
            row_top: float | None = None
            for w in words:
                top = float(w["top"])
                if row_top is None or top - row_top > ROW_Y_TOLERANCE:
                    rows.append([])
                    row_top = top
                rows[-1].append(
                    {
                        "text": w["text"],
                        "x0": float(w["x0"]),
                        "x1": float(w["x1"]),
                        "top": top,
                        "bottom": float(w["bottom"]),
                        "page": pi,
                    }
                )
            ordered = [sorted(row, key=lambda w: w["x0"]) for row in rows]
            pages_out.append(ordered)
    return pages_out

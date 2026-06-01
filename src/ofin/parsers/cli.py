from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .ingest import dry_run_dir, ingest_path


def _dump_json(obj, path: str | None) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    if path:
        Path(path).write_text(text)
    else:
        print(text)


def _short_summary(report: dict) -> str:
    by_doc = report.get("doc_type_counts", {})
    by_sev = report.get("warnings_by_severity", {})
    return (
        f"docs total={report.get('total_pdfs')} "
        f"extrato={by_doc.get('extrato', 0)} fatura={by_doc.get('fatura', 0)} unknown={by_doc.get('unknown', 0)}  "
        f"warn err={by_sev.get('error', 0)} warn={by_sev.get('warn', 0)} info={by_sev.get('info', 0)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser("ofin.parsers")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ingest = sub.add_parser("ingest", help="parse one PDF (debug, no DB)")
    ingest.add_argument("path")
    ingest.add_argument("--json", help="write JSON to this path (else stdout)", default=None)

    dryrun = sub.add_parser("dry-run", help="parse all PDFs in a directory (no DB)")
    dryrun.add_argument("directory")
    dryrun.add_argument("--json", help="write full JSON report here", default=None)
    dryrun.add_argument("--brief", action="store_true", help="print per-doc table to stdout")

    commit = sub.add_parser("commit", help="parse + write to DB (requires DATABASE_URL)")
    commit.add_argument("directory")

    args = parser.parse_args(argv)

    if args.cmd == "ingest":
        result = ingest_path(args.path)
        _dump_json(result, args.json)
        return 0

    if args.cmd == "dry-run":
        report = dry_run_dir(args.directory)
        if args.json:
            _dump_json(report, args.json)
        print(_short_summary(report))
        if args.brief:
            for d in report["docs"]:
                err = sum(1 for w in d.get("warnings", []) if w.get("severity") == "error")
                warn = sum(1 for w in d.get("warnings", []) if w.get("severity") == "warn")
                name = Path(d["source_path"]).name
                period = ""
                summ = d.get("summary") or {}
                if d.get("doc_type") == "extrato":
                    period = f"{summ.get('period_year')}-{int(summ.get('period_month') or 0):02d}"
                elif d.get("doc_type") == "fatura":
                    period = summ.get("posting_date") or ""
                print(f"  {d.get('doc_type', '?'):8s} {period:12s}  err={err} warn={warn}  {name}")
        return 0

    if args.cmd == "commit":
        from ..import_pdfs import import_directory
        result = asyncio.run(import_directory(args.directory))
        print(json.dumps(result.get("counts", {}), indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())

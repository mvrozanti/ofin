"""Anon HTTP smoke tests for ofin. Usage: python scripts/smoke.py <base_url>.

Exits 0 if all checks pass; non-zero exit code = count of failures.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass

import httpx


@dataclass
class Result:
    name: str
    ok: bool
    detail: str


def fetch(client: httpx.Client, method: str, path: str, **kw) -> httpx.Response:
    return client.request(method, path, follow_redirects=False, **kw)


def check_get(client, path: str, want_status: int) -> Result:
    r = fetch(client, "GET", path)
    ok = r.status_code == want_status
    return Result(f"GET {path} → {want_status}", ok, f"got {r.status_code}")


def check_post_blocked(client, path: str) -> Result:
    r = fetch(client, "POST", path, data={})
    ok = r.status_code == 403
    return Result(f"POST {path} → 403", ok, f"got {r.status_code}")


import re as _re

_SCRIPT_RE = _re.compile(r"<script\b[^>]*>.*?</script>", _re.DOTALL | _re.IGNORECASE)


def check_no_brl(client, path: str) -> Result:
    r = fetch(client, "GET", path)
    if r.status_code != 200:
        return Result(f"{path} HTML no R$", False, f"status {r.status_code}")
    body = _SCRIPT_RE.sub("", r.text)
    leaks = body.count("R$")
    ok = leaks == 0
    return Result(f"{path} HTML no R$", ok, f"{leaks} R$ occurrences (scripts stripped)")


def check_no_pii(client, path: str, pii: tuple[str, ...]) -> Result:
    r = fetch(client, "GET", path)
    if r.status_code != 200:
        return Result(f"{path} no PII", False, f"status {r.status_code}")
    hits = [n for n in pii if n.lower() in r.text.lower()]
    return Result(f"{path} no PII", not hits, f"leaks: {hits}" if hits else "clean")


def check_accounts_balance_null(client) -> Result:
    r = fetch(client, "GET", "/api/accounts")
    if r.status_code != 200:
        return Result("/api/accounts balance=null", False, f"status {r.status_code}")
    data = r.json()
    bad = [a for a in data if a.get("balance") is not None]
    return Result("/api/accounts balance=null", not bad, f"{len(bad)} leaked balances")


def check_sankey_scaled(client) -> Result:
    r = fetch(client, "GET", "/api/sankey?preset=all")
    if r.status_code != 200:
        return Result("/api/sankey scaled", False, f"status {r.status_code}")
    data = r.json()
    totals = data.get("totals", {})
    if totals.get("income") is not None or totals.get("spend") is not None or totals.get("net") is not None:
        return Result("/api/sankey scaled", False, f"totals leaked: {totals}")
    link_vals = [lk["value"] for lk in data.get("links", [])]
    if link_vals:
        s = sum(link_vals)
        if not (90 < s < 110):
            return Result("/api/sankey scaled", False, f"links sum={s:.2f} not ~100")
    return Result("/api/sankey scaled", True, f"links sum={sum(link_vals):.2f}")


def check_whoami_anon(client) -> Result:
    r = fetch(client, "GET", "/whoami")
    if r.status_code != 200:
        return Result("/whoami anon", False, f"status {r.status_code}")
    data = r.json()
    ok = data.get("authed") is False and data.get("user") is None
    return Result("/whoami anon", ok, json.dumps(data))


def check_readonly_flag(client) -> Result:
    r = fetch(client, "GET", "/readonly")
    if r.status_code != 200:
        return Result("/readonly", False, f"status {r.status_code}")
    return Result("/readonly", r.json().get("read_only") is True, r.text)


PII = ("Roberta", "Edgard", "Lucas", "Caique", "MARCELO")


def main(base: str) -> int:
    base = base.rstrip("/")
    client = httpx.Client(base_url=base, timeout=15.0)
    checks: list[Result] = []
    pages = ["/", "/sankey", "/savings", "/transactions", "/rules"]
    for p in pages:
        checks.append(check_no_brl(client, p))
        checks.append(check_no_pii(client, p, PII))

    checks.append(check_get(client, "/documents", 403))
    checks.append(check_get(client, "/api/documents", 403))
    checks.append(check_get(client, "/transactions.csv", 403))
    checks.append(check_post_blocked(client, "/recategorize"))
    checks.append(check_post_blocked(client, "/rules"))
    checks.append(check_post_blocked(client, "/snapshots"))
    checks.append(check_post_blocked(client, "/loans"))
    checks.append(check_post_blocked(client, "/api/transactions/abc/categorize"))
    checks.append(check_post_blocked(client, "/api/transactions/bulk_categorize"))

    checks.append(check_whoami_anon(client))
    checks.append(check_readonly_flag(client))
    checks.append(check_accounts_balance_null(client))
    checks.append(check_sankey_scaled(client))

    fails = 0
    for c in checks:
        status = "PASS" if c.ok else "FAIL"
        print(f"[{status}] {c.name} — {c.detail}")
        if not c.ok:
            fails += 1
    print()
    print(f"{fails} failure(s)" if fails else f"all {len(checks)} checks passed")
    return fails


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
    sys.exit(main(url))

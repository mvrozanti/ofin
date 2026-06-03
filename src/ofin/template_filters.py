from __future__ import annotations

from jinja2 import pass_context

from .masking import MASK_STR, fmt_money


def _authed_from(ctx) -> bool:
    flag = ctx.get("_authed_flag")
    if flag is True:
        return True
    req = ctx.get("request")
    if req is None:
        return False
    auth = getattr(req.state, "auth", None)
    return bool(auth and auth.authed)


@pass_context
def money_ctx(ctx, v) -> str:
    if not _authed_from(ctx):
        return MASK_STR
    return fmt_money(v)


def money_raw(v) -> str:
    return fmt_money(v)


@pass_context
def mask_text(ctx, v):
    if v is None or v == "":
        return "—"
    if _authed_from(ctx):
        return v
    return MASK_STR


@pass_context
def mask_pessoa(ctx, mega, category):
    if not _authed_from(ctx) and mega == "pessoas":
        return MASK_STR
    return category


def pct(v, total) -> str:
    if not total:
        return "—"
    return f"{(float(v) / float(total)) * 100:.1f}%"


def delta_class(cur, prev) -> str:
    try:
        if cur > prev:
            return "up"
        if cur < prev:
            return "down"
    except Exception:
        pass
    return "flat"


@pass_context
def is_authed(ctx) -> bool:
    return _authed_from(ctx)


@pass_context
def auth_user(ctx):
    req = ctx.get("request")
    if req is None:
        return None
    return getattr(req.state, "auth", None)


def register(templates) -> None:
    templates.env.filters["money"] = money_ctx
    templates.env.filters["money_raw"] = money_raw
    templates.env.filters["pct"] = pct
    templates.env.filters["delta_class"] = delta_class
    templates.env.filters["mask_text"] = mask_text
    templates.env.globals["authed"] = is_authed
    templates.env.globals["auth_user"] = auth_user
    templates.env.globals["mask_pessoa"] = mask_pessoa

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import session_dep
from ..models import CategoryRule, Transaction
from ..parsers.categorize_engine import apply_rules_to_all, bump_cache

router = APIRouter(tags=["rules"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["read_only"] = settings().read_only

from ..template_filters import register as _register_filters  # noqa: E402

_register_filters(templates)


@router.get("/rules", response_class=HTMLResponse)
async def rules_list(
    request: Request,
    s: AsyncSession = Depends(session_dep),
    mega: str | None = None,
    search: str | None = None,
):
    q = select(CategoryRule).order_by(CategoryRule.priority, CategoryRule.id)
    if mega:
        q = q.where(CategoryRule.mega == mega)
    if search:
        like = f"%{search.lower()}%"
        q = q.where(func.lower(CategoryRule.pattern).like(like))
    rules = (await s.execute(q)).scalars().all()

    match_q = (
        select(Transaction.rule_id, func.count())
        .where(Transaction.rule_id.is_not(None))
        .group_by(Transaction.rule_id)
    )
    match_map = dict((await s.execute(match_q)).all())

    megas_q = select(CategoryRule.mega, func.count()).group_by(CategoryRule.mega).order_by(CategoryRule.mega)
    megas = (await s.execute(megas_q)).all()

    return templates.TemplateResponse(
        "rules.html",
        {
            "request": request,
            "rules": rules,
            "match_map": match_map,
            "megas": megas,
            "mega_filter": mega,
            "search": search or "",
        },
    )


@router.post("/rules")
async def rules_create(
    pattern_type: str = Form(...),
    pattern: str = Form(...),
    mega: str = Form(...),
    category: str = Form(...),
    account_type: str = Form(""),
    sign: str = Form(""),
    is_internal: str = Form(""),
    priority: int = Form(100),
    notes: str = Form(""),
    s: AsyncSession = Depends(session_dep),
):
    rule = CategoryRule(
        pattern_type=pattern_type,
        pattern=pattern.lower().strip(),
        mega=mega.strip(),
        category=category.strip(),
        account_type=(account_type or None) or None,
        sign=(sign or None) or None,
        is_internal=bool(is_internal),
        priority=int(priority or 100),
        notes=notes or None,
        enabled=True,
    )
    s.add(rule)
    await s.commit()
    bump_cache()
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/toggle")
async def rules_toggle(rule_id: int, s: AsyncSession = Depends(session_dep)):
    r = await s.get(CategoryRule, rule_id)
    if not r:
        raise HTTPException(404)
    r.enabled = not r.enabled
    r.updated_at = datetime.now(timezone.utc)
    await s.commit()
    bump_cache()
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/delete")
async def rules_delete(rule_id: int, s: AsyncSession = Depends(session_dep)):
    r = await s.get(CategoryRule, rule_id)
    if not r:
        raise HTTPException(404)
    await s.delete(r)
    await s.commit()
    bump_cache()
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/edit")
async def rules_edit(
    rule_id: int,
    pattern_type: str = Form(...),
    pattern: str = Form(...),
    mega: str = Form(...),
    category: str = Form(...),
    account_type: str = Form(""),
    sign: str = Form(""),
    is_internal: str = Form(""),
    priority: int = Form(100),
    notes: str = Form(""),
    s: AsyncSession = Depends(session_dep),
):
    r = await s.get(CategoryRule, rule_id)
    if not r:
        raise HTTPException(404)
    r.pattern_type = pattern_type
    r.pattern = pattern.lower().strip()
    r.mega = mega.strip()
    r.category = category.strip()
    r.account_type = (account_type or None) or None
    r.sign = (sign or None) or None
    r.is_internal = bool(is_internal)
    r.priority = int(priority or 100)
    r.notes = notes or None
    r.updated_at = datetime.now(timezone.utc)
    await s.commit()
    bump_cache()
    return RedirectResponse("/rules", status_code=303)


@router.post("/recategorize", response_class=HTMLResponse)
async def recategorize_all(
    request: Request,
    s: AsyncSession = Depends(session_dep),
    force: int = 0,
):
    updated, skipped = await apply_rules_to_all(s, force=bool(force))
    await s.commit()
    return templates.TemplateResponse(
        "rules.html",
        {
            "request": request,
            "rules": (await s.execute(select(CategoryRule).order_by(CategoryRule.priority, CategoryRule.id))).scalars().all(),
            "match_map": {},
            "megas": (await s.execute(select(CategoryRule.mega, func.count()).group_by(CategoryRule.mega))).all(),
            "mega_filter": None,
            "search": "",
            "recategorized": updated,
            "skipped_overrides": skipped,
        },
    )

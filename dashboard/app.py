# -*- coding: utf-8 -*-
"""
İzleme paneli (Jinja2 + FastAPI router).

Uç noktalar:
    GET /panel              -> HTML panel (istatistik kartları + olay tablosu)
    GET /panel/api/ozet     -> JSON: kartlar + son olaylar (canlı akış için)

Panel salt okunurdur; günlükte zaten maskelenmiş veriyi gösterir, ham kişisel
veriye erişimi yoktur.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from logs import db

SABLON_DIZINI = Path(__file__).resolve().parent / "templates"
sablonlar = Jinja2Templates(directory=str(SABLON_DIZINI))

panel_yonlendirici = APIRouter(prefix="/panel", tags=["panel"])


@panel_yonlendirici.get("", response_class=HTMLResponse, include_in_schema=False)
@panel_yonlendirici.get("/", response_class=HTMLResponse, include_in_schema=False)
async def panel(request: Request):
    """Panelin ilk yüklemesi; sonrasında JS 3 saniyede bir /api/ozet çeker."""
    ozet = await run_in_threadpool(db.istatistikler)
    olaylar = await run_in_threadpool(db.son_olaylar, 50)
    return sablonlar.TemplateResponse(
        request,
        "index.html",
        {"ozet": ozet, "olaylar": olaylar},
    )


@panel_yonlendirici.get("/api/ozet")
async def api_ozet(limit: int = Query(50, ge=1, le=500)):
    """Canlı akış için JSON: istatistikler + son olaylar."""
    ozet = await run_in_threadpool(db.istatistikler)
    olaylar = await run_in_threadpool(db.son_olaylar, limit)
    return {"ozet": ozet, "olaylar": olaylar}

# -*- coding: utf-8 -*-
"""
Erişim denetimi (FastAPI bağımlılıkları).

  * API uçları (/chat, /kurallar): `DENETIM_API_ANAHTARI` doluysa istek
    "X-API-Key: <anahtar>" veya "Authorization: Bearer <anahtar>" taşımalıdır.
  * Panel (/panel): `PANEL_SIFRESI` doluysa HTTP Basic istenir (kullanıcı adı
    serbest). Tarayıcı kimlik bilgisini kendisi saklar; panelin 3 saniyelik
    fetch çağrıları da otomatik olarak aynı bilgiyi gönderir.

Değerler boşsa ilgili denetim kapalıdır (V0.1 ile geriye dönük uyum). Bu
durumda proxy yalnızca güvenilir ağda çalıştırılmalıdır.

Karşılaştırmalar `hmac.compare_digest` ile sabit zamanlıdır.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from config import AYARLAR

_basic = HTTPBasic(auto_error=False)


def _esit(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _istekteki_anahtar(request: Request) -> Optional[str]:
    anahtar = request.headers.get("x-api-key")
    if anahtar:
        return anahtar
    yetki = request.headers.get("authorization", "")
    tur, _, deger = yetki.partition(" ")
    if tur.lower() == "bearer" and deger:
        return deger.strip()
    return None


async def api_dogrula(request: Request) -> None:
    """/chat ve /kurallar için API anahtarı denetimi."""
    beklenen = AYARLAR.api_anahtari
    if not beklenen:
        return
    gelen = _istekteki_anahtar(request)
    if not gelen or not _esit(gelen, beklenen):
        raise HTTPException(
            status_code=401,
            detail="Geçerli bir API anahtarı gerekli",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def panel_dogrula(request: Request) -> None:
    """/panel için HTTP Basic denetimi."""
    beklenen = AYARLAR.panel_sifresi
    if not beklenen:
        return
    kimlik: Optional[HTTPBasicCredentials] = await _basic(request)
    if kimlik is None or not _esit(kimlik.password, beklenen):
        raise HTTPException(
            status_code=401,
            detail="Panel için kimlik doğrulama gerekli",
            headers={"WWW-Authenticate": 'Basic realm="denetim-paneli"'},
        )

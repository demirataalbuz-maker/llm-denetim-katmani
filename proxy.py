# -*- coding: utf-8 -*-
"""
LLM Denetim Katmanı — proxy sunucu (V0.1).

Akış:

    kullanıcı isteği
        -> [1] giriş denetimi (kural tabanlı + isteğe bağlı model filtresi)
             ENGEL ise: LLM'e hiç gidilmez, standart mesaj döner
        -> [2] (isteğe bağlı) girişteki kişisel veriyi maskele
        -> [3] LLM (Ollama /api/chat)
        -> [4] çıkış denetimi (kişisel veri taraması + maskeleme)
        -> [5] SQLite log + panel
        -> kullanıcı

Çalıştırma:
    uvicorn proxy:app --reload --port 8000

Uç noktalar:
    POST /chat      : denetimli sohbet
    GET  /saglik    : sağlık kontrolü
    GET  /kurallar  : etkin kural ve kalıp envanteri
    GET  /panel     : izleme paneli (dashboard/)
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from config import AYARLAR
from dashboard.app import panel_yonlendirici
from filters import Karar, varsayilan_zincir
from filters.rule_based import KURALLAR
from logs import db
from scanners import KALIPLAR, TaramaSonucu
from scanners.pii_scanner import varsayilan_tarayici

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
kayitci = logging.getLogger("denetim.proxy")

# Denetim bileşenleri süreç ömrü boyunca tek örnektir (regexler önceden derli).
GIRIS_ZINCIRI = varsayilan_zincir()
CIKIS_TARAYICI = varsayilan_tarayici(
    dusuk_guven_dahil=AYARLAR.dusuk_guven_kaliplari
)


# ---------------------------------------------------------------------------
# İstek / yanıt modelleri
# ---------------------------------------------------------------------------


class SohbetIstegi(BaseModel):
    """POST /chat gövdesi."""

    mesaj: str = Field(..., min_length=1, description="Kullanıcının isteği")
    oturum: Optional[str] = Field(None, description="Oturum/konuşma kimliği")
    model: Optional[str] = Field(None, description="Hedef Ollama modeli")
    sistem: Optional[str] = Field(None, description="Sistem mesajı (opsiyonel)")


class DenetimOzeti(BaseModel):
    karar: str
    puan: float
    kategoriler: list[str] = []
    kural_sayisi: int = 0


class MaskelemeOzeti(BaseModel):
    adet: int = 0
    dagilim: dict[str, int] = {}


class SohbetYaniti(BaseModel):
    """POST /chat başarılı yanıtı."""

    olay_id: str
    yanit: str
    engellendi: bool = False
    giris_denetimi: DenetimOzeti
    cikis_maskeleme: MaskelemeOzeti
    gecikme_ms: int


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _onizleme(metin: str) -> str:
    """Log için kısaltılmış önizleme (metin ÖNCEDEN maskelenmiş olmalıdır)."""
    tek_satir = " ".join((metin or "").split())
    sinir = AYARLAR.onizleme_uzunlugu
    return tek_satir if len(tek_satir) <= sinir else tek_satir[: sinir - 1] + "…"


def _guvenli_onizleme(metin: str) -> str:
    """Ham metni önce kişisel veriden arındırır, sonra kısaltır."""
    return _onizleme(CIKIS_TARAYICI.maskele(metin or "").temiz_metin)


def _ihlalleri_maskele(ihlaller: list[dict]) -> list[dict]:
    """Kural kanıtları kullanıcı metninden geldiği için onlar da maskelenir."""
    temiz = []
    for ihlal in ihlaller:
        kopya = dict(ihlal)
        kanit = kopya.get("kanit") or ""
        kopya["kanit"] = CIKIS_TARAYICI.maskele(kanit).temiz_metin
        temiz.append(kopya)
    return temiz


async def _ollama_cagir(
    istemci: httpx.AsyncClient, model: str, mesajlar: list[dict]
) -> str:
    """Ollama /api/chat çağrısı; yanıt metnini döndürür."""
    yanit = await istemci.post(
        f"{AYARLAR.ollama_url}/api/chat",
        json={"model": model, "messages": mesajlar, "stream": False},
    )
    yanit.raise_for_status()
    veri = yanit.json()
    return (veri.get("message") or {}).get("content", "") or ""


# ---------------------------------------------------------------------------
# Uygulama
# ---------------------------------------------------------------------------


@asynccontextmanager
async def yasam_dongusu(uygulama: FastAPI):
    """Açılışta veritabanını ve HTTP istemcisini hazırlar."""
    db.baslat()
    uygulama.state.http = httpx.AsyncClient(timeout=AYARLAR.ollama_timeout)
    kayitci.info(
        "Denetim katmanı hazır | hedef=%s model=%s | engel eşiği=%.2f",
        AYARLAR.ollama_url, AYARLAR.ollama_model, AYARLAR.giris_engel_esigi,
    )
    try:
        yield
    finally:
        await uygulama.state.http.aclose()
        db.kapat()


app = FastAPI(
    title="LLM Denetim Katmanı",
    description=(
        "Yapay zekâ uygulamaları için giriş/çıkış denetimi yapan ara katman. "
        "Yalnızca kendi sistemlerinizde denetim amaçlı kullanım içindir."
    ),
    version="0.1.0",
    lifespan=yasam_dongusu,
)
app.include_router(panel_yonlendirici)


@app.get("/", include_in_schema=False)
async def kok():
    return RedirectResponse(url="/panel")


@app.get("/saglik")
async def saglik(request: Request):
    """Sunucu ve hedef LLM erişilebilirlik kontrolü."""
    ollama_durum = "bilinmiyor"
    try:
        yanit = await request.app.state.http.get(
            f"{AYARLAR.ollama_url}/api/tags", timeout=3.0
        )
        ollama_durum = "erisilebilir" if yanit.status_code == 200 else "hata"
    except Exception:
        ollama_durum = "erisilemiyor"

    return {
        "durum": "calisiyor",
        "surum": "0.1.0",
        "ollama": {"url": AYARLAR.ollama_url, "durum": ollama_durum,
                   "model": AYARLAR.ollama_model},
        "giris_filtreleri": [f.ad for f in GIRIS_ZINCIRI.filtreler],
        "cikis_kaliplari": [k.ad for k in CIKIS_TARAYICI.kaliplar],
    }


@app.get("/kurallar")
async def kural_envanteri():
    """Etkin kural ve veri kalıbı envanteri (şeffaflık / denetlenebilirlik)."""
    return {
        "giris_kurallari": [
            {"ad": k.ad, "kategori": k.kategori, "puan": k.puan,
             "aciklama": k.aciklama}
            for k in KURALLAR
        ],
        "esikler": {
            "engel": AYARLAR.giris_engel_esigi,
            "uyari": AYARLAR.giris_uyari_esigi,
        },
        "veri_kaliplari": [
            {"ad": k.ad, "aciklama": k.aciklama, "duyarlilik": k.duyarlilik,
             "dogrulamali": k.dogrulayici is not None,
             "etkin": k in CIKIS_TARAYICI.kaliplar}
            for k in KALIPLAR
        ],
    }


@app.post("/chat", response_model=SohbetYaniti, responses={403: {}, 502: {}})
async def sohbet(istek: SohbetIstegi, request: Request):
    """Denetimli sohbet uç noktası."""
    olay_id = uuid.uuid4().hex[:12]
    baslangic = time.perf_counter()
    istemci_ip = request.client.host if request.client else None
    model = istek.model or AYARLAR.ollama_model

    # --- [1] Giriş denetimi ------------------------------------------------
    giris_sonuc = GIRIS_ZINCIRI.denetle(istek.mesaj, {"oturum": istek.oturum})
    giris_ozeti = giris_sonuc.ozet()
    giris_ozeti["ihlaller"] = _ihlalleri_maskele(giris_ozeti["ihlaller"])
    giris_onizleme = _guvenli_onizleme(istek.mesaj)

    if giris_sonuc.karar is Karar.ENGEL:
        gecikme = int((time.perf_counter() - baslangic) * 1000)
        await run_in_threadpool(
            db.olay_kaydet,
            olay_id, Karar.ENGEL.value,
            oturum=istek.oturum, istemci=istemci_ip, model=model,
            giris_puani=giris_sonuc.puan, ihlaller=giris_ozeti["ihlaller"],
            giris_onizleme=giris_onizleme, cikis_onizleme="",
            gecikme_ms=gecikme,
        )
        kayitci.warning(
            "ENGEL | olay=%s puan=%.2f kategoriler=%s",
            olay_id, giris_sonuc.puan, ",".join(giris_sonuc.kategoriler),
        )
        govde = SohbetYaniti(
            olay_id=olay_id,
            yanit=AYARLAR.engel_mesaji,
            engellendi=True,
            giris_denetimi=DenetimOzeti(
                karar=giris_sonuc.karar.value,
                puan=giris_sonuc.puan,
                kategoriler=giris_sonuc.kategoriler,
                kural_sayisi=len(giris_sonuc.ihlaller),
            ),
            cikis_maskeleme=MaskelemeOzeti(),
            gecikme_ms=gecikme,
        ).model_dump()
        return JSONResponse(status_code=AYARLAR.engel_http_kodu, content=govde)

    # --- [2] Girişi maskeleme (opsiyonel) ----------------------------------
    # KVKK açısından en güvenli mod: kişisel veri modele hiç ulaşmasın.
    gonderilecek = istek.mesaj
    if AYARLAR.giris_maskeleme_acik:
        gonderilecek = CIKIS_TARAYICI.maskele(istek.mesaj).temiz_metin

    mesajlar: list[dict] = []
    if istek.sistem:
        mesajlar.append({"role": "system", "content": istek.sistem})
    mesajlar.append({"role": "user", "content": gonderilecek})

    # --- [3] LLM çağrısı ---------------------------------------------------
    try:
        ham_yanit = await _ollama_cagir(request.app.state.http, model, mesajlar)
    except httpx.HTTPStatusError as hata:
        return await _hata_yaniti(
            olay_id, baslangic, istek, istemci_ip, model, giris_sonuc,
            giris_onizleme,
            f"LLM {hata.response.status_code} döndürdü", 502,
        )
    except httpx.RequestError as hata:
        return await _hata_yaniti(
            olay_id, baslangic, istek, istemci_ip, model, giris_sonuc,
            giris_onizleme,
            f"LLM'e ulaşılamadı: {hata.__class__.__name__}", 502,
        )

    # --- [4] Çıkış denetimi ------------------------------------------------
    if AYARLAR.cikis_tarama_acik:
        tarama = CIKIS_TARAYICI.maskele(ham_yanit)
    else:
        # Tarama kapalıysa yanıt olduğu gibi geçer (yalnızca hata ayıklama için).
        tarama = TaramaSonucu(temiz_metin=ham_yanit, bulgular=[])

    gecikme = int((time.perf_counter() - baslangic) * 1000)
    karar = (Karar.UYARI if giris_sonuc.karar is Karar.UYARI else Karar.IZIN)

    # --- [5] Loglama -------------------------------------------------------
    await run_in_threadpool(
        db.olay_kaydet,
        olay_id, karar.value,
        oturum=istek.oturum, istemci=istemci_ip, model=model,
        giris_puani=giris_sonuc.puan, ihlaller=giris_ozeti["ihlaller"],
        maskelenen_adet=tarama.adet, bulgular=tarama.tur_dagilimi(),
        giris_onizleme=giris_onizleme,
        cikis_onizleme=_onizleme(tarama.temiz_metin),
        gecikme_ms=gecikme,
    )
    if tarama.adet:
        kayitci.info(
            "MASKELEME | olay=%s adet=%d tur=%s",
            olay_id, tarama.adet, tarama.tur_dagilimi(),
        )

    return SohbetYaniti(
        olay_id=olay_id,
        yanit=tarama.temiz_metin,
        engellendi=False,
        giris_denetimi=DenetimOzeti(
            karar=karar.value,
            puan=giris_sonuc.puan,
            kategoriler=giris_sonuc.kategoriler,
            kural_sayisi=len(giris_sonuc.ihlaller),
        ),
        cikis_maskeleme=MaskelemeOzeti(
            adet=tarama.adet, dagilim=tarama.tur_dagilimi()
        ),
        gecikme_ms=gecikme,
    )


async def _hata_yaniti(
    olay_id: str,
    baslangic: float,
    istek: SohbetIstegi,
    istemci_ip: Optional[str],
    model: str,
    giris_sonuc,
    giris_onizleme: str,
    mesaj: str,
    kod: int,
) -> JSONResponse:
    """LLM erişilemediğinde olayı loglar ve hata gövdesi döndürür."""
    gecikme = int((time.perf_counter() - baslangic) * 1000)
    await run_in_threadpool(
        db.olay_kaydet,
        olay_id, "hata",
        oturum=istek.oturum, istemci=istemci_ip, model=model,
        giris_puani=giris_sonuc.puan,
        giris_onizleme=giris_onizleme, gecikme_ms=gecikme, hata=mesaj,
    )
    kayitci.error("HATA | olay=%s %s", olay_id, mesaj)
    return JSONResponse(
        status_code=kod,
        content={"olay_id": olay_id, "hata": mesaj, "gecikme_ms": gecikme},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("proxy:app", host=AYARLAR.host, port=AYARLAR.port, reload=True)

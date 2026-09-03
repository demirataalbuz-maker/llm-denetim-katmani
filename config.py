# -*- coding: utf-8 -*-
"""
Merkezi yapılandırma.

Tüm ayarlar ortam değişkeniyle ezilebilir (bkz. .env.example). V0.1'de ekstra
bağımlılık getirmemek için pydantic-settings yerine düz `os.environ` okunuyor;
projenin ilerleyen sürümünde `.env` desteği için python-dotenv eklenebilir.

Kullanım:
    from config import AYARLAR
    AYARLAR.ollama_url
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJE_KOK = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Ortam değişkeni okuma yardımcıları
# ---------------------------------------------------------------------------

def _metin(ad: str, varsayilan: str) -> str:
    deger = os.getenv(ad)
    return deger.strip() if deger and deger.strip() else varsayilan


def _mantiksal(ad: str, varsayilan: bool) -> bool:
    ham = os.getenv(ad)
    if ham is None or not ham.strip():
        return varsayilan
    return ham.strip().lower() in {"1", "true", "yes", "evet", "on", "acik", "açık"}


def _tamsayi(ad: str, varsayilan: int) -> int:
    try:
        return int(_metin(ad, str(varsayilan)))
    except ValueError:
        return varsayilan


def _ondalik(ad: str, varsayilan: float) -> float:
    try:
        return float(_metin(ad, str(varsayilan)))
    except ValueError:
        return varsayilan


# ---------------------------------------------------------------------------
# Ayarlar
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ayarlar:
    """Uygulamanın tüm çalışma zamanı ayarları."""

    # --- Hedef LLM (Ollama) ---
    ollama_url: str
    ollama_model: str
    ollama_timeout: float

    # --- Sunucu ---
    host: str
    port: int

    # --- Giriş denetimi ---
    giris_engel_esigi: float   # bu puanın üstü => istek engellenir
    giris_uyari_esigi: float   # bu puanın üstü => geçer ama "uyarı" olarak loglanır
    engel_http_kodu: int
    model_filtresi_acik: bool
    hf_model_adi: str
    hf_model_esigi: float

    # --- Çıkış denetimi ---
    cikis_tarama_acik: bool
    giris_maskeleme_acik: bool
    dusuk_guven_kaliplari: bool

    # --- Loglama ---
    db_yolu: Path
    onizleme_uzunlugu: int

    # --- Kullanıcıya dönülecek standart engel mesajı ---
    engel_mesaji: str = (
        "Bu istek kurum politikası gereği denetim katmanı tarafından "
        "engellendi. Lütfen isteğinizi yeniden ifade edin."
    )


def ayarlari_yukle() -> Ayarlar:
    """Ortam değişkenlerinden `Ayarlar` üretir."""
    db_ham = _metin("DB_YOLU", "logs/denetim.db")
    db_yolu = Path(db_ham)
    if not db_yolu.is_absolute():
        db_yolu = PROJE_KOK / db_yolu

    return Ayarlar(
        ollama_url=_metin("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        ollama_model=_metin("OLLAMA_MODEL", "llama3.1"),
        ollama_timeout=_ondalik("OLLAMA_TIMEOUT", 120.0),
        host=_metin("DENETIM_HOST", "127.0.0.1"),
        port=_tamsayi("DENETIM_PORT", 8000),
        giris_engel_esigi=_ondalik("GIRIS_ENGEL_ESIGI", 0.75),
        giris_uyari_esigi=_ondalik("GIRIS_UYARI_ESIGI", 0.40),
        engel_http_kodu=_tamsayi("ENGEL_HTTP_KODU", 403),
        model_filtresi_acik=_mantiksal("MODEL_FILTRESI_ACIK", False),
        hf_model_adi=_metin("HF_MODEL_ADI", "protectai/deberta-v3-base-prompt-injection-v2"),
        hf_model_esigi=_ondalik("HF_MODEL_ESIGI", 0.80),
        cikis_tarama_acik=_mantiksal("CIKIS_TARAMA_ACIK", True),
        giris_maskeleme_acik=_mantiksal("GIRIS_MASKELEME_ACIK", False),
        dusuk_guven_kaliplari=_mantiksal("DUSUK_GUVEN_KALIPLARI", False),
        db_yolu=db_yolu,
        onizleme_uzunlugu=_tamsayi("ONIZLEME_UZUNLUGU", 240),
    )


AYARLAR = ayarlari_yukle()

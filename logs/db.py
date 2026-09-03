# -*- coding: utf-8 -*-
"""
SQLite denetim günlüğü.

Tasarım ilkesi (KVKK): günlüğe **ham kişisel veri yazılmaz**. Önizlemeler
maskelenmiş metinden alınır, bulgular yalnızca tür/konum/sayı olarak tutulur.
Böylece denetim kaydının kendisi yeni bir veri sızıntısı kaynağına dönüşmez.

Şema tek tablodur (`olaylar`); panel bu tablodan beslenir.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from config import AYARLAR

_KILIT = threading.Lock()
_BAGLANTI: Optional[sqlite3.Connection] = None

SEMA = """
CREATE TABLE IF NOT EXISTS olaylar (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    olay_id           TEXT    NOT NULL,
    zaman             TEXT    NOT NULL,   -- ISO-8601, UTC
    oturum            TEXT,
    istemci           TEXT,               -- IP veya çağıran servis
    model             TEXT,
    karar             TEXT    NOT NULL,   -- izin | uyari | engel | hata
    giris_puani       REAL    NOT NULL DEFAULT 0,
    ihlaller          TEXT,               -- JSON: [{kural, kategori, puan, kanit}]
    maskelenen_adet   INTEGER NOT NULL DEFAULT 0,
    bulgular          TEXT,               -- JSON: {"TCKN": 2, "EPOSTA": 1}
    giris_onizleme    TEXT,               -- MASKELENMİŞ, kısaltılmış
    cikis_onizleme    TEXT,               -- MASKELENMİŞ, kısaltılmış
    gecikme_ms        INTEGER NOT NULL DEFAULT 0,
    hata              TEXT
);
CREATE INDEX IF NOT EXISTS ix_olaylar_zaman ON olaylar (zaman DESC);
CREATE INDEX IF NOT EXISTS ix_olaylar_karar ON olaylar (karar);
"""


# ---------------------------------------------------------------------------
# Bağlantı yönetimi
# ---------------------------------------------------------------------------


def baglanti(yol: Optional[Path] = None) -> sqlite3.Connection:
    """Süreç genelinde tek bağlantı döndürür (WAL modunda)."""
    global _BAGLANTI
    with _KILIT:
        if _BAGLANTI is None:
            hedef = Path(yol or AYARLAR.db_yolu)
            hedef.parent.mkdir(parents=True, exist_ok=True)
            _BAGLANTI = sqlite3.connect(
                hedef, check_same_thread=False, isolation_level=None
            )
            _BAGLANTI.row_factory = sqlite3.Row
            _BAGLANTI.execute("PRAGMA journal_mode=WAL;")
            _BAGLANTI.execute("PRAGMA synchronous=NORMAL;")
        return _BAGLANTI


def baslat(yol: Optional[Path] = None) -> None:
    """Tabloyu ve indeksleri oluşturur (idempotent)."""
    conn = baglanti(yol)
    with _KILIT:
        conn.executescript(SEMA)


def kapat() -> None:
    """Bağlantıyı kapatır (test ve kapanış için)."""
    global _BAGLANTI
    with _KILIT:
        if _BAGLANTI is not None:
            _BAGLANTI.close()
            _BAGLANTI = None


def _simdi() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Yazma
# ---------------------------------------------------------------------------


def olay_kaydet(
    olay_id: str,
    karar: str,
    *,
    oturum: Optional[str] = None,
    istemci: Optional[str] = None,
    model: Optional[str] = None,
    giris_puani: float = 0.0,
    ihlaller: Optional[list[dict]] = None,
    maskelenen_adet: int = 0,
    bulgular: Optional[dict[str, int]] = None,
    giris_onizleme: str = "",
    cikis_onizleme: str = "",
    gecikme_ms: int = 0,
    hata: Optional[str] = None,
) -> int:
    """
    Tek bir denetim olayını yazar ve satır kimliğini döndürür.

    UYARI: `giris_onizleme` / `cikis_onizleme` çağıran tarafından MASKELENMİŞ
    olarak verilmelidir; bu fonksiyon maskeleme yapmaz.
    """
    conn = baglanti()
    with _KILIT:
        imlec = conn.execute(
            """
            INSERT INTO olaylar (
                olay_id, zaman, oturum, istemci, model, karar, giris_puani,
                ihlaller, maskelenen_adet, bulgular, giris_onizleme,
                cikis_onizleme, gecikme_ms, hata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                olay_id,
                _simdi(),
                oturum,
                istemci,
                model,
                karar,
                float(giris_puani),
                json.dumps(ihlaller or [], ensure_ascii=False),
                int(maskelenen_adet),
                json.dumps(bulgular or {}, ensure_ascii=False),
                giris_onizleme,
                cikis_onizleme,
                int(gecikme_ms),
                hata,
            ),
        )
        return int(imlec.lastrowid or 0)


# ---------------------------------------------------------------------------
# Okuma (panel için)
# ---------------------------------------------------------------------------


def _satiri_sozluge_cevir(satir: sqlite3.Row) -> dict[str, Any]:
    kayit = dict(satir)
    for alan in ("ihlaller", "bulgular"):
        try:
            kayit[alan] = json.loads(kayit.get(alan) or "null")
        except (TypeError, ValueError):
            kayit[alan] = None
    return kayit


def son_olaylar(limit: int = 50, karar: Optional[str] = None) -> list[dict]:
    """Panelin canlı akışı için en yeni olaylar."""
    conn = baglanti()
    sorgu = "SELECT * FROM olaylar"
    parametreler: list[Any] = []
    if karar:
        sorgu += " WHERE karar = ?"
        parametreler.append(karar)
    sorgu += " ORDER BY id DESC LIMIT ?"
    parametreler.append(int(limit))

    with _KILIT:
        satirlar = conn.execute(sorgu, parametreler).fetchall()
    return [_satiri_sozluge_cevir(s) for s in satirlar]


def istatistikler(son_saat: int = 24) -> dict[str, Any]:
    """Panel üst kartları: toplamlar, kararlara göre dağılım, veri türleri."""
    conn = baglanti()
    esik = (datetime.now(timezone.utc) - timedelta(hours=son_saat)).isoformat(
        timespec="seconds"
    )

    with _KILIT:
        toplam = conn.execute("SELECT COUNT(*) FROM olaylar").fetchone()[0]
        karar_dagilimi = {
            satir["karar"]: satir["adet"]
            for satir in conn.execute(
                "SELECT karar, COUNT(*) AS adet FROM olaylar GROUP BY karar"
            ).fetchall()
        }
        maskelenen_toplam = conn.execute(
            "SELECT COALESCE(SUM(maskelenen_adet), 0) FROM olaylar"
        ).fetchone()[0]
        son_pencere = conn.execute(
            "SELECT COUNT(*) FROM olaylar WHERE zaman >= ?", (esik,)
        ).fetchone()[0]
        ortalama_gecikme = conn.execute(
            "SELECT COALESCE(AVG(gecikme_ms), 0) FROM olaylar WHERE karar != 'engel'"
        ).fetchone()[0]
        bulgu_satirlari = conn.execute(
            "SELECT bulgular FROM olaylar WHERE maskelenen_adet > 0"
        ).fetchall()

    # Veri türü dağılımı JSON alanından toplanır (V0.1 için yeterli;
    # hacim büyürse ayrı bir `bulgular` tablosuna taşınmalı).
    tur_dagilimi: dict[str, int] = {}
    for satir in bulgu_satirlari:
        try:
            veri = json.loads(satir["bulgular"] or "{}")
        except (TypeError, ValueError):
            continue
        for tur, adet in (veri or {}).items():
            tur_dagilimi[tur] = tur_dagilimi.get(tur, 0) + int(adet)

    return {
        "toplam_istek": toplam,
        "engellenen": karar_dagilimi.get("engel", 0),
        "uyari": karar_dagilimi.get("uyari", 0),
        "izin": karar_dagilimi.get("izin", 0),
        "hata": karar_dagilimi.get("hata", 0),
        "maskelenen_veri": int(maskelenen_toplam or 0),
        "son_pencere_saat": son_saat,
        "son_pencere_istek": son_pencere,
        "ortalama_gecikme_ms": round(float(ortalama_gecikme or 0), 1),
        "tur_dagilimi": dict(
            sorted(tur_dagilimi.items(), key=lambda ikili: -ikili[1])
        ),
    }


def temizle() -> None:
    """Tüm kayıtları siler (yalnızca test/geliştirme içindir)."""
    conn = baglanti()
    with _KILIT:
        conn.execute("DELETE FROM olaylar")

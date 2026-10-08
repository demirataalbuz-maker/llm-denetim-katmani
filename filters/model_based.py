# -*- coding: utf-8 -*-
"""
Model tabanlı giriş filtresi (isteğe bağlı, varsayılan KAPALI).

HuggingFace prompt-injection sınıflandırıcılarını kural filtresinin yanında
ikinci katman olarak çalıştırır.

Hata davranışı: filtre açıkken model yüklenemez veya çıkarım çökerse
`kati=True` (varsayılan, MODEL_FILTRESI_KATI) iken istek ENGELLENİR
(fail-closed). Aksi hâlde saldırgan modeli çökerten bir girdiyle ikinci
katmanı sessizce devre dışı bırakabilir. `kati=False` ile eski davranışa
(yalnızca kural filtresiyle devam) dönülebilir.

Kurulum:
    pip install transformers torch
    MODEL_FILTRESI_ACIK=true HF_MODEL_ADI=<model> uvicorn proxy:app

Not: Model ilk çağrıda tembel (lazy) yüklenir; sunucu açılışını yavaşlatmaz.
Sınıflandırıcının etiket adları modele göre değişir; "INJECTION"/"JAILBREAK"/
"UNSAFE"/"LABEL_1" gibi etiketler riskli kabul edilir.
"""

from __future__ import annotations

import logging
from typing import Optional

from filters.base import FiltreSonucu, GirisFiltresi, Ihlal, Karar

kayitci = logging.getLogger(__name__)

RISKLI_ETIKETLER = {"INJECTION", "JAILBREAK", "UNSAFE", "MALICIOUS", "LABEL_1"}


class ModelTabanliFiltre(GirisFiltresi):
    """HuggingFace metin sınıflandırıcısıyla riskli istek tespiti."""

    ad = "model_tabanli"

    def __init__(
        self,
        model_adi: str,
        esik: float = 0.80,
        acik: bool = False,
        azami_karakter: int = 4000,
        kati: bool = True,
    ) -> None:
        self.model_adi = model_adi
        self.esik = esik
        self.acik = acik
        self.kati = kati
        self.azami_karakter = azami_karakter
        self._boru = None            # transformers pipeline
        self._yukleme_denendi = False
        self._hata: Optional[str] = None

    # -- Tembel yükleme ------------------------------------------------------

    def _boru_hattini_al(self):
        if self._boru is not None or self._yukleme_denendi:
            return self._boru
        self._yukleme_denendi = True
        try:
            from transformers import pipeline  # yerel içe aktarma: zorunlu değil

            self._boru = pipeline(
                "text-classification",
                model=self.model_adi,
                truncation=True,
                max_length=512,
            )
            kayitci.info("Model tabanlı filtre yüklendi: %s", self.model_adi)
        except Exception as hata:  # kütüphane yok, ağ yok, model bulunamadı...
            self._hata = str(hata)
            kayitci.warning(
                "Model tabanlı filtre devre dışı (%s): %s", self.model_adi, hata
            )
        return self._boru

    @property
    def kullanilabilir(self) -> bool:
        # Burada model YÜKLENMEZ: zincir kurulurken çağrıldığı için yükleme
        # ilk denetime ertelenir (tembel yükleme).
        return self.acik

    def _hata_sonucu(self, neden: str) -> FiltreSonucu:
        """Model kullanılamadığında kati moda göre ENGEL veya IZIN döner."""
        if not self.kati:
            return FiltreSonucu(
                karar=Karar.IZIN, puan=0.0, aciklama=neden, filtre_adi=self.ad,
            )
        return FiltreSonucu(
            karar=Karar.ENGEL,
            puan=1.0,
            ihlaller=[Ihlal(
                kural="model_filtresi_kullanilamaz",
                kategori="sistem",
                puan=1.0,
                aciklama="Model filtresi çalışmadı; kati mod gereği engellendi",
                kanit=neden[:80],
            )],
            aciklama=neden,
            filtre_adi=self.ad,
        )

    # -- Denetim -------------------------------------------------------------

    def denetle(self, metin: str, baglam: Optional[dict] = None) -> FiltreSonucu:
        boru = self._boru_hattini_al()
        if boru is None:
            return self._hata_sonucu(
                f"model yüklü değil ({self._hata or 'kapalı'})"
            )

        try:
            cikti = boru(metin[: self.azami_karakter])
        except Exception as hata:
            kayitci.warning("Model çıkarımı başarısız: %s", hata)
            return self._hata_sonucu(f"çıkarım hatası: {hata}")

        # pipeline tek metin için [{"label": ..., "score": ...}] döndürür
        ilk = cikti[0] if isinstance(cikti, list) and cikti else {}
        etiket = str(ilk.get("label", "")).upper()
        skor = float(ilk.get("score", 0.0))

        riskli = etiket in RISKLI_ETIKETLER
        puan = skor if riskli else 0.0

        ihlaller: list[Ihlal] = []
        if riskli and skor >= self.esik:
            ihlaller.append(Ihlal(
                kural="model_siniflandirma",
                kategori="prompt_injection",
                puan=puan,
                aciklama=f"Sınıflandırıcı riskli etiket verdi: {etiket}",
                kanit=f"{etiket} ({skor:.2f})",
            ))
            karar = Karar.ENGEL
        elif riskli and skor >= self.esik * 0.6:
            ihlaller.append(Ihlal(
                kural="model_siniflandirma_zayif",
                kategori="prompt_injection",
                puan=puan,
                aciklama=f"Sınıflandırıcı şüpheli buldu: {etiket}",
                kanit=f"{etiket} ({skor:.2f})",
            ))
            karar = Karar.UYARI
        else:
            karar = Karar.IZIN

        return FiltreSonucu(
            karar=karar,
            puan=round(puan, 3),
            ihlaller=ihlaller,
            aciklama=f"{etiket}={skor:.2f}",
            filtre_adi=self.ad,
        )

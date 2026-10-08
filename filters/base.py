# -*- coding: utf-8 -*-
"""
Giriş filtreleri için ortak sözleşme.

Her filtre bir metni alır ve `FiltreSonucu` döndürür. Birden fazla filtre
`FiltreZinciri` ile birleştirilir; zincirin kararı en kötü karardır, puanı ise
en yüksek puandır (muhafazakâr birleştirme).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Karar(str, Enum):
    """Giriş denetiminin nihai kararı."""

    IZIN = "izin"      # temiz, LLM'e gönder
    UYARI = "uyari"    # şüpheli ama geçir, logla
    ENGEL = "engel"    # LLM'e hiç gitmesin

    @property
    def agirlik(self) -> int:
        return {"izin": 0, "uyari": 1, "engel": 2}[self.value]


@dataclass
class Ihlal:
    """Tetiklenen tek bir kural/sinyal."""

    kural: str          # "talimat_gecersiz_kilma"
    kategori: str       # "prompt_injection", "veri_sizdirma" ...
    puan: float         # 0.0 - 1.0 arası katkı
    aciklama: str = ""
    kanit: str = ""     # eşleşen kısa metin parçası (kısaltılmış)

    def ozet(self) -> dict:
        return {
            "kural": self.kural,
            "kategori": self.kategori,
            "puan": round(self.puan, 3),
            "kanit": self.kanit,
        }


@dataclass
class FiltreSonucu:
    """Bir filtrenin (veya zincirin) çıktısı."""

    karar: Karar
    puan: float
    ihlaller: list[Ihlal] = field(default_factory=list)
    aciklama: str = ""
    filtre_adi: str = ""

    @property
    def engellendi(self) -> bool:
        return self.karar is Karar.ENGEL

    @property
    def kategoriler(self) -> list[str]:
        return sorted({i.kategori for i in self.ihlaller})

    def ozet(self) -> dict:
        """Loglanabilir gösterim."""
        return {
            "karar": self.karar.value,
            "puan": round(self.puan, 3),
            "filtre": self.filtre_adi,
            "kategoriler": self.kategoriler,
            "ihlaller": [i.ozet() for i in self.ihlaller],
        }


class GirisFiltresi(ABC):
    """Tüm giriş filtrelerinin uyması gereken arayüz."""

    ad: str = "isimsiz"

    @abstractmethod
    def denetle(self, metin: str, baglam: Optional[dict] = None) -> FiltreSonucu:
        """Metni denetler; asla istisna fırlatmamalıdır (fail-open riski)."""

    @property
    def kullanilabilir(self) -> bool:
        """Filtrenin çalışmaya hazır olup olmadığı (örn. model yüklendi mi)."""
        return True


class FiltreZinciri(GirisFiltresi):
    """
    Birden çok filtreyi sırayla çalıştırır.

    `erken_cikis=True` iken ilk ENGEL kararında durur; bu, pahalı model tabanlı
    filtreyi ucuz kural filtresi zaten engellediğinde çalıştırmamayı sağlar.

    `hatada_engelle=True` iken istisna fırlatan bir filtre isteği ENGELLER
    (fail-closed); denetlenemeyen istek LLM'e gitmez.
    """

    ad = "zincir"

    def __init__(
        self,
        filtreler: list[GirisFiltresi],
        erken_cikis: bool = True,
        hatada_engelle: bool = True,
    ):
        self.filtreler = [f for f in filtreler if f.kullanilabilir]
        self.erken_cikis = erken_cikis
        self.hatada_engelle = hatada_engelle

    def denetle(self, metin: str, baglam: Optional[dict] = None) -> FiltreSonucu:
        toplu_ihlaller: list[Ihlal] = []
        en_yuksek_puan = 0.0
        en_kotu_karar = Karar.IZIN
        aciklamalar: list[str] = []
        tetikleyen = ""

        for filtre in self.filtreler:
            try:
                sonuc = filtre.denetle(metin, baglam)
            except Exception as hata:
                if not self.hatada_engelle:  # eski davranış: zincir devam etsin
                    toplu_ihlaller.append(Ihlal(
                        kural="filtre_hatasi", kategori="sistem", puan=0.0,
                        aciklama=f"{filtre.ad}: {hata}",
                    ))
                    continue
                sonuc = FiltreSonucu(
                    karar=Karar.ENGEL,
                    puan=1.0,
                    ihlaller=[Ihlal(
                        kural="filtre_hatasi", kategori="sistem", puan=1.0,
                        aciklama=f"{filtre.ad} çöktü; istek engellendi",
                        kanit=hata.__class__.__name__,
                    )],
                    filtre_adi=filtre.ad,
                )

            toplu_ihlaller.extend(sonuc.ihlaller)
            en_yuksek_puan = max(en_yuksek_puan, sonuc.puan)
            if sonuc.karar.agirlik > en_kotu_karar.agirlik:
                en_kotu_karar = sonuc.karar
                tetikleyen = filtre.ad
            if sonuc.aciklama:
                aciklamalar.append(f"[{filtre.ad}] {sonuc.aciklama}")

            if self.erken_cikis and sonuc.engellendi:
                break

        return FiltreSonucu(
            karar=en_kotu_karar,
            puan=round(en_yuksek_puan, 3),
            ihlaller=toplu_ihlaller,
            aciklama=" | ".join(aciklamalar),
            filtre_adi=tetikleyen or self.ad,
        )

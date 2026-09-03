# -*- coding: utf-8 -*-
"""
Çıkış tarayıcıları için ortak sözleşme (arayüz + veri sınıfları).

Yeni bir tarayıcı yazmak için `CikisTarayici` sınıfından türetip `tara()` ve
`maskele()` metotlarını uygulayın; proxy.py hiçbir değişiklik gerektirmeden
zincire ekleyebilir.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Bulgu:
    """
    Metin içinde tespit edilen tek bir kişisel veri örneği.

    DİKKAT: `ham_deger` yalnızca maskeleme sırasında bellekte kullanılır,
    asla loglanmaz veya panele yazılmaz. Log kaydına `ozet()` çıktısı gider.
    """

    tur: str            # "TCKN", "EPOSTA" ...
    baslangic: int
    bitis: int
    ham_deger: str
    maskeli_deger: str
    duyarlilik: str = "yuksek"

    @property
    def uzunluk(self) -> int:
        return self.bitis - self.baslangic

    def ozet(self) -> dict:
        """Loglanabilir (kişisel veri içermeyen) gösterim."""
        return {
            "tur": self.tur,
            "konum": self.baslangic,
            "uzunluk": self.uzunluk,
            "duyarlilik": self.duyarlilik,
            "maskeli": self.maskeli_deger,
        }


@dataclass
class TaramaSonucu:
    """Bir metnin taranmış ve maskelenmiş hâli."""

    temiz_metin: str
    bulgular: list[Bulgu] = field(default_factory=list)

    @property
    def maskelendi_mi(self) -> bool:
        return bool(self.bulgular)

    @property
    def adet(self) -> int:
        return len(self.bulgular)

    def tur_dagilimi(self) -> dict[str, int]:
        """{"TCKN": 2, "EPOSTA": 1} biçiminde sayım."""
        dagilim: dict[str, int] = {}
        for b in self.bulgular:
            dagilim[b.tur] = dagilim.get(b.tur, 0) + 1
        return dagilim

    def ozet(self) -> dict:
        return {
            "adet": self.adet,
            "dagilim": self.tur_dagilimi(),
            "bulgular": [b.ozet() for b in self.bulgular],
        }


class CikisTarayici(ABC):
    """Tüm çıkış tarayıcılarının uyması gereken arayüz."""

    ad: str = "isimsiz"

    @abstractmethod
    def tara(self, metin: str) -> list[Bulgu]:
        """Metindeki bulguları döndürür, metni değiştirmez."""

    @abstractmethod
    def maskele(self, metin: str) -> TaramaSonucu:
        """Metni tarar ve bulguları maskeleyerek yeni metni döndürür."""

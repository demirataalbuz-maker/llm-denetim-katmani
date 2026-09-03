# -*- coding: utf-8 -*-
"""
Kişisel veri tarayıcısı (çıkış denetimi).

Akış:
  1. Etkin her kalıbın regexi metin üzerinde çalıştırılır.
  2. Eşleşme, kalıbın algoritmik doğrulayıcısından geçirilir (TCKN mod-10,
     IBAN mod-97, kart için Luhn). Geçmeyen eşleşme atılır -> yanlış pozitif
     azalır.
  3. Çakışan eşleşmeler öncelik sırasına göre teke indirilir (aynı 11 hane
     hem TCKN hem telefon gibi görünebilir).
  4. Kalan bulgular sondan başa doğru maskelenir (indeksler kaymasın diye).

Kullanım:
    tarayici = PIITarayici()
    sonuc = tarayici.maskele("TCKN: 10000000078")
    sonuc.temiz_metin  -> "TCKN: *********78"
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from scanners.base import Bulgu, CikisTarayici, TaramaSonucu
from scanners.patterns import KALIPLAR, VeriKalibi


class PIITarayici(CikisTarayici):
    """Regex + algoritmik doğrulama tabanlı Türkçe kişisel veri tarayıcısı."""

    ad = "pii_tarayici"

    def __init__(
        self,
        kaliplar: Optional[Sequence[VeriKalibi]] = None,
        etkin_turler: Optional[Iterable[str]] = None,
        dusuk_guven_dahil: bool = False,
    ) -> None:
        """
        kaliplar        : kullanılacak kalıp listesi (varsayılan: tüm kütüphane)
        etkin_turler    : yalnızca bu türler taransın (örn. {"TCKN", "EPOSTA"})
        dusuk_guven_dahil: varsayilan_acik=False olan kalıpları da aç
        """
        havuz = list(kaliplar if kaliplar is not None else KALIPLAR)

        if etkin_turler is not None:
            istenen = set(etkin_turler)
            havuz = [k for k in havuz if k.ad in istenen]
        elif not dusuk_guven_dahil:
            havuz = [k for k in havuz if k.varsayilan_acik]

        # Öncelik sırası: çakışma çözümünde büyük öncelik kazanır.
        self.kaliplar = sorted(havuz, key=lambda k: k.oncelik, reverse=True)

    # -- Tarama --------------------------------------------------------------

    def tara(self, metin: str) -> list[Bulgu]:
        """Metindeki doğrulanmış, çakışmasız bulguları döndürür."""
        if not metin:
            return []

        adaylar: list[tuple[int, Bulgu]] = []  # (oncelik, bulgu)
        for kalip in self.kaliplar:
            for eslesme in kalip.regex.finditer(metin):
                deger = eslesme.group(0)
                if not kalip.gecerli_mi(deger):
                    continue
                adaylar.append((
                    kalip.oncelik,
                    Bulgu(
                        tur=kalip.ad,
                        baslangic=eslesme.start(),
                        bitis=eslesme.end(),
                        ham_deger=deger,
                        maskeli_deger=kalip.maskele(deger),
                        duyarlilik=kalip.duyarlilik,
                    ),
                ))

        return self._cakismalari_coz(adaylar)

    @staticmethod
    def _cakismalari_coz(adaylar: list[tuple[int, Bulgu]]) -> list[Bulgu]:
        """
        Üst üste binen eşleşmelerden birini seçer.
        Sıralama ölçütü: önce yüksek öncelik, sonra uzun eşleşme, sonra konum.
        """
        adaylar.sort(key=lambda ikili: (-ikili[0], -ikili[1].uzunluk,
                                        ikili[1].baslangic))
        secilenler: list[Bulgu] = []
        for _, bulgu in adaylar:
            cakisiyor = any(
                bulgu.baslangic < s.bitis and s.baslangic < bulgu.bitis
                for s in secilenler
            )
            if not cakisiyor:
                secilenler.append(bulgu)

        secilenler.sort(key=lambda b: b.baslangic)
        return secilenler

    # -- Maskeleme -----------------------------------------------------------

    def maskele(self, metin: str) -> TaramaSonucu:
        """Metni tarar, bulguları maskeler ve sonucu döndürür."""
        bulgular = self.tara(metin)
        if not bulgular:
            return TaramaSonucu(temiz_metin=metin, bulgular=[])

        parcalar: list[str] = []
        imlec = 0
        for bulgu in bulgular:
            parcalar.append(metin[imlec:bulgu.baslangic])
            parcalar.append(bulgu.maskeli_deger)
            imlec = bulgu.bitis
        parcalar.append(metin[imlec:])

        return TaramaSonucu(temiz_metin="".join(parcalar), bulgular=bulgular)


def varsayilan_tarayici(dusuk_guven_dahil: bool = False) -> PIITarayici:
    """Uygulama genelinde kullanılan tarayıcıyı üretir."""
    return PIITarayici(dusuk_guven_dahil=dusuk_guven_dahil)

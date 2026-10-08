# -*- coding: utf-8 -*-
"""
Türkiye'ye özgü kişisel veri kalıpları, doğrulayıcıları ve maskeleme
stratejileri.

Bu modül projenin özgün çekirdeği: yabancı DLP/guardrail araçları TC kimlik
numarası, TR IBAN veya TR telefon biçimlerini tanımaz. Burada her kalıp
"regex + algoritmik doğrulama" ikilisiyle tanımlanır; böylece 11 haneli her
sayı TCKN sanılmaz, yanlış pozitif oranı düşer.

Yeni bir kalıp eklemek için: VeriKalibi üretip KALIPLAR listesine ekleyin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional, Pattern

# ---------------------------------------------------------------------------
# Doğrulayıcılar (algoritmik kontroller)
# ---------------------------------------------------------------------------


def _sadece_rakam(deger: str) -> str:
    return re.sub(r"\D", "", deger)


def dogrula_tckn(deger: str) -> bool:
    """
    T.C. Kimlik Numarası doğrulaması (NVI algoritması).

    Kurallar:
      * 11 hane, ilk hane 0 olamaz.
      * 10. hane = ((1,3,5,7,9. hanelerin toplamı * 7) - (2,4,6,8. hanelerin
        toplamı)) mod 10
      * 11. hane = ilk 10 hanenin toplamı mod 10

    Örn. 10000000078 geçerli bir test değeridir.
    """
    haneler = _sadece_rakam(deger)
    if len(haneler) != 11 or haneler[0] == "0":
        return False
    if len(set(haneler)) == 1:  # 11111111111 gibi tekrarlı diziler
        return False

    r = [int(h) for h in haneler]
    tek_toplam = r[0] + r[2] + r[4] + r[6] + r[8]
    cift_toplam = r[1] + r[3] + r[5] + r[7]

    if (tek_toplam * 7 - cift_toplam) % 10 != r[9]:
        return False
    if sum(r[:10]) % 10 != r[10]:
        return False
    return True


def dogrula_iban_tr(deger: str) -> bool:
    """TR IBAN doğrulaması: 26 karakter + ISO 7064 mod-97 kontrolü."""
    s = re.sub(r"\s+", "", deger).upper()
    if len(s) != 26 or not s.startswith("TR") or not s[2:].isdigit():
        return False
    tasinmis = s[4:] + s[:4]  # ilk 4 karakter sona alınır
    sayisal = "".join(str(int(c, 36)) if c.isalpha() else c for c in tasinmis)
    return int(sayisal) % 97 == 1


def dogrula_luhn(deger: str) -> bool:
    """Kredi kartı numarası için Luhn (mod-10) kontrolü."""
    haneler = _sadece_rakam(deger)
    if not (13 <= len(haneler) <= 19):
        return False
    toplam = 0
    for i, ch in enumerate(reversed(haneler)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        toplam += n
    return toplam % 10 == 0


def dogrula_gsm_tr(deger: str) -> bool:
    """TR cep telefonu: normalize edildiğinde 5xx ile başlayan 10 hane."""
    haneler = _sadece_rakam(deger)
    if haneler.startswith("0090"):
        haneler = haneler[4:]
    elif haneler.startswith("90") and len(haneler) == 12:
        haneler = haneler[2:]
    elif haneler.startswith("0") and len(haneler) == 11:
        haneler = haneler[1:]
    return len(haneler) == 10 and haneler.startswith("5")


# ---------------------------------------------------------------------------
# Maskeleme stratejileri
# ---------------------------------------------------------------------------


def maskele_kismi(deger: str, gorunur_bas: int = 0, gorunur_son: int = 2,
                  dolgu: str = "*") -> str:
    """
    Ayraçları (boşluk, tire, parantez) koruyarak alfanümerik karakterleri
    yıldızlar. Baştan/sondan belirtilen kadar karakter denetim izlenebilirliği
    için açık bırakılır (örn. 10000000078 -> *********78).
    """
    karakterler = list(deger)
    indeksler = [i for i, c in enumerate(karakterler) if c.isalnum()]
    korunan = set(indeksler[:gorunur_bas])
    if gorunur_son:
        korunan |= set(indeksler[len(indeksler) - gorunur_son:])
    for i in indeksler:
        if i not in korunan:
            karakterler[i] = dolgu
    return "".join(karakterler)


def maskele_eposta(deger: str) -> str:
    """a****@ornek.com — alan adı korunur (yönlendirme/analiz için gerekli)."""
    yerel, ayrac, alan = deger.partition("@")
    if not ayrac:
        return maskele_kismi(deger, gorunur_son=0)
    bas = yerel[:1]
    return f"{bas}{'*' * max(len(yerel) - 1, 1)}@{alan}"


def maskele_tam(deger: str, tur: str = "VERI") -> str:
    """Değeri tamamen etiketle değiştirir: [TCKN]"""
    return f"[{tur}]"


# ---------------------------------------------------------------------------
# Kalıp tanımı
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VeriKalibi:
    """Tek bir kişisel veri türünün tanımı (eklenti birimi)."""

    ad: str                                   # "TCKN", "EPOSTA" ...
    aciklama: str
    regex: Pattern[str]
    dogrulayici: Optional[Callable[[str], bool]] = None
    strateji: str = "kismi"                   # kismi | eposta | tam
    gorunur_bas: int = 0
    gorunur_son: int = 2
    duyarlilik: str = "yuksek"                # yuksek | orta | dusuk
    oncelik: int = 50                         # çakışan eşleşmede büyük olan kazanır
    varsayilan_acik: bool = True

    def gecerli_mi(self, deger: str) -> bool:
        """Regex eşleşmesini algoritmik olarak doğrular."""
        return self.dogrulayici(deger) if self.dogrulayici else True

    def maskele(self, deger: str) -> str:
        if self.strateji == "eposta":
            return maskele_eposta(deger)
        if self.strateji == "tam":
            return maskele_tam(deger, tur=self.ad)
        return maskele_kismi(deger, self.gorunur_bas, self.gorunur_son)


# ---------------------------------------------------------------------------
# Kalıp kütüphanesi
# ---------------------------------------------------------------------------

KALIPLAR: list[VeriKalibi] = [
    VeriKalibi(
        ad="TCKN",
        aciklama="T.C. Kimlik Numarası (algoritmik doğrulamalı)",
        # 11 hane; öncesinde/sonrasında rakam yok, ilk hane 1-9
        regex=re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)"),
        dogrulayici=dogrula_tckn,
        gorunur_son=2,
        duyarlilik="yuksek",
        oncelik=100,
    ),
    VeriKalibi(
        ad="IBAN_TR",
        aciklama="Türkiye IBAN (mod-97 doğrulamalı)",
        regex=re.compile(
            r"(?<![A-Za-z0-9])TR\d{2}(?:[ ]?\d{4}){5}[ ]?\d{2}(?![A-Za-z0-9])"
        ),
        dogrulayici=dogrula_iban_tr,
        gorunur_bas=4,
        gorunur_son=4,
        duyarlilik="yuksek",
        oncelik=95,
    ),
    VeriKalibi(
        ad="KREDI_KARTI",
        aciklama="Kredi kartı numarası (Luhn doğrulamalı)",
        regex=re.compile(r"(?<!\d)(?:\d{4}[ -]?){3}\d{4}(?!\d)"),
        dogrulayici=dogrula_luhn,
        gorunur_son=4,
        duyarlilik="yuksek",
        oncelik=90,
    ),
    VeriKalibi(
        ad="GSM_TR",
        aciklama="Türkiye cep telefonu numarası",
        regex=re.compile(
            r"(?<![\w+])(?:\+90|0090|90|0)?[ .\-]?\(?5\d{2}\)?[ .\-]?"
            r"\d{3}[ .\-]?\d{2}[ .\-]?\d{2}(?!\d)"
        ),
        dogrulayici=dogrula_gsm_tr,
        gorunur_son=2,
        duyarlilik="yuksek",
        oncelik=80,
    ),
    VeriKalibi(
        ad="SABIT_TEL_TR",
        aciklama="Türkiye sabit hat numarası (alan kodu 2/3/4 ile başlar)",
        regex=re.compile(
            r"(?<![\w+])(?:\+90|0090|0)[ .\-]?\(?[2-4]\d{2}\)?[ .\-]?"
            r"\d{3}[ .\-]?\d{2}[ .\-]?\d{2}(?!\d)"
        ),
        gorunur_son=2,
        duyarlilik="orta",
        oncelik=70,
    ),
    VeriKalibi(
        ad="EPOSTA",
        aciklama="E-posta adresi",
        regex=re.compile(
            r"(?<![\w.+-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![\w.])"
        ),
        strateji="eposta",
        duyarlilik="orta",
        oncelik=60,
    ),
    VeriKalibi(
        ad="EPOSTA_GIZLENMIS",
        aciklama="Gizlenmiş e-posta (ali [at] ornek [dot] com, ali @ ornek.com)",
        regex=re.compile(
            r"(?<![\w.+-])[A-Za-z0-9._%+\-]+\s*"
            r"(?:[\[({<]\s*(?:at|et)\s*[\])}>]|\s@\s?|@\s)\s*"
            r"[A-Za-z0-9\-]+"
            r"(?:\s*(?:\.|[\[({<]\s*(?:dot|nokta)\s*[\])}>])\s*[A-Za-z0-9\-]+)*"
            r"\s*(?:\.|[\[({<]\s*(?:dot|nokta)\s*[\])}>])\s*[A-Za-z]{2,}(?![\w.])",
            re.IGNORECASE,
        ),
        strateji="tam",
        duyarlilik="orta",
        oncelik=55,
    ),
    VeriKalibi(
        ad="ADRES_IPUCU",
        aciklama="Türkçe açık adres kalıbı (Mah./Cad./Sok. + No)",
        regex=re.compile(
            r"[A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü]*\s+"
            r"(?:Mah\.?|Mahallesi|Cad\.?|Caddesi|Sok\.?|Sokak|Bulv\.?|Bulvarı)"
            r"(?:[^\n]{0,40}?No[:\s]\s*\d+[\w/]*)?"
        ),
        strateji="tam",
        duyarlilik="orta",
        oncelik=10,
        # Yanlış pozitifi yüksek; DUSUK_GUVEN_KALIPLARI=true ile açılır.
        varsayilan_acik=False,
    ),
]

KALIP_HARITASI: dict[str, VeriKalibi] = {k.ad: k for k in KALIPLAR}

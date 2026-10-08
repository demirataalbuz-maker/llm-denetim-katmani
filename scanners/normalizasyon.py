# -*- coding: utf-8 -*-
"""
Çıkış taraması için metin görünümleri (views).

Sorun: model kişisel veriyi biçimini değiştirerek yazabilir ve düz regex
taraması bunu kaçırır:

    100 000 000 78                 (rakam grupları arasında boşluk)
    1-0-0-0-0-0-0-0-0-7-8          (her rakam arasında ayraç)
    TR33-0006-1005-...             (tireli IBAN)
    １００００００００７８            (tam genişlikli Unicode rakamlar)
    bir sıfır sıfır ... yedi sekiz (yazıyla rakamlar)

Çözüm: metnin birkaç "görünümü" üretilir; her görünüm karakter başına
orijinal metindeki [başlangıç, bitiş) aralığını taşır. Kalıplar her görünümde
çalıştırılır, eşleşme orijinal aralığa geri eşlenir ve maskeleme ORİJİNAL
metin üzerinde yapılır. Böylece dönüşüm ne kadar agresif olursa olsun yanıt
metni bozulmaz.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Sıfır genişlikli / yön değiştiren karakterler: rakamların arasına
# serpiştirilerek regex eşleşmesini bozmak için kullanılabilir.
GORUNMEZ = re.compile("[­​-‏‪-‮⁠-⁤﻿]")

# Rakamlar arasında yok sayılabilecek ayraçlar (en fazla 3 karakterlik dizi).
AYRACLAR = " \t.-_/(), ‐‑‒–—"
_AZAMI_AYRAC = 3

_TR_KATLAMA = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})

RAKAM_KELIMELERI: dict[str, str] = {
    # Türkçe (ASCII'ye katlanmış)
    "sifir": "0", "bir": "1", "iki": "2", "uc": "3", "dort": "4",
    "bes": "5", "alti": "6", "yedi": "7", "sekiz": "8", "dokuz": "9",
    # İngilizce
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_KELIME = re.compile(r"[^\W\d_]+")


@dataclass
class Gorunum:
    """Dönüştürülmüş metin + her karakterin orijinal metindeki aralığı."""

    metin: str
    konumlar: list[tuple[int, int]]

    def orijinal_aralik(self, baslangic: int, bitis: int) -> tuple[int, int]:
        return self.konumlar[baslangic][0], self.konumlar[bitis - 1][1]


def kimlik(metin: str) -> Gorunum:
    """Dönüşümsüz görünüm."""
    return Gorunum(metin, [(i, i + 1) for i in range(len(metin))])


def nfkc(g: Gorunum) -> Gorunum:
    """Unicode NFKC (tam genişlikli rakam -> ASCII) + görünmez karakter silme."""
    metin: list[str] = []
    konumlar: list[tuple[int, int]] = []
    for ch, aralik in zip(g.metin, g.konumlar):
        if GORUNMEZ.match(ch):
            continue
        for yeni in unicodedata.normalize("NFKC", ch):
            metin.append(yeni)
            konumlar.append(aralik)
    return Gorunum("".join(metin), konumlar)


def rakam_kelimeleri(g: Gorunum) -> Gorunum:
    """'bir sıfır sıfır' -> '100'; her rakam kelimenin orijinal aralığını alır."""
    metin: list[str] = []
    konumlar: list[tuple[int, int]] = []
    imlec = 0
    for eslesme in _KELIME.finditer(g.metin):
        kelime = eslesme.group(0).translate(_TR_KATLAMA).lower()
        rakam = RAKAM_KELIMELERI.get(kelime)
        if rakam is None:
            continue
        metin.extend(g.metin[imlec:eslesme.start()])
        konumlar.extend(g.konumlar[imlec:eslesme.start()])
        metin.append(rakam)
        konumlar.append((g.konumlar[eslesme.start()][0],
                         g.konumlar[eslesme.end() - 1][1]))
        imlec = eslesme.end()
    metin.extend(g.metin[imlec:])
    konumlar.extend(g.konumlar[imlec:])
    return Gorunum("".join(metin), konumlar)


def ayraclari_sik(g: Gorunum) -> Gorunum:
    """
    İki rakam (veya 'TR' öneki ile rakam) arasındaki kısa ayraç dizilerini
    siler: '100 000 000 78' -> '10000000078', 'TR33-0006' -> 'TR330006'.
    """
    s = g.metin
    silinecek: set[int] = set()
    i = 0
    while i < len(s):
        if s[i] not in AYRACLAR:
            i += 1
            continue
        j = i
        while j < len(s) and s[j] in AYRACLAR:
            j += 1
        sol_rakam = i > 0 and (s[i - 1].isdigit()
                               or s[max(0, i - 2):i].upper() == "TR")
        sag_rakam = j < len(s) and s[j].isdigit()
        if sol_rakam and sag_rakam and j - i <= _AZAMI_AYRAC:
            silinecek.update(range(i, j))
        i = j

    if not silinecek:
        return g
    return Gorunum(
        "".join(c for k, c in enumerate(s) if k not in silinecek),
        [a for k, a in enumerate(g.konumlar) if k not in silinecek],
    )


def gorunumler(metin: str) -> list[Gorunum]:
    """Taranacak tüm görünümler; ilki her zaman dönüşümsüz metindir."""
    ham = kimlik(metin)
    temiz = nfkc(ham)
    return [
        ham,
        temiz,
        ayraclari_sik(temiz),
        ayraclari_sik(rakam_kelimeleri(temiz)),
    ]

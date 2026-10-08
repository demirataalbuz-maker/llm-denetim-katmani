# -*- coding: utf-8 -*-
"""
Kural tabanlı giriş filtresi.

Amaç: kullanıcı isteğini LLM'e göndermeden önce "yönerge dışı" kalıpları
yakalamak. Kapsam:
  * talimat geçersiz kılma (önceki talimatları unut / ignore all instructions)
  * sistem promptu sızdırma
  * rol değiştirme / jailbreak kalıpları
  * toplu veri sızdırma istekleri (KVKK açısından en kritik başlık)
  * komut/kod enjeksiyonu
  * gizli yönerge işaretleri, base64 blob, görünmez Unicode, ayraç spam'i

Puanlama: her kural bir ağırlık ekler, toplam 1.0 ile sınırlanır.
  puan >= engel_esigi  -> ENGEL
  puan >= uyari_esigi  -> UYARI
  aksi                 -> IZIN

Kurallar metnin iki görünümünde çalışır:
  1. `normalize()`  — Türkçe karakterler ASCII'ye katlanır (ı->i, ş->s ...).
     Katlama 1:1 olduğu için eşleşme indeksleri orijinal metinle aynı kalır;
     kanıt parçası orijinal metinden kesilir.
  2. `derin_normalize()` — atlatma tekniklerini geri alır: NFKC, görünmez
     karakter silme, Kiril/Yunan homoglifleri, leetspeak (0nceki, 1gn0re) ve
     harf aralarına konan boşluk/nokta (Ö n c e k i, i.g.n.o.r.e). Yalnızca
     bu görünümde eşleşen kuralın kanıtı derin metinden alınır.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Pattern

from filters.base import FiltreSonucu, GirisFiltresi, Ihlal, Karar

# ---------------------------------------------------------------------------
# Metin normalizasyonu
# ---------------------------------------------------------------------------

_KATLAMA = str.maketrans({
    "ı": "i", "İ": "i", "I": "i", "i": "i",
    "ş": "s", "Ş": "s",
    "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u",
    "ö": "o", "Ö": "o",
    "ç": "c", "Ç": "c",
    "â": "a", "î": "i", "û": "u",
})

# Sıfır genişlikli / yön değiştiren Unicode karakterler: gizli yönerge
# saklamak veya filtre atlatmak için kullanılır, olağan metinde bulunmazlar.
GORUNMEZ_KOD_NOKTALARI = [
    (0x200B, 0x200F),   # ZWSP, ZWNJ, ZWJ, LRM, RLM
    (0x202A, 0x202E),   # bidi yön değiştiriciler (RLO vb.)
    (0x2060, 0x2064),   # word joiner, görünmez operatörler
    (0xFEFF, 0xFEFF),   # BOM / zero width no-break space
    (0x00AD, 0x00AD),   # soft hyphen
]
GORUNMEZ_KARAKTERLER = re.compile(
    "[" + "".join(chr(a) + "-" + chr(b) for a, b in GORUNMEZ_KOD_NOKTALARI) + "]"
)


def normalize(metin: str) -> str:
    """Küçük harfe indirger ve Türkçe karakterleri ASCII'ye katlar (1:1)."""
    return metin.translate(_KATLAMA).lower()


# Latin harfe benzeyen Kiril / Yunan harfleri (küçük harf sonrası).
_HOMOGLIF = str.maketrans({
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h",
    "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "і": "i",
    "ї": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ɡ": "g", "һ": "h", "ԛ": "q",
    "ԝ": "w", "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v",
    "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
})
_LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
         "@": "a", "$": "s"}
# Yalnızca harfe bitişik leet karakterleri çevrilir; saf rakam dizileri
# (TCKN, sipariş no) dokunulmadan kalır.
_LEET_KARAKTER = re.compile(
    r"(?<=[a-z])[013457@$]|[013457@$](?=[a-z])"
)
# "o n c e k i", "i.g.n.o.r.e", "u-n-u-t" gibi tek harf dizileri.
_ARALIKLI_HARFLER = re.compile(r"(?<!\w)(?:\w[ .\-_*+]{1,2}){2,}\w(?!\w)")


def derin_normalize(metin: str) -> str:
    """Atlatma tekniklerini geri alan, indeksleri KORUMAYAN normalizasyon."""
    s = unicodedata.normalize("NFKC", metin)
    s = GORUNMEZ_KARAKTERLER.sub("", s)
    s = normalize(s).translate(_HOMOGLIF)
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if not unicodedata.combining(c))
    s = _LEET_KARAKTER.sub(lambda m: _LEET[m.group(0)], s)
    s = _ARALIKLI_HARFLER.sub(
        lambda m: re.sub(r"[ .\-_*+]", "", m.group(0)), s
    )
    return re.sub(r"\s+", " ", s)


# ---------------------------------------------------------------------------
# Kural tanımı
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Kural:
    """Tek bir tespit kuralı (eklenti birimi)."""

    ad: str
    kategori: str
    regex: Pattern[str]
    puan: float
    aciklama: str = ""


# Ağırlıklar deneyimsel başlangıç değerleridir; kurum politikasına göre
# ayarlanmalıdır. Tek başına 0.75'i geçen kurallar tek eşleşmede engeller.
KURALLAR: list[Kural] = [
    Kural(
        ad="talimat_gecersiz_kilma",
        kategori="prompt_injection",
        regex=re.compile(
            # "önceki / sana verilen / yukarıda verilen ... talimatları ... unut"
            r"(onceki|yukarida\w*|butun|tum|ilk|sana\s+verilen|verilen|mevcut)"
            r"\s+(\w+\s+){0,2}"
            r"(talimat|yonerge|kural|komut|mesaj|sinirlama|kisitlama)\w*\s+"
            r"(\w+\s+){0,2}"
            r"(unut(?!ma)|yok say|gormezden gel|dikkate alma|iptal et|sil\b"
            r"|onemseme|bir kenara birak|uyma\b|cigne|gecersiz)"
            r"|ignore\s+(\w+\s+){0,4}(previous|prior|above|earlier|preceding|initial)"
            r"\s+(\w+\s+){0,2}(instruction|prompt|rule|direction|guideline)s?"
            r"|disregard\s+(\w+\s+){0,3}(previous|above|prior|earlier|instruction|rule)"
            r"|forget\s+(everything|all|any|what)\s+(\w+\s+){0,5}"
            r"(told|said|instruct|above|before|previous|earlier)"
            r"|(override|bypass)\s+(\w+\s+){0,2}(instruction|system prompt|rule)s?"
        ),
        puan=0.80,
        aciklama="Sistem talimatlarını geçersiz kılma girişimi",
    ),
    Kural(
        ad="sistem_promptu_sizdirma",
        kategori="prompt_injection",
        regex=re.compile(
            r"(sistem|system)\s*(prompt|mesaj|talimat|yonerge)\w*\s*"
            r"(nedir|ne|goster|yaz|payla|soyle|aktar|kopyala)"
            r"|(baslangic|gizli|senin|sana verilen|orijinal)\s+"
            r"(talimat|yonerge|prompt)\w*\s*(\w+\s*){0,2}?"
            r"(nedir|neydi|goster|yaz|payla|soyle|aktar|tekrarla|kopyala|listele)"
            r"|(reveal|show|print|repeat|output)\s+(your\s+|the\s+)?"
            r"(system\s+)?(prompt|instructions|rules)"
            r"|repeat\s+everything\s+above"
        ),
        puan=0.75,
        aciklama="Sistem promptunu sızdırma girişimi",
    ),
    Kural(
        ad="rol_degistirme_jailbreak",
        kategori="jailbreak",
        regex=re.compile(
            r"\bdan\s*mod\w*|\bdeveloper\s*mode\b|\bjailbreak\b"
            r"|(kisitlama|sinirlama|filtre|sansur)\w*\s*(olmadan|olmaksizin|disi|yok)"
            r"|(kisitlamasiz|filtresiz|sansursuz|kuralsiz)\s+"
            r"(bir\s+)?(yapay zeka|asistan|model|ai|mod)"
            r"|artik\s+(bir\s+)?\w+\s+degilsin"
            r"|(sen\s+artik|bundan boyle)\s+\w*\s*(hicbir\s+)?kural"
            r"|act\s+as\s+(an?\s+)?\w+\s+without\s+(any\s+)?(restriction|filter|rule)"
            r"|pretend\s+(you\s+are|to\s+be)\s+.{0,30}(unrestricted|uncensored)"
        ),
        puan=0.70,
        aciklama="Rol değiştirme / kısıtlama aşma girişimi",
    ),
    Kural(
        ad="toplu_veri_sizdirma",
        kategori="veri_sizdirma",
        regex=re.compile(
            r"(tum|butun|hepsi|hepsini|toplu)\s+"
            r"(\w+\s+){0,3}"
            r"(musteri|kullanici|calisan|personel|hasta|ogrenci|uye|abone|vatandas)"
            r"\w*\s*(\w+\s+){0,3}"
            r"(bilgi|kayit|liste|veri|tc|kimlik|telefon|adres|eposta|e-posta|iban|maas)"
            r"|(musteri|kullanici|calisan|hasta|uye)\s*(listesi|veritabani|tablosu)\w*\s*"
            r"(ver|gonder|yaz|cikar|listele|paylas|export|dok)"
            r"|(veritabani|database)\w*\s*(dok|dump|kopyala|disari|export)"
            r"|select\s+\*\s+from\b"
            r"|(list|dump|export)\s+all\s+(customer|user|employee|patient)s?"
        ),
        # KVKK açısından en kritik başlık: tek eşleşmede engellenir.
        puan=0.80,
        aciklama="Toplu kişisel veri talebi (KVKK riski)",
    ),
    Kural(
        ad="kod_komut_enjeksiyonu",
        kategori="komut_enjeksiyonu",
        regex=re.compile(
            r"\brm\s+-rf\b|\bos\.system\s*\(|\bsubprocess\.\w+\s*\("
            r"|\b__import__\s*\(|\beval\s*\(|\bexec\s*\("
            r"|\bdrop\s+table\b|\btruncate\s+table\b|\bunion\s+select\b"
            r"|<script[\s>]"
        ),
        puan=0.55,
        aciklama="Kod veya komut enjeksiyonu kalıbı",
    ),
    Kural(
        ad="gizli_yonerge_isareti",
        kategori="prompt_injection",
        regex=re.compile(
            r"<\|[a-z_]+\|>|\[\[\s*system\s*\]\]|###\s*(system|instruction|talimat)"
            r"|^\s*system\s*:|\bassistant\s*:\s*$"
            r"|\{\{\s*(system|prompt|instruction)\w*\s*\}\}",
            re.MULTILINE,
        ),
        puan=0.40,
        aciklama="Sohbet biçimini taklit eden gizli yönerge işareti",
    ),
    Kural(
        ad="kodlanmis_yuk",
        kategori="kacinma",
        regex=re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{80,}={0,2}(?![A-Za-z0-9+/])"),
        puan=0.30,
        aciklama="Uzun base64 benzeri blok (filtre atlatma denemesi olabilir)",
    ),
    Kural(
        ad="rol_yeniden_tanimlama",
        kategori="jailbreak",
        regex=re.compile(
            r"(bu\s+bir\s+)?(test|deneme|simulasyon)\s+(ortami|modu)\w*\s*"
            r"(oldugu icin|olduğundan)?\s*(kural|kisitlama|filtre)"
            r"|guvenlik\s+(kontrol|filtre|denetim)\w*\s*(kapat|devre disi|atla|bypass)"
            r"|(bypass|disable|turn\s+off)\s+(the\s+)?(safety|security|filter|guardrail)"
        ),
        puan=0.60,
        aciklama="Denetim mekanizmasını devre dışı bırakma girişimi",
    ),
]


# ---------------------------------------------------------------------------
# Filtre
# ---------------------------------------------------------------------------


class KuralTabanliFiltre(GirisFiltresi):
    """Regex kuralları + biçimsel sezgisel kontroller."""

    ad = "kural_tabanli"

    def __init__(
        self,
        engel_esigi: float = 0.75,
        uyari_esigi: float = 0.40,
        kurallar: Optional[list[Kural]] = None,
        azami_uzunluk: int = 8000,
        kanit_uzunlugu: int = 80,
    ) -> None:
        self.engel_esigi = engel_esigi
        self.uyari_esigi = uyari_esigi
        self.kurallar = list(kurallar if kurallar is not None else KURALLAR)
        self.azami_uzunluk = azami_uzunluk
        self.kanit_uzunlugu = kanit_uzunlugu

    # -- Yardımcılar ---------------------------------------------------------

    def _kanit(self, metin: str, baslangic: int, bitis: int) -> str:
        """Eşleşen parçayı orijinal metinden kısaltarak alır."""
        parca = metin[baslangic:bitis].replace("\n", " ").strip()
        if len(parca) > self.kanit_uzunlugu:
            parca = parca[: self.kanit_uzunlugu - 1] + "…"
        return parca

    def _sezgisel_kontroller(self, metin: str) -> list[Ihlal]:
        """Regex dışı biçimsel sinyaller."""
        ihlaller: list[Ihlal] = []

        gorunmez = GORUNMEZ_KARAKTERLER.findall(metin)
        if gorunmez:
            ihlaller.append(Ihlal(
                kural="gorunmez_karakter",
                kategori="kacinma",
                puan=0.50,
                aciklama="Sıfır genişlikli / yön değiştiren Unicode karakter",
                kanit=f"{len(gorunmez)} adet görünmez karakter",
            ))

        if len(metin) > self.azami_uzunluk:
            ihlaller.append(Ihlal(
                kural="asiri_uzunluk",
                kategori="bicimsel",
                puan=0.20,
                aciklama="İstek olağandışı uzun (bağlam taşırma denemesi olabilir)",
                kanit=f"{len(metin)} karakter",
            ))

        # "----", "====", "####" gibi ayraç yığınları prompt bölme denemesidir.
        ayrac_yigini = re.findall(r"(?:[-=#*_]{10,})", metin)
        if len(ayrac_yigini) >= 2:
            ihlaller.append(Ihlal(
                kural="ayrac_yigini",
                kategori="bicimsel",
                puan=0.20,
                aciklama="Çok sayıda ayraç bloğu (sahte bölüm sınırı)",
                kanit=f"{len(ayrac_yigini)} blok",
            ))

        return ihlaller

    # -- Ana giriş noktası ---------------------------------------------------

    def denetle(self, metin: str, baglam: Optional[dict] = None) -> FiltreSonucu:
        if not metin or not metin.strip():
            return FiltreSonucu(
                karar=Karar.IZIN, puan=0.0, aciklama="boş istek",
                filtre_adi=self.ad,
            )

        normalize_metin = normalize(metin)
        derin_metin = derin_normalize(metin)
        ihlaller: list[Ihlal] = []

        for kural in self.kurallar:
            eslesme = kural.regex.search(normalize_metin)
            if eslesme:
                # indeksler katlama sonrası da aynı olduğu için orijinalden kesiyoruz
                kanit = self._kanit(metin, eslesme.start(), eslesme.end())
            else:
                eslesme = kural.regex.search(derin_metin)
                if not eslesme:
                    continue
                kanit = "[normalize] " + self._kanit(
                    derin_metin, eslesme.start(), eslesme.end()
                )
            ihlaller.append(Ihlal(
                kural=kural.ad,
                kategori=kural.kategori,
                puan=kural.puan,
                aciklama=kural.aciklama,
                kanit=kanit,
            ))

        ihlaller.extend(self._sezgisel_kontroller(metin))

        puan = min(1.0, sum(i.puan for i in ihlaller))
        if puan >= self.engel_esigi:
            karar = Karar.ENGEL
        elif puan >= self.uyari_esigi:
            karar = Karar.UYARI
        else:
            karar = Karar.IZIN

        aciklama = "; ".join(sorted({i.aciklama for i in ihlaller if i.aciklama}))

        return FiltreSonucu(
            karar=karar,
            puan=round(puan, 3),
            ihlaller=ihlaller,
            aciklama=aciklama,
            filtre_adi=self.ad,
        )

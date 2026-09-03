# -*- coding: utf-8 -*-
"""
Giriş filtreleri paketi.

`varsayilan_zincir()` uygulamanın kullandığı filtre zincirini kurar:
    1. KuralTabanliFiltre  (ucuz, her zaman açık)
    2. ModelTabanliFiltre  (pahalı, MODEL_FILTRESI_ACIK=true ise)

Kendi filtrenizi eklemek için `GirisFiltresi` sınıfından türetip zincire
katın; proxy tarafında değişiklik gerekmez.
"""

from config import AYARLAR
from filters.base import (
    FiltreSonucu,
    FiltreZinciri,
    GirisFiltresi,
    Ihlal,
    Karar,
)
from filters.model_based import ModelTabanliFiltre
from filters.rule_based import KURALLAR, Kural, KuralTabanliFiltre

__all__ = [
    "Karar",
    "Ihlal",
    "FiltreSonucu",
    "GirisFiltresi",
    "FiltreZinciri",
    "KuralTabanliFiltre",
    "ModelTabanliFiltre",
    "Kural",
    "KURALLAR",
    "varsayilan_zincir",
]


def varsayilan_zincir() -> FiltreZinciri:
    """Ayarlara göre giriş filtresi zincirini kurar."""
    filtreler: list[GirisFiltresi] = [
        KuralTabanliFiltre(
            engel_esigi=AYARLAR.giris_engel_esigi,
            uyari_esigi=AYARLAR.giris_uyari_esigi,
        )
    ]

    if AYARLAR.model_filtresi_acik:
        filtreler.append(
            ModelTabanliFiltre(
                model_adi=AYARLAR.hf_model_adi,
                esik=AYARLAR.hf_model_esigi,
                acik=True,
            )
        )

    return FiltreZinciri(filtreler, erken_cikis=True)

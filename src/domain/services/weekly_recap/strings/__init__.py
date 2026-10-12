"""Weekly recap copy tables, one per enabled app locale.

Every table has the same keys and placeholders as English, which is also
the fallback for any key a table lacks.
"""

from __future__ import annotations

from src.domain.services.weekly_recap.strings.de_cards import DE_CARDS
from src.domain.services.weekly_recap.strings.de_story import DE_STORY
from src.domain.services.weekly_recap.strings.en_cards import EN_CARDS
from src.domain.services.weekly_recap.strings.en_story import EN_STORY
from src.domain.services.weekly_recap.strings.es_cards import ES_CARDS
from src.domain.services.weekly_recap.strings.es_story import ES_STORY
from src.domain.services.weekly_recap.strings.fr_cards import FR_CARDS
from src.domain.services.weekly_recap.strings.fr_story import FR_STORY
from src.domain.services.weekly_recap.strings.ja_cards import JA_CARDS
from src.domain.services.weekly_recap.strings.ja_story import JA_STORY
from src.domain.services.weekly_recap.strings.vi_cards import VI_CARDS
from src.domain.services.weekly_recap.strings.vi_story import VI_STORY
from src.domain.services.weekly_recap.strings.zh_cards import ZH_CARDS
from src.domain.services.weekly_recap.strings.zh_story import ZH_STORY

TABLES: dict[str, dict[str, str]] = {
    "en": {**EN_STORY, **EN_CARDS},
    "vi": {**VI_STORY, **VI_CARDS},
    "es": {**ES_STORY, **ES_CARDS},
    "fr": {**FR_STORY, **FR_CARDS},
    "de": {**DE_STORY, **DE_CARDS},
    "ja": {**JA_STORY, **JA_CARDS},
    "zh": {**ZH_STORY, **ZH_CARDS},
}
EN = TABLES["en"]

__all__ = ["EN", "TABLES"]

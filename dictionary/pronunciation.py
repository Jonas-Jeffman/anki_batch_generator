from __future__ import annotations

from typing import List, Sequence, Tuple

from dictionary.common import normalize_dictionary_url
from models import DictionaryEntryResult


WORD_IPA_PRIORITY = ("oxford", "cambridge", "longman")
WORD_AUDIO_PRIORITY = ("oxford", "cambridge", "longman")


def select_word_ipa(
    entries: Sequence[DictionaryEntryResult],
) -> Tuple[str, str]:
    by_source = {entry.source: entry for entry in entries}
    for source in WORD_IPA_PRIORITY:
        entry = by_source.get(source)
        value = str(entry.ipa_uk if entry else "").strip()
        if value:
            return value, source
    return "", ""


def ordered_word_audio_urls(
    entries: Sequence[DictionaryEntryResult],
) -> List[Tuple[str, str]]:
    by_source = {entry.source: entry for entry in entries}
    selected: List[Tuple[str, str]] = []
    seen = set()
    for source in WORD_AUDIO_PRIORITY:
        entry = by_source.get(source)
        url = normalize_dictionary_url(entry.audio_uk_url if entry else "")
        if url and url not in seen:
            seen.add(url)
            selected.append((url, source))
    return selected

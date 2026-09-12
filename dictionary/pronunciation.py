from __future__ import annotations

from typing import List, Sequence, Tuple

from dictionary.common import normalize_dictionary_url
from english_terms import normalize_pos_tag
from models import DictionaryEntryResult, ProviderEntry


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


def ordered_headword_audio_urls(
    entries: Sequence[ProviderEntry],
    headword: str,
    requested_pos: str = "",
) -> List[Tuple[str, str]]:
    """Return UK word-audio candidates without guessing between homographs."""
    normalized_headword = " ".join((headword or "").strip().split())
    if not normalized_headword:
        return []
    normalized_pos = normalize_pos_tag(requested_pos)
    selected: List[Tuple[str, str]] = []
    seen_urls = set()

    for source in WORD_AUDIO_PRIORITY:
        candidates: List[Tuple[str, str]] = []
        for entry in entries:
            entry_headword = " ".join((entry.word or "").strip().split())
            if entry.source != source or entry_headword != normalized_headword:
                continue
            url = normalize_dictionary_url(entry.pronunciation.audio_uk_url)
            if not url:
                continue
            entry_pos = normalize_pos_tag(entry.pronunciation.pos or entry.pos)
            candidates.append((url, entry_pos))

        exact_urls = list(dict.fromkeys(
            url for url, entry_pos in candidates if normalized_pos and entry_pos == normalized_pos
        ))
        source_urls = list(dict.fromkeys(url for url, _entry_pos in candidates))
        if exact_urls:
            # A matching POS is safe even when this headword has other pronunciations.
            usable_urls = exact_urls
        elif len(source_urls) == 1:
            # One URL across the headword can be shared with a POS missing from this source.
            usable_urls = source_urls
        else:
            # Multiple URLs without a POS match indicate a homograph/variant ambiguity.
            usable_urls = []

        for url in usable_urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            selected.append((url, source))
    return selected


def headword_audio_selection_scope(
    entries: Sequence[ProviderEntry],
    headword: str,
    requested_pos: str,
    source: str,
    audio_url: str,
) -> str:
    normalized_headword = " ".join((headword or "").strip().split())
    normalized_pos = normalize_pos_tag(requested_pos)
    normalized_url = normalize_dictionary_url(audio_url)
    if not normalized_headword or not normalized_url:
        return ""
    for entry in entries:
        entry_headword = " ".join((entry.word or "").strip().split())
        entry_pos = normalize_pos_tag(entry.pronunciation.pos or entry.pos)
        entry_url = normalize_dictionary_url(entry.pronunciation.audio_uk_url)
        if (
            entry.source == source
            and entry_headword == normalized_headword
            and entry_pos == normalized_pos
            and entry_url == normalized_url
        ):
            return "exact_pos"
    return "headword_shared"

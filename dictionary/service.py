from __future__ import annotations

from typing import Dict, List, Optional

from english_terms import normalize_pos_tag, parse_english_term, strip_pos_labels_from_term
from models import DictionaryEntryResult, DictionarySenseCandidate, EnglishPronunciationInfo
from dictionary.common import _is_candidate_dictionary_image_url, _normalize_url
from dictionary.canonical import IMAGE_SOURCE, select_definition
from dictionary.pronunciation import (
    ordered_word_audio_urls,
    select_word_ipa,
)
from dictionary.cambridge import (
    _cambridge_entry_result,
    fetch_cambridge_sense_candidates,
    fetch_english_from_cambridge,
)
from dictionary.longman import (
    _longman_entry_result,
    fetch_english_from_longman,
    fetch_longman_sense_candidates,
)
from dictionary.oxford import (
    _oxford_entry_result,
    fetch_english_from_oxford,
    fetch_oxford_sense_candidates,
)


def fetch_english_pronunciation(term: str) -> EnglishPronunciationInfo:
    """Select real BrE pronunciation as Oxford → Cambridge → Longman."""
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])

    o = fetch_english_from_oxford(clean_term)
    c = fetch_english_from_cambridge(clean_term)
    l = fetch_english_from_longman(clean_term)

    seen = set()
    audio_list: List[str] = []
    for u in (o.audio_url, c.audio_url, l.audio_url):
        u = (u or "").strip()
        if not u:
            continue
        u = _normalize_url(u)
        if u not in seen:
            seen.add(u)
            audio_list.append(u)
    primary_audio = audio_list[0] if audio_list else ""
    audio_fallbacks = audio_list[1:]

    phonetic = ""
    source = ""
    for info, label in ((o, "oxford"), (c, "cambridge"), (l, "longman")):
        p = (info.phonetic or "").strip()
        if p:
            phonetic = p
            source = label
            break
    if not source:
        for info, label in ((o, "oxford"), (c, "cambridge"), (l, "longman")):
            if (info.phonetic or "").strip() or (info.audio_url or "").strip() or info.pos_tags:
                source = label
                break

    pos_tags: List[str] = []
    for info in (o, c, l):
        if info.pos_tags:
            pos_tags = list(info.pos_tags)
            break

    return EnglishPronunciationInfo(
        phonetic=phonetic,
        audio_url=primary_audio,
        pos_tags=pos_tags,
        source=source,
        audio_fallback_urls=audio_fallbacks,
    )


def fetch_english_pronunciation_two_words(term: str) -> EnglishPronunciationInfo:
    """Verified phrase-level BrE IPA only. No audio, no per-word guesses."""
    clean = strip_pos_labels_from_term(term)
    if len(clean.split()) != 2:
        return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])

    phrase = fetch_english_pronunciation(clean)
    phonetic = (phrase.phonetic or "").strip()
    if not phonetic:
        return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])

    return EnglishPronunciationInfo(
        phonetic=phonetic,
        audio_url="",
        pos_tags=list(phrase.pos_tags),
        source=phrase.source,
    )
def fetch_dictionary_entries(word: str, requested_pos: str = "") -> List[DictionaryEntryResult]:
    entries: List[DictionaryEntryResult] = []
    for fetcher in (
        _cambridge_entry_result,
        _oxford_entry_result,
        _longman_entry_result,
    ):
        entry = fetcher(word, requested_pos)
        if entry:
            entries.append(entry)
    return entries


def merge_dictionary_entries(entries: List[DictionaryEntryResult]) -> DictionaryEntryResult:
    merged = DictionaryEntryResult()
    requested_pos = next((entry.requested_pos for entry in entries if entry.requested_pos), "")
    isolated_entries = [
        entry
        for entry in entries
        if not requested_pos or normalize_pos_tag(entry.actual_pos) == requested_pos
    ]
    for entry in isolated_entries:
        if not merged.word and entry.word:
            merged.word = entry.word
        if not merged.requested_pos and entry.requested_pos:
            merged.requested_pos = entry.requested_pos
        if not merged.actual_pos and entry.actual_pos:
            merged.actual_pos = entry.actual_pos
        if (
            entry.source == IMAGE_SOURCE
            and not merged.image_url
            and entry.image_url
            and _is_candidate_dictionary_image_url(entry.image_url)
        ):
            merged.image_url = entry.image_url
            merged.image_alt = entry.image_alt
            merged.image_source = entry.source

    merged.ipa_uk, merged.ipa_source = select_word_ipa(isolated_entries)
    audio_urls = ordered_word_audio_urls(isolated_entries)
    if audio_urls:
        merged.audio_uk_url, merged.audio_source = audio_urls[0]
    (
        merged.definition,
        merged.definition_source,
        merged.sense_id,
    ) = select_definition(isolated_entries)

    merged.source = merged.definition_source or merged.ipa_source or merged.audio_source or merged.image_source
    return merged


def dictionary_result_preview(raw_term: str) -> Dict:
    parsed = parse_english_term(raw_term)
    entries = fetch_dictionary_entries(parsed.word, parsed.requested_pos)
    merged = merge_dictionary_entries(entries)
    return {
        "raw": parsed.raw,
        "word": parsed.word,
        "requested_pos": parsed.requested_pos,
        "actual_pos": merged.actual_pos,
        "definition": merged.definition,
        "definition_source": merged.definition_source,
        "ipa_uk": merged.ipa_uk,
        "ipa_source": merged.ipa_source,
        "audio_uk_url": merged.audio_uk_url,
        "audio_source": merged.audio_source,
        "image_url": merged.image_url,
        "image_source": merged.image_source,
        "sense_id": merged.sense_id,
        "provider_entries": [
            {
                "source": entry.source,
                "actual_pos": entry.actual_pos,
                "definition": entry.definition,
                "ipa_uk": entry.ipa_uk,
                "audio_uk_url": entry.audio_uk_url,
                "image_url": entry.image_url,
                "sense_id": entry.sense_id,
            }
            for entry in entries
        ],
    }
def fetch_best_dictionary_sense(term: str) -> Optional[DictionarySenseCandidate]:
    for fetcher in (
        fetch_cambridge_sense_candidates,
        fetch_longman_sense_candidates,
        fetch_oxford_sense_candidates,
    ):
        for candidate in fetcher(term):
            if (candidate.definition or "").strip():
                return candidate
    return None

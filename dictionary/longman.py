from __future__ import annotations

import html
import re
from typing import List, Optional
from urllib.parse import quote, urljoin

import requests

from english_terms import normalize_pos_tag, strip_pos_labels_from_term
from models import (
    DictionaryEntryResult,
    DictionaryExample,
    DictionarySenseCandidate,
    EnglishPronunciationInfo,
    ProviderEntry,
    ProviderSense,
    WordPronunciation,
)
from utils import retry_call
from dictionary.dom import parse_html_document
from dictionary.common import (
    _all_match_texts,
    _choose_image_url,
    _clean_dictionary_text,
    _definition_is_usable,
    _entry_matches_requested_pos,
    _extract_first_img_alt,
    _extract_image_urls_from_html,
    _finalize_entry_sources,
    _find_nearest_pos_before,
    _first_dictionary_image_in_block,
    _first_ipa,
    _first_match_text,
    _first_uk_audio_url,
    _headword_matches_term,
    _normalize_url,
    _strip_tags,
)


def fetch_english_from_longman(term: str) -> EnglishPronunciationInfo:
    normalized = term.strip().replace(" ", "-")
    if not normalized:
        return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
    url = f"https://www.ldoceonline.com/dictionary/{quote(normalized)}"

    def _do_request() -> EnglishPronunciationInfo:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok:
            return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
        if "/spellcheck/" in (resp.url or "").lower():
            return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
        body = resp.text
        headword_match = re.search(r"<h1[^>]*>([^<]+)</h1>", body, flags=re.IGNORECASE)
        headword = _strip_tags(html.unescape(headword_match.group(1))) if headword_match else ""
        if not headword or not _headword_matches_term(headword, term):
            return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
        pos_tags = list(dict.fromkeys(normalize_pos_tag(p) for p in re.findall(r'class="POS"[^>]*>([^<]+)<', body, flags=re.IGNORECASE)))
        pos_tags = [p for p in pos_tags if p]

        ipa = ""
        # Prefer BrE pronunciation block when available.
        uk_pron = re.search(r'class="[^"]*\bPRON\b[^"]*"[^>]*>([^<]+)<', body, flags=re.IGNORECASE)
        if uk_pron:
            ipa = _strip_tags(html.unescape(uk_pron.group(1))).replace(" ", "")

        audio = ""
        for candidate in re.findall(r'data-src-mp3="([^"]+)"', body, flags=re.IGNORECASE):
            low = candidate.lower()
            if "breprons" in low or "_gb_" in low or "/gb/" in low:
                audio = _normalize_url(html.unescape(candidate))
                break
        return EnglishPronunciationInfo(
            phonetic=ipa, audio_url=audio, pos_tags=pos_tags, source="longman"
        )

    try:
        return retry_call(_do_request, retries=2, base_sleep=1.0)
    except Exception:
        return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
def _longman_entry_blocks(body: str) -> List[str]:
    starts = [
        match.start()
        for match in re.finditer(
            r'<span[^>]*class="[^"]*\bldoceEntry\b[^"]*\bEntry\b[^"]*"[^>]*>',
            body or "",
            flags=re.IGNORECASE,
        )
    ]
    if not starts:
        return [body or ""]
    blocks: List[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        blocks.append(body[start:end])
    return blocks


def _longman_entry_result(word: str, requested_pos: str = "") -> Optional[DictionaryEntryResult]:
    normalized = word.strip().replace(" ", "-")
    if not normalized:
        return None
    page_url = f"https://www.ldoceonline.com/dictionary/{quote(normalized)}"

    def _do_request() -> Optional[DictionaryEntryResult]:
        resp = requests.get(
            page_url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok or "/spellcheck/" in (resp.url or "").lower():
            return None
        for index, block in enumerate(_longman_entry_blocks(resp.text), start=1):
            headword = _first_match_text(
                block,
                [
                    r'<span[^>]*class="[^"]*\bHWD\b[^"]*"[^>]*>(.*?)</span>',
                    r"<h1[^>]*>(.*?)</h1>",
                ],
            )
            if headword and not _headword_matches_term(headword, word):
                continue
            actual_pos = _first_match_text(
                block,
                [r'<span[^>]*class="[^"]*\bPOS\b[^"]*"[^>]*>(.*?)</span>'],
            )
            actual_pos = normalize_pos_tag(actual_pos)
            if not _entry_matches_requested_pos(actual_pos, requested_pos):
                continue
            definition = _first_match_text(
                block,
                [r'<span[^>]*class="[^"]*\bDEF\b[^"]*"[^>]*>(.*?)</span>'],
            )
            if definition and not _definition_is_usable(definition, requested_pos):
                continue
            entry = DictionaryEntryResult(
                source="longman",
                word=headword or word,
                requested_pos=requested_pos,
                actual_pos=actual_pos,
                ipa_uk=_first_ipa(
                    block,
                    [r'<span[^>]*class="[^"]*\bPRON\b[^"]*"[^>]*>(.*?)</span>'],
                ),
                audio_uk_url=_first_uk_audio_url(
                    block,
                    "https://www.ldoceonline.com",
                    ["breprons", "/gb/", "_gb_"],
                ),
                definition=definition,
                image_url=_first_dictionary_image_in_block(
                    block,
                    resp.url,
                    ["/media/english/illustration/"],
                ),
                image_alt=_extract_first_img_alt(block),
                sense_id=f"longman:{normalized}:{actual_pos or 'entry'}:{index}",
            )
            return _finalize_entry_sources(entry)
        return None

    try:
        return retry_call(_do_request, retries=2, base_sleep=1.0)
    except Exception:
        return None
def fetch_longman_sense_candidates(term: str) -> List[DictionarySenseCandidate]:
    return [
        DictionarySenseCandidate(
            source=sense.source,
            word=entry.word,
            pos=sense.pos,
            sense_id=sense.native_id,
            definition=sense.definition,
            examples=[example.text for example in sense.examples],
        )
        for entry in fetch_longman_provider_entries(term)
        for sense in entry.senses
    ]


def parse_longman_provider_entries(
    body: str,
    page_url: str,
    term: str,
) -> List[ProviderEntry]:
    """Parse LDOCE entries with example audio bound inside its example node."""
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return []
    document = parse_html_document(body)
    entry_nodes = [
        node
        for node in document.descendants("span")
        if "ldoceEntry" in node.classes or "bussdictEntry" in node.classes
    ]
    entries: List[ProviderEntry] = []
    for entry_index, entry_node in enumerate(entry_nodes, start=1):
        dataset = "business" if "bussdictEntry" in entry_node.classes else "ldoce"
        headword_node = entry_node.find_first("span", "HWD")
        headword = _clean_dictionary_text(headword_node.text_content()) if headword_node else ""
        if not headword or not _headword_matches_term(headword, clean_term):
            continue
        pos_node = entry_node.find_first("span", "POS")
        pos = normalize_pos_tag(pos_node.text_content() if pos_node else "")
        native_id = entry_node.attrs.get("id", "")
        entry_class = "bussdictEntry" if dataset == "business" else "ldoceEntry"
        entry_path = (
            f'span.{entry_class}[id="{native_id}"]'
            if native_id
            else f"span.{entry_class}:nth-of-type({entry_index})"
        )
        pronunciation_audio = ""
        for audio_node in entry_node.descendants():
            raw_audio = audio_node.attrs.get("data-src-mp3", "")
            if raw_audio and any(
                marker in raw_audio.lower() for marker in ("breprons", "/gb/", "_gb_")
            ):
                pronunciation_audio = urljoin(page_url, raw_audio)
                break
        pronunciation_node = entry_node.find_first("span", "PRON")
        pronunciation_ipa = (
            _clean_dictionary_text(pronunciation_node.text_content()).replace(" ", "")
            if pronunciation_node
            else ""
        )
        pronunciation = WordPronunciation(
            source="longman",
            pos=pos,
            ipa_uk=pronunciation_ipa,
            audio_uk_url=pronunciation_audio,
            dom_path=(
                f"{entry_path} .PRON"
                if pronunciation_audio or pronunciation_ipa
                else ""
            ),
        )
        sense_nodes = [
            node
            for node in entry_node.descendants("span")
            if "Sense" in node.classes and node.attrs.get("id")
        ]
        senses: List[ProviderSense] = []
        cross_reference_ids: List[str] = []
        other_sense_ids: List[str] = []
        for sense_node in sense_nodes:
            native_sense_id = sense_node.attrs["id"]
            definition_node = sense_node.find_first("span", "DEF")
            definition = (
                _clean_dictionary_text(definition_node.text_content())
                if definition_node
                else ""
            )
            if not definition:
                if sense_node.find_first("span", "Crossref"):
                    cross_reference_ids.append(native_sense_id)
                else:
                    other_sense_ids.append(native_sense_id)
                continue
            sense_path = f'{entry_path} > span.Sense[id="{native_sense_id}"]'
            examples: List[DictionaryExample] = []
            example_nodes = [
                node
                for node in sense_node.descendants("span")
                if "EXAMPLE" in node.classes
            ]
            for example_index, example_node in enumerate(example_nodes, start=1):
                example_audio = ""
                for audio_node in example_node.descendants():
                    raw_audio = audio_node.attrs.get("data-src-mp3", "")
                    if "/exaProns/" in raw_audio:
                        example_audio = urljoin(page_url, raw_audio)
                        break
                examples.append(
                    DictionaryExample(
                        text=_clean_dictionary_text(example_node.text_content()),
                        audio_url=example_audio,
                        dom_path=f"{sense_path} .EXAMPLE:nth-of-type({example_index})",
                    )
                )
            senses.append(
                ProviderSense(
                    source="longman",
                    native_id=native_sense_id,
                    pos=pos,
                    definition=definition,
                    examples=examples,
                    dom_path=sense_path,
                    definition_dom_path=f"{sense_path} > .DEF",
                )
            )
        entries.append(
            ProviderEntry(
                source="longman",
                dataset=dataset,
                native_id=native_id,
                word=headword,
                pos=pos,
                pronunciation=pronunciation,
                senses=senses,
                cross_reference_ids=cross_reference_ids,
                other_sense_ids=other_sense_ids,
                dom_path=entry_path,
            )
        )
    claimed_sense_ids = {
        sense.native_id for entry in entries for sense in entry.senses
    } | {
        sense_id
        for entry in entries
        for sense_id in entry.cross_reference_ids + entry.other_sense_ids
    }
    orphan_sense_ids = [
        node.attrs["id"]
        for node in document.descendants("span")
        if "Sense" in node.classes
        and node.attrs.get("id")
        and node.attrs["id"] not in claimed_sense_ids
    ]
    if entries:
        entries[-1].other_sense_ids.extend(orphan_sense_ids)
    return entries


def fetch_longman_provider_entries(term: str) -> List[ProviderEntry]:
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return []
    normalized = clean_term.strip().replace(" ", "-")
    url = f"https://www.ldoceonline.com/dictionary/{quote(normalized)}"

    def _do_request() -> List[ProviderEntry]:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok or "/spellcheck/" in (resp.url or "").lower():
            return []
        return parse_longman_provider_entries(resp.text, resp.url, clean_term)

    try:
        return retry_call(_do_request, retries=2, base_sleep=1.0)
    except Exception:
        return []
def fetch_longman_image_url(term: str) -> str:
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return ""
    normalized = clean_term.strip().replace(" ", "-")
    url = f"https://www.ldoceonline.com/dictionary/{quote(normalized)}"

    def _do_request() -> str:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok or "/spellcheck/" in (resp.url or "").lower():
            return ""
        body = resp.text
        headword_match = re.search(r"<h1[^>]*>([^<]+)</h1>", body, flags=re.IGNORECASE)
        headword = _strip_tags(html.unescape(headword_match.group(1))) if headword_match else ""
        if not headword or not _headword_matches_term(headword, clean_term):
            return ""
        candidates = _extract_image_urls_from_html(body, resp.url)
        return _choose_image_url(candidates, ["/media/english/illustration/", "/illustration/"])

    try:
        return str(retry_call(_do_request, retries=2, base_sleep=1.0) or "").strip()
    except Exception:
        return ""

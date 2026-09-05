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


def _oxford_page_is_misspelling(body: str) -> bool:
    title = re.search(r"<title>([^<]+)</title>", body, flags=re.IGNORECASE)
    return bool(title and "did you spell" in title.group(1).lower())


def _extract_oxford_headword(body: str) -> str:
    match = re.search(
        r'<h1[^>]*\bclass="[^"]*\bheadword\b[^"]*"[^>]*>(.*?)</h1>',
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return _strip_tags(html.unescape(match.group(1))) if match else ""


def fetch_english_from_oxford(term: str) -> EnglishPronunciationInfo:
    """Oxford Advanced Learner's: UK/BrE block (phons_br, uk_pron / __gb_)."""
    normalized = term.strip().replace(" ", "-")
    if not normalized:
        return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
    url = f"https://www.oxfordlearnersdictionaries.com/definition/english/{quote(normalized)}"

    def _do_request() -> EnglishPronunciationInfo:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok:
            return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
        body = resp.text
        if _oxford_page_is_misspelling(body):
            return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
        headword = _extract_oxford_headword(body)
        if headword and not _headword_matches_term(headword, term):
            return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
        pos_tags = list(
            dict.fromkeys(
                normalize_pos_tag(p)
                for p in re.findall(
                    r'<span[^>]*\bclass="pos"[^>]*>([^<]+)</span>',
                    body,
                    flags=re.IGNORECASE,
                )
            )
        )
        pos_tags = [p for p in pos_tags if p]

        ipa = ""
        audio = ""
        br = re.search(
            r'<div[^>]*\bphons_br\b[^>]*>.*?data-src-mp3="([^"]+)".*?<span[^>]*\bclass="phon"[^>]*>([^<]+)</span>',
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if br:
            audio = _normalize_url(html.unescape(br.group(1)))
            ipa = _strip_tags(html.unescape(br.group(2))).replace(" ", "")
        if not audio:
            for candidate in re.findall(r'data-src-mp3="([^"]+)"', body, flags=re.IGNORECASE):
                low = candidate.lower()
                if "uk_pron" in low and ("__gb_" in candidate or "_gb_" in low):
                    audio = _normalize_url(html.unescape(candidate))
                    break
        if not ipa:
            phon_br = re.search(
                r'<div[^>]*\bphons_br\b[^>]*>.*?<span[^>]*\bclass="phon"[^>]*>([^<]+)</span>',
                body,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if phon_br:
                ipa = _strip_tags(html.unescape(phon_br.group(1))).replace(" ", "")

        return EnglishPronunciationInfo(
            phonetic=ipa, audio_url=audio, pos_tags=pos_tags, source="oxford"
        )

    try:
        return retry_call(_do_request, retries=2, base_sleep=1.0)
    except Exception:
        return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
def _oxford_entry_result(word: str, requested_pos: str = "") -> Optional[DictionaryEntryResult]:
    normalized = word.strip().replace(" ", "-")
    if not normalized:
        return None
    slugs = [normalized] + [f"{normalized}_{i}" for i in range(1, 5)]

    def _fetch_one(slug: str, index: int) -> Optional[DictionaryEntryResult]:
        page_url = f"https://www.oxfordlearnersdictionaries.com/definition/english/{quote(slug)}"
        resp = requests.get(
            page_url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok:
            return None
        body = resp.text
        if _oxford_page_is_misspelling(body):
            return None
        headword = _extract_oxford_headword(body)
        if headword and not _headword_matches_term(headword, word):
            return None
        actual_pos = _first_match_text(
            body,
            [r'<span[^>]*class="[^"]*\bpos\b[^"]*"[^>]*>(.*?)</span>'],
        )
        actual_pos = normalize_pos_tag(actual_pos)
        if not _entry_matches_requested_pos(actual_pos, requested_pos):
            return None
        definition = _first_match_text(
            body,
            [r'<span[^>]*class="[^"]*\bdef\b[^"]*"[^>]*>(.*?)</span>'],
        )
        if definition and not _definition_is_usable(definition, requested_pos):
            return None
        entry = DictionaryEntryResult(
            source="oxford",
            word=headword or word,
            requested_pos=requested_pos,
            actual_pos=actual_pos,
            ipa_uk=_first_ipa(
                body,
                [r'<div[^>]*class="[^"]*\bphons_br\b[^"]*"[^>]*>.*?<span[^>]*class="[^"]*\bphon\b[^"]*"[^>]*>(.*?)</span>'],
            ),
            audio_uk_url=_first_uk_audio_url(
                body,
                "https://www.oxfordlearnersdictionaries.com",
                ["/media/english/uk_pron/", "uk_pron", "__gb_", "_gb_"],
            ),
            definition=definition,
            image_url=_first_dictionary_image_in_block(
                body,
                resp.url,
                ["/media/english/fullsize/", "/media/english/thumb/"],
            ),
            image_alt=_extract_first_img_alt(body),
            sense_id=f"oxford:{slug}:{actual_pos or 'entry'}:{index}",
        )
        return _finalize_entry_sources(entry)

    try:
        for index, slug in enumerate(slugs):
            entry = retry_call(lambda slug=slug, index=index: _fetch_one(slug, index), retries=2, base_sleep=1.0)
            if entry:
                return entry
    except Exception:
        return None
    return None
def fetch_oxford_sense_candidates(term: str) -> List[DictionarySenseCandidate]:
    return [
        DictionarySenseCandidate(
            source=sense.source,
            word=entry.word,
            pos=sense.pos,
            sense_id=sense.native_id,
            definition=sense.definition,
            examples=[example.text for example in sense.examples],
        )
        for entry in fetch_oxford_provider_entries(term)
        for sense in entry.senses
    ]


def parse_oxford_provider_entries(
    body: str,
    page_url: str,
    term: str,
) -> List[ProviderEntry]:
    """Parse one OALD lexical entry and keep idioms outside lexical senses."""
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term or _oxford_page_is_misspelling(body):
        return []
    document = parse_html_document(body)
    entry_node = next(
        (
            node
            for node in document.descendants("div")
            if "entry" in node.classes and node.find_first("h1", "headword")
        ),
        None,
    )
    if not entry_node:
        return []
    headword_node = entry_node.find_first("h1", "headword")
    headword = _clean_dictionary_text(headword_node.text_content()) if headword_node else ""
    if headword and not _headword_matches_term(headword, clean_term):
        return []
    pos_node = entry_node.find_first("span", "pos")
    pos = normalize_pos_tag(pos_node.text_content() if pos_node else "")
    entry_native_id = entry_node.attrs.get("id", "")
    entry_path = (
        f'div.entry[id="{entry_native_id}"]' if entry_native_id else "div.entry"
    )
    br_node = entry_node.find_first("div", "phons_br")
    ipa_node = br_node.find_first("span", "phon") if br_node else None
    audio_node = br_node.find_first("div", "sound") if br_node else None
    audio_url = audio_node.attrs.get("data-src-mp3", "") if audio_node else ""
    pronunciation = WordPronunciation(
        source="oxford",
        pos=pos,
        ipa_uk=_clean_dictionary_text(ipa_node.text_content()).replace(" ", "") if ipa_node else "",
        audio_uk_url=urljoin(page_url, audio_url) if audio_url else "",
        dom_path=f"{entry_path} .phons_br" if br_node else "",
    )
    sense_nodes = [
        node
        for node in entry_node.descendants("li")
        if "sense" in node.classes
        and node.attrs.get("id")
        and not node.has_ancestor_class("idioms")
    ]
    senses: List[ProviderSense] = []
    for sense_node in sense_nodes:
        native_sense_id = sense_node.attrs["id"]
        definition_node = sense_node.find_first("span", "def")
        definition = (
            _clean_dictionary_text(definition_node.text_content())
            if definition_node
            else ""
        )
        if not definition:
            continue
        sense_path = f'{entry_path} li.sense[id="{native_sense_id}"]'
        example_nodes = [
            node for node in sense_node.descendants("span") if "x" in node.classes
        ]
        senses.append(
            ProviderSense(
                source="oxford",
                native_id=native_sense_id,
                pos=pos,
                definition=definition,
                examples=[
                    DictionaryExample(
                        text=_clean_dictionary_text(example_node.text_content()),
                        dom_path=f"{sense_path} .x:nth-of-type({index})",
                    )
                    for index, example_node in enumerate(example_nodes, start=1)
                ],
                dom_path=sense_path,
                definition_dom_path=f"{sense_path} > .def",
            )
        )
    idiom_ids = [
        node.attrs["id"]
        for node in entry_node.descendants("span")
        if node.classes == {"idm"} and node.attrs.get("id")
    ]
    return [
        ProviderEntry(
            source="oxford",
            dataset="oald",
            native_id=entry_native_id,
            word=headword or clean_term,
            pos=pos,
            pronunciation=pronunciation,
            senses=senses,
            idiom_ids=idiom_ids,
            dom_path=entry_path,
        )
    ]


def fetch_oxford_provider_entries(term: str) -> List[ProviderEntry]:
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return []
    normalized = clean_term.strip().replace(" ", "-")
    url = f"https://www.oxfordlearnersdictionaries.com/definition/english/{quote(normalized)}"

    def _do_request() -> List[ProviderEntry]:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok or _oxford_page_is_misspelling(resp.text):
            return []
        headword = _extract_oxford_headword(resp.text)
        if headword and not _headword_matches_term(headword, clean_term):
            return []
        return parse_oxford_provider_entries(resp.text, resp.url, clean_term)

    try:
        return retry_call(_do_request, retries=2, base_sleep=1.0)
    except Exception:
        return []
def fetch_oxford_image_url(term: str) -> str:
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return ""
    normalized = clean_term.strip().replace(" ", "-")
    url = f"https://www.oxfordlearnersdictionaries.com/definition/english/{quote(normalized)}"

    def _do_request() -> str:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok:
            return ""
        body = resp.text
        if _oxford_page_is_misspelling(body):
            return ""
        headword = _extract_oxford_headword(body)
        if headword and not _headword_matches_term(headword, clean_term):
            return ""
        candidates = _extract_image_urls_from_html(body, resp.url)
        return _choose_image_url(candidates, ["/media/english/fullsize/", "/fullsize/", "/media/english/"])

    try:
        return str(retry_call(_do_request, retries=2, base_sleep=1.0) or "").strip()
    except Exception:
        return ""

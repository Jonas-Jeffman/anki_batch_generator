from __future__ import annotations

import html
import re
from typing import List, Optional
from urllib.parse import quote, unquote, urljoin, urlparse

import requests

from anki_generator.inputs.english import normalize_pos_tag, strip_pos_labels_from_term
from anki_generator.models import (
    DictionaryEntryResult,
    DictionaryExample,
    DictionarySenseCandidate,
    EnglishPronunciationInfo,
    ProviderEntry,
    ProviderSense,
    WordPronunciation,
)
from anki_generator.utils import retry_call
from anki_generator.dictionary.dom import Element, parse_html_document
from anki_generator.dictionary.common import (
    _all_match_texts,
    _clean_dictionary_text,
    _extract_first_img_alt,
    _extract_image_urls_from_html,
    _finalize_entry_sources,
    _find_nearest_pos_before,
    _first_dictionary_image_in_block,
    _first_ipa,
    _first_match_text,
    _first_uk_audio_url,
    _headword_matches_term,
    _is_candidate_dictionary_image_url,
    _choose_image_url,
    _normalize_url,
    _strip_tags,
    _entry_matches_requested_pos,
    _definition_is_usable,
    _dictionary_slug,
)


def _cambridge_final_url_matches_term(final_url: str, term: str) -> bool:
    slug = _dictionary_slug(term)
    path = unquote(urlparse(final_url).path.rstrip("/")).lower()
    return path.endswith(f"/english/{slug}")


def _extract_cambridge_entry_scope(body: str, term: str) -> str:
    clean = strip_pos_labels_from_term(term)
    if not clean:
        return ""

    for block_match in re.finditer(
        r'<div class="idiom-block">(.*?)(?=</div>\s*<div class="idiom-block"|</div>\s*<div class="plus-other-dict")',
        body,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        block = block_match.group(1)
        hw_match = re.search(
            r'<h2[^>]*class="[^"]*\b(?:headword|dhw)\b[^"]*"[^>]*>(.*?)</h2>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if hw_match and _headword_matches_term(
            _strip_tags(html.unescape(hw_match.group(1))), clean
        ):
            return block

    for header_match in re.finditer(
        r'<div class="[^"]*\bpos-header\b[^"]*"[^>]*>',
        body,
        flags=re.IGNORECASE,
    ):
        start = header_match.start()
        next_header = re.search(
            r'<div class="[^"]*\bpos-header\b[^"]*"[^>]*>',
            body[start + 20:],
            flags=re.IGNORECASE,
        )
        end = start + 20 + next_header.start() if next_header else start + 12000
        block = body[start:end]
        hw_match = re.search(
            r'<span class="hw dhw[^"]*">(.*?)</span>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if hw_match and _headword_matches_term(
            _strip_tags(html.unescape(hw_match.group(1))), clean
        ):
            return block

    return ""
def fetch_english_from_cambridge(term: str) -> EnglishPronunciationInfo:
    normalized = term.strip().replace(" ", "-")
    if not normalized:
        return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
    url = f"https://dictionary.cambridge.org/dictionary/english/{quote(normalized)}"

    def _do_request() -> EnglishPronunciationInfo:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok:
            return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
        if not _cambridge_final_url_matches_term(resp.url, term):
            return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
        body = resp.text
        entry_scope = _extract_cambridge_entry_scope(body, term)
        if not entry_scope:
            return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
        pos_tags = list(dict.fromkeys(normalize_pos_tag(p) for p in re.findall(r'class="pos dpos"[^>]*>([^<]+)<', entry_scope, flags=re.IGNORECASE)))
        pos_tags = [p for p in pos_tags if p]

        ipa = ""
        uk_block = re.search(
            r'(<span[^>]*class="[^"]*\buk dpron-i\b[^"]*"[^>]*>.*?</span>)',
            entry_scope,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if uk_block:
            ipa_match = re.search(
                r'class="[^"]*\bipa\b[^"]*"[^>]*>(.*?)<',
                uk_block.group(1),
                flags=re.IGNORECASE | re.DOTALL,
            )
            if ipa_match:
                ipa = _strip_tags(html.unescape(ipa_match.group(1))).replace(" ", "")

        audio = ""
        if uk_block:
            audio_match = re.search(r'(?:data-src-mp3|src)="([^"]+)"', uk_block.group(1), flags=re.IGNORECASE)
            if audio_match:
                audio = _normalize_url(html.unescape(audio_match.group(1)))
        if not audio:
            for candidate in re.findall(r'(?:data-src-mp3|src)="([^"]+)"', entry_scope, flags=re.IGNORECASE):
                low = candidate.lower()
                if "/uk_" in low or "_uk_" in low or "uk_pron" in low:
                    audio = _normalize_url(html.unescape(candidate))
                    break
        return EnglishPronunciationInfo(
            phonetic=ipa, audio_url=audio, pos_tags=pos_tags, source="cambridge"
        )

    try:
        return retry_call(_do_request, retries=2, base_sleep=1.0)
    except Exception:
        return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])
def _cambridge_pos_blocks(body: str, word: str) -> List[str]:
    starts = [
        match.start()
        for match in re.finditer(
            r'<div[^>]*class="[^"]*\bpos-header\b[^"]*"[^>]*>',
            body or "",
            flags=re.IGNORECASE,
        )
    ]
    blocks: List[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else min(len(body), start + 30000)
        block = body[start:end]
        hw_match = re.search(
            r'<span class="hw dhw[^"]*">(.*?)</span>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if hw_match and _headword_matches_term(_clean_dictionary_text(hw_match.group(1)), word):
            blocks.append(block)
    return blocks


def _cambridge_entry_result(word: str, requested_pos: str = "") -> Optional[DictionaryEntryResult]:
    normalized = word.strip().replace(" ", "-")
    if not normalized:
        return None
    page_url = f"https://dictionary.cambridge.org/dictionary/english/{quote(normalized)}"

    def _do_request() -> Optional[DictionaryEntryResult]:
        resp = requests.get(
            page_url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok or not _cambridge_final_url_matches_term(resp.url, word):
            return None
        for index, block in enumerate(_cambridge_pos_blocks(resp.text, word), start=1):
            actual_pos = _first_match_text(
                block,
                [r'<span[^>]*class="[^"]*\bpos\b[^"]*\bdpos\b[^"]*"[^>]*>(.*?)</span>'],
            )
            actual_pos = normalize_pos_tag(actual_pos)
            if not _entry_matches_requested_pos(actual_pos, requested_pos):
                continue
            definition = _first_match_text(
                block,
                [r'<div[^>]*class="[^"]*\bdef\b[^"]*\bddef_d\b[^"]*"[^>]*>(.*?)</div>'],
            )
            if definition and not _definition_is_usable(definition, requested_pos):
                continue
            uk_block_match = re.search(
                r'(<span[^>]*class="[^"]*\buk dpron-i\b[^"]*"[^>]*>.*?</span>)',
                block,
                flags=re.IGNORECASE | re.DOTALL,
            )
            audio_block = uk_block_match.group(1) if uk_block_match else block
            entry = DictionaryEntryResult(
                source="cambridge",
                word=word,
                requested_pos=requested_pos,
                actual_pos=actual_pos,
                ipa_uk=_first_ipa(
                    audio_block,
                    [r'<span[^>]*class="[^"]*\bipa\b[^"]*"[^>]*>(.*?)</span>'],
                ),
                audio_uk_url=_first_uk_audio_url(
                    audio_block,
                    "https://dictionary.cambridge.org",
                    ["/media/english/uk_pron/", "/uk_pron/", "/uk_"],
                ),
                definition=definition,
                image_url=_first_dictionary_image_in_block(
                    block,
                    resp.url,
                    ["/images/full/", "/images/thumb/"],
                ),
                image_alt=_extract_first_img_alt(block),
                sense_id=f"cambridge:{normalized}:{actual_pos or 'entry'}:{index}",
            )
            return _finalize_entry_sources(entry)
        return None

    try:
        return retry_call(_do_request, retries=2, base_sleep=1.0)
    except Exception:
        return None
def fetch_cambridge_sense_candidates(term: str) -> List[DictionarySenseCandidate]:
    return [
        DictionarySenseCandidate(
            source=sense.source,
            word=entry.word,
            pos=sense.pos,
            sense_id=sense.native_id,
            definition=sense.definition,
            image_url=sense.image_url,
            image_alt=sense.image_alt,
            examples=[example.text for example in sense.examples],
        )
        for entry in fetch_cambridge_provider_entries(term)
        for sense in entry.senses
    ]


def parse_cambridge_provider_entries(
    body: str,
    page_url: str,
    term: str,
) -> List[ProviderEntry]:
    """Parse structured CALD entries while retaining native sense boundaries."""
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return []
    document = parse_html_document(body)
    dataset = next(
        (
            node
            for node in document.descendants("div")
            if node.has_classes("dictionary") and node.attrs.get("data-id") == "cald4"
        ),
        None,
    )
    if not dataset:
        return []
    entry_nodes = [
        node for node in dataset.descendants("div") if node.has_classes("entry-body__el")
    ]
    entries: List[ProviderEntry] = []
    for entry_index, entry_node in enumerate(entry_nodes, start=1):
        headword_node = entry_node.find_first("span", "hw", "dhw")
        headword = _clean_dictionary_text(headword_node.text_content()) if headword_node else ""
        if headword and not _headword_matches_term(headword, clean_term):
            continue
        pos_node = entry_node.find_first("span", "pos", "dpos")
        pos = normalize_pos_tag(pos_node.text_content() if pos_node else "")
        entry_path = (
            'div.dictionary[data-id="cald4"] '
            f'> div.entry-body__el:nth-of-type({entry_index})'
        )
        uk_node = entry_node.find_first("span", "uk", "dpron-i")
        ipa_node = uk_node.find_first("span", "ipa") if uk_node else None
        audio_url = ""
        if uk_node:
            for source_node in uk_node.descendants("source"):
                candidate = source_node.attrs.get("src", "")
                if "/uk_pron/" in candidate and candidate.lower().split("?", 1)[0].endswith(".mp3"):
                    audio_url = urljoin("https://dictionary.cambridge.org", candidate)
                    break
        pronunciation = WordPronunciation(
            source="cambridge",
            pos=pos,
            ipa_uk=_clean_dictionary_text(ipa_node.text_content()).replace(" ", "") if ipa_node else "",
            audio_uk_url=audio_url,
            dom_path=f"{entry_path} .uk.dpron-i" if uk_node else "",
        )
        sense_nodes = [
            node
            for node in entry_node.descendants("div")
            if node.has_classes("def-block") and node.attrs.get("data-wl-senseid")
        ]
        senses: List[ProviderSense] = []
        for sense_node in sense_nodes:
            native_id = sense_node.attrs["data-wl-senseid"]
            definition_node = sense_node.find_first("div", "def", "ddef_d")
            definition = (
                _clean_dictionary_text(definition_node.text_content())
                if definition_node
                else ""
            )
            if not definition:
                continue
            sense_path = f'{entry_path} > div.def-block[data-wl-senseid="{native_id}"]'
            region: List[Element] = [sense_node]
            for sibling in sense_node.following_element_siblings():
                if sibling.has_classes("def-block"):
                    break
                region.append(sibling)
            example_texts: List[str] = []
            image_node = None
            for region_node in region:
                example_nodes = [
                    node
                    for node in region_node.descendants()
                    if node.tag in {"div", "span"} and node.has_classes("eg", "deg")
                ]
                for example_node in example_nodes:
                    text = _clean_dictionary_text(example_node.text_content())
                    if text and text not in example_texts:
                        example_texts.append(text)
                candidate_image = (
                    region_node if region_node.has_classes("dimg") else region_node.find_first("div", "dimg")
                )
                if candidate_image and image_node is None:
                    image_node = candidate_image.find_first("amp-img")
            image_url = ""
            image_alt = ""
            if image_node:
                full_match = re.search(r"src:\s*'([^']+)'", image_node.attrs.get("on", ""))
                image_url = urljoin(page_url, full_match.group(1) if full_match else image_node.attrs.get("src", ""))
                image_alt = image_node.attrs.get("alt", "").strip()
            senses.append(
                ProviderSense(
                    source="cambridge",
                    native_id=native_id,
                    pos=pos,
                    definition=definition,
                    examples=[
                        DictionaryExample(
                            text=text,
                            dom_path=f"{sense_path} .eg.deg:nth-of-type({index})",
                        )
                        for index, text in enumerate(example_texts, start=1)
                    ],
                    image_url=image_url,
                    image_alt=image_alt,
                    dom_path=sense_path,
                    definition_dom_path=f"{sense_path} .def.ddef_d",
                    image_dom_path=(
                        f"{sense_path} + div.dimg amp-img" if image_url else ""
                    ),
                )
            )
        entries.append(
            ProviderEntry(
                source="cambridge",
                dataset="cald4",
                native_id="",
                word=headword or clean_term,
                pos=pos,
                pronunciation=pronunciation,
                senses=senses,
                dom_path=entry_path,
            )
        )
    return entries


def fetch_cambridge_provider_entries(term: str) -> List[ProviderEntry]:
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return []
    normalized = clean_term.strip().replace(" ", "-")
    url = f"https://dictionary.cambridge.org/dictionary/english/{quote(normalized)}"

    def _do_request() -> List[ProviderEntry]:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok or not _cambridge_final_url_matches_term(resp.url, clean_term):
            return []
        return parse_cambridge_provider_entries(resp.text, resp.url, clean_term)

    try:
        return retry_call(_do_request, retries=2, base_sleep=1.0)
    except Exception:
        return []
def fetch_cambridge_image_url(term: str) -> str:
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return ""
    normalized = clean_term.strip().replace(" ", "-")
    url = f"https://dictionary.cambridge.org/dictionary/english/{quote(normalized)}"

    def _do_request() -> str:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok or not _cambridge_final_url_matches_term(resp.url, clean_term):
            return ""
        body = resp.text
        entry_scope = _extract_cambridge_entry_scope(body, clean_term) or body
        candidates = _extract_image_urls_from_html(entry_scope, resp.url)
        return _choose_image_url(candidates, ["/images/full/", "/images/thumb/", "/images/"])

    try:
        return str(retry_call(_do_request, retries=2, base_sleep=1.0) or "").strip()
    except Exception:
        return ""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class InputItem:
    mode: str
    term: str
    hint: str = ""
    tags: List[str] | None = None

    def normalized_tags(self) -> List[str]:
        if not self.tags:
            return []
        return [str(t).strip().replace(" ", "_") for t in self.tags if str(t).strip()]


@dataclass
class ParsedEnglishTerm:
    raw: str
    word: str
    requested_pos: str = ""
    requested_sense_index: Optional[int] = None


@dataclass
class BuiltCard:
    front: str
    back: str
    tags: List[str]
    guid_seed: str


@dataclass
class AudioAsset:
    filename: str
    filepath: Path
    source_type: str = ""
    source: str = ""
    source_url: str = ""


class EnglishPronunciationInfo:
    phonetic: str
    audio_url: str
    pos_tags: List[str]
    # Which source supplied phonetic (priority OALD → LDOCE → Cambridge): "oxford" | "longman" | "cambridge" | ""
    source: str = ""
    # After audio_url, try these UK URLs in order (Oxford → Longman → Cambridge); TTS only if all fail.
    audio_fallback_urls: List[str] = field(default_factory=list)


@dataclass
class DictionarySenseCandidate:
    source: str
    word: str
    pos: str
    sense_id: str
    definition: str
    image_url: str = ""
    image_alt: str = ""
    examples: List[str] = field(default_factory=list)


@dataclass
class DictionaryExample:
    text: str
    audio_url: str = ""
    dom_path: str = ""


@dataclass
class WordPronunciation:
    source: str
    pos: str = ""
    ipa_uk: str = ""
    audio_uk_url: str = ""
    dom_path: str = ""


@dataclass
class ProviderSense:
    source: str
    native_id: str
    pos: str
    definition: str
    examples: List[DictionaryExample] = field(default_factory=list)
    image_url: str = ""
    image_alt: str = ""
    dom_path: str = ""
    definition_dom_path: str = ""
    image_dom_path: str = ""


@dataclass
class ProviderEntry:
    source: str
    dataset: str
    native_id: str
    word: str
    pos: str
    pronunciation: WordPronunciation
    senses: List[ProviderSense] = field(default_factory=list)
    cross_reference_ids: List[str] = field(default_factory=list)
    other_sense_ids: List[str] = field(default_factory=list)
    idiom_ids: List[str] = field(default_factory=list)
    dom_path: str = ""


@dataclass
class SenseAlignment:
    cambridge_sense_id: str
    longman_sense_ids: List[str] = field(default_factory=list)
    oxford_sense_ids: List[str] = field(default_factory=list)
    relation: str = "equivalent"
    confidence: float = 0.0


@dataclass
class UnmatchedSenseDecision:
    source: str
    sense_id: str
    decision: str
    related_cambridge_sense_ids: List[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class CanonicalSense:
    word: str
    pos: str
    index: int
    backbone_source: str
    backbone_sense_id: str
    cambridge_sense_id: str = ""
    longman_sense_ids: List[str] = field(default_factory=list)
    oxford_sense_ids: List[str] = field(default_factory=list)
    relation: str = "unmatched"
    confidence: float = 0.0
    canonical_key: str = ""
    fingerprint: str = ""
    status: str = "active"


@dataclass
class CanonicalManifestEntry:
    canonical_key: str
    word: str
    pos: str
    index: int
    native_ids: dict[str, List[str]]
    fingerprint: str
    status: str = "active"


@dataclass
class CanonicalSenseRequest:
    item: InputItem
    word: str
    pos: str
    canonical_sense: CanonicalSense
    provider_entries: List[ProviderEntry] = field(default_factory=list)


@dataclass
class ResolvedContentField:
    value: str = ""
    source: str = ""
    sense_id: str = ""


@dataclass
class ResolvedCanonicalContent:
    word: str
    pos: str
    index: int
    canonical_key: str
    status: str
    definition: ResolvedContentField
    example: ResolvedContentField
    example_audio: ResolvedContentField
    ipa: ResolvedContentField
    word_audio_urls: List[ResolvedContentField] = field(default_factory=list)
    image: ResolvedContentField = field(default_factory=ResolvedContentField)
    image_alt: str = ""


@dataclass
class CardBuildResult:
    cards: List[BuiltCard] = field(default_factory=list)
    assets: List[AudioAsset] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    resolved_contents: List[ResolvedCanonicalContent] = field(default_factory=list)


@dataclass
class DictionaryEntryResult:
    source: str = ""
    word: str = ""
    requested_pos: str = ""
    actual_pos: str = ""
    ipa_uk: str = ""
    audio_uk_url: str = ""
    definition: str = ""
    image_url: str = ""
    image_alt: str = ""
    sense_id: str = ""
    ipa_source: str = ""
    audio_source: str = ""
    definition_source: str = ""
    image_source: str = ""

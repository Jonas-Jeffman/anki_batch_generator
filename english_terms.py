from __future__ import annotations

import re
from typing import List

from models import ParsedEnglishTerm


POS_CANONICAL = {
    "noun": "noun",
    "n": "noun",
    "verb": "verb",
    "v": "verb",
    "adjective": "adjective",
    "adj": "adjective",
    "adverb": "adverb",
    "adv": "adverb",
    "pronoun": "pronoun",
    "pron": "pronoun",
    "preposition": "preposition",
    "prep": "preposition",
    "conjunction": "conjunction",
    "conj": "conjunction",
    "interjection": "interjection",
    "interj": "interjection",
    "determiner": "determiner",
    "det": "determiner",
    "article": "article",
}
POS_PATTERN = re.compile(
    r"(?i)\b("
    r"noun|n|verb|v|adjective|adj|adverb|adv|pronoun|pron|preposition|prep|"
    r"conjunction|conj|interjection|interj|determiner|det|article"
    r")\b"
)
SENSE_INDEX_PATTERN = re.compile(
    r"(?i)(?:^|\s)[(\[{]?\s*"
    r"(?P<pos>noun|n|verb|v|adjective|adj|adverb|adv|pronoun|pron|preposition|prep|"
    r"conjunction|conj|interjection|interj|determiner|det|article)"
    r"\s*[)\]}]?\s+(?P<index>[+-]?\d+)\s*$"
)


def normalize_pos_tag(raw: str) -> str:
    key = re.sub(r"[^a-z]", "", raw.strip().lower())
    return POS_CANONICAL.get(key, "")


def parse_english_term(term: str) -> ParsedEnglishTerm:
    raw = (term or "").strip()
    if not raw:
        return ParsedEnglishTerm(raw="", word="", requested_pos="")

    sense_match = SENSE_INDEX_PATTERN.search(raw)
    requested_sense_index = None
    term_without_sense_index = raw
    if sense_match:
        requested_sense_index = int(sense_match.group("index"))
        if requested_sense_index < 1:
            raise ValueError("English sense index must be a positive integer.")
        term_without_sense_index = raw[:sense_match.start("index")].rstrip()

    tags = extract_pos_tags(term_without_sense_index)
    requested_pos = tags[-1] if tags else ""

    word = strip_pos_labels_from_term(term_without_sense_index) or term_without_sense_index

    return ParsedEnglishTerm(
        raw=raw,
        word=word,
        requested_pos=requested_pos,
        requested_sense_index=requested_sense_index,
    )


def extract_pos_tags(text: str) -> List[str]:
    tags: List[str] = []
    for m in POS_PATTERN.finditer(text or ""):
        normalized = normalize_pos_tag(m.group(1))
        if normalized and normalized not in tags:
            tags.append(normalized)
    return tags


def strip_pos_labels_from_term(term: str) -> str:
    s = (term or "").strip()
    if not s:
        return ""
    # Remove obvious POS wrappers such as "(verb)" "[noun]" "{adj}".
    s = re.sub(r"[\(\[\{]\s*(?:noun|n|verb|v|adjective|adj|adverb|adv|pronoun|pron|preposition|prep|conjunction|conj|interjection|interj|determiner|det|article)\s*[\)\]\}]",
               " ", s, flags=re.IGNORECASE)
    # Remove standalone POS labels, but keep the lexical word itself.
    s = POS_PATTERN.sub(" ", s)
    s = re.sub(r"[\\/|,;]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_single_word_term(term: str) -> bool:
    clean = strip_pos_labels_from_term(term)
    if not clean:
        return False
    # Single lexical item only: no whitespace-separated phrase.
    return len(clean.split()) == 1


def lexical_word_count(term: str) -> int:
    clean = strip_pos_labels_from_term(term)
    if not clean:
        return 0
    return len(clean.split())


def is_two_word_term(term: str) -> bool:
    return lexical_word_count(term) == 2


def en_word_uses_dictionary_lookup(term: str) -> bool:
    """Single word or exactly two words; longer phrases skip dict scrape. EN audio is single-word only."""
    wc = lexical_word_count(term)
    return wc == 1 or wc == 2

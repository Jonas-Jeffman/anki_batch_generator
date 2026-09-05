from __future__ import annotations

import re
from typing import List
from urllib.parse import quote, urlparse

import requests

from english_terms import extract_pos_tags, strip_pos_labels_from_term
from utils import retry_call
from dictionary.cambridge import fetch_cambridge_image_url


def should_attach_noun_image(term: str, hint: str, dict_pos_tags: List[str]) -> bool:
    tags = set(extract_pos_tags(term) + extract_pos_tags(hint) + [t for t in dict_pos_tags if t])
    return "noun" in tags


ABSTRACT_OR_VIRTUAL_KEYWORDS = {
    "app",
    "application",
    "software",
    "service",
    "platform",
    "system",
    "feature",
    "tool",
    "framework",
    "plugin",
    "concept",
    "idea",
    "method",
    "strategy",
    "process",
    "policy",
    "rule",
    "theory",
    "emotion",
    "feeling",
    "quality",
    "state",
    "behavior",
    "behaviour",
    "mindset",
    "culture",
    "language",
    "ability",
    "skill",
    "knowledge",
    "information",
    "data",
    "content",
    "access",
    "security",
    "privacy",
    "economy",
    "society",
    "relationship",
}

CONCRETE_OBJECT_HINTS = {
    "fruit",
    "vegetable",
    "food",
    "drink",
    "animal",
    "bird",
    "fish",
    "insect",
    "plant",
    "flower",
    "tree",
    "tool",
    "instrument",
    "machine",
    "device",
    "vehicle",
    "car",
    "bus",
    "train",
    "bicycle",
    "boat",
    "ship",
    "airplane",
    "furniture",
    "chair",
    "table",
    "bed",
    "sofa",
    "clothing",
    "shoe",
    "hat",
    "bag",
    "kitchen",
    "cup",
    "bottle",
    "plate",
    "book",
    "toy",
    "ball",
    "bat",
    "camera",
    "phone",
    "computer",
}

ABSTRACT_DEFINITION_HINTS = {
    "idea",
    "concept",
    "quality",
    "state",
    "process",
    "system",
    "method",
    "ability",
    "act of",
    "feeling",
    "emotion",
    "condition",
    "relationship",
    "behavior",
    "behaviour",
    "policy",
}


def _tokenize_alpha(text: str) -> List[str]:
    return re.findall(r"[a-z]+", (text or "").lower())


def is_common_concrete_noun(term: str, hint: str, definition_en: str) -> bool:
    clean_term = strip_pos_labels_from_term(term).lower()
    if not clean_term:
        return False

    tokens = _tokenize_alpha(clean_term)
    if not tokens:
        return False

    # Avoid many multiword technical compounds by default (e.g. "app blocker").
    if len(tokens) >= 3:
        return False

    term_and_hint_tokens = set(tokens + _tokenize_alpha(hint))
    definition_low = (definition_en or "").strip().lower()

    # Hard stop for abstract/virtual senses.
    if term_and_hint_tokens.intersection(ABSTRACT_OR_VIRTUAL_KEYWORDS):
        return False
    if any(marker in definition_low for marker in ABSTRACT_DEFINITION_HINTS):
        return False

    # Positive signals for concrete, visual entities.
    if term_and_hint_tokens.intersection(CONCRETE_OBJECT_HINTS):
        return True
    if any(marker in definition_low for marker in CONCRETE_OBJECT_HINTS):
        return True

    # Conservative fallback: single-word nouns are often concrete but not always.
    return len(tokens) == 1 and "-" not in clean_term
def fetch_dictionary_image_url(term: str) -> str:
    # 图片只从 Cambridge 抓取。
    # Oxford / Longman 不再作为图片兜底来源。
    return fetch_cambridge_image_url(term)


def fetch_wikipedia_image_url(term: str) -> str:
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return ""
    api_url = (
        "https://en.wikipedia.org/w/api.php"
        f"?action=query&prop=pageimages&format=json&pithumbsize=600&titles={quote(clean_term)}"
    )

    def _do_request() -> str:
        resp = requests.get(
            api_url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok:
            return ""
        data = resp.json()
        pages = (((data or {}).get("query") or {}).get("pages") or {})
        if not isinstance(pages, dict):
            return ""
        for page in pages.values():
            if not isinstance(page, dict):
                continue
            thumb = page.get("thumbnail") or {}
            src = str((thumb or {}).get("source") or "").strip()
            if src:
                return src
        return ""

    try:
        return str(retry_call(_do_request, retries=2, base_sleep=1.0) or "").strip()
    except Exception:
        return ""


def dictionary_source_from_url(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    if "cambridge.org" in host:
        return "cambridge"
    if "oxfordlearnersdictionaries.com" in host:
        return "oxford"
    if "ldoceonline.com" in host:
        return "longman"
    if "wikipedia.org" in host or "wikimedia.org" in host:
        return "wikipedia"
    return host or "unknown"

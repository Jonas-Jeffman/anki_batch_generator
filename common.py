#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, unquote, urljoin, urlparse

import requests

try:
    import genanki
except ImportError:
    genanki = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

SUPPORTED_MODES = {"en_word", "ja_word", "interview", "paper", "interest"}
DEFAULT_TEXT_MODEL = "gpt-5.4"
DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_SLEEP = 2.0
LLM_SCHEMA_VERSION = "no_zh_v5_safe_ipa"

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TERMS_JSON = SCRIPT_DIR / "terms.json"
DEFAULT_TERMS_TXT = SCRIPT_DIR / "terms.txt"
LOCAL_OPENAI_KEY_FILE = SCRIPT_DIR / ".openai_api_key"

def read_optional_local_openai_key() -> str:
    """Load key from LOCAL_OPENAI_KEY_FILE if present (one secret per line, # comments allowed)."""
    path = LOCAL_OPENAI_KEY_FILE
    if not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("OPENAI_API_KEY="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
            return s
    except OSError:
        return ""
    return ""


def resolve_openai_api_key(cli_value: str) -> str:
    order = (
        cli_value.strip(),
        os.getenv("OPENAI_API_KEY", "").strip(),
        read_optional_local_openai_key().strip(),
    )
    for key in order:
        if key:
            return key
    return ""


def resolve_openai_base_url(cli_value: str) -> Optional[str]:
    """Third-party OpenAI-compatible gateways (e.g. ChatAnywhere) need a custom base_url."""
    for raw in (cli_value.strip(), os.getenv("OPENAI_BASE_URL", "").strip()):
        if raw:
            url = raw.rstrip("/")
            if not url.endswith("/v1"):
                url = f"{url}/v1"
            return url
    return None


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


class CacheStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: Dict[str, Dict] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def get(self, key: str) -> Optional[Dict]:
        value = self.data.get(key)
        return value if isinstance(value, dict) else None

    def set(self, key: str, value: Dict) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def stable_anki_id(seed: str) -> int:
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def stable_guid(*parts: str) -> str:
    base = "||".join(p.strip() for p in parts)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:20]


def slugify(text: str, max_len: int = 60) -> str:
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"[^\w\-\u4e00-\u9fff\u3040-\u30ff]+", "", text)
    return text[:max_len] or "item"


def html_escape(text: str) -> str:
    return html.escape(text or "").replace("\n", "<br>")


def retry_call(fn, retries: int = 3, base_sleep: float = 1.0):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt == retries:
                break
            time.sleep(base_sleep * attempt)
    raise last_exc


def read_terms_from_json_string(terms_json: str) -> List[str]:
    try:
        data = json.loads(terms_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON array: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError("--terms-json must be a JSON array, e.g. '[\"apologise\", \"burgeon\"]'.")

    terms: List[str] = []
    for item in data:
        if not isinstance(item, str):
            raise ValueError("Each array item must be a string.")
        item = item.strip()
        if item:
            terms.append(item)
    return terms


def read_terms_from_json_file(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Terms JSON file not found: {path}")
    return read_terms_from_json_string(path.read_text(encoding="utf-8"))


def read_terms_from_txt_file(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Terms text file not found: {path}")
    terms: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        terms.append(stripped)
    return terms


def read_terms_from_path(path: Path) -> List[str]:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return read_terms_from_txt_file(path)
    if suffix == ".json":
        return read_terms_from_json_file(path)
    raise ValueError(f"Unsupported terms file type: {path} (use .json or .txt)")


def load_items(args: argparse.Namespace) -> List[InputItem]:
    terms: List[str] = []
    if args.terms_json:
        terms.extend(read_terms_from_json_string(args.terms_json))
    if args.terms_file:
        terms.extend(read_terms_from_path(Path(args.terms_file).expanduser().resolve()))

    deduped = []
    seen = set()
    for term in terms:
        key = (args.mode, term)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(InputItem(mode=args.mode, term=term, hint=args.hint, tags=args.tags or []))
    return deduped


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


def normalize_pos_tag(raw: str) -> str:
    key = re.sub(r"[^a-z]", "", raw.strip().lower())
    return POS_CANONICAL.get(key, "")


def parse_english_term(term: str) -> ParsedEnglishTerm:
    raw = (term or "").strip()
    if not raw:
        return ParsedEnglishTerm(raw="", word="", requested_pos="")

    tags = extract_pos_tags(raw)
    requested_pos = tags[-1] if tags else ""

    word = strip_pos_labels_from_term(raw) or raw

    return ParsedEnglishTerm(
        raw=raw,
        word=word,
        requested_pos=requested_pos,
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

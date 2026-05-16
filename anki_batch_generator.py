#!/usr/bin/env python3
"""
Batch-generate or extend an Anki deck from a JSON array.

Examples:
  # Uses terms.json (or terms.txt) next to this script if you omit --terms-json/--terms-file.
  python anki_batch_generator_optimized.py \
    --mode en_word \
    --deck-name 'English::My Deck' \
    --openai-api-key "$OPENAI_API_KEY"

  python anki_batch_generator_optimized.py \
    --mode en_word \
    --terms-json '["apologise", "burgeon"]' \
    --deck-name 'English::My Deck' \
    --model gpt-5.4 \
    --openai-api-key "$OPENAI_API_KEY"

  python anki_batch_generator_optimized.py \
    --mode interview \
    --terms-json '["Explain Qwen MoE in simple terms"]' \
    --deck-name 'Interview::Tech' \
    --model gpt-5.4

Key upgrades compared with the original script:
- Accepts JSON arrays directly instead of requiring CSV.
- Supports extending an existing deck by reusing the same deck name and stable note GUIDs.
- Downloads English/Japanese audio into the .apkg media package.
- Uses higher-end OpenAI models by default.
- Raises default sleep between requests.
- Adds retries, caching, and clearer error handling.
"""

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
from urllib.parse import quote

import genanki
import requests
from openai import OpenAI

SUPPORTED_MODES = {"en_word", "ja_word", "interview", "paper", "interest"}
DEFAULT_TEXT_MODEL = "gpt-5.4"
DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_SLEEP = 2.0

# Bump when LLM JSON schema changes so disk cache does not reuse stale shapes.
LLM_SCHEMA_VERSION = "no_zh_v4_en2_no_tts"

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TERMS_JSON = SCRIPT_DIR / "terms.json"
DEFAULT_TERMS_TXT = SCRIPT_DIR / "terms.txt"
# Local-only file: put your API key on the first non-empty, non-# line. Do not commit (see .gitignore).
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
class BuiltCard:
    front: str
    back: str
    tags: List[str]
    guid_seed: str


@dataclass
class AudioAsset:
    filename: str
    filepath: Path


@dataclass
class EnglishPronunciationInfo:
    phonetic: str
    audio_url: str
    pos_tags: List[str]
    # Which source supplied phonetic (priority OALD → LDOCE → Cambridge): "oxford" | "longman" | "cambridge" | ""
    source: str = ""
    # After audio_url, try these UK URLs in order (Oxford → Longman → Cambridge); TTS only if all fail.
    audio_fallback_urls: List[str] = field(default_factory=list)


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


def en_word_uses_dictionary_and_audio(term: str) -> bool:
    """Single word or exactly two words; longer phrases skip dict scrape and EN audio."""
    wc = lexical_word_count(term)
    return wc == 1 or wc == 2


def _normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/media/english/"):
        return f"https://dictionary.cambridge.org{u}"
    return u


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


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
        body = resp.text
        pos_tags = list(dict.fromkeys(normalize_pos_tag(p) for p in re.findall(r'class="pos dpos"[^>]*>([^<]+)<', body, flags=re.IGNORECASE)))
        pos_tags = [p for p in pos_tags if p]

        uk_block = re.search(r'(<span[^>]*class="[^"]*\buk dpron-i\b[^"]*"[^>]*>.*?</span>)', body, flags=re.IGNORECASE | re.DOTALL)
        scope = uk_block.group(1) if uk_block else body
        ipa = ""
        ipa_match = re.search(r'class="[^"]*\bipa\b[^"]*"[^>]*>(.*?)<', scope, flags=re.IGNORECASE | re.DOTALL)
        if ipa_match:
            ipa = _strip_tags(html.unescape(ipa_match.group(1))).replace(" ", "")

        audio = ""
        audio_match = re.search(r'(?:data-src-mp3|src)="([^"]+)"', scope, flags=re.IGNORECASE)
        if audio_match:
            audio = _normalize_url(html.unescape(audio_match.group(1)))
        if not audio:
            for candidate in re.findall(r'(?:data-src-mp3|src)="([^"]+)"', body, flags=re.IGNORECASE):
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
        body = resp.text
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


def fetch_english_pronunciation(term: str) -> EnglishPronunciationInfo:
    """Merge Oxford (OALD) → Longman (LDOCE) → Cambridge: BrE IPA priority; BrE audio URL fallbacks then TTS."""
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])

    o = fetch_english_from_oxford(clean_term)
    l = fetch_english_from_longman(clean_term)
    c = fetch_english_from_cambridge(clean_term)

    seen = set()
    audio_list: List[str] = []
    for u in (o.audio_url, l.audio_url, c.audio_url):
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
    for info, label in ((o, "oxford"), (l, "longman"), (c, "cambridge")):
        p = (info.phonetic or "").strip()
        if p:
            phonetic = p
            source = label
            break
    if not source:
        for info, label in ((o, "oxford"), (l, "longman"), (c, "cambridge")):
            if (info.phonetic or "").strip() or (info.audio_url or "").strip() or info.pos_tags:
                source = label
                break

    pos_tags: List[str] = []
    for info in (o, l, c):
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


def _ipa_core(phonetic: str) -> str:
    return (phonetic or "").strip().strip("/").replace(" ", "")


def _combine_two_ipa_fragments(left: str, right: str) -> str:
    a = _ipa_core(left)
    b = _ipa_core(right)
    if a and b:
        return f"/{a}/ /{b}/"
    if a:
        return (left or "").strip() or f"/{a}/"
    if b:
        return (right or "").strip() or f"/{b}/"
    return ""


def fetch_english_pronunciation_two_words(term: str) -> EnglishPronunciationInfo:
    """Phrase-level dictionaries first; if no phrase IPA, concatenate per-word BrE IPA."""
    clean = strip_pos_labels_from_term(term)
    parts = clean.split()
    if len(parts) != 2:
        return EnglishPronunciationInfo(phonetic="", audio_url="", pos_tags=[])

    phrase = fetch_english_pronunciation(clean)
    if (phrase.phonetic or "").strip():
        return phrase

    left = fetch_english_pronunciation(parts[0])
    right = fetch_english_pronunciation(parts[1])
    combined_ipa = _combine_two_ipa_fragments(left.phonetic, right.phonetic)

    pos_tags: List[str] = []
    if phrase.pos_tags:
        pos_tags = list(phrase.pos_tags)
    elif left.pos_tags:
        pos_tags = list(left.pos_tags)
    elif right.pos_tags:
        pos_tags = list(right.pos_tags)

    return EnglishPronunciationInfo(
        phonetic=combined_ipa,
        audio_url=phrase.audio_url,
        pos_tags=pos_tags,
        source="",
        audio_fallback_urls=list(phrase.audio_fallback_urls or []),
    )


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


def _is_plausible_image(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 1024:
            return False
        with path.open("rb") as f:
            head = f.read(12)
        return (
            head.startswith(b"\xff\xd8\xff")  # JPG
            or head.startswith(b"\x89PNG\r\n\x1a\n")  # PNG
            or head.startswith(b"GIF87a")
            or head.startswith(b"GIF89a")
            or (len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP")
        )
    except OSError:
        return False


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


def ensure_noun_image(media_dir: Path, item: InputItem) -> Optional[AudioAsset]:
    clean_term = strip_pos_labels_from_term(item.term) or item.term.strip()
    if not clean_term:
        return None
    image_url = fetch_wikipedia_image_url(clean_term)
    if not image_url:
        return None
    ext = ".jpg"
    low = image_url.lower()
    if ".png" in low:
        ext = ".png"
    elif ".webp" in low:
        ext = ".webp"
    elif ".gif" in low:
        ext = ".gif"
    filename = f"img_en_{slugify(clean_term)}_{stable_guid(item.mode, item.term)[:8]}{ext}"
    filepath = media_dir / filename
    if _is_plausible_image(filepath):
        return AudioAsset(filename=filename, filepath=filepath)

    def _download() -> bool:
        resp = requests.get(
            image_url,
            timeout=15,
            stream=True,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok:
            return False
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with filepath.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return True

    try:
        ok = bool(retry_call(_download, retries=2, base_sleep=1.0))
    except Exception:
        ok = False
    if not ok or not _is_plausible_image(filepath):
        try:
            filepath.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return AudioAsset(filename=filename, filepath=filepath)


def build_en_word_prompt() -> str:
    return (
        "You are an expert Anki flashcard author for English vocabulary.\n\n"

        "Your task is to turn the given `term` (and optional `hint`) into a concise, "
        "high-quality Anki flashcard.\n\n"

        "OUTPUT FORMAT (hard requirements):\n"
        "- Return a single JSON object only.\n"
        "- No markdown, no explanations, no extra text.\n"
        "- Keys must be exactly:\n"
        "  pronunciation_text, definition_en, example_simple_en\n"
        "- All values must be strings.\n"
        "- If unknown, use \"\".\n\n"

        "STYLE:\n"
        "- Use simple, clear English.\n"
        "- Be concise, no filler.\n"
        "- Definition must explain meaning, not restate the word.\n"
        "- Do NOT use circular definitions.\n"
        "- Example must be natural and easy to understand.\n"
        "- Avoid complex contexts (war, politics, etc.).\n\n"

        "STRUCTURE (semantic intent for each JSON field):\n"
        "- pronunciation_text: British English (BrE) IPA only, aligned with the user payload below\n"
        "- definition_en: short gloss\n"
        "- example_simple_en: one short natural sentence\n\n"

        "PRONUNCIATION SCOPE:\n"
        "- Only provide pronunciation_text when `lexical_word_count` is 1 or 2 (see user payload).\n"
        "- For two-word expressions, IPA may be two slash-groups separated by one ASCII space, "
        "e.g. \"/ˈpleɪ/ /ˈhʊki/\", matching `phonetic_hint` when it is non-empty.\n"
        "- If `lexical_word_count` is 3 or more, pronunciation_text must be \"\".\n"
        "- Examples: 'savvy' -> IPA; 'play hooky' -> IPA as above; 'everything in moderation' -> \"\".\n\n"

        "MINIMAL JSON EXAMPLE (shape only; use your own values):\n"
        '{"pronunciation_text":"/ˌɒp.ə.tjuːˈnɪs.tɪk/",'
        '"definition_en":"using a situation to gain an advantage",'
        '"example_simple_en":"She made an opportunistic decision to take the job."}\n\n'

        "LANGUAGE RULES:\n"
        "- Output only English.\n"
        "- Do NOT include Chinese unless explicitly required in `hint`.\n\n"

        "PHONETIC SOURCE (user payload `phonetic_source` + `phonetic_hint`):\n"
        "- `phonetic_source` is which dictionary supplied the BrE IPA (if any): oxford, longman, "
        "cambridge, or none. (Oxford = Oxford Learner's; Longman = LDOCE; Cambridge = Cambridge.)\n"
        "- For oxford, longman, or cambridge, `phonetic_hint` is the site’s UK/BrE IPA. Set "
        "`pronunciation_text` to match `phonetic_hint` (you may add leading/trailing slashes and "
        "trim spaces; do not substitute US/American IPA).\n"
        "- When `phonetic_source` is `none` or `phonetic_hint` is empty, output standard BrE-style "
        "IPA from your own knowledge (e.g. avoid using US-only symbols where BrE would use /ɜː/).\n\n"

        "ACCURACY:\n"
        "- If `phonetic_hint` is non-empty, it overrides your own guess for `pronunciation_text`.\n"
        "- If no `phonetic_hint`, output British English (BrE) IPA style.\n"
        "- Ignore POS labels such as noun/verb/adj in pronunciation.\n"
        "- Choose the most common meaning.\n"
    )

def build_ja_word_prompt() -> str:
    return (
        "You are an expert Anki flashcard author for Japanese vocabulary.\n\n"

        "OUTPUT FORMAT (hard requirements):\n"
        "- Return a single JSON object only.\n"
        "- No markdown, no explanations.\n"
        "- Keys must be exactly:\n"
        "  reading_kana, explanation_ja, example_simple_ja\n"
        "- All values must be strings.\n"
        "- If unknown, use \"\".\n\n"

        "STYLE:\n"
        "- Use natural and simple Japanese.\n"
        "- Be concise, no filler.\n"
        "- If the concept is complex, express it clearly but briefly.\n"
        "- Example sentences must be natural and easy to understand.\n\n"

        "LANGUAGE RULES:\n"
        "- Output must be entirely in Japanese.\n"
        "- Do NOT include Chinese.\n\n"

        "ACCURACY:\n"
        "- Use correct kana reading.\n"
        "- Prefer the most common meaning.\n"
    )

def build_interview_prompt() -> str:
    return (
        "You are an expert Anki flashcard author for technical interview preparation.\n\n"

        "OUTPUT FORMAT (hard requirements):\n"
        "- Return a single JSON object only.\n"
        "- No markdown, no explanations.\n"
        "- Keys must be exactly:\n"
        "  question_title, concise_answer, key_points, easy_example\n"
        "- key_points must be an array of 2–5 short strings.\n"
        "- Other fields must be strings.\n"
        "- If unknown, use \"\" or [].\n\n"

        "STYLE:\n"
        "- Be concise and high-signal.\n"
        "- Avoid long paragraphs.\n"
        "- If concept is complex, break it into clear key points.\n"
        "- Each key point should contain one idea only.\n\n"

        "CONTENT RULES:\n"
        "- Answer must directly address the question.\n"
        "- Avoid vague or generic statements.\n"
        "- Example must be simple and intuitive.\n\n"

        "ACCURACY:\n"
        "- Do not fabricate facts.\n"
        "- Prefer standard and widely accepted explanations.\n"
    )

def build_paper_prompt() -> str:
    return (
        "You are an expert Anki flashcard author for technical and research concepts.\n\n"

        "OUTPUT FORMAT (hard requirements):\n"
        "- Return a single JSON object only.\n"
        "- No markdown, no explanations.\n"
        "- Keys must be exactly:\n"
        "  topic_title, core_idea, why_it_matters, easy_example\n"
        "- All values must be strings.\n"
        "- If unknown, use \"\".\n\n"

        "STYLE:\n"
        "- Be concise but informative.\n"
        "- Focus only on the core idea.\n"
        "- Avoid unnecessary background.\n"
        "- If concept is complex, explain it clearly in a structured way.\n\n"

        "CONTENT RULES:\n"
        "- core_idea = what it is\n"
        "- why_it_matters = why it is useful\n"
        "- example must be simple and intuitive\n\n"

        "ACCURACY:\n"
        "- Do not fabricate claims.\n"
        "- Prefer widely accepted interpretations.\n"
    )

def build_interest_prompt() -> str:
    return (
        "You are an expert Anki flashcard author for general knowledge and interesting facts.\n\n"

        "OUTPUT FORMAT (hard requirements):\n"
        "- Return a single JSON object only.\n"
        "- No markdown, no explanations.\n"
        "- Keys must be exactly:\n"
        "  topic_title, what_it_is, fun_fact, easy_example\n"
        "- All values must be strings.\n"
        "- If unknown, use \"\".\n\n"

        "STYLE:\n"
        "- Be concise and easy to remember.\n"
        "- Avoid long explanations.\n"
        "- If topic is complex, explain it simply.\n\n"

        "CONTENT RULES:\n"
        "- what_it_is: simple explanation\n"
        "- fun_fact: interesting detail\n"
        "- example: concrete and intuitive\n\n"

        "ACCURACY:\n"
        "- Do not fabricate facts.\n"
        "- Prefer common and reliable knowledge.\n"
    )


# Must be defined after all build_*_prompt functions (references by name).
PROMPT_MAP: Dict[str, Callable[[], str]] = {
    "en_word": build_en_word_prompt,
    "ja_word": build_ja_word_prompt,
    "interview": build_interview_prompt,
    "paper": build_paper_prompt,
    "interest": build_interest_prompt,
}


def build_user_payload(
    item: InputItem,
    phonetic_hint: str,
    term_for_pronunciation: str,
    phonetic_source: str = "",
) -> str:
    """Variable inputs only; mode-specific rules live in PROMPT_MAP system prompts."""
    return (
        "Fill the JSON fields described in your system instructions using this input.\n\n"
        f"term: {item.term}\n"
        f"term_for_pronunciation: {term_for_pronunciation or item.term}\n"
        f"lexical_word_count: {lexical_word_count(item.term)}\n"
        f"is_single_word_term: {'true' if is_single_word_term(item.term) else 'false'}\n"
        f"is_two_word_expression: {'true' if is_two_word_term(item.term) else 'false'}\n"
        f"hint: {item.hint or '(none)'}\n"
        f"phonetic_source: {phonetic_source or 'none'}\n"
        f"phonetic_hint: {phonetic_hint or '(none)'}\n"
    )


def call_openai_json(
    client: OpenAI,
    model: str,
    item: InputItem,
    phonetic_hint: str,
    term_for_pronunciation: str,
    reasoning_effort: str,
    phonetic_source: str = "",
) -> Dict:
    system_prompt = PROMPT_MAP[item.mode]()
    user_prompt = build_user_payload(
        item, phonetic_hint, term_for_pronunciation, phonetic_source
    )

    def _call() -> Dict:
        kwargs = {
            "model": model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        # Newer GPT-5 series supports reasoning effort; older families will ignore this parameter
        # if the installed SDK/API surface accepts it.
        if model.startswith("gpt-5"):
            kwargs["reasoning_effort"] = reasoning_effort

        response = client.chat.completions.create(**kwargs)
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("OpenAI returned empty content.")
        return json.loads(text)

    return retry_call(_call, retries=3, base_sleep=2.0)


def synthesize_tts_to_file(
    client: OpenAI,
    tts_model: str,
    voice: str,
    text: str,
    filepath: Path,
) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)

    def _call() -> None:
        # openai>=1.14 uses response_format; older code used format= (removed).
        with client.audio.speech.with_streaming_response.create(
            model=tts_model,
            voice=voice,
            input=text,
            response_format="mp3",
        ) as response:
            response.stream_to_file(filepath)

    retry_call(_call, retries=3, base_sleep=2.0)


def _is_plausible_mp3(path: Path) -> bool:
    """Reject empty files or HTML error bodies saved with .mp3 extension."""
    try:
        if not path.is_file() or path.stat().st_size < 256:
            return False
        with path.open("rb") as f:
            head = f.read(4)
        if head.startswith(b"ID3"):
            return True
        if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
            return True
        # Not MP3 (often '<htm' or '{"er')
        return False
    except OSError:
        return False


def _english_external_audio_url_queue(
    preferred_external_url: str,
    extra_audio_urls: Optional[List[str]],
) -> List[str]:
    url_queue: List[str] = []
    u0 = (preferred_external_url or "").strip()
    if u0:
        url_queue.append(_normalize_url(u0))
    for u in extra_audio_urls or []:
        u = (u or "").strip()
        if not u:
            continue
        u = _normalize_url(u)
        if u and u not in url_queue:
            url_queue.append(u)
    return url_queue


def _try_download_english_mp3_from_urls(url_queue: List[str], filepath: Path) -> bool:
    for url in url_queue:
        if not maybe_download_external_audio(url, filepath):
            continue
        if _is_plausible_mp3(filepath):
            return True
        try:
            filepath.unlink(missing_ok=True)
        except OSError:
            pass
    return False


def maybe_download_external_audio(audio_url: str, filepath: Path) -> bool:
    if not audio_url:
        return False

    def _call() -> bool:
        resp = requests.get(
            audio_url,
            timeout=15,
            stream=True,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok:
            return False
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with filepath.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return filepath.exists() and filepath.stat().st_size > 0

    try:
        return bool(retry_call(_call, retries=2, base_sleep=1.0))
    except Exception:
        return False


def ensure_english_audio(
    client: OpenAI,
    media_dir: Path,
    item: InputItem,
    spoken_term: str,
    preferred_external_url: str,
    tts_model: str,
    voice: str,
    extra_audio_urls: Optional[List[str]] = None,
    *,
    disable_tts: bool = False,
    filename_suffix: str = "",
) -> Optional[AudioAsset]:
    safe_suffix = filename_suffix if filename_suffix.startswith("_") else (
        f"_{filename_suffix}" if filename_suffix else ""
    )
    filename = f"audio_en_{slugify(item.term)}_{stable_guid(item.mode, item.term)[:8]}{safe_suffix}.mp3"
    filepath = media_dir / filename
    if _is_plausible_mp3(filepath):
        return AudioAsset(filename=filename, filepath=filepath)

    url_queue = _english_external_audio_url_queue(preferred_external_url, extra_audio_urls)
    if _try_download_english_mp3_from_urls(url_queue, filepath):
        return AudioAsset(filename=filename, filepath=filepath)

    if disable_tts:
        return None

    try:
        synthesize_tts_to_file(client, tts_model, voice, spoken_term, filepath)
    except Exception:
        # Third-party gateways may not implement TTS; still produce the card without audio.
        return None
    if not _is_plausible_mp3(filepath):
        try:
            filepath.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return AudioAsset(filename=filename, filepath=filepath)


def ensure_japanese_audio(
    client: OpenAI,
    media_dir: Path,
    item: InputItem,
    tts_model: str,
    voice: str,
) -> Optional[AudioAsset]:
    filename = f"audio_ja_{slugify(item.term)}_{stable_guid(item.mode, item.term)[:8]}.mp3"
    filepath = media_dir / filename
    if _is_plausible_mp3(filepath):
        return AudioAsset(filename=filename, filepath=filepath)

    try:
        synthesize_tts_to_file(client, tts_model, voice, item.term, filepath)
    except Exception:
        return None
    if not _is_plausible_mp3(filepath):
        try:
            filepath.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return AudioAsset(filename=filename, filepath=filepath)


def build_en_word_card(
    term: str,
    llm: Dict,
    dict_phonetic: str,
    audio_assets: List[AudioAsset],
    image: Optional[AudioAsset],
) -> BuiltCard:
    ipa = (dict_phonetic or "").strip() or str(llm.get("pronunciation_text") or "").strip()
    if ipa and not ipa.startswith("/"):
        ipa = f"/{ipa.strip('/')}/"

    definition = str(llm.get("definition_en") or "").strip()
    example_en = str(llm.get("example_simple_en") or "").strip()

    sound_line = "".join(f"[sound:{a.filename}]" for a in (audio_assets or []))
    image_html = ""
    if image:
        image_html = (
            f'<br><b>Image:</b><br>'
            f'<img src="{html_escape(image.filename)}" alt="{html_escape(term)}" '
            'style="max-width:280px; max-height:220px; object-fit:contain;">'
        )
    # Front: one centered line — <b>word</b> + one ASCII space + IPA (normal weight, not bold).
    ipa_html = html_escape(ipa).strip()
    word_ipa_gap = " "
    if ipa_html:
        front = (
            '<div style="text-align:center">'
            f"<b>{html_escape(term)}</b>{word_ipa_gap}{ipa_html}"
            "</div>"
        )
    else:
        front = (
            '<div style="text-align:center">'
            f"<b>{html_escape(term)}</b>"
            "</div>"
        )
    # Put [sound:] on its own line first — some AnkiDroid/WebView builds handle this more reliably.
    if sound_line:
        back = (
            f"{sound_line}<br>"
            f"<b>Definition (EN):</b> {html_escape(definition)}<br>"
            f"<b>Example:</b><br>{html_escape(example_en)}"
            f"{image_html}"
        )
    else:
        back = (
            f"<b>Definition (EN):</b> {html_escape(definition)}<br>"
            f"<b>Example:</b><br>{html_escape(example_en)}"
            f"{image_html}"
        )
    return BuiltCard(
        front=front,
        back=back,
        tags=["english", "vocab"],
        guid_seed=f"en_word::{term.lower()}",
    )


def build_ja_word_card(term: str, llm: Dict, audio: Optional[AudioAsset]) -> BuiltCard:
    reading = str(llm.get("reading_kana") or "").strip()
    explanation_ja = str(llm.get("explanation_ja") or "").strip()
    example_ja = str(llm.get("example_simple_ja") or "").strip()

    sound = f"[sound:{audio.filename}]" if audio else ""
    front = html_escape(term)
    if sound:
        back = (
            f"{sound}<br>"
            f"<b>読み方:</b> {html_escape(reading)}<br>"
            f"<b>説明 (日本語):</b> {html_escape(explanation_ja)}<br>"
            f"<b>例文:</b> {html_escape(example_ja)}"
        )
    else:
        back = (
            f"<b>読み方:</b> {html_escape(reading)}<br>"
            f"<b>説明 (日本語):</b> {html_escape(explanation_ja)}<br>"
            f"<b>例文:</b> {html_escape(example_ja)}"
        )
    return BuiltCard(
        front=front,
        back=back,
        tags=["japanese", "vocab"],
        guid_seed=f"ja_word::{term}",
    )


def build_knowledge_card(mode: str, term: str, llm: Dict) -> BuiltCard:
    mode_tag = {
        "interview": "interview",
        "paper": "paper",
        "interest": "interest",
    }[mode]

    if mode == "interview":
        title = str(llm.get("question_title") or term).strip()
        answer = str(llm.get("concise_answer") or "").strip()
        points = llm.get("key_points") or []
        if not isinstance(points, list):
            points = []
        points_html = "".join(f"<li>{html_escape(str(p))}</li>" for p in points[:5])
        example = str(llm.get("easy_example") or "").strip()
        front = html_escape(title)
        back = (
            f"<b>Answer:</b> {html_escape(answer)}<br>"
            f"<b>Key Points:</b><ul>{points_html}</ul>"
            f"<b>Example:</b> {html_escape(example)}"
        )
    elif mode == "paper":
        title = str(llm.get("topic_title") or term).strip()
        core = str(llm.get("core_idea") or "").strip()
        why = str(llm.get("why_it_matters") or "").strip()
        example = str(llm.get("easy_example") or "").strip()
        front = html_escape(title)
        back = (
            f"<b>Core Idea:</b> {html_escape(core)}<br>"
            f"<b>Why It Matters:</b> {html_escape(why)}<br>"
            f"<b>Example:</b> {html_escape(example)}"
        )
    else:
        title = str(llm.get("topic_title") or term).strip()
        what_is = str(llm.get("what_it_is") or "").strip()
        fun_fact = str(llm.get("fun_fact") or "").strip()
        example = str(llm.get("easy_example") or "").strip()
        front = html_escape(title)
        back = (
            f"<b>What It Is:</b> {html_escape(what_is)}<br>"
            f"<b>Fun Fact:</b> {html_escape(fun_fact)}<br>"
            f"<b>Example:</b> {html_escape(example)}"
        )

    return BuiltCard(
        front=front,
        back=back,
        tags=[mode_tag, "knowledge"],
        guid_seed=f"{mode}::{term}",
    )


def build_card(
    client: OpenAI,
    item: InputItem,
    model: str,
    tts_model: str,
    media_dir: Path,
    tts_voice_en: str,
    tts_voice_ja: str,
    cache: CacheStore,
    reasoning_effort: str,
) -> Tuple[BuiltCard, List[AudioAsset]]:
    dict_phonetic = ""
    dict_phonetic_source = ""
    dict_audio = ""
    dict_audio_fallbacks: List[str] = []
    dict_pos_tags: List[str] = []
    spoken_term = item.term.strip()
    if item.mode == "en_word":
        spoken_term = strip_pos_labels_from_term(item.term) or item.term.strip()
        if en_word_uses_dictionary_and_audio(item.term):
            if is_two_word_term(item.term):
                pron_info = fetch_english_pronunciation_two_words(spoken_term)
            else:
                pron_info = fetch_english_pronunciation(spoken_term)
            dict_phonetic = pron_info.phonetic
            dict_phonetic_source = (pron_info.source or "").strip()
            dict_audio = pron_info.audio_url
            dict_audio_fallbacks = list(pron_info.audio_fallback_urls or [])
            dict_pos_tags = pron_info.pos_tags

    cache_key = hashlib.sha1(
        json.dumps(
            {
                "mode": item.mode,
                "term": item.term,
                "hint": item.hint,
                "model": model,
                "llm_schema": LLM_SCHEMA_VERSION,
                "dict_phonetic": dict_phonetic,
                "dict_phonetic_source": dict_phonetic_source,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    cached = cache.get(cache_key)
    if cached:
        llm = cached
    else:
        llm = call_openai_json(
            client=client,
            model=model,
            item=item,
            phonetic_hint=dict_phonetic,
            term_for_pronunciation=spoken_term,
            reasoning_effort=reasoning_effort,
            phonetic_source=dict_phonetic_source,
        )
        cache.set(cache_key, llm)

    assets: List[AudioAsset] = []
    if item.mode == "en_word":
        audio_assets: List[AudioAsset] = []
        if en_word_uses_dictionary_and_audio(item.term):
            if is_two_word_term(item.term):
                phrase_audio = ensure_english_audio(
                    client=client,
                    media_dir=media_dir,
                    item=item,
                    spoken_term=spoken_term,
                    preferred_external_url=dict_audio,
                    tts_model=tts_model,
                    voice=tts_voice_en,
                    extra_audio_urls=dict_audio_fallbacks,
                    disable_tts=True,
                )
                if phrase_audio:
                    audio_assets = [phrase_audio]
            else:
                single = ensure_english_audio(
                    client=client,
                    media_dir=media_dir,
                    item=item,
                    spoken_term=spoken_term,
                    preferred_external_url=dict_audio,
                    tts_model=tts_model,
                    voice=tts_voice_en,
                    extra_audio_urls=dict_audio_fallbacks,
                )
                if single:
                    audio_assets = [single]
        image: Optional[AudioAsset] = None
        definition_en = str(llm.get("definition_en") or "").strip()
        if should_attach_noun_image(item.term, item.hint, dict_pos_tags) and is_common_concrete_noun(
            item.term,
            item.hint,
            definition_en,
        ):
            image = ensure_noun_image(media_dir=media_dir, item=item)
        assets.extend(audio_assets)
        if image:
            assets.append(image)
        built = build_en_word_card(item.term, llm, dict_phonetic, audio_assets, image)
    elif item.mode == "ja_word":
        audio = ensure_japanese_audio(
            client=client,
            media_dir=media_dir,
            item=item,
            tts_model=tts_model,
            voice=tts_voice_ja,
        )
        if audio:
            assets.append(audio)
        built = build_ja_word_card(item.term, llm, audio)
    else:
        built = build_knowledge_card(item.mode, item.term, llm)

    final_tags = list(dict.fromkeys(built.tags + item.normalized_tags()))
    return BuiltCard(
        front=built.front,
        back=built.back,
        tags=final_tags,
        guid_seed=built.guid_seed,
    ), assets


def write_preview_json(path: Path, rows: List[BuiltCard]) -> None:
    payload = [
        {"front": r.front, "back": r.back, "tags": r.tags, "guid_seed": r.guid_seed}
        for r in rows
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def create_deck_apkg(
    deck_name: str,
    output_apkg: Path,
    cards: List[BuiltCard],
    media_files: Iterable[Path],
) -> None:
    deck_id = stable_anki_id(f"deck::{deck_name}")
    model_id = stable_anki_id("model::anki_batch_generator::basic_v2")

    model = genanki.Model(
        model_id=model_id,
        name="BatchAIGeneratedBasicModelV2",
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[
            {
                "name": "Card 1",
                "qfmt": "{{Front}}",
                "afmt": "{{FrontSide}}<hr id=\"answer\">{{Back}}",
            }
        ],
        css="""
.card {
  font-family: Arial, sans-serif;
  font-size: 20px;
  text-align: left;
  color: #111;
  background-color: #fff;
  line-height: 1.6;
}
ul {
  margin-top: 4px;
  margin-bottom: 8px;
}
""",
    )

    deck = genanki.Deck(deck_id, deck_name)
    for card in cards:
        note = genanki.Note(
            model=model,
            fields=[card.front, card.back],
            tags=card.tags,
            guid=stable_guid(deck_name, card.guid_seed),
        )
        deck.add_note(note)

    package = genanki.Package(deck)
    package.media_files = [str(p) for p in media_files]
    output_apkg.parent.mkdir(parents=True, exist_ok=True)
    package.write_to_file(str(output_apkg))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate or extend an Anki deck from a JSON array.")
    parser.add_argument("--mode", required=True, choices=sorted(SUPPORTED_MODES))
    parser.add_argument(
        "--terms-json",
        default="",
        help='JSON array string, e.g. ["apologise", "burgeon"]',
    )
    parser.add_argument(
        "--terms-file",
        default="",
        help=(
            "Path to a .json array or .txt (one term per line, # for comments). "
            "If omitted with empty --terms-json, uses terms.json next to this script, "
            "else terms.txt in the same folder."
        ),
    )
    parser.add_argument("--hint", default="", help="Optional shared hint applied to all items.")
    parser.add_argument("--tags", nargs="*", default=[], help="Optional extra tags.")
    parser.add_argument("--deck-name", required=True, help="Use the same deck name as the existing deck to extend it.")
    parser.add_argument("--output", default="anki_batch_output.apkg", help="Output .apkg path.")
    parser.add_argument("--preview-json", default="anki_batch_preview.json", help="Preview JSON path.")
    parser.add_argument("--cache-path", default="anki_batch_cache.json", help="LLM cache JSON path.")
    parser.add_argument("--media-dir", default="anki_media", help="Temporary folder for audio media files.")
    parser.add_argument("--model", default=DEFAULT_TEXT_MODEL, help="OpenAI text model.")
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL, help="OpenAI TTS model.")
    parser.add_argument(
        "--tts-voice-en",
        default="alloy",
        help=(
            "English TTS voice for single-word cards only when all UK dictionary MP3 URLs fail. "
            "Two-word expressions never use TTS (phrase or per-word UK clips only)."
        ),
    )
    parser.add_argument("--tts-voice-ja", default="alloy", help="Japanese TTS voice.")
    parser.add_argument("--reasoning-effort", default="medium", choices=["minimal", "low", "medium", "high"])
    parser.add_argument(
        "--openai-api-key",
        default="",
        help=(
            "OpenAI API key. If empty: uses OPENAI_API_KEY env, else first line of "
            f"{LOCAL_OPENAI_KEY_FILE.name} next to this script."
        ),
    )
    parser.add_argument(
        "--openai-base-url",
        default="",
        help=(
            "OpenAI-compatible API base URL, e.g. https://api.chatanywhere.tech "
            "(trailing /v1 added if missing). If empty, uses OPENAI_BASE_URL env, else official api.openai.com."
        ),
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP,
        help="Sleep seconds between items. Default is intentionally higher to reduce rate spikes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    api_key = resolve_openai_api_key(args.openai_api_key)
    if not api_key:
        print(
            "ERROR: OpenAI API key is missing. Set --openai-api-key, or OPENAI_API_KEY, or create "
            f"{LOCAL_OPENAI_KEY_FILE} (one line, not committed to git)."
        )
        return 1

    if not args.terms_json and not args.terms_file:
        if DEFAULT_TERMS_JSON.is_file():
            args.terms_file = str(DEFAULT_TERMS_JSON)
            print(f"Using default terms file: {args.terms_file}")
        elif DEFAULT_TERMS_TXT.is_file():
            args.terms_file = str(DEFAULT_TERMS_TXT)
            print(f"Using default terms file: {args.terms_file}")
        else:
            print(
                "ERROR: provide --terms-json or --terms-file, or create "
                f"{DEFAULT_TERMS_JSON} or {DEFAULT_TERMS_TXT} next to this script."
            )
            return 1

    try:
        items = load_items(args)
    except Exception as exc:
        print(f"ERROR: failed to parse input terms: {exc}")
        return 1

    if not items:
        print("ERROR: no valid input items.")
        return 1

    output_apkg = Path(args.output).expanduser().resolve()
    preview_json = Path(args.preview_json).expanduser().resolve()
    cache_path = Path(args.cache_path).expanduser().resolve()
    media_dir = Path(args.media_dir).expanduser().resolve()

    base_url = resolve_openai_base_url(args.openai_base_url)
    if base_url:
        print(f"Using OpenAI-compatible base_url: {base_url}")
        client = OpenAI(api_key=api_key, base_url=base_url)
    else:
        client = OpenAI(api_key=api_key)
    cache = CacheStore(cache_path)

    built_cards: List[BuiltCard] = []
    media_files: Dict[str, Path] = {}
    total = len(items)

    for i, item in enumerate(items, start=1):
        print(f"[{i}/{total}] Generating card: mode={item.mode}, term={item.term}")
        try:
            card, assets = build_card(
                client=client,
                item=item,
                model=args.model,
                tts_model=args.tts_model,
                media_dir=media_dir,
                tts_voice_en=args.tts_voice_en,
                tts_voice_ja=args.tts_voice_ja,
                cache=cache,
                reasoning_effort=args.reasoning_effort,
            )
            built_cards.append(card)
            for asset in assets:
                media_files[asset.filename] = asset.filepath
        except Exception as exc:
            print(f"[ERROR] Failed on '{item.term}' ({item.mode}): {exc}")
        time.sleep(max(0.0, args.sleep))

    cache.save()

    if not built_cards:
        print("ERROR: all items failed; no deck generated.")
        return 1

    write_preview_json(preview_json, built_cards)
    create_deck_apkg(
        deck_name=args.deck_name,
        output_apkg=output_apkg,
        cards=built_cards,
        media_files=media_files.values(),
    )

    print("\nDone.")
    print(f"- Cards generated: {len(built_cards)} / {total}")
    print(f"- Deck file: {output_apkg}")
    print(f"- Preview JSON: {preview_json}")
    print(f"- Media files: {len(media_files)}")
    print("- To extend an existing Anki deck, keep --deck-name the same as that deck and import the new .apkg.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

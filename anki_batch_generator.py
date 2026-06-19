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

# Bump when LLM JSON schema changes so disk cache does not reuse stale shapes.
LLM_SCHEMA_VERSION = "no_zh_v5_safe_ipa"

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


@dataclass
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
    parts = raw.split()
    requested_pos = normalize_pos_tag(parts[-1]) if parts else ""
    if requested_pos and len(parts) >= 2:
        return ParsedEnglishTerm(
            raw=raw,
            word=" ".join(parts[:-1]).strip(),
            requested_pos=requested_pos,
        )
    return ParsedEnglishTerm(raw=raw, word=strip_pos_labels_from_term(raw) or raw, requested_pos="")


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


def _dictionary_slug(term: str) -> str:
    clean = strip_pos_labels_from_term(term)
    return clean.strip().replace(" ", "-").lower()


def _normalize_headword(text: str) -> str:
    s = html.unescape(text or "").replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s.lower().strip())
    return s


def _headword_matches_term(headword: str, term: str) -> bool:
    query = _normalize_headword(strip_pos_labels_from_term(term))
    found = _normalize_headword(headword)
    if not query or not found:
        return False
    query_variants = {query, query.replace("-", " "), query.replace(" ", "")}
    found_variants = {found, found.replace("-", " "), found.replace(" ", "")}
    if query_variants & found_variants:
        return True
    return query.replace(" ", "-") == found.replace(" ", "-")


def is_cross_reference_definition(definition: str) -> bool:
    text = _normalize_headword(definition)
    if not text:
        return False
    patterns = (
        r"^past simple of\b",
        r"^past tense of\b",
        r"^past participle of\b",
        r"^plural of\b",
        r"^comparative of\b",
        r"^superlative of\b",
        r"^present participle of\b",
        r"^third person singular of\b",
        r"^see\b",
        r"^see also\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


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
            or (len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP")
        )
    except OSError:
        return False


IMAGE_IGNORE_KEYWORDS = {
    "ad",
    "ads",
    "1x1",
    "ad-banner",
    "ad_banner",
    "adserver",
    "analytics",
    "advert",
    "advertisement",
    "beacon",
    "blank",
    "banner-ad",
    "banner_ad",
    "banner",
    "cookie",
    "doubleclick",
    "favicon",
    "icon",
    "og-image",
    "googlesyndication",
    "icon-close",
    "lazy",
    "loader",
    "loading",
    "logo",
    "pixel",
    "placeholder",
    "spacer",
    "sprite",
    "transparent",
    "tracker",
    "tracking",
    "trackpixel",
}

DICTIONARY_IMAGE_RULES = {
    "dictionary.cambridge.org": ("/images/full/", "/images/thumb/"),
    "www.ldoceonline.com": ("/media/english/illustration/",),
    "www.oxfordlearnersdictionaries.com": (
        "/media/english/fullsize/",
        "/media/english/thumb/",
    ),
}


def _normalize_image_url(url: str, page_url: str) -> str:
    u = html.unescape((url or "").strip().strip("'\""))
    if not u:
        return ""
    if u.startswith("//"):
        return f"https:{u}"
    return urljoin(page_url, u)


def _is_candidate_dictionary_image_url(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    low = u.lower()
    if low.startswith("data:") or low.startswith("data:image") or "base64" in low or "svg" in low:
        return False
    parsed = urlparse(u)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.netloc or "").lower()
    path = unquote(parsed.path or "").lower()
    filename = Path(path).name
    stem = filename.rsplit(".", 1)[0]
    if not re.search(r"\.(?:jpe?g|png|webp)$", path):
        return False
    if any(keyword in host or keyword in path for keyword in IMAGE_IGNORE_KEYWORDS):
        return False
    if stem in {"ad", "ads", "close", "favicon", "icon", "logo", "pixel", "sprite"}:
        return False
    allowed_prefixes = DICTIONARY_IMAGE_RULES.get(host)
    if not allowed_prefixes:
        return False
    if not any(path.startswith(prefix) for prefix in allowed_prefixes):
        return False
    return True


def _extract_image_urls_from_html(body: str, page_url: str) -> List[str]:
    candidates: List[str] = []

    for img_match in re.finditer(r"<img\b[^>]*>", body or "", flags=re.IGNORECASE | re.DOTALL):
        tag = img_match.group(0)
        for attr in ("data-src", "data-original", "data-lazy-src", "src"):
            attr_match = re.search(
                rf'\b{attr}\s*=\s*["\']([^"\']+)["\']',
                tag,
                flags=re.IGNORECASE,
            )
            if attr_match:
                candidates.append(attr_match.group(1))
        for attr in ("data-srcset", "srcset"):
            srcset_match = re.search(
                rf'\b{attr}\s*=\s*["\']([^"\']+)["\']',
                tag,
                flags=re.IGNORECASE,
            )
            if not srcset_match:
                continue
            for part in srcset_match.group(1).split(","):
                src = part.strip().split(" ")[0]
                if src:
                    candidates.append(src)

    for url_match in re.finditer(
        r'https?://[^"\'<>\s]+\.(?:jpe?g|png|webp)(?:\?[^"\'<>\s]*)?',
        body or "",
        flags=re.IGNORECASE,
    ):
        candidates.append(url_match.group(0))
    for path_match in re.finditer(
        r'["\']((?:/[^"\'<>\s]+)?/(?:images|media)/[^"\'<>\s]+\.(?:jpe?g|png|webp)(?:\?[^"\']*)?)["\']',
        body or "",
        flags=re.IGNORECASE,
    ):
        candidates.append(path_match.group(1))

    seen = set()
    urls: List[str] = []
    for raw in candidates:
        url = _normalize_image_url(raw, page_url)
        if not _is_candidate_dictionary_image_url(url) or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _choose_image_url(candidates: List[str], preferred_markers: List[str]) -> str:
    if not candidates:
        return ""
    for marker in preferred_markers:
        for url in candidates:
            if marker in url.lower():
                return url
    return candidates[0]


def _clean_dictionary_text(raw: str) -> str:
    text = html.unescape(_strip_tags(raw or "")).replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_first_attr(tag: str, attr: str) -> str:
    match = re.search(
        rf'\b{attr}\s*=\s*["\']([^"\']+)["\']',
        tag or "",
        flags=re.IGNORECASE,
    )
    return html.unescape(match.group(1)).strip() if match else ""


def _extract_first_img_alt(block: str) -> str:
    match = re.search(r"<img\b[^>]*>", block or "", flags=re.IGNORECASE | re.DOTALL)
    return _extract_first_attr(match.group(0), "alt") if match else ""


def _first_dictionary_image_in_block(
    block: str,
    page_url: str,
    preferred_markers: List[str],
) -> str:
    candidates = _extract_image_urls_from_html(block, page_url)
    return _choose_image_url(candidates, preferred_markers)


def _first_match_text(block: str, patterns: List[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, block or "", flags=re.IGNORECASE | re.DOTALL)
        if match:
            text = _clean_dictionary_text(match.group(1))
            if text:
                return text
    return ""


def _all_match_texts(block: str, patterns: List[str], limit: int = 3) -> List[str]:
    values: List[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, block or "", flags=re.IGNORECASE | re.DOTALL):
            text = _clean_dictionary_text(match.group(1))
            if text and text not in values:
                values.append(text)
                if len(values) >= limit:
                    return values
    return values


def _find_nearest_pos_before(body: str, block_start: int, patterns: List[str]) -> str:
    scope = (body or "")[:block_start]
    pos = ""
    for pattern in patterns:
        for match in re.finditer(pattern, scope, flags=re.IGNORECASE | re.DOTALL):
            candidate = normalize_pos_tag(_clean_dictionary_text(match.group(1)))
            if candidate:
                pos = candidate
    return pos


PROVIDER_ORDER = ["cambridge", "oxford", "longman"]


def _attr_pattern(attr: str) -> str:
    return rf"\b{attr}\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>]+))"


def _find_attrs(html_text: str, attr: str) -> List[str]:
    values: List[str] = []
    for match in re.finditer(_attr_pattern(attr), html_text or "", flags=re.IGNORECASE):
        value = next((g for g in match.groups() if g), "")
        value = html.unescape(value).strip()
        if value:
            values.append(value)
    return values


def _dictionary_url(base_url: str, raw_url: str) -> str:
    raw = html.unescape((raw_url or "").strip().strip("'\""))
    if not raw:
        return ""
    if raw.startswith("//"):
        return f"https:{raw}"
    return urljoin(base_url, raw)


def _first_uk_audio_url(block: str, base_url: str, preferred_markers: List[str]) -> str:
    urls: List[str] = []
    for attr in ("data-src-mp3", "src"):
        for raw in _find_attrs(block, attr):
            url = _dictionary_url(base_url, raw)
            if url.lower().endswith(".mp3") and url not in urls:
                urls.append(url)
    for marker in preferred_markers:
        marker_low = marker.lower()
        for url in urls:
            if marker_low in url.lower():
                return url
    return urls[0] if urls else ""


def _first_ipa(block: str, patterns: List[str]) -> str:
    ipa = _first_match_text(block, patterns)
    return ipa.replace(" ", "")


def _entry_matches_requested_pos(actual_pos: str, requested_pos: str) -> bool:
    if not requested_pos:
        return True
    return normalize_pos_tag(actual_pos) == requested_pos


def _definition_is_usable(definition: str, requested_pos: str) -> bool:
    if not definition:
        return False
    if is_cross_reference_definition(definition):
        return False
    return True


def _finalize_entry_sources(entry: DictionaryEntryResult) -> DictionaryEntryResult:
    if entry.ipa_uk and not entry.ipa_source:
        entry.ipa_source = entry.source
    if entry.audio_uk_url and not entry.audio_source:
        entry.audio_source = entry.source
    if entry.definition and not entry.definition_source:
        entry.definition_source = entry.source
    if entry.image_url and not entry.image_source:
        entry.image_source = entry.source
    return entry


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


def fetch_dictionary_entries(word: str, requested_pos: str = "") -> List[DictionaryEntryResult]:
    entries: List[DictionaryEntryResult] = []
    providers = {
        "cambridge": _cambridge_entry_result,
        "oxford": _oxford_entry_result,
        "longman": _longman_entry_result,
    }
    for provider in PROVIDER_ORDER:
        entry = providers[provider](word, requested_pos)
        if entry:
            entries.append(entry)
    return entries


def merge_dictionary_entries(entries: List[DictionaryEntryResult]) -> DictionaryEntryResult:
    merged = DictionaryEntryResult()
    by_source = {entry.source: entry for entry in entries}
    for source in PROVIDER_ORDER:
        entry = by_source.get(source)
        if not entry:
            continue
        if not merged.word and entry.word:
            merged.word = entry.word
        if not merged.requested_pos and entry.requested_pos:
            merged.requested_pos = entry.requested_pos
        if not merged.actual_pos and entry.actual_pos:
            merged.actual_pos = entry.actual_pos
        if not merged.ipa_uk and entry.ipa_uk:
            merged.ipa_uk = entry.ipa_uk
            merged.ipa_source = source
        if not merged.audio_uk_url and entry.audio_uk_url:
            merged.audio_uk_url = entry.audio_uk_url
            merged.audio_source = source
        if not merged.definition and entry.definition:
            merged.definition = entry.definition
            merged.definition_source = source
            merged.sense_id = entry.sense_id
        if not merged.image_url and entry.image_url:
            merged.image_url = entry.image_url
            merged.image_alt = entry.image_alt
            merged.image_source = source
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


def fetch_cambridge_sense_candidates(term: str) -> List[DictionarySenseCandidate]:
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return []
    normalized = clean_term.strip().replace(" ", "-")
    url = f"https://dictionary.cambridge.org/dictionary/english/{quote(normalized)}"

    def _do_request() -> List[DictionarySenseCandidate]:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok or not _cambridge_final_url_matches_term(resp.url, clean_term):
            return []
        body = resp.text
        entry_scope = _extract_cambridge_entry_scope(body, clean_term)
        if not entry_scope:
            return []

        candidates: List[DictionarySenseCandidate] = []
        for index, match in enumerate(
            re.finditer(
                r'<div[^>]*class="[^"]*\bdef-block\b[^"]*"[^>]*>(.*?)(?=<div[^>]*class="[^"]*\bdef-block\b|<div[^>]*class="[^"]*\bpr\b|</article>|$)',
                entry_scope,
                flags=re.IGNORECASE | re.DOTALL,
            ),
            start=1,
        ):
            block = match.group(1)
            definition = _first_match_text(
                block,
                [r'<div[^>]*class="[^"]*\bdef\b[^"]*\bddef_d\b[^"]*"[^>]*>(.*?)</div>'],
            )
            if not definition:
                continue
            pos = _find_nearest_pos_before(
                entry_scope,
                match.start(),
                [r'<span[^>]*class="[^"]*\bpos\b[^"]*\bdpos\b[^"]*"[^>]*>(.*?)</span>'],
            )
            examples = _all_match_texts(
                block,
                [
                    r'<div[^>]*class="[^"]*\beg\b[^"]*\bdeg\b[^"]*"[^>]*>(.*?)</div>',
                    r'<span[^>]*class="[^"]*\beg\b[^"]*\bdeg\b[^"]*"[^>]*>(.*?)</span>',
                ],
            )
            image_url = _first_dictionary_image_in_block(
                block,
                resp.url,
                ["/images/full/", "/images/thumb/"],
            )
            candidates.append(
                DictionarySenseCandidate(
                    source="cambridge",
                    word=clean_term,
                    pos=pos,
                    sense_id=f"cambridge:{normalized}:{index}",
                    definition=definition,
                    image_url=image_url,
                    image_alt=_extract_first_img_alt(block),
                    examples=examples,
                )
            )
        return candidates

    try:
        return retry_call(_do_request, retries=2, base_sleep=1.0)
    except Exception:
        return []


def fetch_oxford_sense_candidates(term: str) -> List[DictionarySenseCandidate]:
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return []
    normalized = clean_term.strip().replace(" ", "-")
    url = f"https://www.oxfordlearnersdictionaries.com/definition/english/{quote(normalized)}"

    def _do_request() -> List[DictionarySenseCandidate]:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok:
            return []
        body = resp.text
        if _oxford_page_is_misspelling(body):
            return []
        headword = _extract_oxford_headword(body)
        if headword and not _headword_matches_term(headword, clean_term):
            return []

        candidates: List[DictionarySenseCandidate] = []
        for index, match in enumerate(
            re.finditer(
                r'<li[^>]*class="[^"]*\bsense\b[^"]*"[^>]*>(.*?)(?=<li[^>]*class="[^"]*\bsense\b|<span[^>]*class="[^"]*\bid-g\b|</ol>|$)',
                body,
                flags=re.IGNORECASE | re.DOTALL,
            ),
            start=1,
        ):
            block = match.group(1)
            definition = _first_match_text(
                block,
                [r'<span[^>]*class="[^"]*\bdef\b[^"]*"[^>]*>(.*?)</span>'],
            )
            if not definition:
                continue
            pos = _find_nearest_pos_before(
                body,
                match.start(),
                [r'<span[^>]*class="[^"]*\bpos\b[^"]*"[^>]*>(.*?)</span>'],
            )
            examples = _all_match_texts(
                block,
                [r'<span[^>]*class="[^"]*\bx\b[^"]*"[^>]*>(.*?)</span>'],
            )
            image_url = _first_dictionary_image_in_block(
                block,
                resp.url,
                ["/media/english/fullsize/", "/media/english/thumb/"],
            )
            candidates.append(
                DictionarySenseCandidate(
                    source="oxford",
                    word=headword or clean_term,
                    pos=pos,
                    sense_id=f"oxford:{normalized}:{index}",
                    definition=definition,
                    image_url=image_url,
                    image_alt=_extract_first_img_alt(block),
                    examples=examples,
                )
            )
        return candidates

    try:
        return retry_call(_do_request, retries=2, base_sleep=1.0)
    except Exception:
        return []


def fetch_longman_sense_candidates(term: str) -> List[DictionarySenseCandidate]:
    clean_term = strip_pos_labels_from_term(term)
    if not clean_term:
        return []
    normalized = clean_term.strip().replace(" ", "-")
    url = f"https://www.ldoceonline.com/dictionary/{quote(normalized)}"

    def _do_request() -> List[DictionarySenseCandidate]:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok or "/spellcheck/" in (resp.url or "").lower():
            return []
        body = resp.text
        headword_match = re.search(r"<h1[^>]*>([^<]+)</h1>", body, flags=re.IGNORECASE)
        headword = _strip_tags(html.unescape(headword_match.group(1))) if headword_match else ""
        if not headword or not _headword_matches_term(headword, clean_term):
            return []

        candidates: List[DictionarySenseCandidate] = []
        for index, match in enumerate(
            re.finditer(
                r'<span[^>]*class="[^"]*\bSense\b[^"]*"[^>]*>(.*?)(?=<span[^>]*class="[^"]*\bSense\b|<span[^>]*class="[^"]*\bEntry\b|$)',
                body,
                flags=re.IGNORECASE | re.DOTALL,
            ),
            start=1,
        ):
            block = match.group(1)
            definition = _first_match_text(
                block,
                [r'<span[^>]*class="[^"]*\bDEF\b[^"]*"[^>]*>(.*?)</span>'],
            )
            if not definition:
                continue
            pos = _find_nearest_pos_before(
                body,
                match.start(),
                [r'<span[^>]*class="[^"]*\bPOS\b[^"]*"[^>]*>(.*?)</span>'],
            )
            examples = _all_match_texts(
                block,
                [r'<span[^>]*class="[^"]*\bEXAMPLE\b[^"]*"[^>]*>(.*?)</span>'],
            )
            image_url = _first_dictionary_image_in_block(
                block,
                resp.url,
                ["/media/english/illustration/"],
            )
            candidates.append(
                DictionarySenseCandidate(
                    source="longman",
                    word=headword or clean_term,
                    pos=pos,
                    sense_id=f"longman:{normalized}:{index}",
                    definition=definition,
                    image_url=image_url,
                    image_alt=_extract_first_img_alt(block),
                    examples=examples,
                )
            )
        return candidates

    try:
        return retry_call(_do_request, retries=2, base_sleep=1.0)
    except Exception:
        return []


def fetch_best_dictionary_sense(term: str) -> Optional[DictionarySenseCandidate]:
    for fetcher in (
        fetch_cambridge_sense_candidates,
        fetch_oxford_sense_candidates,
        fetch_longman_sense_candidates,
    ):
        for candidate in fetcher(term):
            if (candidate.definition or "").strip():
                return candidate
    return None


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


def fetch_dictionary_image_url(term: str) -> str:
    for fetcher in (
        fetch_cambridge_image_url,
        fetch_longman_image_url,
        fetch_oxford_image_url,
    ):
        image_url = fetcher(term)
        if image_url:
            return image_url
    return ""


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


def ensure_noun_image(
    media_dir: Path,
    item: InputItem,
    preferred_image_url: str = "",
) -> Optional[AudioAsset]:
    clean_term = strip_pos_labels_from_term(item.term) or item.term.strip()
    if not clean_term:
        return None
    image_url = (preferred_image_url or "").strip()
    if image_url and not _is_candidate_dictionary_image_url(image_url):
        image_url = ""
    if not image_url:
        image_url = fetch_dictionary_image_url(clean_term)
    if not image_url:
        return None
    ext = ".jpg"
    low = image_url.lower()
    if ".png" in low:
        ext = ".png"
    elif ".webp" in low:
        ext = ".webp"
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
        "- Only provide pronunciation_text when `lexical_word_count` is 1 (see user payload).\n"
        "- If `lexical_word_count` is 2 or more, pronunciation_text must be \"\".\n"
        "- Examples: 'savvy' -> IPA; 'play hooky' -> \"\"; 'everything in moderation' -> \"\".\n\n"

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
        "IPA from your own knowledge only for single-word terms (`lexical_word_count` is 1). "
        "For multi-word terms, pronunciation_text must be \"\".\n\n"

        "ACCURACY:\n"
        "- If `phonetic_hint` is non-empty and `lexical_word_count` is 1, it overrides your own "
        "guess for `pronunciation_text`.\n"
        "- Never invent or copy IPA for multi-word terms.\n"
        "- If no `phonetic_hint`, output British English (BrE) IPA style.\n"
        "- Ignore POS labels such as noun/verb/adj in pronunciation.\n"
        "- For en_word, `example_simple_en` must match `requested_pos` and `dictionary_definition` "
        "when provided.\n"
        "- If `requested_pos` is noun, use the word as a noun; if verb, use it as a verb; if adjective "
        "or adverb, use that part of speech.\n"
        "- Do not generate an example for a different part of speech.\n"
        "- If `dictionary_definition` is non-empty, you may leave `definition_en` empty or repeat that "
        "definition; the final card will use the dictionary definition.\n"
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
    dictionary_definition: str = "",
    requested_pos: str = "",
    parsed_word: str = "",
) -> str:
    """Variable inputs only; mode-specific rules live in PROMPT_MAP system prompts."""
    return (
        "Fill the JSON fields described in your system instructions using this input.\n\n"
        f"raw_term: {item.term}\n"
        f"word: {parsed_word or term_for_pronunciation or item.term}\n"
        f"requested_pos: {requested_pos or '(none)'}\n"
        f"dictionary_definition: {dictionary_definition or '(none)'}\n"
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
    dictionary_definition: str = "",
    requested_pos: str = "",
    parsed_word: str = "",
) -> Dict:
    system_prompt = PROMPT_MAP[item.mode]()
    user_prompt = build_user_payload(
        item,
        phonetic_hint,
        term_for_pronunciation,
        phonetic_source,
        dictionary_definition=dictionary_definition,
        requested_pos=requested_pos,
        parsed_word=parsed_word,
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
    if is_two_word_term(term) or lexical_word_count(term) >= 3:
        ipa = (dict_phonetic or "").strip()
    else:
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
        guid_seed=f"en_word::{term.strip()}",
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
    dictionary_entry = DictionaryEntryResult()
    parsed_en = ParsedEnglishTerm(raw=item.term.strip(), word=item.term.strip(), requested_pos="")
    spoken_term = item.term.strip()
    if item.mode == "en_word":
        parsed_en = parse_english_term(item.term)
        spoken_term = parsed_en.word or item.term.strip()
        if en_word_uses_dictionary_lookup(spoken_term):
            dictionary_entry = merge_dictionary_entries(
                fetch_dictionary_entries(spoken_term, parsed_en.requested_pos)
            )
            dict_phonetic = dictionary_entry.ipa_uk
            dict_phonetic_source = (dictionary_entry.ipa_source or "").strip()
            dict_audio = dictionary_entry.audio_uk_url
            dict_audio_fallbacks = []
            dict_pos_tags = [
                p
                for p in (parsed_en.requested_pos, dictionary_entry.actual_pos)
                if p
            ]

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
                "parsed_word": parsed_en.word if item.mode == "en_word" else "",
                "requested_pos": parsed_en.requested_pos if item.mode == "en_word" else "",
                "dictionary_definition": dictionary_entry.definition if item.mode == "en_word" else "",
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
            dictionary_definition=dictionary_entry.definition if item.mode == "en_word" else "",
            requested_pos=parsed_en.requested_pos if item.mode == "en_word" else "",
            parsed_word=parsed_en.word if item.mode == "en_word" else "",
        )
        cache.set(cache_key, llm)

    if item.mode == "en_word" and dictionary_entry.definition:
        llm = dict(llm)
        llm["definition_en"] = dictionary_entry.definition

    assets: List[AudioAsset] = []
    if item.mode == "en_word":
        audio_assets: List[AudioAsset] = []
        if is_single_word_term(spoken_term):
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
        if dictionary_entry.image_url:
            image = ensure_noun_image(
                media_dir=media_dir,
                item=item,
                preferred_image_url=dictionary_entry.image_url,
            )
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


def write_dict_preview_json(path: Path, payload: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_dictionary_test_only(items: List[InputItem], preview_json: Path) -> None:
    payload = [dictionary_result_preview(item.term) for item in items if item.mode == "en_word"]
    if preview_json:
        write_dict_preview_json(preview_json, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_self_test() -> None:
    parsed = parse_english_term("pin noun")
    assert parsed.raw == "pin noun"
    assert parsed.word == "pin"
    assert parsed.requested_pos == "noun"

    parsed = parse_english_term("pin verb")
    assert parsed.raw == "pin verb"
    assert parsed.word == "pin"
    assert parsed.requested_pos == "verb"

    parsed = parse_english_term("rose")
    assert parsed.raw == "rose"
    assert parsed.word == "rose"
    assert parsed.requested_pos == ""

    assert _normalize_url("/media/english/uk_pron/u/ukr/ukroo/ukrooke025.mp3") == (
        "https://dictionary.cambridge.org/media/english/uk_pron/u/ukr/ukroo/ukrooke025.mp3"
    )
    assert _is_candidate_dictionary_image_url(
        "https://dictionary.cambridge.org/images/full/rose_noun_002_32331.jpg"
    )
    assert not _is_candidate_dictionary_image_url(
        "https://dictionary.cambridge.org/external/images/og-image.png"
    )
    assert not _is_candidate_dictionary_image_url(
        "https://www.ldoceonline.com/external/images/logo.svg"
    )
    assert not _is_candidate_dictionary_image_url(
        "https://www.ldoceonline.com/media/english/illustration/banner_ad.jpg"
    )
    assert is_cross_reference_definition("past simple of rise")


def create_deck_apkg(
    deck_name: str,
    output_apkg: Path,
    cards: List[BuiltCard],
    media_files: Iterable[Path],
) -> None:
    if genanki is None:
        raise RuntimeError("genanki is not installed; install project requirements to generate .apkg files.")
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
    parser.add_argument("--mode", default="", choices=[""] + sorted(SUPPORTED_MODES))
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
    parser.add_argument("--deck-name", default="", help="Use the same deck name as the existing deck to extend it.")
    parser.add_argument("--output", default="anki_batch_output.apkg", help="Output .apkg path.")
    parser.add_argument("--preview-json", default="anki_batch_preview.json", help="Preview JSON path.")
    parser.add_argument("--cache-path", default="anki_batch_cache.json", help="LLM cache JSON path.")
    parser.add_argument("--media-dir", default="anki_media", help="Temporary folder for audio media files.")
    parser.add_argument("--model", default=DEFAULT_TEXT_MODEL, help="OpenAI text model.")
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL, help="OpenAI TTS model.")
    parser.add_argument(
        "--tts-voice-en",
        default="alloy",
        help="English TTS voice for single-word cards when all UK dictionary MP3 URLs fail.",
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
    parser.add_argument(
        "--dict-test-only",
        action="store_true",
        help="Only fetch and preview English dictionary fields; do not call OpenAI or generate an .apkg.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run lightweight pure-function self-tests and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.self_test:
        run_self_test()
        print("Self-test passed.")
        return 0

    if not args.mode:
        print("ERROR: --mode is required unless --self-test is used.")
        return 1
    if not args.deck_name:
        print("ERROR: --deck-name is required unless --self-test is used.")
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

    if args.dict_test_only:
        if args.mode != "en_word":
            print("ERROR: --dict-test-only is only supported with --mode en_word.")
            return 1
        run_dictionary_test_only(items, preview_json)
        print(f"\nDictionary preview JSON: {preview_json}")
        return 0

    api_key = resolve_openai_api_key(args.openai_api_key)
    if not api_key:
        print(
            "ERROR: OpenAI API key is missing. Set --openai-api-key, or OPENAI_API_KEY, or create "
            f"{LOCAL_OPENAI_KEY_FILE} (one line, not committed to git)."
        )
        return 1
    if OpenAI is None:
        print("ERROR: openai package is not installed. Install project requirements to generate cards.")
        return 1

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

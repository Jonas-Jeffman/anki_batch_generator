#!/usr/bin/env python3
from __future__ import annotations

from common import *

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

    # 图片只允许 Cambridge。
    if host != "dictionary.cambridge.org":
        return False

    if not re.search(r"\.(?:jpe?g|png|webp)$", path):
        return False

    # Cambridge 词典图片路径，只接受 full / thumb。
    allowed_prefixes = (
        "/images/full/",
        "/images/thumb/",
    )
    if not any(path.startswith(prefix) for prefix in allowed_prefixes):
        return False

    # 明显不是词条图片的文件名。
    blocked_exact_stems = {
        "ad",
        "ads",
        "blank",
        "close",
        "favicon",
        "icon",
        "logo",
        "pixel",
        "placeholder",
        "sprite",
        "transparent",
    }
    if stem in blocked_exact_stems:
        return False

    blocked_keywords = {
        "ad-banner",
        "ad_banner",
        "adserver",
        "analytics",
        "advert",
        "advertisement",
        "beacon",
        "banner-ad",
        "banner_ad",
        "cookie",
        "doubleclick",
        "googlesyndication",
        "icon-close",
        "loader",
        "loading",
        "og-image",
        "spacer",
        "tracker",
        "tracking",
        "trackpixel",
    }
    if any(keyword in host or keyword in path for keyword in blocked_keywords):
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
        # 图片只接受 Cambridge，避免 Oxford / Longman 抓到错误图或占位图。
        if (
            source == "cambridge"
            and not merged.image_url
            and entry.image_url
            and _is_candidate_dictionary_image_url(entry.image_url)
        ):
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

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import List
from urllib.parse import unquote, urljoin, urlparse

from anki_generator.inputs.english import normalize_pos_tag, strip_pos_labels_from_term
from anki_generator.models import DictionaryEntryResult


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


# Public names for consumers outside the dictionary package.  Private aliases
# remain available through dictionary_sources.py during the compatibility phase.
normalize_dictionary_url = _normalize_url
is_plausible_dictionary_image = _is_plausible_image
is_candidate_dictionary_image_url = _is_candidate_dictionary_image_url

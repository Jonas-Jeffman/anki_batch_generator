from __future__ import annotations

import gzip
import hashlib
import html
import re
from pathlib import Path
from typing import Dict, List

from tests.support import FIXTURE_ROOT


FIXTURE_DIR = FIXTURE_ROOT / "dictionary_pages"


def read_page(term: str, provider: str) -> str:
    path = FIXTURE_DIR / f"{term}_{provider}.md.gz"
    with gzip.open(path, "rt", encoding="utf-8", errors="strict") as handle:
        return handle.read()


def clean(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip(" :")


def fixture_metadata(term: str, provider: str) -> Dict:
    path = FIXTURE_DIR / f"{term}_{provider}.md.gz"
    body = read_page(term, provider)
    return {
        "fixture": path.name,
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "html_bytes": len(body.encode("utf-8")),
    }


def cambridge_snapshot(term: str) -> Dict:
    body = read_page(term, "cambridge")
    dataset_matches = list(
        re.finditer(
            r'<div class="[^"]*\bdictionary\b[^"]*"[^>]*data-id="([^"]+)"',
            body,
            flags=re.IGNORECASE,
        )
    )
    datasets = [match.group(1) for match in dataset_matches]
    start = next(match.start() for match in dataset_matches if match.group(1) == "cald4")
    later = [match.start() for match in dataset_matches if match.start() > start]
    cald = body[start:min(later) if later else len(body)]
    starts = list(
        re.finditer(
            r'<div[^>]*class="[^"]*\bdef-block\b[^"]*"[^>]*data-wl-senseid="([^"]+)"[^>]*>',
            cald,
            flags=re.IGNORECASE,
        )
    )
    senses: List[Dict] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(cald)
        segment = cald[match.start():end]
        before = cald[:match.start()]
        pos_matches = list(
            re.finditer(
                r'<span[^>]*class="[^"]*\bpos\b[^\"]*\bdpos\b[^\"]*"[^>]*>(.*?)</span>',
                before,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        definition = re.search(
            r'<div[^>]*class="[^"]*\bdef\b[^\"]*\bddef_d\b[^\"]*"[^>]*>(.*?)</div>',
            segment,
            flags=re.IGNORECASE | re.DOTALL,
        )
        examples = [
            clean(value)
            for value in re.findall(
                r'<span[^>]*class="[^"]*\beg\b[^\"]*\bdeg\b[^\"]*"[^>]*>(.*?)</span>',
                segment,
                flags=re.IGNORECASE | re.DOTALL,
            )
        ]
        full_image = re.search(r"src:\s*'([^']*/images/full/[^']+)'", segment)
        thumb_image = re.search(r'(?:src|\"src\")[:=]\s*["\']([^"\']*/images/thumb/[^"\']+)', segment)
        image = full_image.group(1) if full_image else (thumb_image.group(1) if thumb_image else "")
        senses.append(
            {
                "native_id": match.group(1),
                "pos": clean(pos_matches[-1].group(1)) if pos_matches else "",
                "definition": clean(definition.group(1)) if definition else "",
                "examples": examples,
                "image_path": html.unescape(image),
                "image_dom_relation": "before_next_def_block" if image else "none",
            }
        )
    return {"dataset_ids": datasets, "cald_senses": senses}


def _longman_entry_ranges(body: str):
    matches = list(
        re.finditer(
            r'<span[^>]*class="([^"]*\b(?:ldoceEntry|bussdictEntry)\b[^\"]*)"[^>]*>',
            body,
            flags=re.IGNORECASE,
        )
    )
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        yield match.group(1), body[match.start():end]


def longman_snapshot(term: str) -> Dict:
    body = read_page(term, "longman")
    entries = []
    for classes, entry in _longman_entry_ranges(body):
        entry_type = "business" if "bussdictEntry" in classes else "ldoce"
        headword = re.search(r'<span[^>]*class="[^"]*\bHWD\b[^\"]*"[^>]*>(.*?)</span>', entry, re.I | re.S)
        pos = re.search(r'<span[^>]*class="[^"]*\bPOS\b[^\"]*"[^>]*>(.*?)</span>', entry, re.I | re.S)
        sense_starts = list(re.finditer(r'<span[^>]*class="[^"]*\bSense\b[^\"]*"[^>]*id="([^"]+)"[^>]*>', entry, re.I))
        senses = []
        cross_reference_ids = []
        other_sense_ids = []
        for index, match in enumerate(sense_starts):
            end = sense_starts[index + 1].start() if index + 1 < len(sense_starts) else len(entry)
            segment = entry[match.start():end]
            definition = re.search(r'<span[^>]*class="[^"]*\bDEF\b[^\"]*"[^>]*>(.*?)</span>', segment, re.I | re.S)
            examples = []
            for example in re.finditer(r'<span[^>]*class="[^"]*\bEXAMPLE\b[^\"]*"[^>]*>(.*?)(?=</span>\s*(?:</span>|<span class="(?:EXAMPLE|Sense|GramExa|ColloExa))|$)', segment, re.I | re.S):
                fragment = example.group(1)
                audio = re.search(r'data-src-mp3="([^"]*/exaProns/[^"]+)"', fragment, re.I)
                examples.append({"text": clean(fragment), "audio_url": html.unescape(audio.group(1)) if audio else ""})
            if not definition:
                target = cross_reference_ids if re.search(r'class="[^"]*\bCrossref\b', segment, re.I) else other_sense_ids
                target.append(match.group(1))
                continue
            senses.append(
                {
                    "native_id": match.group(1),
                    "definition": clean(definition.group(1)),
                    "examples": examples,
                }
            )
        entries.append(
            {
                "entry_type": entry_type,
                "headword": clean(headword.group(1)) if headword else "",
                "pos": clean(pos.group(1)) if pos else "",
                "senses": senses,
                "cross_reference_ids": cross_reference_ids,
                "other_sense_ids": other_sense_ids,
            }
        )
    return {"entries": [entry for entry in entries if entry["headword"]]}


def oxford_snapshot(term: str) -> Dict:
    body = read_page(term, "oxford")
    headword = re.search(r'<h1[^>]*class="[^"]*\bheadword\b[^\"]*"[^>]*>(.*?)</h1>', body, re.I | re.S)
    pos = re.search(r'<span[^>]*class="pos"[^>]*>(.*?)</span>', body, re.I | re.S)
    br = re.search(
        r'<div[^>]*class="[^"]*\bphons_br\b[^\"]*"[^>]*>.*?data-src-mp3="([^"]+)".*?<span[^>]*class="phon"[^>]*>(.*?)</span>',
        body,
        re.I | re.S,
    )
    idiom_start = body.find('class="idioms"')
    lexical_region = body[:idiom_start] if idiom_start >= 0 else body
    lexical = []
    for match in re.finditer(r'<li[^>]*class="sense"[^>]*id="([^"]+)"[^>]*>(.*?)(?=<li[^>]*class="sense"|</ol>)', lexical_region, re.I | re.S):
        definition = re.search(r'<span[^>]*class="def"[^>]*>(.*?)</span>', match.group(2), re.I | re.S)
        if definition:
            lexical.append({"native_id": match.group(1), "definition": clean(definition.group(1))})
    idioms = re.findall(r'<span[^>]*class="idm"[^>]*id="([^"]+)"', body, re.I)
    return {
        "headword": clean(headword.group(1)) if headword else "",
        "pos": clean(pos.group(1)) if pos else "",
        "uk_ipa": clean(br.group(2)) if br else "",
        "uk_audio_url": html.unescape(br.group(1)) if br else "",
        "lexical_senses": lexical,
        "idiom_ids": idioms,
    }


def build_snapshot() -> Dict:
    result = {"fixtures": [], "pages": {}}
    for term in ("nail", "trunk"):
        result["pages"][term] = {
            "cambridge": cambridge_snapshot(term),
            "longman": longman_snapshot(term),
            "oxford": oxford_snapshot(term),
        }
        for provider in ("cambridge", "longman", "oxford"):
            result["fixtures"].append(fixture_metadata(term, provider))
    return result

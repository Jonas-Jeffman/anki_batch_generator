from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from canonical_store import canonical_guid_seed
from dictionary.service import dictionary_result_preview
from models import BuiltCard, InputItem, ResolvedCanonicalContent


SENSE_PREVIEW_SCHEMA_VERSION = "sense_aware_v1"


def _field_preview(field) -> Dict[str, str]:
    return {
        "value": field.value,
        "source": field.source,
        "sense_id": field.sense_id,
    }


def write_preview_json(
    path: Path,
    rows: List[BuiltCard],
    resolved_contents: Optional[List[ResolvedCanonicalContent]] = None,
) -> None:
    legacy_rows = [
        {"front": r.front, "back": r.back, "tags": r.tags, "guid_seed": r.guid_seed}
        for r in rows
    ]
    if resolved_contents is None:
        payload = legacy_rows
    else:
        by_guid_seed = {
            canonical_guid_seed(content.canonical_key): content
            for content in resolved_contents
        }
        cards = []
        for card, legacy in zip(rows, legacy_rows):
            content = by_guid_seed.get(card.guid_seed)
            if content is None:
                cards.append({**legacy, "sense": None, "content": None})
                continue
            cards.append(
                {
                    **legacy,
                    "sense": {
                        "word": content.word,
                        "pos": content.pos,
                        "index": content.index,
                        "canonical_key": content.canonical_key,
                        "status": content.status,
                    },
                    "content": {
                        "definition": _field_preview(content.definition),
                        "example": _field_preview(content.example),
                        "example_audio": _field_preview(content.example_audio),
                        "ipa": _field_preview(content.ipa),
                        "word_audio_urls": [
                            _field_preview(field)
                            for field in content.word_audio_urls
                        ],
                        "image": _field_preview(content.image),
                        "image_alt": content.image_alt,
                    },
                }
            )
        payload = {
            "schema_version": SENSE_PREVIEW_SCHEMA_VERSION,
            "card_count": len(cards),
            "cards": cards,
        }
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


__all__ = [
    "SENSE_PREVIEW_SCHEMA_VERSION",
    "write_preview_json",
    "write_dict_preview_json",
    "run_dictionary_test_only",
]

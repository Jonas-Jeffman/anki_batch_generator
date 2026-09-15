from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional


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


def build_cache_key(
    *,
    mode: str,
    term: str,
    hint: str,
    model: str,
    llm_schema: str,
    dict_phonetic: str,
    dict_phonetic_source: str,
    parsed_word: str,
    requested_pos: str,
    dictionary_definition: str,
) -> str:
    return hashlib.sha1(
        json.dumps(
            {
                "mode": mode,
                "term": term,
                "hint": hint,
                "model": model,
                "llm_schema": llm_schema,
                "dict_phonetic": dict_phonetic,
                "dict_phonetic_source": dict_phonetic_source,
                "parsed_word": parsed_word,
                "requested_pos": requested_pos,
                "dictionary_definition": dictionary_definition,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def build_alignment_cache_key(
    *,
    word: str,
    pos: str,
    model: str,
    schema_version: str,
    senses: List[Dict[str, str]],
) -> str:
    normalized_senses = sorted(
        (
            {
                "source": str(sense.get("source", "")),
                "sense_id": str(sense.get("sense_id", "")),
                "definition": str(sense.get("definition", "")),
            }
            for sense in senses
        ),
        key=lambda sense: (sense["source"], sense["sense_id"]),
    )
    return hashlib.sha1(
        json.dumps(
            {
                "kind": "sense_alignment",
                "word": word,
                "pos": pos,
                "model": model,
                "schema_version": schema_version,
                "senses": normalized_senses,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

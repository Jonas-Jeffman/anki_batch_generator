from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Dict, List, Sequence

from anki_generator.inputs.english import normalize_pos_tag
from anki_generator.models import CanonicalManifestEntry, CanonicalSense, ProviderSense


MANIFEST_VERSION = 1
ACTIVE = "active"
INACTIVE = "inactive"
VALID_STATUSES = {ACTIVE, INACTIVE}
PROVIDERS = ("cambridge", "longman", "oxford")


def definition_fingerprint(definition: str) -> str:
    normalized = re.sub(r"[^\w]+", " ", definition.casefold(), flags=re.UNICODE)
    normalized = " ".join(normalized.split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def build_canonical_key(
    word: str,
    pos: str,
    fingerprint: str,
    identity_hint: str = "",
) -> str:
    identity = json.dumps(
        {
            "word": " ".join(word.casefold().split()),
            "pos": normalize_pos_tag(pos),
            "fingerprint": fingerprint,
            "identity_hint": identity_hint,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"canonical::{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:20]}"


def canonical_guid_seed(canonical_key: str) -> str:
    return f"en_word::{canonical_key}"


class CanonicalStore:
    """Persistent English sense identities; separate from disposable LLM caches."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: List[CanonicalManifestEntry] = []
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("version") != MANIFEST_VERSION or not isinstance(
                    raw.get("entries"), list
                ):
                    return
                self.entries = [self._entry_from_dict(item) for item in raw["entries"]]
                self._validate_entries(self.entries)
            except Exception:
                self.entries = []

    @staticmethod
    def _entry_from_dict(value: Dict) -> CanonicalManifestEntry:
        if set(value) != {
            "canonical_key",
            "word",
            "pos",
            "index",
            "native_ids",
            "fingerprint",
            "status",
        }:
            raise ValueError("invalid canonical manifest entry")
        native_ids = value["native_ids"]
        if not isinstance(native_ids, dict) or set(native_ids) != set(PROVIDERS):
            raise ValueError("invalid canonical native IDs")
        if any(
            not isinstance(native_ids[source], list)
            or not all(isinstance(item, str) and item for item in native_ids[source])
            for source in PROVIDERS
        ):
            raise ValueError("invalid canonical native ID list")
        return CanonicalManifestEntry(
            canonical_key=str(value["canonical_key"]),
            word=str(value["word"]),
            pos=str(value["pos"]),
            index=int(value["index"]),
            native_ids={
                source: [str(item) for item in native_ids[source]]
                for source in PROVIDERS
            },
            fingerprint=str(value["fingerprint"]),
            status=str(value["status"]),
        )

    @staticmethod
    def _validate_entries(entries: Sequence[CanonicalManifestEntry]) -> None:
        keys = set()
        indexes = set()
        for entry in entries:
            scope_index = (entry.word.casefold(), entry.pos, entry.index)
            if (
                not entry.canonical_key
                or not entry.word
                or normalize_pos_tag(entry.pos) != entry.pos
                or entry.index < 1
                or not entry.fingerprint
                or entry.status not in VALID_STATUSES
                or any(len(ids) != len(set(ids)) for ids in entry.native_ids.values())
                or entry.canonical_key in keys
                or scope_index in indexes
            ):
                raise ValueError("invalid or duplicate canonical manifest identity")
            keys.add(entry.canonical_key)
            indexes.add(scope_index)

    def save(self) -> None:
        self._validate_entries(self.entries)
        payload = {
            "version": MANIFEST_VERSION,
            "entries": [
                asdict(entry)
                for entry in sorted(
                    self.entries,
                    key=lambda item: (item.word.casefold(), item.pos, item.index),
                )
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def reconcile(
        self,
        *,
        word: str,
        pos: str,
        canonical_senses: Sequence[CanonicalSense],
        provider_senses: Sequence[ProviderSense],
    ) -> List[CanonicalSense]:
        normalized_pos = normalize_pos_tag(pos)
        if not normalized_pos:
            raise ValueError("canonical manifest requires a supported POS")
        scoped = [
            entry
            for entry in self.entries
            if entry.word.casefold() == word.casefold() and entry.pos == normalized_pos
        ]
        definitions = {
            (sense.source, sense.native_id): sense.definition
            for sense in provider_senses
            if normalize_pos_tag(sense.pos) == normalized_pos
        }
        current = []
        for sense in canonical_senses:
            definition = definitions.get((sense.backbone_source, sense.backbone_sense_id), "")
            fingerprint = definition_fingerprint(definition)
            if not definition.strip():
                raise ValueError("canonical senses require non-empty backbone definitions")
            current.append((sense, fingerprint, self._native_ids(sense)))

        matched_keys = set()
        output = []
        next_index = max((entry.index for entry in scoped), default=0) + 1
        for sense, fingerprint, native_ids in current:
            match = self._find_match(scoped, native_ids, fingerprint, matched_keys)
            if match is None:
                key = build_canonical_key(
                    word,
                    normalized_pos,
                    fingerprint,
                    f"{sense.backbone_source}:{sense.backbone_sense_id}",
                )
                if any(entry.canonical_key == key for entry in self.entries):
                    raise ValueError("canonical key collision")
                match = CanonicalManifestEntry(
                    canonical_key=key,
                    word=word,
                    pos=normalized_pos,
                    index=next_index,
                    native_ids=native_ids,
                    fingerprint=fingerprint,
                )
                self.entries.append(match)
                scoped.append(match)
                next_index += 1
            else:
                match.native_ids = {
                    source: list(dict.fromkeys(match.native_ids[source] + native_ids[source]))
                    for source in PROVIDERS
                }
                match.status = ACTIVE
            matched_keys.add(match.canonical_key)
            output.append(
                replace(
                    sense,
                    index=match.index,
                    canonical_key=match.canonical_key,
                    fingerprint=match.fingerprint,
                    status=ACTIVE,
                )
            )

        for entry in scoped:
            if entry.canonical_key not in matched_keys:
                entry.status = INACTIVE
        return sorted(output, key=lambda sense: sense.index)

    @staticmethod
    def _native_ids(sense: CanonicalSense) -> Dict[str, List[str]]:
        values = {
            "cambridge": [sense.cambridge_sense_id] if sense.cambridge_sense_id else [],
            "longman": list(sense.longman_sense_ids),
            "oxford": list(sense.oxford_sense_ids),
        }
        if sense.backbone_sense_id and sense.backbone_source in values:
            values[sense.backbone_source].append(sense.backbone_sense_id)
        return {source: list(dict.fromkeys(values[source])) for source in PROVIDERS}

    @staticmethod
    def _find_match(
        entries: Sequence[CanonicalManifestEntry],
        native_ids: Dict[str, List[str]],
        fingerprint: str,
        matched_keys: set,
    ) -> CanonicalManifestEntry | None:
        native_matches = [
            entry
            for entry in entries
            if entry.canonical_key not in matched_keys
            and any(
                set(entry.native_ids[source]).intersection(native_ids[source])
                for source in PROVIDERS
            )
        ]
        if len(native_matches) > 1:
            raise ValueError("ambiguous canonical native ID match")
        if native_matches:
            return native_matches[0]
        fingerprint_matches = [
            entry
            for entry in entries
            if entry.canonical_key not in matched_keys and entry.fingerprint == fingerprint
        ]
        if len(fingerprint_matches) > 1:
            raise ValueError("ambiguous canonical fingerprint match")
        return fingerprint_matches[0] if fingerprint_matches else None


__all__ = [
    "ACTIVE",
    "INACTIVE",
    "MANIFEST_VERSION",
    "CanonicalStore",
    "build_canonical_key",
    "canonical_guid_seed",
    "definition_fingerprint",
]

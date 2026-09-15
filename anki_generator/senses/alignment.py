from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Sequence, Tuple

from anki_generator.llm.cache import CacheStore, build_alignment_cache_key
from anki_generator.dictionary.cambridge import fetch_cambridge_provider_entries
from anki_generator.dictionary.longman import fetch_longman_provider_entries
from anki_generator.dictionary.oxford import fetch_oxford_provider_entries
from anki_generator.inputs.english import normalize_pos_tag
from anki_generator.llm.client import generate_json
from anki_generator.llm.prompts import build_sense_alignment_payload, build_sense_alignment_prompt
from anki_generator.models import (
    CanonicalSense,
    DictionaryEntryResult,
    ProviderSense,
    SenseAlignment,
    UnmatchedSenseDecision,
)

if TYPE_CHECKING:
    from anki_generator.senses.store import CanonicalStore


CANONICAL_SENSE_SOURCE = "cambridge"
DEFINITION_PRIORITY = ("longman", "cambridge", "oxford", "llm")
EXAMPLE_PRIORITY = ("longman", "cambridge", "oxford", "llm")
EXAMPLE_AUDIO_SOURCE = "longman"
IMAGE_SOURCE = "cambridge"
SENSE_ALIGNMENT_SCHEMA_VERSION = "cambridge_centered_v1"
MIN_ALIGNMENT_CONFIDENCE = 0.75
ALIGNMENT_RELATIONS = {"equivalent", "broader", "narrower", "one_to_many", "no_match"}
UNMATCHED_DECISIONS = {"append", "ignore"}


def select_definition(
    entries: Sequence[DictionaryEntryResult],
) -> Tuple[str, str, str]:
    by_source = {entry.source: entry for entry in entries}
    for source in DEFINITION_PRIORITY:
        entry = by_source.get(source)
        if entry and entry.definition:
            return entry.definition, source, entry.sense_id
    return "", "", ""


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("alignment confidence must be a number")
    confidence = float(value)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("alignment confidence must be between 0 and 1")
    return confidence


def _id_list(value: Any, field_name: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{field_name} must be a list of non-empty IDs")
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} contains duplicate IDs")
    return list(value)


def validate_alignment_response(
    response: Dict,
    cambridge_senses: Sequence[ProviderSense],
    longman_senses: Sequence[ProviderSense],
    oxford_senses: Sequence[ProviderSense],
) -> Tuple[List[SenseAlignment], List[UnmatchedSenseDecision]]:
    if not isinstance(response, dict) or set(response) != {"alignments", "unmatched"}:
        raise ValueError("alignment response must contain exactly alignments and unmatched")
    if not isinstance(response["alignments"], list) or not isinstance(response["unmatched"], list):
        raise ValueError("alignments and unmatched must be arrays")

    cambridge_ids = {sense.native_id for sense in cambridge_senses}
    longman_ids = {sense.native_id for sense in longman_senses}
    oxford_ids = {sense.native_id for sense in oxford_senses}
    if len(cambridge_ids) != len(cambridge_senses):
        raise ValueError("duplicate Cambridge sense ID in provider input")
    if len(longman_ids) != len(longman_senses):
        raise ValueError("duplicate Longman sense ID in provider input")
    if len(oxford_ids) != len(oxford_senses):
        raise ValueError("duplicate Oxford sense ID in provider input")

    alignments: List[SenseAlignment] = []
    seen_cambridge = set()
    seen_longman = set()
    seen_oxford = set()
    alignment_keys = {
        "cambridge_sense_id",
        "longman_sense_ids",
        "oxford_sense_ids",
        "relation",
        "confidence",
    }
    for raw in response["alignments"]:
        if not isinstance(raw, dict) or set(raw) != alignment_keys:
            raise ValueError("invalid alignment object shape")
        cambridge_id = raw["cambridge_sense_id"]
        if cambridge_id not in cambridge_ids or cambridge_id in seen_cambridge:
            raise ValueError(f"invalid or duplicate Cambridge sense ID: {cambridge_id}")
        longman = _id_list(raw["longman_sense_ids"], "longman_sense_ids")
        oxford = _id_list(raw["oxford_sense_ids"], "oxford_sense_ids")
        if not set(longman) <= longman_ids or seen_longman.intersection(longman):
            raise ValueError("invalid or duplicate Longman sense ID")
        if not set(oxford) <= oxford_ids or seen_oxford.intersection(oxford):
            raise ValueError("invalid or duplicate Oxford sense ID")
        relation = raw["relation"]
        if relation not in ALIGNMENT_RELATIONS:
            raise ValueError(f"invalid alignment relation: {relation}")
        alignment = SenseAlignment(
            cambridge_sense_id=cambridge_id,
            longman_sense_ids=longman,
            oxford_sense_ids=oxford,
            relation=relation,
            confidence=_confidence(raw["confidence"]),
        )
        alignments.append(alignment)
        seen_cambridge.add(cambridge_id)
        seen_longman.update(longman)
        seen_oxford.update(oxford)

    decisions: List[UnmatchedSenseDecision] = []
    seen_unmatched = {"longman": set(), "oxford": set()}
    unmatched_keys = {
        "source",
        "sense_id",
        "decision",
        "related_cambridge_sense_ids",
        "confidence",
    }
    for raw in response["unmatched"]:
        if not isinstance(raw, dict) or set(raw) != unmatched_keys:
            raise ValueError("invalid unmatched object shape")
        source = raw["source"]
        if source not in {"longman", "oxford"}:
            raise ValueError(f"invalid unmatched source: {source}")
        valid_ids = longman_ids if source == "longman" else oxford_ids
        matched_ids = seen_longman if source == "longman" else seen_oxford
        sense_id = raw["sense_id"]
        if (
            sense_id not in valid_ids
            or sense_id in matched_ids
            or sense_id in seen_unmatched[source]
        ):
            raise ValueError(f"invalid or duplicate unmatched sense ID: {sense_id}")
        decision = raw["decision"]
        if decision not in UNMATCHED_DECISIONS or (source == "oxford" and decision != "ignore"):
            raise ValueError(f"invalid unmatched decision: {decision}")
        related = _id_list(
            raw["related_cambridge_sense_ids"],
            "related_cambridge_sense_ids",
        )
        if not set(related) <= cambridge_ids:
            raise ValueError("unmatched decision contains invalid Cambridge sense ID")
        decisions.append(
            UnmatchedSenseDecision(
                source=source,
                sense_id=sense_id,
                decision=decision,
                related_cambridge_sense_ids=related,
                confidence=_confidence(raw["confidence"]),
            )
        )
        seen_unmatched[source].add(sense_id)

    if seen_cambridge != cambridge_ids:
        raise ValueError("alignment response does not cover every Cambridge sense")
    if seen_longman | seen_unmatched["longman"] != longman_ids:
        raise ValueError("alignment response does not cover every Longman sense")
    if seen_oxford | seen_unmatched["oxford"] != oxford_ids:
        raise ValueError("alignment response does not cover every Oxford sense")
    return alignments, decisions


def align_canonical_senses(
    *,
    client,
    word: str,
    pos: str,
    cambridge_senses: Sequence[ProviderSense],
    longman_senses: Sequence[ProviderSense],
    oxford_senses: Sequence[ProviderSense],
    model: str,
    reasoning_effort: str,
    cache: CacheStore,
    canonical_store: "CanonicalStore | None" = None,
) -> List[CanonicalSense]:
    normalized_pos = normalize_pos_tag(pos)
    if not normalized_pos:
        raise ValueError("sense alignment requires a supported POS")

    def same_pos(
        senses: Sequence[ProviderSense],
        expected_source: str,
    ) -> List[ProviderSense]:
        if any(sense.source != expected_source for sense in senses):
            raise ValueError(f"{expected_source} sense list contains another provider")
        return [sense for sense in senses if normalize_pos_tag(sense.pos) == normalized_pos]

    cambridge = same_pos(cambridge_senses, "cambridge")
    longman = same_pos(longman_senses, "longman")
    oxford = same_pos(oxford_senses, "oxford")
    cache_senses = [
        {
            "source": sense.source,
            "sense_id": sense.native_id,
            "definition": sense.definition,
        }
        for senses in (cambridge, longman, oxford)
        for sense in senses
    ]
    cache_key = build_alignment_cache_key(
        word=word,
        pos=normalized_pos,
        model=model,
        schema_version=SENSE_ALIGNMENT_SCHEMA_VERSION,
        senses=cache_senses,
    )
    response = cache.get(cache_key)
    if response is None:
        response = generate_json(
            client,
            model=model,
            system_prompt=build_sense_alignment_prompt(),
            user_prompt=build_sense_alignment_payload(
                word,
                normalized_pos,
                cambridge,
                longman,
                oxford,
            ),
            reasoning_effort=reasoning_effort,
        )
        alignments, decisions = validate_alignment_response(
            response,
            cambridge,
            longman,
            oxford,
        )
        cache.set(cache_key, response)
    else:
        alignments, decisions = validate_alignment_response(
            response,
            cambridge,
            longman,
            oxford,
        )

    by_cambridge = {alignment.cambridge_sense_id: alignment for alignment in alignments}
    canonical: List[CanonicalSense] = []
    for sense in cambridge:
        alignment = by_cambridge[sense.native_id]
        accepted = alignment.confidence >= MIN_ALIGNMENT_CONFIDENCE
        canonical.append(
            CanonicalSense(
                word=word,
                pos=normalized_pos,
                index=len(canonical) + 1,
                backbone_source="cambridge",
                backbone_sense_id=sense.native_id,
                cambridge_sense_id=sense.native_id,
                longman_sense_ids=list(alignment.longman_sense_ids) if accepted else [],
                oxford_sense_ids=list(alignment.oxford_sense_ids) if accepted else [],
                relation=alignment.relation if accepted else "unmatched",
                confidence=alignment.confidence,
            )
        )

    decisions_by_id = {
        decision.sense_id: decision
        for decision in decisions
        if decision.source == "longman"
    }
    for sense in longman:
        decision = decisions_by_id.get(sense.native_id)
        if (
            decision
            and decision.decision == "append"
            and decision.confidence >= MIN_ALIGNMENT_CONFIDENCE
        ):
            canonical.append(
                CanonicalSense(
                    word=word,
                    pos=normalized_pos,
                    index=len(canonical) + 1,
                    backbone_source="longman",
                    backbone_sense_id=sense.native_id,
                    longman_sense_ids=[sense.native_id],
                    relation="unmatched",
                    confidence=decision.confidence,
                )
            )
    if canonical_store is None:
        return canonical
    return canonical_store.reconcile(
        word=word,
        pos=normalized_pos,
        canonical_senses=canonical,
        provider_senses=[*cambridge, *longman, *oxford],
    )


def fetch_and_align_canonical_senses(
    *,
    client,
    word: str,
    pos: str,
    model: str,
    reasoning_effort: str,
    cache: CacheStore,
    canonical_store: "CanonicalStore | None" = None,
) -> List[CanonicalSense]:
    cambridge_entries = fetch_cambridge_provider_entries(word)
    longman_entries = fetch_longman_provider_entries(word)
    oxford_entries = fetch_oxford_provider_entries(word)
    return align_canonical_senses(
        client=client,
        word=word,
        pos=pos,
        cambridge_senses=[
            sense for entry in cambridge_entries for sense in entry.senses
        ],
        longman_senses=[sense for entry in longman_entries for sense in entry.senses],
        oxford_senses=[sense for entry in oxford_entries for sense in entry.senses],
        model=model,
        reasoning_effort=reasoning_effort,
        cache=cache,
        canonical_store=canonical_store,
    )

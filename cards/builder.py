from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

from canonical_store import CanonicalStore, canonical_guid_seed
from cache import CacheStore, build_cache_key
from cards.renderers import (
    build_canonical_en_word_card,
    build_en_word_card,
    build_ja_word_card,
    build_knowledge_card,
)
from config import LLM_SCHEMA_VERSION
from dictionary.cambridge import fetch_cambridge_provider_entries
from dictionary.canonical import align_canonical_senses
from dictionary.longman import fetch_longman_provider_entries
from dictionary.oxford import fetch_oxford_provider_entries
from dictionary.pronunciation import ordered_word_audio_urls, select_word_ipa
from dictionary.service import fetch_dictionary_entries, merge_dictionary_entries
from english_terms import en_word_uses_dictionary_lookup, is_single_word_term, parse_english_term
from llm.client import LLMClient, generate_json
from llm.prompts import PROMPT_MAP, build_user_payload
from media.audio import ensure_english_audio, ensure_example_audio, ensure_japanese_audio
from media.images import ensure_noun_image
from models import (
    AudioAsset,
    BuiltCard,
    CanonicalSenseRequest,
    CardBuildResult,
    DictionaryEntryResult,
    InputItem,
    ParsedEnglishTerm,
    ProviderEntry,
    ProviderSense,
    ResolvedCanonicalContent,
    ResolvedContentField,
)


def call_openai_json(
    client: LLMClient,
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
    return generate_json(
        client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        reasoning_effort=reasoning_effort,
    )


def collect_audio_fallback_urls(
    entries: List[DictionaryEntryResult], primary_url: str = ""
) -> List[str]:
    return [
        url
        for url, _source in ordered_word_audio_urls(entries)
        if url != primary_url
    ]


def _provider_pos_order(entries: Sequence[ProviderEntry]) -> List[str]:
    result = []
    for source in ("cambridge", "longman", "oxford"):
        for entry in entries:
            if entry.source != source:
                continue
            for sense in entry.senses:
                pos = (sense.pos or entry.pos).strip()
                if pos and pos not in result:
                    result.append(pos)
    return result


def expand_english_item(
    *,
    client: LLMClient,
    item: InputItem,
    model: str,
    reasoning_effort: str,
    cache: CacheStore,
    canonical_store: CanonicalStore,
) -> List[CanonicalSenseRequest]:
    parsed = parse_english_term(item.term)
    if not en_word_uses_dictionary_lookup(parsed.word):
        return []
    provider_entries = [
        *fetch_cambridge_provider_entries(parsed.word),
        *fetch_longman_provider_entries(parsed.word),
        *fetch_oxford_provider_entries(parsed.word),
    ]
    positions = _provider_pos_order(provider_entries)
    if parsed.requested_pos:
        positions = [pos for pos in positions if pos == parsed.requested_pos]

    requests: List[CanonicalSenseRequest] = []
    for pos in positions:
        by_source = {
            source: [
                sense
                for entry in provider_entries
                if entry.source == source
                for sense in entry.senses
                if sense.pos == pos
            ]
            for source in ("cambridge", "longman", "oxford")
        }
        if not any(by_source.values()):
            continue
        canonical_senses = align_canonical_senses(
            client=client,
            word=parsed.word,
            pos=pos,
            cambridge_senses=by_source["cambridge"],
            longman_senses=by_source["longman"],
            oxford_senses=by_source["oxford"],
            model=model,
            reasoning_effort=reasoning_effort,
            cache=cache,
            canonical_store=canonical_store,
        )
        if parsed.requested_sense_index is not None:
            canonical_senses = [
                sense
                for sense in canonical_senses
                if sense.index == parsed.requested_sense_index
            ]
        requests.extend(
            CanonicalSenseRequest(
                item=item,
                word=parsed.word,
                pos=pos,
                canonical_sense=sense,
                provider_entries=list(provider_entries),
            )
            for sense in canonical_senses
        )
    return requests


def _sense_by_id(
    entries: Sequence[ProviderEntry], source: str, native_id: str
) -> ProviderSense | None:
    if not native_id:
        return None
    return next(
        (
            sense
            for entry in entries
            if entry.source == source
            for sense in entry.senses
            if sense.native_id == native_id
        ),
        None,
    )


def resolve_canonical_content(
    request: CanonicalSenseRequest,
) -> ResolvedCanonicalContent:
    canonical = request.canonical_sense
    longman = next(
        (
            _sense_by_id(request.provider_entries, "longman", sense_id)
            for sense_id in canonical.longman_sense_ids
            if _sense_by_id(request.provider_entries, "longman", sense_id)
        ),
        None,
    )
    cambridge = _sense_by_id(
        request.provider_entries, "cambridge", canonical.cambridge_sense_id
    )
    oxford = next(
        (
            _sense_by_id(request.provider_entries, "oxford", sense_id)
            for sense_id in canonical.oxford_sense_ids
            if _sense_by_id(request.provider_entries, "oxford", sense_id)
        ),
        None,
    )
    definition_sense = next(
        (sense for sense in (longman, cambridge, oxford) if sense and sense.definition),
        None,
    )
    example_pair = next(
        (
            (sense, example)
            for sense in (longman, cambridge, oxford)
            if sense
            for example in sense.examples
            if example.text
        ),
        None,
    )

    pronunciation_entries = []
    for source in ("cambridge", "longman", "oxford"):
        entry = next(
            (
                entry
                for entry in request.provider_entries
                if entry.source == source and entry.pos == request.pos
            ),
            None,
        )
        if entry:
            pronunciation_entries.append(
                DictionaryEntryResult(
                    source=source,
                    word=request.word,
                    requested_pos=request.pos,
                    actual_pos=request.pos,
                    ipa_uk=entry.pronunciation.ipa_uk,
                    audio_uk_url=entry.pronunciation.audio_uk_url,
                )
            )
    ipa, ipa_source = select_word_ipa(pronunciation_entries)
    audio_urls = ordered_word_audio_urls(pronunciation_entries)
    example_sense, example = example_pair if example_pair else (None, None)
    example_audio_url = (
        example.audio_url
        if example_sense and example_sense.source == "longman" and example
        else ""
    )
    return ResolvedCanonicalContent(
        word=request.word,
        pos=request.pos,
        index=canonical.index,
        canonical_key=canonical.canonical_key,
        status=canonical.status,
        definition=ResolvedContentField(
            value=definition_sense.definition if definition_sense else "",
            source=definition_sense.source if definition_sense else "",
            sense_id=definition_sense.native_id if definition_sense else "",
        ),
        example=ResolvedContentField(
            value=example.text if example else "",
            source=example_sense.source if example_sense else "",
            sense_id=example_sense.native_id if example_sense else "",
        ),
        example_audio=ResolvedContentField(
            value=example_audio_url,
            source="longman" if example_audio_url else "",
            sense_id=example_sense.native_id if example_audio_url else "",
        ),
        ipa=ResolvedContentField(value=ipa, source=ipa_source),
        word_audio_urls=[
            ResolvedContentField(value=url, source=source)
            for url, source in audio_urls
        ],
        image=ResolvedContentField(
            value=cambridge.image_url if cambridge else "",
            source="cambridge" if cambridge and cambridge.image_url else "",
            sense_id=cambridge.native_id if cambridge and cambridge.image_url else "",
        ),
        image_alt=cambridge.image_alt if cambridge else "",
    )


def build_canonical_sense_card(
    *,
    client: LLMClient,
    request: CanonicalSenseRequest,
    model: str,
    tts_model: str,
    audio_dir: Path,
    image_dir: Path,
    tts_voice_en: str,
    cache: CacheStore,
    reasoning_effort: str,
) -> Tuple[BuiltCard, List[AudioAsset], ResolvedCanonicalContent]:
    content = resolve_canonical_content(request)
    canonical = request.canonical_sense
    target_label = f"{request.word} {request.pos} {canonical.index}"
    media_item = InputItem(
        mode="en_word",
        term=target_label,
        hint=request.item.hint,
        tags=request.item.tags,
    )
    word_media_item = InputItem(
        mode="en_word",
        term=f"{request.word} {request.pos}",
        hint=request.item.hint,
        tags=request.item.tags,
    )
    prompt_item = InputItem(
        mode="en_word",
        term=request.word,
        hint=request.item.hint,
        tags=request.item.tags,
    )
    cache_key = build_cache_key(
        mode="en_word",
        term=canonical.canonical_key,
        hint=request.item.hint,
        model=model,
        llm_schema=LLM_SCHEMA_VERSION,
        dict_phonetic=content.ipa.value,
        dict_phonetic_source=content.ipa.source,
        parsed_word=request.word,
        requested_pos=request.pos,
        dictionary_definition=content.definition.value,
    )
    llm = cache.get(cache_key)
    if not llm:
        llm = call_openai_json(
            client=client,
            model=model,
            item=prompt_item,
            phonetic_hint=content.ipa.value,
            term_for_pronunciation=request.word,
            reasoning_effort=reasoning_effort,
            phonetic_source=content.ipa.source,
            dictionary_definition=content.definition.value,
            requested_pos=request.pos,
            parsed_word=request.word,
        )
        cache.set(cache_key, llm)
    llm = dict(llm)
    if not content.definition.value:
        content = replace(
            content,
            definition=ResolvedContentField(
                value=str(llm.get("definition_en") or "").strip(),
                source="llm",
            ),
        )
    if not content.example.value:
        content = replace(
            content,
            example=ResolvedContentField(
                value=str(llm.get("example_simple_en") or "").strip(),
                source="llm",
            ),
        )

    word_audio_assets = []
    if is_single_word_term(request.word) and content.word_audio_urls:
        audio = ensure_english_audio(
            client=client,
            media_dir=audio_dir,
            item=word_media_item,
            spoken_term=request.word,
            preferred_external_url=content.word_audio_urls[0].value,
            tts_model=tts_model,
            voice=tts_voice_en,
            extra_audio_urls=[field.value for field in content.word_audio_urls[1:]],
        )
        if audio:
            word_audio_assets.append(audio)
    example_audio = None
    if content.example_audio.value:
        example_audio = ensure_example_audio(
            media_dir=audio_dir,
            item=media_item,
            audio_url=content.example_audio.value,
        )
    image = None
    if content.image.value:
        image = ensure_noun_image(
            media_dir=image_dir,
            item=media_item,
            preferred_image_url=content.image.value,
            preferred_image_source="cambridge",
            allow_fallback=False,
        )
    built = build_canonical_en_word_card(
        content,
        word_audio_assets,
        example_audio,
        image,
    )
    tags = list(dict.fromkeys(built.tags + request.item.normalized_tags()))
    card = BuiltCard(
        front=built.front,
        back=built.back,
        tags=tags,
        guid_seed=canonical_guid_seed(canonical.canonical_key),
    )
    assets = [*word_audio_assets]
    if example_audio:
        assets.append(example_audio)
    if image:
        assets.append(image)
    return card, assets, content


def build_cards(
    *,
    client: LLMClient,
    item: InputItem,
    model: str,
    tts_model: str,
    audio_dir: Path,
    image_dir: Path,
    tts_voice_en: str,
    tts_voice_ja: str,
    cache: CacheStore,
    reasoning_effort: str,
    canonical_store: CanonicalStore,
    fallback_builder: Callable | None = None,
) -> CardBuildResult:
    fallback_builder = fallback_builder or build_card
    if item.mode != "en_word":
        card, assets = fallback_builder(
            client, item, model, tts_model, audio_dir, image_dir,
            tts_voice_en, tts_voice_ja, cache, reasoning_effort,
        )
        return CardBuildResult(cards=[card], assets=assets)
    requests = expand_english_item(
        client=client,
        item=item,
        model=model,
        reasoning_effort=reasoning_effort,
        cache=cache,
        canonical_store=canonical_store,
    )
    if not requests:
        parsed = parse_english_term(item.term)
        if parsed.requested_pos:
            target = (
                item.term
                if parsed.requested_sense_index
                else f"{parsed.word} {parsed.requested_pos}"
            )
            return CardBuildResult(errors=[f"{target}: no canonical sense found"])
        card, assets = fallback_builder(
            client, item, model, tts_model, audio_dir, image_dir,
            tts_voice_en, tts_voice_ja, cache, reasoning_effort,
        )
        return CardBuildResult(cards=[card], assets=assets)

    result = CardBuildResult()
    for request in requests:
        try:
            card, assets, content = build_canonical_sense_card(
                client=client,
                request=request,
                model=model,
                tts_model=tts_model,
                audio_dir=audio_dir,
                image_dir=image_dir,
                tts_voice_en=tts_voice_en,
                cache=cache,
                reasoning_effort=reasoning_effort,
            )
            result.cards.append(card)
            result.assets.extend(assets)
            result.resolved_contents.append(content)
        except Exception as exc:
            label = f"{request.word} {request.pos} {request.canonical_sense.index}"
            result.errors.append(f"{label}: {exc}")
    return result


def build_card(
    client: LLMClient,
    item: InputItem,
    model: str,
    tts_model: str,
    audio_dir: Path,
    image_dir: Path,
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
            dictionary_entries = fetch_dictionary_entries(spoken_term, parsed_en.requested_pos)
            dictionary_entry = merge_dictionary_entries(dictionary_entries)
            dict_phonetic = dictionary_entry.ipa_uk
            dict_phonetic_source = (dictionary_entry.ipa_source or "").strip()
            dict_audio = dictionary_entry.audio_uk_url
            dict_audio_fallbacks = collect_audio_fallback_urls(dictionary_entries, dict_audio)
            print(
                f"[dict] term={item.term} word={spoken_term} requested_pos={parsed_en.requested_pos or '(none)'} "
                f"definition_source={dictionary_entry.definition_source or 'none'} "
                f"ipa_source={dictionary_entry.ipa_source or 'none'} "
                f"audio_source={dictionary_entry.audio_source or 'none'} "
                f"image_source={dictionary_entry.image_source or 'none'}"
            )
            dict_pos_tags = [
                p
                for p in (parsed_en.requested_pos, dictionary_entry.actual_pos)
                if p
            ]

    cache_key = build_cache_key(
        mode=item.mode,
        term=item.term,
        hint=item.hint,
        model=model,
        llm_schema=LLM_SCHEMA_VERSION,
        dict_phonetic=dict_phonetic,
        dict_phonetic_source=dict_phonetic_source,
        parsed_word=parsed_en.word if item.mode == "en_word" else "",
        requested_pos=parsed_en.requested_pos if item.mode == "en_word" else "",
        dictionary_definition=dictionary_entry.definition if item.mode == "en_word" else "",
    )

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
                media_dir=audio_dir,
                item=item,
                spoken_term=spoken_term,
                preferred_external_url=dict_audio,
                tts_model=tts_model,
                voice=tts_voice_en,
                extra_audio_urls=dict_audio_fallbacks,
            )
            if single:
                audio_assets = [single]
        image = None
        if dictionary_entry.image_url:
            image = ensure_noun_image(
                media_dir=image_dir,
                item=item,
                preferred_image_url=dictionary_entry.image_url,
                preferred_image_source=dictionary_entry.image_source,
            )
            if image:
                assets.append(image)

        assets.extend(audio_assets)
        if image:
            assets.append(image)
        built = build_en_word_card(item.term, llm, dict_phonetic, audio_assets, image)
    elif item.mode == "ja_word":
        audio = ensure_japanese_audio(
            client=client,
            media_dir=audio_dir,
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


__all__ = [
    "build_card",
    "build_cards",
    "build_canonical_sense_card",
    "call_openai_json",
    "collect_audio_fallback_urls",
    "expand_english_item",
    "resolve_canonical_content",
]

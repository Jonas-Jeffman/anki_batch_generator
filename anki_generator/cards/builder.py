"""Dispatch card modes, isolate sense failures, and build fallback cards."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Tuple

from anki_generator.cards.english import build_canonical_sense_card, expand_english_item
from anki_generator.cards.renderers import (
    build_en_word_card,
    build_ja_word_card,
    build_knowledge_card,
)
from anki_generator.config import LLM_SCHEMA_VERSION
from anki_generator.dictionary.pronunciation import ordered_word_audio_urls
from anki_generator.dictionary.service import fetch_dictionary_entries, merge_dictionary_entries
from anki_generator.inputs.english import (
    en_word_uses_dictionary_lookup,
    is_single_word_term,
    parse_english_term,
)
from anki_generator.llm.cache import CacheStore, build_cache_key
from anki_generator.llm.client import LLMClient
from anki_generator.llm.content import call_openai_json
from anki_generator.media.audio import ensure_english_audio, ensure_japanese_audio
from anki_generator.media.images import ensure_noun_image
from anki_generator.models import (
    AudioAsset,
    BuiltCard,
    CardBuildResult,
    DictionaryEntryResult,
    InputItem,
    ParsedEnglishTerm,
)
from anki_generator.senses.store import CanonicalStore


def collect_audio_fallback_urls(
    entries: List[DictionaryEntryResult], primary_url: str = ""
) -> List[str]:
    return [
        url
        for url, _source in ordered_word_audio_urls(entries)
        if url != primary_url
    ]


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
    result.assets = list({asset.filename: asset for asset in result.assets}.values())
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


__all__ = ["build_card", "build_cards", "collect_audio_fallback_urls"]

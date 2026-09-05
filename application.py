from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List

from anki.exporter import create_deck_apkg
from cache import CacheStore
from canonical_store import CanonicalStore
from cards.builder import build_card, build_cards
from cards.preview import run_dictionary_test_only, write_preview_json
from cli import parse_args
from config import (
    DEFAULT_TERMS_JSON,
    DEFAULT_TERMS_TXT,
    LOCAL_OPENAI_KEY_FILE,
    resolve_openai_api_key,
    resolve_openai_base_url,
)
from dictionary.common import (
    is_candidate_dictionary_image_url,
    is_cross_reference_definition,
    normalize_dictionary_url,
)
from english_terms import parse_english_term
from llm.client import create_llm_client, openai_sdk_available
from models import BuiltCard, ResolvedCanonicalContent
from terms import load_items


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

    assert normalize_dictionary_url("/media/english/uk_pron/u/ukr/ukroo/ukrooke025.mp3") == (
        "https://dictionary.cambridge.org/media/english/uk_pron/u/ukr/ukroo/ukrooke025.mp3"
    )
    assert is_candidate_dictionary_image_url(
        "https://dictionary.cambridge.org/images/full/rose_noun_002_32331.jpg"
    )
    assert not is_candidate_dictionary_image_url(
        "https://dictionary.cambridge.org/external/images/og-image.png"
    )
    assert not is_candidate_dictionary_image_url(
        "https://www.ldoceonline.com/external/images/logo.svg"
    )
    assert not is_candidate_dictionary_image_url(
        "https://www.ldoceonline.com/media/english/illustration/banner_ad.jpg"
    )
    assert is_cross_reference_definition("past simple of rise")


def main() -> int:
    args = parse_args()

    if args.self_test:
        run_self_test()
        print("Self-test passed.")
        return 0

    if not args.mode:
        print("ERROR: --mode is required unless --self-test is used.")
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
    base_media_dir = Path(args.media_dir).expanduser().resolve()
    audio_dir = base_media_dir.parent / "anki_audio"
    image_dir = base_media_dir.parent / "anki_images"

    audio_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    if args.dict_test_only:
        if args.mode != "en_word":
            print("ERROR: --dict-test-only is only supported with --mode en_word.")
            return 1
        run_dictionary_test_only(items, preview_json)
        print(f"\nDictionary preview JSON: {preview_json}")
        return 0

    if not args.deck_name:
        print("ERROR: --deck-name is required unless --self-test or --dict-test-only is used.")
        return 1

    api_key = resolve_openai_api_key(args.openai_api_key)
    if not api_key:
        print(
            "ERROR: OpenAI API key is missing. Set --openai-api-key, or OPENAI_API_KEY, or create "
            f"{LOCAL_OPENAI_KEY_FILE} (one line, not committed to git)."
        )
        return 1
    if not openai_sdk_available():
        print("ERROR: openai package is not installed. Install project requirements to generate cards.")
        return 1

    base_url = resolve_openai_base_url(args.openai_base_url)
    if base_url:
        print(f"Using OpenAI-compatible base_url: {base_url}")
        client = create_llm_client(api_key=api_key, base_url=base_url)
    else:
        client = create_llm_client(api_key=api_key)
    cache = CacheStore(cache_path)
    canonical_store = CanonicalStore(
        cache_path.with_name("anki_canonical_manifest.json")
    )

    built_cards: List[BuiltCard] = []
    resolved_contents: List[ResolvedCanonicalContent] = []
    media_files: Dict[str, Path] = {}
    total = len(items)

    for i, item in enumerate(items, start=1):
        print(f"[{i}/{total}] Generating card: mode={item.mode}, term={item.term}")
        try:
            result = build_cards(
                client=client,
                item=item,
                model=args.model,
                tts_model=args.tts_model,
                audio_dir=audio_dir,
                image_dir=image_dir,
                tts_voice_en=args.tts_voice_en,
                tts_voice_ja=args.tts_voice_ja,
                cache=cache,
                reasoning_effort=args.reasoning_effort,
                canonical_store=canonical_store,
                fallback_builder=build_card,
            )
            built_cards.extend(result.cards)
            resolved_contents.extend(result.resolved_contents)
            for asset in result.assets:
                media_files[asset.filename] = asset.filepath
            for error in result.errors:
                print(f"[ERROR] Failed on '{error}' (en_word)")
        except Exception as exc:
            print(f"[ERROR] Failed on '{item.term}' ({item.mode}): {exc}")
        time.sleep(max(0.0, args.sleep))

    cache.save()
    canonical_store.save()

    if not built_cards:
        print("ERROR: all items failed; no deck generated.")
        return 1

    write_preview_json(preview_json, built_cards, resolved_contents)
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
    print(f"- Audio dir: {audio_dir}")
    print(f"- Image dir: {image_dir}")
    print(f"- Image review dir: {image_dir.parent / 'anki_image_review'}")
    print("- To extend an existing Anki deck, keep --deck-name the same as that deck and import the new .apkg.")
    return 0


__all__ = ["main", "run_self_test"]

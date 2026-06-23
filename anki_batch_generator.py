#!/usr/bin/env python3
from __future__ import annotations

from common import *
from dictionary_sources import (
    _normalize_url,
    _is_candidate_dictionary_image_url,
    is_cross_reference_definition,
    dictionary_result_preview,
)
from card_builder import *

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

    assert _normalize_url("/media/english/uk_pron/u/ukr/ukroo/ukrooke025.mp3") == (
        "https://dictionary.cambridge.org/media/english/uk_pron/u/ukr/ukroo/ukrooke025.mp3"
    )
    assert _is_candidate_dictionary_image_url(
        "https://dictionary.cambridge.org/images/full/rose_noun_002_32331.jpg"
    )
    assert not _is_candidate_dictionary_image_url(
        "https://dictionary.cambridge.org/external/images/og-image.png"
    )
    assert not _is_candidate_dictionary_image_url(
        "https://www.ldoceonline.com/external/images/logo.svg"
    )
    assert not _is_candidate_dictionary_image_url(
        "https://www.ldoceonline.com/media/english/illustration/banner_ad.jpg"
    )
    assert is_cross_reference_definition("past simple of rise")


def create_deck_apkg(
    deck_name: str,
    output_apkg: Path,
    cards: List[BuiltCard],
    media_files: Iterable[Path],
) -> None:
    if genanki is None:
        raise RuntimeError("genanki is not installed; install project requirements to generate .apkg files.")
    deck_id = stable_anki_id(f"deck::{deck_name}")
    model_id = stable_anki_id("model::anki_batch_generator::basic_v2")

    model = genanki.Model(
        model_id=model_id,
        name="BatchAIGeneratedBasicModelV2",
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[
            {
                "name": "Card 1",
                "qfmt": "{{Front}}",
                "afmt": "{{FrontSide}}<hr id=\"answer\">{{Back}}",
            }
        ],
        css="""
.card {
  font-family: Arial, sans-serif;
  font-size: 20px;
  text-align: left;
  color: #111;
  background-color: #fff;
  line-height: 1.6;
}
ul {
  margin-top: 4px;
  margin-bottom: 8px;
}
""",
    )

    deck = genanki.Deck(deck_id, deck_name)
    for card in cards:
        note = genanki.Note(
            model=model,
            fields=[card.front, card.back],
            tags=card.tags,
            guid=stable_guid(deck_name, card.guid_seed),
        )
        deck.add_note(note)

    package = genanki.Package(deck)
    package.media_files = [str(p) for p in media_files]
    output_apkg.parent.mkdir(parents=True, exist_ok=True)
    package.write_to_file(str(output_apkg))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate or extend an Anki deck from a JSON array.")
    parser.add_argument("--mode", default="", choices=[""] + sorted(SUPPORTED_MODES))
    parser.add_argument(
        "--terms-json",
        default="",
        help='JSON array string, e.g. ["apologise", "burgeon"]',
    )
    parser.add_argument(
        "--terms-file",
        default="",
        help=(
            "Path to a .json array or .txt (one term per line, # for comments). "
            "If omitted with empty --terms-json, uses terms.json next to this script, "
            "else terms.txt in the same folder."
        ),
    )
    parser.add_argument("--hint", default="", help="Optional shared hint applied to all items.")
    parser.add_argument("--tags", nargs="*", default=[], help="Optional extra tags.")
    parser.add_argument("--deck-name", default="", help="Use the same deck name as the existing deck to extend it.")
    parser.add_argument("--output", default="anki_batch_output.apkg", help="Output .apkg path.")
    parser.add_argument("--preview-json", default="anki_batch_preview.json", help="Preview JSON path.")
    parser.add_argument("--cache-path", default="anki_batch_cache.json", help="LLM cache JSON path.")
    parser.add_argument("--media-dir", default="anki_media", help="Temporary folder for audio media files.")
    parser.add_argument("--model", default=DEFAULT_TEXT_MODEL, help="OpenAI text model.")
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL, help="OpenAI TTS model.")
    parser.add_argument(
        "--tts-voice-en",
        default="alloy",
        help="English TTS voice for single-word cards when all UK dictionary MP3 URLs fail.",
    )
    parser.add_argument("--tts-voice-ja", default="alloy", help="Japanese TTS voice.")
    parser.add_argument("--reasoning-effort", default="medium", choices=["minimal", "low", "medium", "high"])
    parser.add_argument(
        "--openai-api-key",
        default="",
        help=(
            "OpenAI API key. If empty: uses OPENAI_API_KEY env, else first line of "
            f"{LOCAL_OPENAI_KEY_FILE.name} next to this script."
        ),
    )
    parser.add_argument(
        "--openai-base-url",
        default="",
        help=(
            "OpenAI-compatible API base URL, e.g. https://api.chatanywhere.tech "
            "(trailing /v1 added if missing). If empty, uses OPENAI_BASE_URL env, else official api.openai.com."
        ),
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP,
        help="Sleep seconds between items. Default is intentionally higher to reduce rate spikes.",
    )
    parser.add_argument(
        "--dict-test-only",
        action="store_true",
        help="Only fetch and preview English dictionary fields; do not call OpenAI or generate an .apkg.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run lightweight pure-function self-tests and exit.",
    )
    return parser.parse_args()


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
    if OpenAI is None:
        print("ERROR: openai package is not installed. Install project requirements to generate cards.")
        return 1

    base_url = resolve_openai_base_url(args.openai_base_url)
    if base_url:
        print(f"Using OpenAI-compatible base_url: {base_url}")
        client = OpenAI(api_key=api_key, base_url=base_url)
    else:
        client = OpenAI(api_key=api_key)
    cache = CacheStore(cache_path)

    built_cards: List[BuiltCard] = []
    media_files: Dict[str, Path] = {}
    total = len(items)

    for i, item in enumerate(items, start=1):
        print(f"[{i}/{total}] Generating card: mode={item.mode}, term={item.term}")
        try:
            card, assets = build_card(
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
            )
            built_cards.append(card)
            for asset in assets:
                media_files[asset.filename] = asset.filepath
        except Exception as exc:
            print(f"[ERROR] Failed on '{item.term}' ({item.mode}): {exc}")
        time.sleep(max(0.0, args.sleep))

    cache.save()

    if not built_cards:
        print("ERROR: all items failed; no deck generated.")
        return 1

    write_preview_json(preview_json, built_cards)
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


if __name__ == "__main__":
    sys.exit(main())

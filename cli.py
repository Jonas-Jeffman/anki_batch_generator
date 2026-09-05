from __future__ import annotations

import argparse

from config import (
    DEFAULT_SLEEP,
    DEFAULT_TEXT_MODEL,
    DEFAULT_TTS_MODEL,
    LOCAL_OPENAI_KEY_FILE,
    SUPPORTED_MODES,
)


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


__all__ = ["parse_args"]

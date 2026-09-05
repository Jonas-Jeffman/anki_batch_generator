from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, call, patch

from tests import support  # noqa: F401 - installs offline dependency stubs

import anki_batch_generator
import application
import cli
from anki.exporter import create_deck_apkg
from models import AudioAsset, BuiltCard, CardBuildResult, InputItem


def make_args(**overrides):
    values = {
        "self_test": False,
        "mode": "en_word",
        "terms_json": '["one"]',
        "terms_file": "",
        "hint": "",
        "tags": [],
        "deck_name": "Test::Deck",
        "output": "deck.apkg",
        "preview_json": "preview.json",
        "cache_path": "cache.json",
        "media_dir": "media",
        "model": "fixture-model",
        "tts_model": "fixture-tts",
        "tts_voice_en": "alloy",
        "tts_voice_ja": "alloy",
        "reasoning_effort": "medium",
        "openai_api_key": "fixture-key",
        "openai_base_url": "",
        "sleep": 0.25,
        "dict_test_only": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class _Cache:
    instances = []

    def __init__(self, path):
        self.path = path
        self.saved = 0
        self.instances.append(self)

    def save(self):
        self.saved += 1


class ApplicationSplitTests(unittest.TestCase):
    def test_entry_is_small_compatibility_surface(self):
        self.assertIs(anki_batch_generator.main, application.main)
        self.assertIs(anki_batch_generator.run_self_test, application.run_self_test)
        self.assertIs(anki_batch_generator.parse_args, cli.parse_args)
        self.assertIs(anki_batch_generator.create_deck_apkg, create_deck_apkg)

    def test_self_test_path_preserves_log_and_exit_code(self):
        output = io.StringIO()
        with (
            patch.object(application, "parse_args", return_value=make_args(self_test=True)),
            redirect_stdout(output),
        ):
            result = application.main()
        self.assertEqual(0, result)
        self.assertEqual("Self-test passed.\n", output.getvalue())

    def test_validation_error_order_and_messages(self):
        cases = [
            (
                make_args(mode="", terms_json=""),
                "ERROR: --mode is required unless --self-test is used.\n",
            ),
            (
                make_args(mode="ja_word", dict_test_only=True),
                "ERROR: --dict-test-only is only supported with --mode en_word.\n",
            ),
            (
                make_args(deck_name=""),
                "ERROR: --deck-name is required unless --self-test or --dict-test-only is used.\n",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for args, expected in cases:
                with self.subTest(expected=expected):
                    args.output = str(Path(tmp) / "deck.apkg")
                    args.preview_json = str(Path(tmp) / "preview.json")
                    args.cache_path = str(Path(tmp) / "cache.json")
                    args.media_dir = str(Path(tmp) / "media")
                    output = io.StringIO()
                    with (
                        patch.object(application, "parse_args", return_value=args),
                        patch.object(application, "load_items", return_value=[InputItem(args.mode or "en_word", "one")]),
                        redirect_stdout(output),
                    ):
                        result = application.main()
                    self.assertEqual(1, result)
                    self.assertEqual(expected, output.getvalue())

    def test_dictionary_only_stops_before_client_initialization(self):
        args = make_args(dict_test_only=True)
        items = [InputItem("en_word", "rose")]
        dictionary_preview = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            args.preview_json = str(Path(tmp) / "preview.json")
            args.media_dir = str(Path(tmp) / "media")
            output = io.StringIO()
            with (
                patch.object(application, "parse_args", return_value=args),
                patch.object(application, "load_items", return_value=items),
                patch.object(application, "run_dictionary_test_only", dictionary_preview),
                patch.object(application, "create_llm_client") as client_factory,
                redirect_stdout(output),
            ):
                result = application.main()
        self.assertEqual(0, result)
        dictionary_preview.assert_called_once_with(items, Path(args.preview_json).resolve())
        client_factory.assert_not_called()
        self.assertEqual(
            f"\nDictionary preview JSON: {Path(args.preview_json).resolve()}\n",
            output.getvalue(),
        )

    def test_partial_failure_continues_saves_cache_and_reports_exactly(self):
        items = [InputItem("en_word", "bad"), InputItem("en_word", "good")]
        card = BuiltCard("front", "back", ["english"], "en_word::good")
        asset = AudioAsset("good.mp3", Path("good.mp3"))
        _Cache.instances.clear()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = make_args(
                terms_json='["bad", "good"]',
                output=str(root / "deck.apkg"),
                preview_json=str(root / "preview.json"),
                cache_path=str(root / "cache.json"),
                media_dir=str(root / "media"),
            )
            output = io.StringIO()
            with (
                patch.object(application, "parse_args", return_value=args),
                patch.object(application, "load_items", return_value=items),
                patch.object(application, "resolve_openai_api_key", return_value="key"),
                patch.object(application, "openai_sdk_available", return_value=True),
                patch.object(application, "create_llm_client", return_value="client"),
                patch.object(application, "CacheStore", _Cache),
                patch.object(
                    application,
                    "build_cards",
                    side_effect=[
                        ValueError("boom"),
                        CardBuildResult(cards=[card], assets=[asset]),
                    ],
                ),
                patch.object(application, "write_preview_json") as preview_writer,
                patch.object(application, "create_deck_apkg") as exporter,
                patch.object(application.time, "sleep") as sleep,
                redirect_stdout(output),
            ):
                result = application.main()

            self.assertEqual(0, result)
            self.assertEqual(1, _Cache.instances[0].saved)
            self.assertEqual([call(0.25), call(0.25)], sleep.call_args_list)
            preview_writer.assert_called_once_with(
                (root / "preview.json").resolve(), [card], []
            )
            self.assertEqual(["good.mp3"], [p.name for p in exporter.call_args.kwargs["media_files"]])
            self.assertEqual(
                "[1/2] Generating card: mode=en_word, term=bad\n"
                "[ERROR] Failed on 'bad' (en_word): boom\n"
                "[2/2] Generating card: mode=en_word, term=good\n"
                "\nDone.\n"
                "- Cards generated: 1 / 2\n"
                f"- Deck file: {(root / 'deck.apkg').resolve()}\n"
                f"- Preview JSON: {(root / 'preview.json').resolve()}\n"
                "- Media files: 1\n"
                f"- Audio dir: {root / 'anki_audio'}\n"
                f"- Image dir: {root / 'anki_images'}\n"
                f"- Image review dir: {root / 'anki_image_review'}\n"
                "- To extend an existing Anki deck, keep --deck-name the same as that deck and import the new .apkg.\n",
                output.getvalue(),
            )

    def test_all_failed_saves_cache_before_returning_error(self):
        items = [InputItem("en_word", "bad")]
        _Cache.instances.clear()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = make_args(
                sleep=-1,
                output=str(root / "deck.apkg"),
                preview_json=str(root / "preview.json"),
                cache_path=str(root / "cache.json"),
                media_dir=str(root / "media"),
            )
            output = io.StringIO()
            with (
                patch.object(application, "parse_args", return_value=args),
                patch.object(application, "load_items", return_value=items),
                patch.object(application, "resolve_openai_api_key", return_value="key"),
                patch.object(application, "openai_sdk_available", return_value=True),
                patch.object(application, "create_llm_client", return_value="client"),
                patch.object(application, "CacheStore", _Cache),
                patch.object(
                    application,
                    "build_cards",
                    side_effect=RuntimeError("failed"),
                ),
                patch.object(application, "write_preview_json") as preview_writer,
                patch.object(application, "create_deck_apkg") as exporter,
                patch.object(application.time, "sleep") as sleep,
                redirect_stdout(output),
            ):
                result = application.main()
        self.assertEqual(1, result)
        self.assertEqual(1, _Cache.instances[0].saved)
        sleep.assert_called_once_with(0.0)
        preview_writer.assert_not_called()
        exporter.assert_not_called()
        self.assertTrue(output.getvalue().endswith("ERROR: all items failed; no deck generated.\n"))


if __name__ == "__main__":
    unittest.main()

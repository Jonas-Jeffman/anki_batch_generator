from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import media_assets
from media import audio, images
from models import InputItem
from utils import slugify, stable_guid


def mp3_bytes() -> bytes:
    return b"ID3" + (b"\0" * 253)


def jpeg_bytes() -> bytes:
    return b"\xff\xd8\xff" + (b"\0" * 1021)


class StreamingResponse:
    def __init__(self, chunks, ok=True):
        self.ok = ok
        self._chunks = chunks

    def iter_content(self, chunk_size):
        if chunk_size != 8192:
            raise AssertionError(f"unexpected chunk size: {chunk_size}")
        return iter(self._chunks)


class MediaSplitTests(unittest.TestCase):
    maxDiff = None

    def test_compatibility_exports_are_split_module_functions(self):
        self.assertEqual(
            ["ensure_noun_image", "ensure_english_audio", "ensure_japanese_audio"],
            media_assets.__all__,
        )
        self.assertIs(media_assets.ensure_noun_image, images.ensure_noun_image)
        self.assertIs(media_assets.ensure_english_audio, audio.ensure_english_audio)
        self.assertIs(media_assets.ensure_japanese_audio, audio.ensure_japanese_audio)

    def test_mp3_validation_threshold_and_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            self.assertFalse(audio._is_plausible_mp3(path))
            path.write_bytes(b"ID3" + b"\0" * 252)
            self.assertFalse(audio._is_plausible_mp3(path))
            path.write_bytes(mp3_bytes())
            self.assertTrue(audio._is_plausible_mp3(path))
            path.write_bytes(b"\xff\xe3" + b"\0" * 254)
            self.assertTrue(audio._is_plausible_mp3(path))
            path.write_bytes(b"NOPE" + b"\0" * 252)
            self.assertFalse(audio._is_plausible_mp3(path))

    def test_audio_url_queue_normalization_deduplication_and_order(self):
        self.assertEqual(
            [
                "https://dictionary.cambridge.org/media/english/primary.mp3",
                "https://www.oxfordlearnersdictionaries.com/media/fallback.mp3",
            ],
            audio._english_external_audio_url_queue(
                "/media/english/primary.mp3",
                [
                    "/media/english/primary.mp3",
                    "https://www.oxfordlearnersdictionaries.com/media/fallback.mp3",
                    "",
                ],
            ),
        )

    def test_external_audio_http_parameters_and_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "audio.mp3"
            get = Mock(return_value=StreamingResponse([b"ID3", b"payload"]))
            with patch.object(audio.requests, "get", get):
                self.assertTrue(audio.maybe_download_external_audio("https://example.test/audio.mp3", path))
            get.assert_called_once_with(
                "https://example.test/audio.mp3",
                timeout=15,
                stream=True,
                headers={"User-Agent": "anki-batch-generator/2.0"},
            )
            self.assertEqual(b"ID3payload", path.read_bytes())

    def test_tts_streaming_call_parameters_and_output_path(self):
        calls = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def stream_to_file(self, path):
                calls.append(("stream", path))
                path.write_bytes(mp3_bytes())

        class StreamingResponseFactory:
            def create(self, **kwargs):
                calls.append(("create", kwargs))
                return Response()

        client = type("Client", (), {})()
        client.audio = type("Audio", (), {})()
        client.audio.speech = type("Speech", (), {})()
        client.audio.speech.with_streaming_response = StreamingResponseFactory()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "speech.mp3"
            audio.synthesize_tts_to_file(client, "fixture-tts", "alloy", "rose", path)
            self.assertEqual(mp3_bytes(), path.read_bytes())
            self.assertEqual(
                [
                    (
                        "create",
                        {
                            "model": "fixture-tts",
                            "voice": "alloy",
                            "input": "rose",
                            "response_format": "mp3",
                        },
                    ),
                    ("stream", path),
                ],
                calls,
            )

    def test_each_external_url_failure_combination_preserves_attempt_order(self):
        urls = [
            "https://dictionary.cambridge.org/first.mp3",
            "https://www.oxfordlearnersdictionaries.com/second.mp3",
            "https://www.ldoceonline.com/third.mp3",
        ]
        for successful_index in (0, 1, 2, None):
            with self.subTest(successful_index=successful_index), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "audio.mp3"
                calls = []

                def download(url, filepath):
                    calls.append(url)
                    index = urls.index(url)
                    if index == successful_index:
                        filepath.write_bytes(mp3_bytes())
                        return True
                    return False

                with patch.object(audio, "maybe_download_external_audio", side_effect=download):
                    selected = audio._try_download_english_mp3_from_urls(urls, path)
                expected_calls = urls if successful_index is None else urls[: successful_index + 1]
                expected_url = "" if successful_index is None else urls[successful_index]
                self.assertEqual(expected_calls, calls)
                self.assertEqual(expected_url, selected)

    def test_english_audio_provider_fallback_matrix(self):
        item = InputItem("en_word", "matrix noun")
        urls = [
            "https://www.oxfordlearnersdictionaries.com/matrix.mp3",
            "https://dictionary.cambridge.org/matrix.mp3",
            "https://www.ldoceonline.com/matrix.mp3",
        ]
        cases = [
            ("Oxford available", urls[0], "oxford"),
            ("Oxford fails", urls[1], "cambridge"),
            ("Oxford and Cambridge fail", urls[2], "longman"),
            ("all providers fail", "", ""),
        ]

        for name, selected_url, expected_source in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                with (
                    patch.object(
                        audio,
                        "_try_download_english_mp3_from_urls",
                        return_value=selected_url,
                    ) as download,
                    patch.object(audio, "synthesize_tts_to_file") as tts,
                    redirect_stdout(io.StringIO()),
                ):
                    asset = audio.ensure_english_audio(
                        client=None,
                        media_dir=Path(tmp),
                        item=item,
                        spoken_term="matrix",
                        preferred_external_url=urls[0],
                        extra_audio_urls=urls[1:],
                        tts_model="fixture-tts",
                        voice="alloy",
                    )

                download.assert_called_once_with(
                    urls,
                    Path(tmp)
                    / f"audio_en_matrix_noun_{stable_guid(item.mode, item.term)[:8]}.mp3",
                )
                tts.assert_not_called()
                if selected_url:
                    self.assertEqual(
                        (expected_source, selected_url),
                        (asset.source, asset.source_url),
                    )
                else:
                    self.assertIsNone(asset)

    def test_invalid_download_is_deleted_before_next_fallback(self):
        urls = ["https://dictionary.cambridge.org/invalid.mp3", "https://example.test/valid.mp3"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            calls = []

            def download(url, filepath):
                calls.append(url)
                filepath.write_bytes(b"invalid" if len(calls) == 1 else mp3_bytes())
                return True

            with patch.object(audio, "maybe_download_external_audio", side_effect=download):
                selected = audio._try_download_english_mp3_from_urls(urls, path)
            self.assertEqual(urls, calls)
            self.assertEqual(urls[1], selected)
            self.assertEqual(mp3_bytes(), path.read_bytes())

    def test_english_audio_cache_hit_keeps_filename_and_skips_download_and_tts(self):
        item = InputItem("en_word", "nail noun")
        expected_name = f"audio_en_nail_noun_{stable_guid(item.mode, item.term)[:8]}.mp3"
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            cached_path = media_dir / expected_name
            cached_path.write_bytes(mp3_bytes())
            audio._write_cached_audio_url(
                cached_path, "https://dictionary.cambridge.org/nail.mp3"
            )
            with (
                patch.object(audio, "_try_download_english_mp3_from_urls") as download,
                patch.object(audio, "synthesize_tts_to_file") as tts,
            ):
                asset = audio.ensure_english_audio(
                    client=None,
                    media_dir=media_dir,
                    item=item,
                    spoken_term="nail",
                    preferred_external_url="https://dictionary.cambridge.org/nail.mp3",
                    tts_model="fixture-tts",
                    voice="alloy",
                )
            self.assertEqual(expected_name, asset.filename)
            self.assertEqual("cambridge", asset.source)
            download.assert_not_called()
            tts.assert_not_called()

    def test_same_word_and_pos_downloads_once_then_reuses_the_file(self):
        item = InputItem("en_word", "mutual noun")
        oxford_url = "https://www.oxfordlearnersdictionaries.com/mutual.mp3"
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)

            def download_once(urls, filepath):
                self.assertEqual([oxford_url], urls)
                filepath.write_bytes(mp3_bytes())
                return oxford_url

            with patch.object(
                audio,
                "_try_download_english_mp3_from_urls",
                side_effect=download_once,
            ) as download:
                output = io.StringIO()
                with redirect_stdout(output):
                    first = audio.ensure_english_audio(
                        client=None,
                        media_dir=media_dir,
                        item=item,
                        spoken_term="mutual",
                        preferred_external_url=oxford_url,
                        tts_model="fixture-tts",
                        voice="alloy",
                        audio_selection_scopes={oxford_url: "headword_shared"},
                    )
                    second = audio.ensure_english_audio(
                        client=None,
                        media_dir=media_dir,
                        item=item,
                        spoken_term="mutual",
                        preferred_external_url=oxford_url,
                        tts_model="fixture-tts",
                        voice="alloy",
                        audio_selection_scopes={oxford_url: "headword_shared"},
                    )
            download.assert_called_once()
            self.assertEqual(first.filename, second.filename)
            self.assertEqual(first.filepath, second.filepath)
            self.assertEqual(("oxford", oxford_url), (second.source, second.source_url))
            self.assertEqual(2, output.getvalue().count("selection_scope=headword_shared"))
            self.assertIn("status=downloaded selection_scope=headword_shared", output.getvalue())
            self.assertIn("status=cached selection_scope=headword_shared", output.getvalue())

    def test_english_audio_replaces_stale_lower_priority_cache(self):
        item = InputItem("en_word", "nail noun")
        filename = f"audio_en_nail_noun_{stable_guid(item.mode, item.term)[:8]}.mp3"
        oxford_url = "https://www.oxfordlearnersdictionaries.com/nail.mp3"
        cambridge_url = "https://dictionary.cambridge.org/nail.mp3"
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            cached_path = media_dir / filename
            cached_path.write_bytes(mp3_bytes())
            audio._write_cached_audio_url(cached_path, cambridge_url)

            def replace_with_oxford(urls, filepath):
                self.assertEqual([oxford_url], urls)
                self.assertEqual(f"{filename}.download", filepath.name)
                filepath.write_bytes(mp3_bytes())
                return oxford_url

            with patch.object(
                audio,
                "_try_download_english_mp3_from_urls",
                side_effect=replace_with_oxford,
            ) as download:
                asset = audio.ensure_english_audio(
                    client=None,
                    media_dir=media_dir,
                    item=item,
                    spoken_term="nail",
                    preferred_external_url=oxford_url,
                    extra_audio_urls=[cambridge_url],
                    tts_model="fixture-tts",
                    voice="alloy",
                )
            download.assert_called_once()
            self.assertEqual(("oxford", oxford_url), (asset.source, asset.source_url))
            self.assertEqual(oxford_url, audio._read_cached_audio_url(cached_path))

    def test_higher_priority_failure_reuses_verified_cached_fallback(self):
        item = InputItem("en_word", "mutual noun")
        filename = f"audio_en_mutual_noun_{stable_guid(item.mode, item.term)[:8]}.mp3"
        oxford_url = "https://www.oxfordlearnersdictionaries.com/mutual.mp3"
        cambridge_url = "https://dictionary.cambridge.org/mutual.mp3"
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            cached_path = media_dir / filename
            original_bytes = mp3_bytes() + b"cambridge"
            cached_path.write_bytes(original_bytes)
            audio._write_cached_audio_url(cached_path, cambridge_url)

            with patch.object(
                audio,
                "_try_download_english_mp3_from_urls",
                return_value="",
            ) as download:
                asset = audio.ensure_english_audio(
                    client=None,
                    media_dir=media_dir,
                    item=item,
                    spoken_term="mutual",
                    preferred_external_url=oxford_url,
                    extra_audio_urls=[cambridge_url],
                    tts_model="fixture-tts",
                    voice="alloy",
                )
            download.assert_called_once_with(
                [oxford_url], media_dir / f"{filename}.download"
            )
            self.assertEqual(("cambridge", cambridge_url), (asset.source, asset.source_url))
            self.assertEqual(original_bytes, cached_path.read_bytes())
            self.assertFalse((media_dir / f"{filename}.download").exists())

    def test_audio_source_metadata_records_url_and_derived_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            filepath = Path(tmp) / "word.mp3"
            url = "https://www.oxfordlearnersdictionaries.com/word.mp3"
            audio._write_cached_audio_url(filepath, url)
            metadata = json.loads(
                audio._audio_source_metadata_path(filepath).read_text(encoding="utf-8")
            )
            self.assertEqual({"source": "oxford", "source_url": url}, metadata)

    def test_english_audio_does_not_trust_legacy_cache_without_source_metadata(self):
        item = InputItem("en_word", "nail verb")
        filename = f"audio_en_nail_verb_{stable_guid(item.mode, item.term)[:8]}.mp3"
        oxford_url = "https://www.oxfordlearnersdictionaries.com/nail.mp3"
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            cached_path = media_dir / filename
            cached_path.write_bytes(mp3_bytes())

            def download_oxford(urls, filepath):
                self.assertEqual([oxford_url], urls)
                filepath.write_bytes(mp3_bytes())
                return oxford_url

            with patch.object(
                audio,
                "_try_download_english_mp3_from_urls",
                side_effect=download_oxford,
            ) as download:
                asset = audio.ensure_english_audio(
                    client=None,
                    media_dir=media_dir,
                    item=item,
                    spoken_term="nail",
                    preferred_external_url=oxford_url,
                    tts_model="fixture-tts",
                    voice="alloy",
                )
            download.assert_called_once()
            self.assertEqual("oxford", asset.source)
            self.assertEqual(oxford_url, audio._read_cached_audio_url(cached_path))

    def test_english_audio_all_dictionary_failures_do_not_use_tts(self):
        item = InputItem("en_word", "rose")
        expected_name = f"audio_en_rose_{stable_guid(item.mode, item.term)[:8]}_uk.mp3"
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)

            with (
                patch.object(audio, "_try_download_english_mp3_from_urls", return_value="") as download,
                patch.object(audio, "synthesize_tts_to_file") as tts,
            ):
                asset = audio.ensure_english_audio(
                    client=None,
                    media_dir=media_dir,
                    item=item,
                    spoken_term="rose",
                    preferred_external_url="https://dictionary.cambridge.org/rose.mp3",
                    extra_audio_urls=["https://example.test/fallback.mp3"],
                    tts_model="fixture-tts",
                    voice="alloy",
                    filename_suffix="uk",
                )
            download.assert_called_once_with(
                ["https://dictionary.cambridge.org/rose.mp3", "https://example.test/fallback.mp3"],
                media_dir / expected_name,
            )
            tts.assert_not_called()
            self.assertIsNone(asset)

    def test_english_audio_without_dictionary_url_ignores_stale_cache(self):
        item = InputItem("en_word", "rose")
        filename = f"audio_en_rose_{stable_guid(item.mode, item.term)[:8]}.mp3"
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            (media_dir / filename).write_bytes(mp3_bytes())
            with (
                patch.object(audio, "_try_download_english_mp3_from_urls") as download,
                patch.object(audio, "synthesize_tts_to_file") as tts,
            ):
                asset = audio.ensure_english_audio(
                    client=None,
                    media_dir=media_dir,
                    item=item,
                    spoken_term="rose",
                    preferred_external_url="",
                    extra_audio_urls=[],
                    tts_model="fixture-tts",
                    voice="alloy",
                )
            self.assertIsNone(asset)
            download.assert_not_called()
            tts.assert_not_called()

    def test_japanese_audio_cache_and_tts_paths(self):
        item = InputItem("ja_word", "勉強")
        expected_name = f"audio_ja_{slugify(item.term)}_{stable_guid(item.mode, item.term)[:8]}.mp3"
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            cached_path = media_dir / expected_name
            cached_path.write_bytes(mp3_bytes())
            with patch.object(audio, "synthesize_tts_to_file") as tts:
                cached = audio.ensure_japanese_audio(None, media_dir, item, "fixture-tts", "alloy")
            tts.assert_not_called()
            self.assertEqual("cache", cached.source)

            cached_path.unlink()

            def synthesize(client, model, voice, text, filepath):
                filepath.write_bytes(mp3_bytes())

            with patch.object(audio, "synthesize_tts_to_file", side_effect=synthesize) as tts:
                generated = audio.ensure_japanese_audio(None, media_dir, item, "fixture-tts", "alloy")
            tts.assert_called_once()
            self.assertEqual(expected_name, generated.filename)
            self.assertEqual("openai_tts", generated.source)

    def test_image_cache_hit_keeps_filename_and_review_copy(self):
        item = InputItem("en_word", "rose")
        expected_name = f"img_en_rose_{stable_guid(item.mode, item.term)[:8]}.jpg"
        url = "https://dictionary.cambridge.org/images/full/rose.jpg"
        with tempfile.TemporaryDirectory() as tmp:
            image_dir = Path(tmp) / "anki_images"
            image_dir.mkdir()
            image_path = image_dir / expected_name
            image_path.write_bytes(jpeg_bytes())
            with patch.object(images.requests, "get") as get:
                asset = images.ensure_noun_image(image_dir, item, url, "cambridge")
            get.assert_not_called()
            self.assertEqual(expected_name, asset.filename)
            review = Path(tmp) / "anki_image_review" / "rose__cambridge.jpg"
            self.assertEqual(jpeg_bytes(), review.read_bytes())

    def test_image_download_parameters_review_copy_and_invalid_cleanup(self):
        item = InputItem("en_word", "rose")
        url = "https://dictionary.cambridge.org/images/full/rose.jpg"
        with tempfile.TemporaryDirectory() as tmp:
            image_dir = Path(tmp) / "anki_images"
            get = Mock(return_value=StreamingResponse([jpeg_bytes()]))
            with patch.object(images.requests, "get", get):
                asset = images.ensure_noun_image(image_dir, item, url, "cambridge")
            get.assert_called_once_with(
                url,
                timeout=15,
                stream=True,
                headers={"User-Agent": "anki-batch-generator/2.0"},
            )
            self.assertEqual(asset.filepath.read_bytes(), jpeg_bytes())
            self.assertEqual(
                jpeg_bytes(),
                (Path(tmp) / "anki_image_review" / "rose__cambridge.jpg").read_bytes(),
            )

        with tempfile.TemporaryDirectory() as tmp:
            image_dir = Path(tmp) / "anki_images"
            with patch.object(images.requests, "get", return_value=StreamingResponse([b"broken"])):
                self.assertIsNone(images.ensure_noun_image(image_dir, item, url, "cambridge"))
            self.assertFalse(any(image_dir.glob("*")))

    def test_invalid_preferred_image_uses_dictionary_fallback(self):
        item = InputItem("en_word", "rose noun")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(images, "fetch_dictionary_image_url", return_value="") as fallback:
                self.assertIsNone(
                    images.ensure_noun_image(
                        Path(tmp),
                        item,
                        "https://www.ldoceonline.com/media/english/illustration/rose.jpg",
                        "longman",
                    )
                )
            fallback.assert_called_once_with("rose")


if __name__ == "__main__":
    unittest.main()

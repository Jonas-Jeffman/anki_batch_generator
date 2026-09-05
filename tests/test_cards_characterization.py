from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from tests.support import GOLDEN_ROOT, load_json

import card_builder
from cards import builder as cards_builder
from common import AudioAsset, BuiltCard, CacheStore, DictionaryEntryResult, InputItem, stable_guid
from utils import slugify


LLM_RESULTS = {
    "nail noun": {
        "pronunciation_text": "/neɪl/",
        "definition_en": "LLM definition replaced by dictionary.",
        "example_simple_en": "She hit the nail with a hammer.",
    },
    "nail verb": {
        "pronunciation_text": "/neɪl/",
        "definition_en": "LLM definition replaced by dictionary.",
        "example_simple_en": "He nailed the board to the wall.",
    },
    "rose": {
        "pronunciation_text": "/rəʊz/",
        "definition_en": "LLM definition replaced by dictionary.",
        "example_simple_en": "The rose smells sweet.",
    },
    "play hooky": {
        "pronunciation_text": "",
        "definition_en": "LLM definition replaced by dictionary.",
        "example_simple_en": "They played hooky on Friday.",
    },
}


RENDER_CASES = {
    "en_word": lambda: card_builder.build_en_word_card(
        "rose",
        LLM_RESULTS["rose"],
        "/rəʊz/",
        [AudioAsset("audio_en_rose.mp3", Path("/fixture/audio_en_rose.mp3"))],
        AudioAsset("img_en_rose.jpg", Path("/fixture/img_en_rose.jpg")),
    ),
    "ja_word": lambda: card_builder.build_ja_word_card(
        "勉強",
        {
            "reading_kana": "べんきょう",
            "explanation_ja": "知識を身につけること。",
            "example_simple_ja": "毎日日本語を勉強します。",
        },
        AudioAsset("audio_ja_study.mp3", Path("/fixture/audio_ja_study.mp3")),
    ),
    "interview": lambda: card_builder.build_knowledge_card(
        "interview",
        "What is a mutex?",
        {
            "question_title": "What is a mutex?",
            "concise_answer": "A mutex protects shared state.",
            "key_points": ["One owner", "Prevents races"],
            "easy_example": "Lock a shared counter.",
        },
    ),
    "paper": lambda: card_builder.build_knowledge_card(
        "paper",
        "attention mechanism",
        {
            "topic_title": "Attention mechanism",
            "core_idea": "Weight relevant inputs.",
            "why_it_matters": "It focuses computation.",
            "easy_example": "Focus on current source words.",
        },
    ),
    "interest": lambda: card_builder.build_knowledge_card(
        "interest",
        "aurora",
        {
            "topic_title": "Aurora",
            "what_it_is": "Colored polar light.",
            "fun_fact": "Gas determines color.",
            "easy_example": "Green ribbons cross the sky.",
        },
    ),
}


def _entry(data):
    allowed = {field.name for field in fields(DictionaryEntryResult)}
    return DictionaryEntryResult(**{key: value for key, value in data.items() if key in allowed})


class CardsCharacterizationTests(unittest.TestCase):
    maxDiff = None

    def test_five_mode_renderer_goldens_and_preview_json(self):
        expected = load_json(GOLDEN_ROOT / "renderers.json")
        actual = {mode: vars(factory()) for mode, factory in RENDER_CASES.items()}
        self.assertEqual(expected["cards"], actual)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "preview.json"
            card_builder.write_preview_json(output, [factory() for factory in RENDER_CASES.values()])
            self.assertEqual(expected["preview_json"], output.read_text(encoding="utf-8"))

    def test_english_pipeline_cache_keys_cards_guids_and_media_names(self):
        expected = load_json(GOLDEN_ROOT / "english_pipeline.json")
        dictionary = load_json(GOLDEN_ROOT / "dictionary_results.json")

        for raw_term in ("nail noun", "nail verb", "rose", "play hooky"):
            with self.subTest(term=raw_term), tempfile.TemporaryDirectory() as tmp:
                entries = [_entry(value) for value in dictionary[raw_term]["provider_entries"]]
                cache = CacheStore(Path(tmp) / "cache.json")
                item = InputItem(mode="en_word", term=raw_term, hint="", tags=["custom tag"])
                audio_name = (
                    f"audio_en_{slugify(raw_term)}_"
                    f"{stable_guid('en_word', raw_term)[:8]}.mp3"
                )
                image_name = (
                    f"img_en_{slugify(raw_term)}_"
                    f"{stable_guid('en_word', raw_term)[:8]}.jpg"
                )

                def fake_audio(**kwargs):
                    return AudioAsset(audio_name, Path(tmp) / audio_name, "audio", "fixture", "fixture:audio")

                def fake_image(**kwargs):
                    return AudioAsset(image_name, Path(tmp) / image_name, "image", "cambridge", "fixture:image")

                with (
                    patch.object(cards_builder, "fetch_dictionary_entries", return_value=entries),
                    patch.object(cards_builder, "call_openai_json", return_value=LLM_RESULTS[raw_term]),
                    patch.object(cards_builder, "ensure_english_audio", side_effect=fake_audio) as audio_mock,
                    patch.object(cards_builder, "ensure_noun_image", side_effect=fake_image) as image_mock,
                ):
                    card, assets = card_builder.build_card(
                        client=None,
                        item=item,
                        model="gpt-5.4",
                        tts_model="gpt-4o-mini-tts",
                        audio_dir=Path(tmp) / "audio",
                        image_dir=Path(tmp) / "images",
                        tts_voice_en="alloy",
                        tts_voice_ja="alloy",
                        cache=cache,
                        reasoning_effort="medium",
                    )

                actual = {
                    "cache_keys": list(cache.data),
                    "card": vars(card),
                    "final_guid": stable_guid("Regression::Deck", card.guid_seed),
                    "assets": [asset.filename for asset in assets],
                    "audio_calls": audio_mock.call_count,
                    "image_calls": image_mock.call_count,
                }
                self.assertEqual(expected[raw_term], actual)


if __name__ == "__main__":
    unittest.main()

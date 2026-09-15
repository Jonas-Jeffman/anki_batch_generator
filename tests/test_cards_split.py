from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anki_generator.compat import card_builder
from anki_generator.llm.cache import CacheStore
from anki_generator.cards import builder, preview, renderers
from anki_generator.models import InputItem


MODE_RESULTS = {
    "en_word": {
        "pronunciation_text": "/rəʊz/",
        "definition_en": "a flower",
        "example_simple_en": "The rose is red.",
    },
    "ja_word": {
        "reading_kana": "べんきょう",
        "explanation_ja": "学ぶこと。",
        "example_simple_ja": "毎日勉強します。",
    },
    "interview": {
        "question_title": "What is a mutex?",
        "concise_answer": "A lock for shared state.",
        "key_points": ["One owner"],
        "easy_example": "Lock a counter.",
    },
    "paper": {
        "topic_title": "Attention",
        "core_idea": "Weight relevant inputs.",
        "why_it_matters": "It focuses computation.",
        "easy_example": "Focus on one word.",
    },
    "interest": {
        "topic_title": "Aurora",
        "what_it_is": "Polar light.",
        "fun_fact": "Gas affects color.",
        "easy_example": "Green ribbons appear.",
    },
}


class CardsSplitTests(unittest.TestCase):
    def test_compatibility_module_reexports_split_implementations(self):
        expected = {
            "build_card": builder.build_card,
            "call_openai_json": builder.call_openai_json,
            "collect_audio_fallback_urls": builder.collect_audio_fallback_urls,
            "build_en_word_card": renderers.build_en_word_card,
            "build_ja_word_card": renderers.build_ja_word_card,
            "build_knowledge_card": renderers.build_knowledge_card,
            "write_preview_json": preview.write_preview_json,
            "write_dict_preview_json": preview.write_dict_preview_json,
            "run_dictionary_test_only": preview.run_dictionary_test_only,
        }
        for name, implementation in expected.items():
            self.assertIs(getattr(card_builder, name), implementation)

    def test_full_pipeline_keeps_all_mode_branches_and_tag_order(self):
        terms = {
            "en_word": "rose",
            "ja_word": "勉強",
            "interview": "mutex",
            "paper": "attention",
            "interest": "aurora",
        }
        expected_base_tags = {
            "en_word": ["english", "vocab"],
            "ja_word": ["japanese", "vocab"],
            "interview": ["interview", "knowledge"],
            "paper": ["paper", "knowledge"],
            "interest": ["interest", "knowledge"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for mode, term in terms.items():
                with self.subTest(mode=mode):
                    item = InputItem(
                        mode=mode,
                        term=term,
                        tags=["extra tag", expected_base_tags[mode][0]],
                    )
                    with (
                        patch.object(builder, "fetch_dictionary_entries", return_value=[]),
                        patch.object(builder, "call_openai_json", return_value=MODE_RESULTS[mode]),
                        patch.object(builder, "ensure_english_audio", return_value=None),
                        patch.object(builder, "ensure_japanese_audio", return_value=None),
                    ):
                        card, assets = builder.build_card(
                            client=None,
                            item=item,
                            model="fixture-model",
                            tts_model="fixture-tts",
                            audio_dir=root / "audio",
                            image_dir=root / "images",
                            tts_voice_en="alloy",
                            tts_voice_ja="alloy",
                            cache=CacheStore(root / f"{mode}.json"),
                            reasoning_effort="medium",
                        )

                    self.assertEqual(
                        expected_base_tags[mode] + ["extra_tag"],
                        card.tags,
                    )
                    self.assertEqual(f"{mode}::{term}", card.guid_seed)
                    self.assertEqual([], assets)


if __name__ == "__main__":
    unittest.main()

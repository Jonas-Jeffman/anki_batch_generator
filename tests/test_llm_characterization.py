from __future__ import annotations

import copy
import unittest

from tests.support import GOLDEN_ROOT, load_json

from anki_generator.compat import card_builder
from anki_generator.models import InputItem


CASES = {
    "en_word": {
        "item": InputItem(mode="en_word", term="nail noun", hint="metal fastener"),
        "phonetic_hint": "/neɪl/",
        "term_for_pronunciation": "nail",
        "phonetic_source": "oxford",
        "dictionary_definition": "a small pointed piece of metal",
        "requested_pos": "noun",
        "parsed_word": "nail",
        "response": {
            "pronunciation_text": "/neɪl/",
            "definition_en": "a small pointed piece of metal",
            "example_simple_en": "She hit the nail with a hammer.",
        },
    },
    "ja_word": {
        "item": InputItem(mode="ja_word", term="勉強", hint=""),
        "response": {
            "reading_kana": "べんきょう",
            "explanation_ja": "知識を身につけること。",
            "example_simple_ja": "毎日日本語を勉強します。",
        },
    },
    "interview": {
        "item": InputItem(mode="interview", term="What is a mutex?", hint=""),
        "response": {
            "question_title": "What is a mutex?",
            "concise_answer": "A mutex protects shared state from concurrent access.",
            "key_points": ["Only one owner at a time", "Prevents data races"],
            "easy_example": "Lock before updating a shared counter.",
        },
    },
    "paper": {
        "item": InputItem(mode="paper", term="attention mechanism", hint=""),
        "response": {
            "topic_title": "Attention mechanism",
            "core_idea": "Weight relevant inputs when producing an output.",
            "why_it_matters": "It helps models focus on useful context.",
            "easy_example": "A translator focuses on the current source words.",
        },
    },
    "interest": {
        "item": InputItem(mode="interest", term="aurora", hint=""),
        "response": {
            "topic_title": "Aurora",
            "what_it_is": "Colored light seen in polar skies.",
            "fun_fact": "Its colors depend on atmospheric gases.",
            "easy_example": "Green ribbons appear over a northern town.",
        },
    },
}


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, response_json):
        import json

        self.response_json = response_json
        self.calls = []
        self._json = json

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return _Response(self._json.dumps(self.response_json, ensure_ascii=False))


class _Client:
    def __init__(self, response_json):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _Completions(response_json)


class LLMCharacterizationTests(unittest.TestCase):
    maxDiff = None

    def test_prompts_payload_sdk_kwargs_and_results(self):
        expected = load_json(GOLDEN_ROOT / "llm_calls.json")

        for mode, case in CASES.items():
            with self.subTest(mode=mode):
                client = _Client(case["response"])
                result = card_builder.call_openai_json(
                    client=client,
                    model="gpt-5.4",
                    item=case["item"],
                    phonetic_hint=case.get("phonetic_hint", ""),
                    term_for_pronunciation=case.get("term_for_pronunciation", case["item"].term),
                    reasoning_effort="medium",
                    phonetic_source=case.get("phonetic_source", ""),
                    dictionary_definition=case.get("dictionary_definition", ""),
                    requested_pos=case.get("requested_pos", ""),
                    parsed_word=case.get("parsed_word", ""),
                )
                kwargs = client.chat.completions.calls[0]
                actual = {
                    "system_prompt": kwargs["messages"][0]["content"],
                    "user_payload": kwargs["messages"][1]["content"],
                    "sdk_kwargs": kwargs,
                    "result": result,
                }
                self.assertEqual(expected[mode], actual)


if __name__ == "__main__":
    unittest.main()

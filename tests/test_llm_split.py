from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import card_builder
from llm import client as client_module
from llm import prompts


class _Response:
    def __init__(self, content):
        message = type("Message", (), {"content": content})()
        self.choices = [type("Choice", (), {"message": message})()]


class _SequenceCompletions:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)


def _sdk_with_completions(outcomes):
    sdk = type("SDK", (), {})()
    sdk.chat = type("Chat", (), {})()
    sdk.chat.completions = _SequenceCompletions(outcomes)
    return sdk


class LLMClientSplitTests(unittest.TestCase):
    def test_card_builder_reexports_prompt_api(self):
        for name in (
            "build_en_word_prompt",
            "build_ja_word_prompt",
            "build_interview_prompt",
            "build_paper_prompt",
            "build_interest_prompt",
            "build_user_payload",
        ):
            self.assertIs(getattr(card_builder, name), getattr(prompts, name))
        self.assertIs(card_builder.PROMPT_MAP, prompts.PROMPT_MAP)

    def test_non_gpt5_json_call_uses_exact_kwargs_without_reasoning_effort(self):
        sdk = _sdk_with_completions(['{"answer": 1}'])
        result = client_module.LLMClient(sdk).generate_json(
            model="gpt-4.1",
            system_prompt="system",
            user_prompt="user",
            reasoning_effort="medium",
        )
        self.assertEqual({"answer": 1}, result)
        self.assertEqual(
            [{
                "model": "gpt-4.1",
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "user"},
                ],
            }],
            sdk.chat.completions.calls,
        )

    def test_empty_response_retries_then_preserves_error(self):
        sdk = _sdk_with_completions(["", " ", None])
        with patch("utils.time.sleep") as sleep:
            with self.assertRaisesRegex(ValueError, "OpenAI returned empty content"):
                client_module.LLMClient(sdk).generate_json(
                    model="gpt-5.4",
                    system_prompt="s",
                    user_prompt="u",
                    reasoning_effort="high",
                )
        self.assertEqual(3, len(sdk.chat.completions.calls))
        self.assertEqual([call(2.0), call(4.0)], sleep.call_args_list)

    def test_invalid_json_retries_and_raises_decode_error(self):
        sdk = _sdk_with_completions(["not json"] * 3)
        with patch("utils.time.sleep"):
            with self.assertRaises(json.JSONDecodeError):
                client_module.LLMClient(sdk).generate_json(
                    model="gpt-5.4",
                    system_prompt="s",
                    user_prompt="u",
                    reasoning_effort="low",
                )
        self.assertEqual(3, len(sdk.chat.completions.calls))

    def test_transient_json_failure_retries_to_success(self):
        sdk = _sdk_with_completions([RuntimeError("temporary"), '{"ok": true}'])
        with patch("utils.time.sleep") as sleep:
            result = client_module.LLMClient(sdk).generate_json(
                model="gpt-5.4",
                system_prompt="s",
                user_prompt="u",
                reasoning_effort="medium",
            )
        self.assertEqual({"ok": True}, result)
        sleep.assert_called_once_with(2.0)

    def test_factory_preserves_sdk_constructor_kwargs(self):
        sdk = object()
        constructor = Mock(return_value=sdk)
        with patch.object(client_module, "OpenAISDK", constructor):
            wrapped = client_module.create_llm_client("key", "https://gateway.example")
            self.assertIs(sdk, wrapped.sdk_client)
            constructor.assert_called_once_with(
                api_key="key", base_url="https://gateway.example"
            )

            constructor.reset_mock()
            client_module.create_llm_client("key")
            constructor.assert_called_once_with(api_key="key")

    def test_speech_call_retries_with_exact_kwargs(self):
        destination = Path(tempfile.gettempdir()) / "llm-client-test.mp3"
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        create = Mock(side_effect=[RuntimeError("temporary"), response])
        sdk = type("SDK", (), {})()
        sdk.audio = type("Audio", (), {})()
        sdk.audio.speech = type("Speech", (), {})()
        sdk.audio.speech.with_streaming_response = type("Streaming", (), {"create": create})()

        with patch("utils.time.sleep") as sleep:
            client_module.LLMClient(sdk).generate_speech(
                model="tts-model", voice="alloy", text="hello", output_path=destination
            )

        expected_kwargs = {
            "model": "tts-model",
            "voice": "alloy",
            "input": "hello",
            "response_format": "mp3",
        }
        self.assertEqual([call(**expected_kwargs), call(**expected_kwargs)], create.call_args_list)
        response.stream_to_file.assert_called_once_with(destination)
        sleep.assert_called_once_with(2.0)


if __name__ == "__main__":
    unittest.main()

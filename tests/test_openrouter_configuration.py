from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from anki_generator import config
from anki_generator.llm import client as client_module


class OpenRouterConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        environment_patch = patch.dict(os.environ, {}, clear=True)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)
        local_key_patch = patch.object(
            config, "read_optional_local_openai_key", return_value=""
        )
        local_key_patch.start()
        self.addCleanup(local_key_patch.stop)

    def test_openrouter_url_normalization(self) -> None:
        for url in (
            "https://openrouter.ai/api",
            "https://openrouter.ai/api/v1",
            "https://openrouter.ai/api/v1/",
        ):
            with self.subTest(url=url):
                self.assertEqual(
                    "https://openrouter.ai/api/v1",
                    config.resolve_openai_base_url(url),
                )

    def test_explicit_configuration_overrides_environment(self) -> None:
        os.environ.update(
            OPENAI_API_KEY="env-key",
            OPENAI_BASE_URL="https://other.example/v1",
        )

        self.assertEqual("cli-key", config.resolve_openai_api_key("cli-key"))
        self.assertEqual(
            "https://openrouter.ai/api/v1",
            config.resolve_openai_base_url("https://openrouter.ai/api/v1"),
        )

    def test_environment_configuration_fallback(self) -> None:
        os.environ.update(
            OPENAI_API_KEY="fixture-openrouter-key",
            OPENAI_BASE_URL="https://openrouter.ai/api/v1",
        )

        self.assertEqual(
            "fixture-openrouter-key", config.resolve_openai_api_key("")
        )
        self.assertEqual(
            "https://openrouter.ai/api/v1", config.resolve_openai_base_url("")
        )

    def test_openrouter_factory_configuration(self) -> None:
        sdk = Mock()
        with patch.object(client_module, "OpenAISDK", return_value=sdk) as factory:
            client = client_module.create_llm_client(
                api_key="fixture-openrouter-key",
                base_url="https://openrouter.ai/api/v1",
            )

        factory.assert_called_once_with(
            api_key="fixture-openrouter-key",
            base_url="https://openrouter.ai/api/v1",
        )
        self.assertIs(sdk, client.sdk_client)

    def test_official_factory_without_base_url(self) -> None:
        self.assertIsNone(config.resolve_openai_base_url(""))
        sdk = Mock()
        with patch.object(client_module, "OpenAISDK", return_value=sdk) as factory:
            client = client_module.create_llm_client("fixture-official-key")

        factory.assert_called_once_with(api_key="fixture-official-key")
        self.assertIs(sdk, client.sdk_client)

    def test_openrouter_model_and_json_request_are_preserved(self) -> None:
        sdk = Mock()
        message = Mock(content='{"ok":true}')
        sdk.chat.completions.create.return_value.choices = [Mock(message=message)]

        result = client_module.LLMClient(sdk).generate_json(
            model="openai/gpt-4.1",
            system_prompt="Return JSON only.",
            user_prompt="fixture",
            reasoning_effort="medium",
        )

        self.assertEqual({"ok": True}, result)
        sdk.chat.completions.create.assert_called_once_with(
            model="openai/gpt-4.1",
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": "fixture"},
            ],
        )


if __name__ == "__main__":
    unittest.main()

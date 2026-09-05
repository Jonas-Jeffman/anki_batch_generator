from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from utils import retry_call

try:
    from openai import OpenAI as OpenAISDK
except ImportError:
    OpenAISDK = None


class LLMClient:
    """Small wrapper around the currently configured OpenAI-compatible SDK client."""

    def __init__(self, sdk_client: Any):
        self.sdk_client = sdk_client

    def generate_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        reasoning_effort: str,
    ) -> Dict:
        def _call() -> Dict:
            kwargs = {
                "model": model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            # Newer GPT-5 series supports reasoning effort; older families will ignore this parameter
            # if the installed SDK/API surface accepts it.
            if model.startswith("gpt-5"):
                kwargs["reasoning_effort"] = reasoning_effort

            response = self.sdk_client.chat.completions.create(**kwargs)
            text = (response.choices[0].message.content or "").strip()
            if not text:
                raise ValueError("OpenAI returned empty content.")
            return json.loads(text)

        return retry_call(_call, retries=3, base_sleep=2.0)

    def generate_speech(
        self,
        *,
        model: str,
        voice: str,
        text: str,
        output_path: Path,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        def _call() -> None:
            with self.sdk_client.audio.speech.with_streaming_response.create(
                model=model,
                voice=voice,
                input=text,
                response_format="mp3",
            ) as response:
                response.stream_to_file(output_path)

        retry_call(_call, retries=3, base_sleep=2.0)


def openai_sdk_available() -> bool:
    return OpenAISDK is not None


def create_llm_client(api_key: str, base_url: Optional[str] = None) -> LLMClient:
    if OpenAISDK is None:
        raise RuntimeError("openai package is not installed")
    if base_url:
        sdk_client = OpenAISDK(api_key=api_key, base_url=base_url)
    else:
        sdk_client = OpenAISDK(api_key=api_key)
    return LLMClient(sdk_client)


def generate_json(
    client: Any,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    reasoning_effort: str,
) -> Dict:
    if isinstance(client, LLMClient):
        return client.generate_json(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            reasoning_effort=reasoning_effort,
        )
    return LLMClient(client).generate_json(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        reasoning_effort=reasoning_effort,
    )


def generate_speech(
    client: Any,
    tts_model: str,
    voice: str,
    text: str,
    filepath: Path,
) -> None:
    if isinstance(client, LLMClient):
        client.generate_speech(
            model=tts_model,
            voice=voice,
            text=text,
            output_path=filepath,
        )
        return
    LLMClient(client).generate_speech(
        model=tts_model,
        voice=voice,
        text=text,
        output_path=filepath,
    )

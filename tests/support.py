from __future__ import annotations

import gzip
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


TEST_ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = TEST_ROOT / "fixtures"
GOLDEN_ROOT = FIXTURE_ROOT / "golden"


class FixtureResponse:
    def __init__(self, *, text: str, url: str, status: int = 200, json_data: Any = None):
        self.text = text
        self.url = url
        self.status_code = status
        self.ok = 200 <= status < 400
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            return json.loads(self.text)
        return self._json_data


class DictionaryFixtureHTTP:
    """Replay recorded dictionary pages and retain the exact request trace."""

    def __init__(self):
        self.manifest = load_json(FIXTURE_ROOT / "dictionary" / "manifest.json")
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs) -> FixtureResponse:
        self.calls.append({"url": url, **kwargs})
        path = urlparse(url).path.rstrip("/")
        provider = self._provider(url)
        term = path.rsplit("/", 1)[-1]
        key = f"{provider}:{term}"
        record = self.manifest.get(key)
        if record is None and provider == "oxford":
            base_term = term.removesuffix("_1").removesuffix("_2").removesuffix("_3").removesuffix("_4")
            record = self.manifest.get(f"{provider}:{base_term}")
        if record is None:
            raise AssertionError(f"unrecorded HTTP request: {url}")
        fixture = FIXTURE_ROOT / "dictionary" / record["fixture"]
        with gzip.open(fixture, "rt", encoding="utf-8", errors="replace") as handle:
            body = handle.read()
        return FixtureResponse(
            text=body,
            url=record["final_url"],
            status=record["status"],
        )

    @staticmethod
    def _provider(url: str) -> str:
        if "cambridge.org" in url:
            return "cambridge"
        if "oxfordlearnersdictionaries.com" in url:
            return "oxford"
        if "ldoceonline.com" in url:
            return "longman"
        raise AssertionError(f"unexpected HTTP host: {url}")


def public_dataclass(value: Any) -> dict[str, Any]:
    return asdict(value)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalized_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "url": call["url"],
            "timeout": call.get("timeout"),
            "headers": call.get("headers"),
            "stream": call.get("stream", False),
        }
        for call in calls
    ]

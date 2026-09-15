from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


SUPPORTED_MODES = {"en_word", "ja_word", "interview", "paper", "interest"}
DEFAULT_TEXT_MODEL = "gpt-5.4"
DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_SLEEP = 2.0
LLM_SCHEMA_VERSION = "no_zh_v5_safe_ipa"

PACKAGE_DIR = Path(__file__).resolve().parent
# User files stay beside the CLI entry point, not inside the Python package.
SCRIPT_DIR = PACKAGE_DIR.parent
EXAMPLE_AUDIO_ICON_PATH = PACKAGE_DIR / "resources" / "audio_bre_initial.svg"
EXAMPLE_AUDIO_ICON_FILENAME = EXAMPLE_AUDIO_ICON_PATH.name
DEFAULT_TERMS_JSON = SCRIPT_DIR / "terms.json"
DEFAULT_TERMS_TXT = SCRIPT_DIR / "terms.txt"
LOCAL_OPENAI_KEY_FILE = SCRIPT_DIR / ".openai_api_key"


def read_optional_local_openai_key() -> str:
    """Load key from LOCAL_OPENAI_KEY_FILE if present (one secret per line, # comments allowed)."""
    path = LOCAL_OPENAI_KEY_FILE
    if not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("OPENAI_API_KEY="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
            return s
    except OSError:
        return ""
    return ""


def resolve_openai_api_key(cli_value: str) -> str:
    order = (
        cli_value.strip(),
        os.getenv("OPENAI_API_KEY", "").strip(),
        read_optional_local_openai_key().strip(),
    )
    for key in order:
        if key:
            return key
    return ""


def resolve_openai_base_url(cli_value: str) -> Optional[str]:
    """Third-party OpenAI-compatible gateways (e.g. ChatAnywhere) need a custom base_url."""
    for raw in (cli_value.strip(), os.getenv("OPENAI_BASE_URL", "").strip()):
        if raw:
            url = raw.rstrip("/")
            if not url.endswith("/v1"):
                url = f"{url}/v1"
            return url
    return None

from __future__ import annotations

import hashlib
import html
import re
import time


def stable_anki_id(seed: str) -> int:
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def stable_guid(*parts: str) -> str:
    base = "||".join(p.strip() for p in parts)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:20]


def slugify(text: str, max_len: int = 60) -> str:
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"[^\w\-\u4e00-\u9fff\u3040-\u30ff]+", "", text)
    return text[:max_len] or "item"


def html_escape(text: str) -> str:
    return html.escape(text or "").replace("\n", "<br>")


def retry_call(fn, retries: int = 3, base_sleep: float = 1.0):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt == retries:
                break
            time.sleep(base_sleep * attempt)
    raise last_exc

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import requests

from anki_generator.dictionary.common import normalize_dictionary_url
from anki_generator.dictionary.images import dictionary_source_from_url
from anki_generator.llm.client import LLMClient, generate_speech
from anki_generator.models import AudioAsset, InputItem
from anki_generator.utils import retry_call, slugify, stable_guid


synthesize_tts_to_file = generate_speech


def _audio_source_metadata_path(filepath: Path) -> Path:
    return filepath.with_name(f"{filepath.name}.source.json")


def _read_cached_audio_url(filepath: Path) -> str:
    try:
        data = json.loads(
            _audio_source_metadata_path(filepath).read_text(encoding="utf-8")
        )
        return normalize_dictionary_url(str(data.get("source_url") or ""))
    except (OSError, ValueError, TypeError):
        return ""


def _write_cached_audio_url(filepath: Path, source_url: str) -> None:
    metadata_path = _audio_source_metadata_path(filepath)
    temporary_path = metadata_path.with_suffix(f"{metadata_path.suffix}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(
                {
                    "source": dictionary_source_from_url(source_url),
                    "source_url": normalize_dictionary_url(source_url),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary_path.replace(metadata_path)
    except OSError:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def _is_plausible_mp3(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 256:
            return False
        with path.open("rb") as f:
            head = f.read(4)
        if head.startswith(b"ID3"):
            return True
        if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
            return True
        return False
    except OSError:
        return False


def _english_external_audio_url_queue(
    preferred_external_url: str,
    extra_audio_urls: Optional[List[str]],
) -> List[str]:
    url_queue: List[str] = []
    u0 = (preferred_external_url or "").strip()
    if u0:
        url_queue.append(normalize_dictionary_url(u0))
    for u in extra_audio_urls or []:
        u = (u or "").strip()
        if not u:
            continue
        u = normalize_dictionary_url(u)
        if u and u not in url_queue:
            url_queue.append(u)
    return url_queue


def maybe_download_external_audio(audio_url: str, filepath: Path) -> bool:
    if not audio_url:
        return False

    def _call() -> bool:
        resp = requests.get(
            audio_url,
            timeout=15,
            stream=True,
            headers={"User-Agent": "anki-batch-generator/2.0"},
        )
        if not resp.ok:
            return False
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with filepath.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return filepath.exists() and filepath.stat().st_size > 0

    try:
        return bool(retry_call(_call, retries=2, base_sleep=1.0))
    except Exception:
        return False


def _try_download_english_mp3_from_urls(url_queue: List[str], filepath: Path) -> str:
    for url in url_queue:
        if not maybe_download_external_audio(url, filepath):
            print(f"[media][audio] source={dictionary_source_from_url(url)} url={url} status=download_failed")
            continue
        if _is_plausible_mp3(filepath):
            return url
        try:
            filepath.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"[media][audio] source={dictionary_source_from_url(url)} url={url} status=invalid_mp3")
    return ""


def ensure_english_audio(
    client: LLMClient,
    media_dir: Path,
    item: InputItem,
    spoken_term: str,
    preferred_external_url: str,
    tts_model: str,
    voice: str,
    extra_audio_urls: Optional[List[str]] = None,
    *,
    filename_suffix: str = "",
    audio_selection_scopes: Optional[Dict[str, str]] = None,
) -> Optional[AudioAsset]:
    safe_suffix = filename_suffix if filename_suffix.startswith("_") else (
        f"_{filename_suffix}" if filename_suffix else ""
    )
    filename = f"audio_en_{slugify(item.term)}_{stable_guid(item.mode, item.term)[:8]}{safe_suffix}.mp3"
    filepath = media_dir / filename
    url_queue = _english_external_audio_url_queue(preferred_external_url, extra_audio_urls)
    selection_scopes = {
        normalize_dictionary_url(url): scope
        for url, scope in (audio_selection_scopes or {}).items()
        if url and scope
    }

    def scope_log(url: str) -> str:
        scope = selection_scopes.get(normalize_dictionary_url(url), "")
        return f" selection_scope={scope}" if scope else ""

    if not url_queue:
        print(f"[media][audio] term={item.term} status=not_found")
        return None
    cached_url = _read_cached_audio_url(filepath)
    cached_file_is_valid = _is_plausible_mp3(filepath)
    if (
        cached_file_is_valid
        and cached_url
        and cached_url == url_queue[0]
    ):
        cached_source = dictionary_source_from_url(cached_url)
        print(f"[media][audio] term={item.term} source={cached_source} url={cached_url or '(unknown cached file)'} file={filename} status=cached{scope_log(cached_url)}")
        return AudioAsset(filename=filename, filepath=filepath, source_type="audio", source=cached_source, source_url=cached_url)

    cached_queue_index = (
        url_queue.index(cached_url)
        if cached_file_is_valid and cached_url in url_queue
        else -1
    )
    download_queue = (
        url_queue[:cached_queue_index]
        if cached_queue_index > 0
        else url_queue
    )
    download_path = filepath
    replace_cached_file = cached_file_is_valid
    if replace_cached_file:
        download_path = filepath.with_name(f"{filepath.name}.download")
        try:
            download_path.unlink(missing_ok=True)
        except OSError:
            pass

    downloaded_url = _try_download_english_mp3_from_urls(
        download_queue, download_path
    )
    if downloaded_url:
        if replace_cached_file:
            try:
                download_path.replace(filepath)
            except OSError:
                downloaded_url = ""
        if downloaded_url:
            _write_cached_audio_url(filepath, downloaded_url)
            audio_source = dictionary_source_from_url(downloaded_url)
            print(f"[media][audio] term={item.term} source={audio_source} url={downloaded_url} file={filename} status=downloaded{scope_log(downloaded_url)}")
            return AudioAsset(filename=filename, filepath=filepath, source_type="audio", source=audio_source, source_url=downloaded_url)
    if replace_cached_file:
        try:
            download_path.unlink(missing_ok=True)
        except OSError:
            pass
    if cached_queue_index > 0:
        cached_source = dictionary_source_from_url(cached_url)
        print(f"[media][audio] term={item.term} source={cached_source} url={cached_url} file={filename} status=cached_fallback{scope_log(cached_url)}")
        return AudioAsset(filename=filename, filepath=filepath, source_type="audio", source=cached_source, source_url=cached_url)
    print(f"[media][audio] term={item.term} status=not_found")
    return None


def ensure_example_audio(
    media_dir: Path,
    item: InputItem,
    audio_url: str,
) -> Optional[AudioAsset]:
    normalized_url = normalize_dictionary_url(audio_url)
    if (
        dictionary_source_from_url(normalized_url) != "longman"
        or "/media/english/exaProns/" not in normalized_url
    ):
        return None
    filename = (
        f"audio_en_example_{slugify(item.term)}_"
        f"{stable_guid(item.mode, item.term, normalized_url)[:8]}.mp3"
    )
    filepath = media_dir / filename
    if _is_plausible_mp3(filepath):
        print(
            f"[media][example-audio] term={item.term} source=longman "
            f"url={normalized_url} file={filename} status=cached"
        )
        return AudioAsset(
            filename=filename,
            filepath=filepath,
            source_type="example_audio",
            source="longman",
            source_url=normalized_url,
        )
    downloaded_url = _try_download_english_mp3_from_urls(
        [normalized_url], filepath
    )
    if not downloaded_url:
        print(f"[media][example-audio] term={item.term} status=not_found")
        return None
    print(
        f"[media][example-audio] term={item.term} source=longman "
        f"url={normalized_url} file={filename} status=downloaded"
    )
    return AudioAsset(
        filename=filename,
        filepath=filepath,
        source_type="example_audio",
        source="longman",
        source_url=normalized_url,
    )


def ensure_japanese_audio(
    client: LLMClient,
    media_dir: Path,
    item: InputItem,
    tts_model: str,
    voice: str,
) -> Optional[AudioAsset]:
    filename = f"audio_ja_{slugify(item.term)}_{stable_guid(item.mode, item.term)[:8]}.mp3"
    filepath = media_dir / filename
    if _is_plausible_mp3(filepath):
        print(f"[media][audio] term={item.term} source=cache file={filename} status=cached")
        return AudioAsset(filename=filename, filepath=filepath, source_type="audio", source="cache", source_url="")

    try:
        synthesize_tts_to_file(client, tts_model, voice, item.term, filepath)
    except Exception:
        print(f"[media][audio] term={item.term} source=openai_tts status=failed")
        return None
    if not _is_plausible_mp3(filepath):
        try:
            filepath.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"[media][audio] term={item.term} source=openai_tts status=invalid_mp3")
        return None
    print(f"[media][audio] term={item.term} source=openai_tts model={tts_model} voice={voice} file={filename} status=synthesized")
    return AudioAsset(filename=filename, filepath=filepath, source_type="audio", source="openai_tts", source_url=f"tts:{tts_model}:{voice}")

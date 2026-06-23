#!/usr/bin/env python3
from __future__ import annotations
import shutil
from pathlib import Path
from typing import Optional

from common import *
from dictionary_sources import (
    _is_candidate_dictionary_image_url,
    _is_plausible_image,
    _normalize_url,
    fetch_dictionary_image_url,
    dictionary_source_from_url,
)


def ensure_noun_image(
    media_dir: Path,
    item: InputItem,
    preferred_image_url: str = "",
    preferred_image_source: str = "",
) -> Optional[AudioAsset]:
    clean_term = strip_pos_labels_from_term(item.term) or item.term.strip()
    if not clean_term:
        return None

    image_url = (preferred_image_url or "").strip()
    image_source = (preferred_image_source or "").strip() or "dictionary"

    if image_url and not _is_candidate_dictionary_image_url(image_url):
        image_url = ""

    if not image_url:
        image_url = fetch_dictionary_image_url(clean_term)
        image_source = "dictionary"

    if not image_url:
        print(f"[media][image] term={item.term} status=not_found")
        return None

    ext = ".jpg"
    low = image_url.lower()
    if ".png" in low:
        ext = ".png"
    elif ".webp" in low:
        ext = ".webp"

    # Anki 内部使用的媒体文件名：继续保留 hash，避免重名冲突。
    filename = f"img_en_{slugify(clean_term)}_{stable_guid(item.mode, item.term)[:8]}{ext}"
    filepath = media_dir / filename

    # 人工检查用目录：放在 anki_media 同级目录下。
    review_dir = media_dir.parent / "anki_image_review"
    review_filename = f"{slugify(clean_term)}__{image_source}{ext}"
    review_path = review_dir / review_filename

    # 如果 Anki 媒体目录里已经有这张图片，就直接复制到检查目录。
    if _is_plausible_image(filepath):
        review_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(filepath, review_path)
            print(
                f"[media][image-review] term={item.term} "
                f"source={image_source} file={review_path} status=copied_from_cache"
            )
        except OSError as exc:
            print(
                f"[media][image-review] term={item.term} "
                f"source={image_source} file={review_path} status=copy_failed error={exc}"
            )

        print(
            f"[media][image] term={item.term} "
            f"source={image_source} url={image_url} file={filename} status=cached"
        )
        return AudioAsset(filename=filename, filepath=filepath)

    def _download() -> bool:
        resp = requests.get(
            image_url,
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
        return True

    try:
        ok = bool(retry_call(_download, retries=2, base_sleep=1.0))
    except Exception as exc:
        ok = False
        print(
            f"[media][image] term={item.term} "
            f"source={image_source} url={image_url} status=download_failed error={exc}"
        )

    if not ok or not _is_plausible_image(filepath):
        try:
            filepath.unlink(missing_ok=True)
        except OSError:
            pass

        print(
            f"[media][image] term={item.term} "
            f"source={image_source} url={image_url} status=invalid_or_failed"
        )
        return None

    # 下载成功后，额外复制一份到 anki_image_review，方便人工检查。
    review_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(filepath, review_path)
        print(
            f"[media][image-review] term={item.term} "
            f"source={image_source} url={image_url} file={review_path} status=saved"
        )
    except OSError as exc:
        print(
            f"[media][image-review] term={item.term} "
            f"source={image_source} url={image_url} file={review_path} status=copy_failed error={exc}"
        )

    print(
        f"[media][image] term={item.term} "
        f"source={image_source} url={image_url} file={filename} status=downloaded"
    )

    return AudioAsset(filename=filename, filepath=filepath)

    def _download() -> bool:
        resp = requests.get(
            image_url,
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
        return True

    try:
        ok = bool(retry_call(_download, retries=2, base_sleep=1.0))
    except Exception as exc:
        ok = False
        print(
            f"[media][image] term={item.term} "
            f"source={image_source} url={image_url} status=download_failed error={exc}"
        )

    if not ok or not _is_plausible_image(filepath):
        try:
            filepath.unlink(missing_ok=True)
        except OSError:
            pass

        print(
            f"[media][image] term={item.term} "
            f"source={image_source} url={image_url} status=invalid_or_failed"
        )
        return None

    # 额外复制一份到 anki_image_review，便于人工检查图片是否正确。
    review_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(filepath, review_path)
        print(
            f"[media][image-review] term={item.term} "
            f"source={image_source} url={image_url} file={review_path} status=saved"
        )
    except OSError as exc:
        print(
            f"[media][image-review] term={item.term} "
            f"source={image_source} url={image_url} file={review_path} status=copy_failed error={exc}"
        )

    print(
        f"[media][image] term={item.term} "
        f"source={image_source} url={image_url} file={filename} status=downloaded"
    )

    return AudioAsset(filename=filename, filepath=filepath)

def synthesize_tts_to_file(
    client: OpenAI,
    tts_model: str,
    voice: str,
    text: str,
    filepath: Path,
) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)

    def _call() -> None:
        with client.audio.speech.with_streaming_response.create(
            model=tts_model,
            voice=voice,
            input=text,
            response_format="mp3",
        ) as response:
            response.stream_to_file(filepath)

    retry_call(_call, retries=3, base_sleep=2.0)


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
        url_queue.append(_normalize_url(u0))
    for u in extra_audio_urls or []:
        u = (u or "").strip()
        if not u:
            continue
        u = _normalize_url(u)
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
    client: OpenAI,
    media_dir: Path,
    item: InputItem,
    spoken_term: str,
    preferred_external_url: str,
    tts_model: str,
    voice: str,
    extra_audio_urls: Optional[List[str]] = None,
    *,
    filename_suffix: str = "",
) -> Optional[AudioAsset]:
    safe_suffix = filename_suffix if filename_suffix.startswith("_") else (
        f"_{filename_suffix}" if filename_suffix else ""
    )
    filename = f"audio_en_{slugify(item.term)}_{stable_guid(item.mode, item.term)[:8]}{safe_suffix}.mp3"
    filepath = media_dir / filename
    url_queue = _english_external_audio_url_queue(preferred_external_url, extra_audio_urls)
    if _is_plausible_mp3(filepath):
        cached_url = url_queue[0] if url_queue else ""
        cached_source = dictionary_source_from_url(cached_url) if cached_url else "cache"
        print(f"[media][audio] term={item.term} source={cached_source} url={cached_url or '(unknown cached file)'} file={filename} status=cached")
        return AudioAsset(filename=filename, filepath=filepath, source_type="audio", source=cached_source, source_url=cached_url)

    downloaded_url = _try_download_english_mp3_from_urls(url_queue, filepath)
    if downloaded_url:
        audio_source = dictionary_source_from_url(downloaded_url)
        print(f"[media][audio] term={item.term} source={audio_source} url={downloaded_url} file={filename} status=downloaded")
        return AudioAsset(filename=filename, filepath=filepath, source_type="audio", source=audio_source, source_url=downloaded_url)

    try:
        synthesize_tts_to_file(client, tts_model, voice, spoken_term, filepath)
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


def ensure_japanese_audio(
    client: OpenAI,
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

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import requests

from anki_generator.dictionary.common import (
    is_candidate_dictionary_image_url,
    is_plausible_dictionary_image,
)
from anki_generator.dictionary.images import fetch_dictionary_image_url
from anki_generator.inputs.english import strip_pos_labels_from_term
from anki_generator.models import AudioAsset, InputItem
from anki_generator.utils import retry_call, slugify, stable_guid


def ensure_noun_image(
    media_dir: Path,
    item: InputItem,
    preferred_image_url: str = "",
    preferred_image_source: str = "",
    *,
    allow_fallback: bool = True,
) -> Optional[AudioAsset]:
    clean_term = strip_pos_labels_from_term(item.term) or item.term.strip()
    if not clean_term:
        return None

    image_url = (preferred_image_url or "").strip()
    image_source = (preferred_image_source or "").strip() or "dictionary"

    if image_url and not is_candidate_dictionary_image_url(image_url):
        image_url = ""

    if not image_url and allow_fallback:
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
    if is_plausible_dictionary_image(filepath):
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

    if not ok or not is_plausible_dictionary_image(filepath):
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

    if not ok or not is_plausible_dictionary_image(filepath):
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

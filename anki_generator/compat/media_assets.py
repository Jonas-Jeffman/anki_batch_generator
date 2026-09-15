#!/usr/bin/env python3
"""Compatibility exports for the split media package."""

from __future__ import annotations

from anki_generator.media.audio import ensure_english_audio, ensure_japanese_audio
from anki_generator.media.images import ensure_noun_image


__all__ = [
    "ensure_noun_image",
    "ensure_english_audio",
    "ensure_japanese_audio",
]

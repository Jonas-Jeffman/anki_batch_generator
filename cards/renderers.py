from __future__ import annotations

from typing import Dict, List, Optional

from english_terms import is_two_word_term, lexical_word_count
from models import AudioAsset, BuiltCard, ResolvedCanonicalContent
from utils import html_escape


def build_en_word_card(
    term: str,
    llm: Dict,
    dict_phonetic: str,
    audio_assets: List[AudioAsset],
    image: Optional[AudioAsset],
) -> BuiltCard:
    if is_two_word_term(term) or lexical_word_count(term) >= 3:
        ipa = (dict_phonetic or "").strip()
    else:
        ipa = (dict_phonetic or "").strip() or str(llm.get("pronunciation_text") or "").strip()
    if ipa and not ipa.startswith("/"):
        ipa = f"/{ipa.strip('/')}/"

    definition = str(llm.get("definition_en") or "").strip()
    example_en = str(llm.get("example_simple_en") or "").strip()

    sound_line = "".join(f"[sound:{a.filename}]" for a in (audio_assets or []))
    image_html = ""
    if image:
        image_html = (
            f'<br><b>Image:</b><br>'
            f'<img src="{html_escape(image.filename)}" alt="{html_escape(term)}" '
            'style="max-width:280px; max-height:220px; object-fit:contain;">'
        )
    # Front: one centered line — <b>word</b> + one ASCII space + IPA (normal weight, not bold).
    ipa_html = html_escape(ipa).strip()
    word_ipa_gap = " "
    if ipa_html:
        front = (
            '<div style="text-align:center">'
            f"<b>{html_escape(term)}</b>{word_ipa_gap}{ipa_html}"
            "</div>"
        )
    else:
        front = (
            '<div style="text-align:center">'
            f"<b>{html_escape(term)}</b>"
            "</div>"
        )
    # Put [sound:] on its own line first — some AnkiDroid/WebView builds handle this more reliably.
    if sound_line:
        back = (
            f"{sound_line}<br>"
            f"<b>Definition (EN):</b> {html_escape(definition)}<br>"
            f"<b>Example:</b><br>{html_escape(example_en)}"
            f"{image_html}"
        )
    else:
        back = (
            f"<b>Definition (EN):</b> {html_escape(definition)}<br>"
            f"<b>Example:</b><br>{html_escape(example_en)}"
            f"{image_html}"
        )
    return BuiltCard(
        front=front,
        back=back,
        tags=["english", "vocab"],
        guid_seed=f"en_word::{term.strip()}",
    )


def build_canonical_en_word_card(
    content: ResolvedCanonicalContent,
    word_audio_assets: List[AudioAsset],
    example_audio: Optional[AudioAsset],
    image: Optional[AudioAsset],
) -> BuiltCard:
    ipa = content.ipa.value.strip()
    if ipa and not ipa.startswith("/"):
        ipa = f"/{ipa.strip('/ ')}/"
    ipa_html = html_escape(ipa).strip()
    front = (
        '<div style="text-align:center">'
        f"<b>{html_escape(content.word)}</b>"
        f"{' ' + ipa_html if ipa_html else ''}"
        f'<div class="sense-label">{html_escape(content.pos)} {content.index}</div>'
        "</div>"
    )
    word_sound = "".join(
        f"[sound:{asset.filename}]" for asset in word_audio_assets
    )
    example_control = ""
    if example_audio:
        example_control = (
            '<br><button type="button" '
            'onclick="this.nextElementSibling.play()">▶ Play example</button>'
            f'<audio preload="none" src="{html_escape(example_audio.filename)}" '
            'style="display:none"></audio>'
        )
    image_html = ""
    if image:
        image_html = (
            '<br><b>Image:</b><br>'
            f'<img src="{html_escape(image.filename)}" '
            f'alt="{html_escape(content.image_alt or content.word)}" '
            'style="max-width:280px; max-height:220px; object-fit:contain;">'
        )
    prefix = f"{word_sound}<br>" if word_sound else ""
    back = (
        f"{prefix}<b>Definition (EN):</b> {html_escape(content.definition.value)}<br>"
        f"<b>Example:</b><br>{html_escape(content.example.value)}"
        f"{example_control}{image_html}"
    )
    return BuiltCard(
        front=front,
        back=back,
        tags=["english", "vocab"],
        guid_seed=f"en_word::{content.canonical_key}",
    )


def build_ja_word_card(term: str, llm: Dict, audio: Optional[AudioAsset]) -> BuiltCard:
    reading = str(llm.get("reading_kana") or "").strip()
    explanation_ja = str(llm.get("explanation_ja") or "").strip()
    example_ja = str(llm.get("example_simple_ja") or "").strip()

    sound = f"[sound:{audio.filename}]" if audio else ""
    front = html_escape(term)
    if sound:
        back = (
            f"{sound}<br>"
            f"<b>読み方:</b> {html_escape(reading)}<br>"
            f"<b>説明 (日本語):</b> {html_escape(explanation_ja)}<br>"
            f"<b>例文:</b> {html_escape(example_ja)}"
        )
    else:
        back = (
            f"<b>読み方:</b> {html_escape(reading)}<br>"
            f"<b>説明 (日本語):</b> {html_escape(explanation_ja)}<br>"
            f"<b>例文:</b> {html_escape(example_ja)}"
        )
    return BuiltCard(
        front=front,
        back=back,
        tags=["japanese", "vocab"],
        guid_seed=f"ja_word::{term}",
    )


def build_knowledge_card(mode: str, term: str, llm: Dict) -> BuiltCard:
    mode_tag = {
        "interview": "interview",
        "paper": "paper",
        "interest": "interest",
    }[mode]

    if mode == "interview":
        title = str(llm.get("question_title") or term).strip()
        answer = str(llm.get("concise_answer") or "").strip()
        points = llm.get("key_points") or []
        if not isinstance(points, list):
            points = []
        points_html = "".join(f"<li>{html_escape(str(p))}</li>" for p in points[:5])
        example = str(llm.get("easy_example") or "").strip()
        front = html_escape(title)
        back = (
            f"<b>Answer:</b> {html_escape(answer)}<br>"
            f"<b>Key Points:</b><ul>{points_html}</ul>"
            f"<b>Example:</b> {html_escape(example)}"
        )
    elif mode == "paper":
        title = str(llm.get("topic_title") or term).strip()
        core = str(llm.get("core_idea") or "").strip()
        why = str(llm.get("why_it_matters") or "").strip()
        example = str(llm.get("easy_example") or "").strip()
        front = html_escape(title)
        back = (
            f"<b>Core Idea:</b> {html_escape(core)}<br>"
            f"<b>Why It Matters:</b> {html_escape(why)}<br>"
            f"<b>Example:</b> {html_escape(example)}"
        )
    else:
        title = str(llm.get("topic_title") or term).strip()
        what_is = str(llm.get("what_it_is") or "").strip()
        fun_fact = str(llm.get("fun_fact") or "").strip()
        example = str(llm.get("easy_example") or "").strip()
        front = html_escape(title)
        back = (
            f"<b>What It Is:</b> {html_escape(what_is)}<br>"
            f"<b>Fun Fact:</b> {html_escape(fun_fact)}<br>"
            f"<b>Example:</b> {html_escape(example)}"
        )

    return BuiltCard(
        front=front,
        back=back,
        tags=[mode_tag, "knowledge"],
        guid_seed=f"{mode}::{term}",
    )


__all__ = [
    "build_canonical_en_word_card",
    "build_en_word_card",
    "build_ja_word_card",
    "build_knowledge_card",
]

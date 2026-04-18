#!/usr/bin/env python3
"""
Batch-generate Anki decks for vocabulary and knowledge cards.

Supported modes:
- en_word: English vocabulary
- ja_word: Japanese vocabulary
- interview: interview questions / concepts
- paper: paper knowledge points
- interest: random life interests / facts
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import genanki
import requests
from openai import OpenAI


SUPPORTED_MODES = {"en_word", "ja_word", "interview", "paper", "interest"}


@dataclass
class InputRow:
    mode: str
    term: str
    hint: str
    tags: List[str]


@dataclass
class BuiltCard:
    front: str
    back: str
    tags: List[str]


def stable_anki_id(seed: str) -> int:
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def normalize_tags(raw: str) -> List[str]:
    if not raw.strip():
        return []
    return [t.strip().replace(" ", "_") for t in raw.split(",") if t.strip()]


def read_input_csv(input_path: Path) -> List[InputRow]:
    rows: List[InputRow] = []
    with input_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"mode", "term"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")

        for idx, row in enumerate(reader, start=2):
            mode = (row.get("mode") or "").strip()
            term = (row.get("term") or "").strip()
            hint = (row.get("hint") or "").strip()
            tags = normalize_tags(row.get("tags") or "")

            if not mode or not term:
                print(f"[WARN] Skip line {idx}: mode/term is empty.")
                continue
            if mode not in SUPPORTED_MODES:
                print(
                    f"[WARN] Skip line {idx}: unsupported mode '{mode}'. "
                    f"Supported: {sorted(SUPPORTED_MODES)}"
                )
                continue

            rows.append(InputRow(mode=mode, term=term, hint=hint, tags=tags))
    return rows


def fetch_english_phonetic_and_audio(term: str) -> Tuple[str, str]:
    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote_plus(term)}"
    try:
        resp = requests.get(url, timeout=8)
        if not resp.ok:
            return "", ""
        data = resp.json()
        if not isinstance(data, list) or not data:
            return "", ""
        first = data[0] if isinstance(data[0], dict) else {}
        phonetic = (first.get("phonetic") or "").strip()
        audio = ""
        for ph in first.get("phonetics", []) or []:
            if isinstance(ph, dict) and ph.get("audio"):
                audio = str(ph["audio"]).strip()
                break
        return phonetic, audio
    except Exception:
        return "", ""


def call_openai_json(
    client: OpenAI,
    model: str,
    mode: str,
    term: str,
    hint: str,
    phonetic_hint: str,
) -> Dict:
    system_prompt = (
        "You are a flashcard content generator. "
        "Return strict JSON only, no markdown. "
        "Keep examples simple and easy to memorize."
    )

    mode_guide = {
        "en_word": (
            "For English word cards, return keys: "
            "pronunciation_text, definition_en, example_simple_en. "
            "definition_en and example_simple_en must be in easy English."
        ),
        "ja_word": (
            "For Japanese word cards, return keys: "
            "reading_kana, explanation_ja, example_simple_ja. "
            "Use natural and simple Japanese."
        ),
        "interview": (
            "For interview mode, return keys: "
            "question_title, concise_answer, key_points(array of 2-4 strings), "
            "easy_example."
        ),
        "paper": (
            "For paper mode, return keys: "
            "topic_title, core_idea, why_it_matters, easy_example."
        ),
        "interest": (
            "For interest mode, return keys: "
            "topic_title, what_it_is, fun_fact, easy_example."
        ),
    }

    user_prompt = (
        f"mode: {mode}\n"
        f"term: {term}\n"
        f"hint: {hint or '(none)'}\n"
        f"phonetic_hint: {phonetic_hint or '(none)'}\n"
        f"instructions: {mode_guide[mode]}"
    )

    response = client.chat.completions.create(
        model=model,
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("OpenAI returned empty response.")
    return json.loads(text)


def html_escape(text: str) -> str:
    return html.escape(text or "").replace("\n", "<br>")


def fallback_en_audio_link(word: str) -> str:
    return (
        "https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&tl=en&q="
        + quote_plus(word)
    )


def fallback_ja_audio_link(word: str) -> str:
    return (
        "https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&tl=ja&q="
        + quote_plus(word)
    )


def english_links(word: str) -> Tuple[str, str]:
    normalized = re.sub(r"\s+", "-", word.strip().lower())
    longman = f"https://www.ldoceonline.com/dictionary/{quote_plus(normalized)}"
    cambridge = (
        "https://dictionary.cambridge.org/dictionary/english/"
        + quote_plus(word.strip().lower())
    )
    return longman, cambridge


def build_en_word_card(term: str, llm: Dict, dict_phonetic: str, dict_audio: str) -> BuiltCard:
    ipa = (dict_phonetic or "").strip()
    if not ipa:
        ipa = (llm.get("pronunciation_text") or "").strip()
    if ipa and not ipa.startswith("/"):
        ipa = f"/{ipa.strip('/')}/"

    pronunciation = (llm.get("pronunciation_text") or "").strip() or ipa.strip("/")
    definition = (llm.get("definition_en") or "").strip()
    example = (llm.get("example_simple_en") or "").strip()

    audio_url = dict_audio.strip() if dict_audio else fallback_en_audio_link(term)
    longman, cambridge = english_links(term)

    front = f"{html_escape(term)} {html_escape(ipa)}".strip()
    back = (
        f"<b>Pronunciation:</b> {html_escape(pronunciation)} "
        f"<a href=\"{audio_url}\">🔊</a><br>"
        f"<b>Definition (EN):</b> {html_escape(definition)}<br>"
        f"<b>Example:</b> {html_escape(example)}<br>"
        f"<small>Fallback audio: "
        f"<a href=\"{longman}\">🔊Longman</a> | "
        f"<a href=\"{cambridge}\">🔊Cambridge</a></small>"
    )
    return BuiltCard(front=front, back=back, tags=["english", "vocab"])


def build_ja_word_card(term: str, llm: Dict) -> BuiltCard:
    reading = (llm.get("reading_kana") or "").strip()
    explanation = (llm.get("explanation_ja") or "").strip()
    example = (llm.get("example_simple_ja") or "").strip()
    audio_url = fallback_ja_audio_link(term)

    front = html_escape(term)
    back = (
        f"<b>読み方:</b> {html_escape(reading)} "
        f"<a href=\"{audio_url}\">🔊</a><br>"
        f"<b>説明 (日本語):</b> {html_escape(explanation)}<br>"
        f"<b>例文:</b> {html_escape(example)}"
    )
    return BuiltCard(front=front, back=back, tags=["japanese", "vocab"])


def build_knowledge_card(mode: str, term: str, llm: Dict) -> BuiltCard:
    mode_tag = {
        "interview": "interview",
        "paper": "paper",
        "interest": "interest",
    }[mode]

    if mode == "interview":
        title = (llm.get("question_title") or term).strip()
        answer = (llm.get("concise_answer") or "").strip()
        points = llm.get("key_points") or []
        if not isinstance(points, list):
            points = []
        points_html = "".join(f"<li>{html_escape(str(p))}</li>" for p in points[:4])
        example = (llm.get("easy_example") or "").strip()
        front = html_escape(title)
        back = (
            f"<b>Answer:</b> {html_escape(answer)}<br>"
            f"<b>Key Points:</b><ul>{points_html}</ul>"
            f"<b>Example:</b> {html_escape(example)}"
        )
    elif mode == "paper":
        title = (llm.get("topic_title") or term).strip()
        core = (llm.get("core_idea") or "").strip()
        why = (llm.get("why_it_matters") or "").strip()
        example = (llm.get("easy_example") or "").strip()
        front = html_escape(title)
        back = (
            f"<b>Core Idea:</b> {html_escape(core)}<br>"
            f"<b>Why It Matters:</b> {html_escape(why)}<br>"
            f"<b>Example:</b> {html_escape(example)}"
        )
    else:
        title = (llm.get("topic_title") or term).strip()
        what_is = (llm.get("what_it_is") or "").strip()
        fun_fact = (llm.get("fun_fact") or "").strip()
        example = (llm.get("easy_example") or "").strip()
        front = html_escape(title)
        back = (
            f"<b>What It Is:</b> {html_escape(what_is)}<br>"
            f"<b>Fun Fact:</b> {html_escape(fun_fact)}<br>"
            f"<b>Example:</b> {html_escape(example)}"
        )

    return BuiltCard(front=front, back=back, tags=[mode_tag, "knowledge"])


def build_card(client: OpenAI, model: str, row: InputRow) -> BuiltCard:
    dict_phonetic = ""
    dict_audio = ""
    if row.mode == "en_word":
        dict_phonetic, dict_audio = fetch_english_phonetic_and_audio(row.term)

    llm = call_openai_json(
        client=client,
        model=model,
        mode=row.mode,
        term=row.term,
        hint=row.hint,
        phonetic_hint=dict_phonetic,
    )

    if row.mode == "en_word":
        built = build_en_word_card(row.term, llm, dict_phonetic, dict_audio)
    elif row.mode == "ja_word":
        built = build_ja_word_card(row.term, llm)
    else:
        built = build_knowledge_card(row.mode, row.term, llm)

    # Merge auto tags with user tags.
    final_tags = list(dict.fromkeys(built.tags + row.tags))
    return BuiltCard(front=built.front, back=built.back, tags=final_tags)


def write_preview_csv(path: Path, rows: List[BuiltCard]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Front", "Back", "Tags"])
        for r in rows:
            writer.writerow([r.front, r.back, " ".join(r.tags)])


def create_deck_apkg(deck_name: str, output_apkg: Path, cards: List[BuiltCard]) -> None:
    deck_id = stable_anki_id(f"deck::{deck_name}")
    model_id = stable_anki_id("model::batch_anki_generator::basic")

    model = genanki.Model(
        model_id=model_id,
        name="BatchAIGeneratedBasicModel",
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[
            {
                "name": "Card 1",
                "qfmt": "{{Front}}",
                "afmt": "{{FrontSide}}<hr id=\"answer\">{{Back}}",
            }
        ],
        css="""
.card {
  font-family: Arial, sans-serif;
  font-size: 20px;
  text-align: left;
  color: #111;
  background-color: #fff;
  line-height: 1.5;
}
a {
  text-decoration: none;
}
""",
    )

    deck = genanki.Deck(deck_id, deck_name)
    for idx, card in enumerate(cards):
        note = genanki.Note(
            model=model,
            fields=[card.front, card.back],
            tags=card.tags,
            guid=genanki.guid_for(deck_name, str(idx), card.front),
        )
        deck.add_note(note)

    package = genanki.Package(deck)
    package.write_to_file(str(output_apkg))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch generate Anki deck from CSV input.")
    parser.add_argument("--input", required=True, help="Path to CSV file.")
    parser.add_argument(
        "--output",
        default="anki_batch_output.apkg",
        help="Output .apkg file path.",
    )
    parser.add_argument("--deck-name", default="AI Batch Deck", help="Anki deck name.")
    parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI model name.")
    parser.add_argument(
        "--openai-api-key",
        default=os.getenv("OPENAI_API_KEY", ""),
        help="OpenAI API key. Defaults to OPENAI_API_KEY env.",
    )
    parser.add_argument(
        "--preview-csv",
        default="anki_batch_preview.csv",
        help="Export generated Front/Back to CSV for checking.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Sleep seconds between API calls (avoid rate spikes).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.openai_api_key:
        print("ERROR: OpenAI API key is missing. Use --openai-api-key or OPENAI_API_KEY.")
        return 1

    input_path = Path(args.input).expanduser().resolve()
    output_apkg = Path(args.output).expanduser().resolve()
    preview_csv = Path(args.preview_csv).expanduser().resolve()

    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}")
        return 1

    rows = read_input_csv(input_path)
    if not rows:
        print("ERROR: no valid rows in input CSV.")
        return 1

    client = OpenAI(api_key=args.openai_api_key)

    built_cards: List[BuiltCard] = []
    total = len(rows)
    for i, row in enumerate(rows, start=1):
        print(f"[{i}/{total}] Generating card: mode={row.mode}, term={row.term}")
        try:
            card = build_card(client=client, model=args.model, row=row)
            built_cards.append(card)
        except Exception as e:
            print(f"[ERROR] Failed on '{row.term}' ({row.mode}): {e}")
        time.sleep(max(0, args.sleep))

    if not built_cards:
        print("ERROR: all rows failed; no deck generated.")
        return 1

    write_preview_csv(preview_csv, built_cards)
    create_deck_apkg(deck_name=args.deck_name, output_apkg=output_apkg, cards=built_cards)

    print("\nDone.")
    print(f"- Cards generated: {len(built_cards)} / {total}")
    print(f"- Deck file: {output_apkg}")
    print(f"- Preview CSV: {preview_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

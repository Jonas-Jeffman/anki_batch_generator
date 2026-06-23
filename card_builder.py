#!/usr/bin/env python3
from __future__ import annotations

from common import *
from dictionary_sources import *
from media_assets import *

def build_en_word_prompt() -> str:
    return (
        "You are an expert Anki flashcard author for English vocabulary.\n\n"

        "Your task is to turn the given `term` and optional `hint` into a concise, "
        "high-quality Anki flashcard.\n\n"

        "OUTPUT FORMAT (hard requirements):\n"
        "- Return a single JSON object only.\n"
        "- No markdown, no explanations, no extra text.\n"
        "- Keys must be exactly:\n"
        "  pronunciation_text, definition_en, example_simple_en\n"
        "- All values must be strings.\n"
        "- If unknown, use \"\".\n\n"

        "STYLE:\n"
        "- Use simple, clear English.\n"
        "- Be concise, no filler.\n"
        "- Definition must explain meaning, not restate the word.\n"
        "- Do NOT use circular definitions.\n"
        "- Example must be natural and easy to understand.\n"
        "- Avoid complex contexts such as war, politics, crime, or violence unless necessary.\n\n"

        "STRUCTURE (semantic intent for each JSON field):\n"
        "- pronunciation_text: Modern RP / contemporary Standard Southern British English IPA only.\n"
        "- definition_en: short English gloss.\n"
        "- example_simple_en: one short natural English sentence.\n\n"

        "PRONUNCIATION / IPA RULES:\n"
        "- pronunciation_text must use Modern RP / contemporary Standard Southern British English IPA.\n"
        "- Use non-rhotic British pronunciation: do not pronounce post-vocalic /r/ unless followed by a vowel.\n"
        "- Prefer modern British learner-dictionary style IPA, close to Oxford Learner's, Cambridge, or Longman BrE entries.\n"
        "- Do NOT use General American IPA.\n"
        "- Do NOT use rhotic American transcriptions.\n"
        "- Use length marks where appropriate, such as /ɑː/, /ɔː/, /ɜː/, /iː/, /uː/.\n"
        "- Only output IPA between slashes, for example /ˈkɒf.i/.\n"
        "- Do not include labels such as BrE, UK, RP, or Modern RP inside pronunciation_text.\n"
        "- If unsure about the exact Modern RP IPA, provide the closest standard British learner-dictionary IPA.\n\n"

        "BRITISH IPA EXAMPLES:\n"
        "- car -> /kɑː/, not /kɑr/.\n"
        "- doctor -> /ˈdɒk.tə/, not /ˈdɑːk.tɚ/.\n"
        "- water -> /ˈwɔː.tə/, not /ˈwɑː.t̬ɚ/.\n"
        "- coffee -> /ˈkɒf.i/, not /ˈkɑː.fi/.\n"
        "- hot -> /hɒt/, not /hɑːt/.\n\n"

        "PRONUNCIATION SCOPE:\n"
        "- Only provide pronunciation_text when `lexical_word_count` is 1.\n"
        "- If `lexical_word_count` is 2 or more, pronunciation_text must be \"\".\n"
        "- Examples: 'savvy' -> IPA; 'play hooky' -> \"\"; 'everything in moderation' -> \"\".\n"
        "- Ignore POS labels such as noun, verb, adjective, adverb in pronunciation.\n\n"

        "MINIMAL JSON EXAMPLE (shape only; use your own values):\n"
        '{"pronunciation_text":"/ˌɒp.ə.tjuːˈnɪs.tɪk/",'
        '"definition_en":"using a situation to gain an advantage",'
        '"example_simple_en":"She made an opportunistic decision to take the job."}\n\n'

        "LANGUAGE RULES:\n"
        "- Output only English.\n"
        "- Do NOT include Chinese unless explicitly required in `hint`.\n\n"

        "PHONETIC SOURCE (user payload `phonetic_source` + `phonetic_hint`):\n"
        "- `phonetic_source` is which dictionary supplied the BrE IPA, if any: oxford, longman, "
        "cambridge, or none. Oxford = Oxford Learner's; Longman = LDOCE; Cambridge = Cambridge.\n"
        "- For oxford, longman, or cambridge, `phonetic_hint` is the site's UK/BrE IPA.\n"
        "- If `phonetic_hint` is non-empty and `lexical_word_count` is 1, set `pronunciation_text` "
        "to match `phonetic_hint` exactly as much as possible. You may add leading/trailing slashes "
        "and trim spaces, but do not replace it with your own guess.\n"
        "- Do not substitute US/American IPA when a BrE dictionary hint is provided.\n"
        "- When `phonetic_source` is `none` or `phonetic_hint` is empty, generate pronunciation_text "
        "from your own knowledge only for single-word terms, using Modern RP / contemporary Standard "
        "Southern British English IPA.\n"
        "- For multi-word terms, pronunciation_text must always be \"\".\n\n"

        "ACCURACY:\n"
        "- If `phonetic_hint` is non-empty and `lexical_word_count` is 1, it overrides your own guess.\n"
        "- Never invent or copy IPA for multi-word terms.\n"
        "- If no `phonetic_hint`, output Modern RP / contemporary Standard Southern British English IPA.\n"
        "- For en_word, `example_simple_en` must match `requested_pos` and `dictionary_definition` "
        "when provided.\n"
        "- If `requested_pos` is noun, use the word as a noun.\n"
        "- If `requested_pos` is verb, use the word as a verb.\n"
        "- If `requested_pos` is adjective, use the word as an adjective.\n"
        "- If `requested_pos` is adverb, use the word as an adverb.\n"
        "- Do not generate an example for a different part of speech.\n"
        "- If `dictionary_definition` is non-empty, you may leave `definition_en` empty or repeat that "
        "definition; the final card will use the dictionary definition.\n"
        "- Choose the most common meaning unless `hint`, `requested_pos`, or `dictionary_definition` "
        "clearly specifies another meaning.\n"
    )

def build_ja_word_prompt() -> str:
    return (
        "You are an expert Anki flashcard author for Japanese vocabulary.\n\n"

        "OUTPUT FORMAT (hard requirements):\n"
        "- Return a single JSON object only.\n"
        "- No markdown, no explanations.\n"
        "- Keys must be exactly:\n"
        "  reading_kana, explanation_ja, example_simple_ja\n"
        "- All values must be strings.\n"
        "- If unknown, use \"\".\n\n"

        "STYLE:\n"
        "- Use natural and simple Japanese.\n"
        "- Be concise, no filler.\n"
        "- If the concept is complex, express it clearly but briefly.\n"
        "- Example sentences must be natural and easy to understand.\n\n"

        "LANGUAGE RULES:\n"
        "- Output must be entirely in Japanese.\n"
        "- Do NOT include Chinese.\n\n"

        "ACCURACY:\n"
        "- Use correct kana reading.\n"
        "- Prefer the most common meaning.\n"
    )

def build_interview_prompt() -> str:
    return (
        "You are an expert Anki flashcard author for technical interview preparation.\n\n"

        "OUTPUT FORMAT (hard requirements):\n"
        "- Return a single JSON object only.\n"
        "- No markdown, no explanations.\n"
        "- Keys must be exactly:\n"
        "  question_title, concise_answer, key_points, easy_example\n"
        "- key_points must be an array of 2–5 short strings.\n"
        "- Other fields must be strings.\n"
        "- If unknown, use \"\" or [].\n\n"

        "STYLE:\n"
        "- Be concise and high-signal.\n"
        "- Avoid long paragraphs.\n"
        "- If concept is complex, break it into clear key points.\n"
        "- Each key point should contain one idea only.\n\n"

        "CONTENT RULES:\n"
        "- Answer must directly address the question.\n"
        "- Avoid vague or generic statements.\n"
        "- Example must be simple and intuitive.\n\n"

        "ACCURACY:\n"
        "- Do not fabricate facts.\n"
        "- Prefer standard and widely accepted explanations.\n"
    )

def build_paper_prompt() -> str:
    return (
        "You are an expert Anki flashcard author for technical and research concepts.\n\n"

        "OUTPUT FORMAT (hard requirements):\n"
        "- Return a single JSON object only.\n"
        "- No markdown, no explanations.\n"
        "- Keys must be exactly:\n"
        "  topic_title, core_idea, why_it_matters, easy_example\n"
        "- All values must be strings.\n"
        "- If unknown, use \"\".\n\n"

        "STYLE:\n"
        "- Be concise but informative.\n"
        "- Focus only on the core idea.\n"
        "- Avoid unnecessary background.\n"
        "- If concept is complex, explain it clearly in a structured way.\n\n"

        "CONTENT RULES:\n"
        "- core_idea = what it is\n"
        "- why_it_matters = why it is useful\n"
        "- example must be simple and intuitive\n\n"

        "ACCURACY:\n"
        "- Do not fabricate claims.\n"
        "- Prefer widely accepted interpretations.\n"
    )

def build_interest_prompt() -> str:
    return (
        "You are an expert Anki flashcard author for general knowledge and interesting facts.\n\n"

        "OUTPUT FORMAT (hard requirements):\n"
        "- Return a single JSON object only.\n"
        "- No markdown, no explanations.\n"
        "- Keys must be exactly:\n"
        "  topic_title, what_it_is, fun_fact, easy_example\n"
        "- All values must be strings.\n"
        "- If unknown, use \"\".\n\n"

        "STYLE:\n"
        "- Be concise and easy to remember.\n"
        "- Avoid long explanations.\n"
        "- If topic is complex, explain it simply.\n\n"

        "CONTENT RULES:\n"
        "- what_it_is: simple explanation\n"
        "- fun_fact: interesting detail\n"
        "- example: concrete and intuitive\n\n"

        "ACCURACY:\n"
        "- Do not fabricate facts.\n"
        "- Prefer common and reliable knowledge.\n"
    )


# Must be defined after all build_*_prompt functions (references by name).
PROMPT_MAP: Dict[str, Callable[[], str]] = {
    "en_word": build_en_word_prompt,
    "ja_word": build_ja_word_prompt,
    "interview": build_interview_prompt,
    "paper": build_paper_prompt,
    "interest": build_interest_prompt,
}


def build_user_payload(
    item: InputItem,
    phonetic_hint: str,
    term_for_pronunciation: str,
    phonetic_source: str = "",
    dictionary_definition: str = "",
    requested_pos: str = "",
    parsed_word: str = "",
) -> str:
    """Variable inputs only; mode-specific rules live in PROMPT_MAP system prompts."""
    return (
        "Fill the JSON fields described in your system instructions using this input.\n\n"
        f"raw_term: {item.term}\n"
        f"word: {parsed_word or term_for_pronunciation or item.term}\n"
        f"requested_pos: {requested_pos or '(none)'}\n"
        f"dictionary_definition: {dictionary_definition or '(none)'}\n"
        f"term: {item.term}\n"
        f"term_for_pronunciation: {term_for_pronunciation or item.term}\n"
        f"lexical_word_count: {lexical_word_count(item.term)}\n"
        f"is_single_word_term: {'true' if is_single_word_term(item.term) else 'false'}\n"
        f"is_two_word_expression: {'true' if is_two_word_term(item.term) else 'false'}\n"
        f"hint: {item.hint or '(none)'}\n"
        f"phonetic_source: {phonetic_source or 'none'}\n"
        f"phonetic_hint: {phonetic_hint or '(none)'}\n"
    )


def call_openai_json(
    client: OpenAI,
    model: str,
    item: InputItem,
    phonetic_hint: str,
    term_for_pronunciation: str,
    reasoning_effort: str,
    phonetic_source: str = "",
    dictionary_definition: str = "",
    requested_pos: str = "",
    parsed_word: str = "",
) -> Dict:
    system_prompt = PROMPT_MAP[item.mode]()
    user_prompt = build_user_payload(
        item,
        phonetic_hint,
        term_for_pronunciation,
        phonetic_source,
        dictionary_definition=dictionary_definition,
        requested_pos=requested_pos,
        parsed_word=parsed_word,
    )

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

        response = client.chat.completions.create(**kwargs)
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("OpenAI returned empty content.")
        return json.loads(text)

    return retry_call(_call, retries=3, base_sleep=2.0)



def collect_audio_fallback_urls(entries: List[DictionaryEntryResult], primary_url: str = "") -> List[str]:
    urls: List[str] = []
    seen = {primary_url} if primary_url else set()
    for source in PROVIDER_ORDER:
        for entry in entries:
            if entry.source != source:
                continue
            url = (entry.audio_uk_url or "").strip()
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls
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


def build_card(
    client: OpenAI,
    item: InputItem,
    model: str,
    tts_model: str,
    audio_dir: Path,
    image_dir: Path,
    tts_voice_en: str,
    tts_voice_ja: str,
    cache: CacheStore,
    reasoning_effort: str,
) -> Tuple[BuiltCard, List[AudioAsset]]:
    dict_phonetic = ""
    dict_phonetic_source = ""
    dict_audio = ""
    dict_audio_fallbacks: List[str] = []
    dict_pos_tags: List[str] = []
    dictionary_entry = DictionaryEntryResult()
    parsed_en = ParsedEnglishTerm(raw=item.term.strip(), word=item.term.strip(), requested_pos="")
    spoken_term = item.term.strip()
    if item.mode == "en_word":
        parsed_en = parse_english_term(item.term)
        spoken_term = parsed_en.word or item.term.strip()
        if en_word_uses_dictionary_lookup(spoken_term):
            dictionary_entries = fetch_dictionary_entries(spoken_term, parsed_en.requested_pos)
            dictionary_entry = merge_dictionary_entries(dictionary_entries)
            dict_phonetic = dictionary_entry.ipa_uk
            dict_phonetic_source = (dictionary_entry.ipa_source or "").strip()
            dict_audio = dictionary_entry.audio_uk_url
            dict_audio_fallbacks = collect_audio_fallback_urls(dictionary_entries, dict_audio)
            print(
                f"[dict] term={item.term} word={spoken_term} requested_pos={parsed_en.requested_pos or '(none)'} "
                f"definition_source={dictionary_entry.definition_source or 'none'} "
                f"ipa_source={dictionary_entry.ipa_source or 'none'} "
                f"audio_source={dictionary_entry.audio_source or 'none'} "
                f"image_source={dictionary_entry.image_source or 'none'}"
            )
            dict_pos_tags = [
                p
                for p in (parsed_en.requested_pos, dictionary_entry.actual_pos)
                if p
            ]

    cache_key = hashlib.sha1(
        json.dumps(
            {
                "mode": item.mode,
                "term": item.term,
                "hint": item.hint,
                "model": model,
                "llm_schema": LLM_SCHEMA_VERSION,
                "dict_phonetic": dict_phonetic,
                "dict_phonetic_source": dict_phonetic_source,
                "parsed_word": parsed_en.word if item.mode == "en_word" else "",
                "requested_pos": parsed_en.requested_pos if item.mode == "en_word" else "",
                "dictionary_definition": dictionary_entry.definition if item.mode == "en_word" else "",
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    cached = cache.get(cache_key)
    if cached:
        llm = cached
    else:
        llm = call_openai_json(
            client=client,
            model=model,
            item=item,
            phonetic_hint=dict_phonetic,
            term_for_pronunciation=spoken_term,
            reasoning_effort=reasoning_effort,
            phonetic_source=dict_phonetic_source,
            dictionary_definition=dictionary_entry.definition if item.mode == "en_word" else "",
            requested_pos=parsed_en.requested_pos if item.mode == "en_word" else "",
            parsed_word=parsed_en.word if item.mode == "en_word" else "",
        )
        cache.set(cache_key, llm)

    if item.mode == "en_word" and dictionary_entry.definition:
        llm = dict(llm)
        llm["definition_en"] = dictionary_entry.definition

    assets: List[AudioAsset] = []
    if item.mode == "en_word":
        audio_assets: List[AudioAsset] = []
        if is_single_word_term(spoken_term):
            single = ensure_english_audio(
                client=client,
                media_dir=audio_dir,
                item=item,
                spoken_term=spoken_term,
                preferred_external_url=dict_audio,
                tts_model=tts_model,
                voice=tts_voice_en,
                extra_audio_urls=dict_audio_fallbacks,
            )
            if single:
                audio_assets = [single]
        image: Optional[AudioAsset] = None
        if dictionary_entry.image_url:
            image = ensure_noun_image(
                media_dir=image_dir,
                item=item,
                preferred_image_url=dictionary_entry.image_url,
                preferred_image_source=dictionary_entry.image_source,
            )
            if image:
                assets.append(image)

        assets.extend(audio_assets)
        if image:
            assets.append(image)
        built = build_en_word_card(item.term, llm, dict_phonetic, audio_assets, image)
    elif item.mode == "ja_word":
        audio = ensure_japanese_audio(
            client=client,
            media_dir=audio_dir,
            item=item,
            tts_model=tts_model,
            voice=tts_voice_ja,
        )
        if audio:
            assets.append(audio)
        built = build_ja_word_card(item.term, llm, audio)
    else:
        built = build_knowledge_card(item.mode, item.term, llm)

    final_tags = list(dict.fromkeys(built.tags + item.normalized_tags()))
    return BuiltCard(
        front=built.front,
        back=built.back,
        tags=final_tags,
        guid_seed=built.guid_seed,
    ), assets


def write_preview_json(path: Path, rows: List[BuiltCard]) -> None:
    payload = [
        {"front": r.front, "back": r.back, "tags": r.tags, "guid_seed": r.guid_seed}
        for r in rows
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_dict_preview_json(path: Path, payload: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_dictionary_test_only(items: List[InputItem], preview_json: Path) -> None:
    payload = [dictionary_result_preview(item.term) for item in items if item.mode == "en_word"]
    if preview_json:
        write_dict_preview_json(preview_json, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))

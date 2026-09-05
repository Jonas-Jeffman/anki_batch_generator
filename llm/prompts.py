from __future__ import annotations

import json
from typing import Callable, Dict, Sequence

from english_terms import is_single_word_term, is_two_word_term, lexical_word_count
from models import InputItem, ProviderSense


def build_sense_alignment_prompt() -> str:
    return (
        "You align dictionary senses for one English word and one part of speech.\n\n"
        "Return one JSON object with exactly these keys: alignments, unmatched.\n"
        "alignments must be an array of objects with exactly these keys:\n"
        "cambridge_sense_id, longman_sense_ids, oxford_sense_ids, relation, confidence.\n"
        "unmatched must be an array of objects with exactly these keys:\n"
        "source, sense_id, decision, related_cambridge_sense_ids, confidence.\n\n"
        "Match senses by definition meaning, never by array position or numeric order.\n"
        "Cambridge is the canonical backbone. Include every Cambridge sense exactly once.\n"
        "Every Longman and Oxford sense ID must occur exactly once, either in an alignment "
        "or in unmatched.\n"
        "A Cambridge sense may match multiple provider senses when one dictionary splits a meaning.\n"
        "relation must be one of: equivalent, broader, narrower, one_to_many, no_match.\n"
        "For unmatched, source must be longman or oxford. decision must be append or ignore.\n"
        "Only an independent, useful Longman sense may use append. Oxford unmatched senses must use ignore.\n"
        "confidence must be a number from 0 to 1.\n"
        "Use only IDs supplied in the input. Do not return definitions, examples, images, or audio.\n"
        "Do not rewrite dictionary text. Return JSON only."
    )


def build_sense_alignment_payload(
    word: str,
    pos: str,
    cambridge_senses: Sequence[ProviderSense],
    longman_senses: Sequence[ProviderSense],
    oxford_senses: Sequence[ProviderSense],
) -> str:
    def records(senses: Sequence[ProviderSense]):
        return [
            {"sense_id": sense.native_id, "definition": sense.definition}
            for sense in senses
        ]

    return json.dumps(
        {
            "word": word,
            "pos": pos,
            "cambridge_senses": records(cambridge_senses),
            "longman_senses": records(longman_senses),
            "oxford_senses": records(oxford_senses),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


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

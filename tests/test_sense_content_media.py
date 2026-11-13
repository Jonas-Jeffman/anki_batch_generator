from __future__ import annotations

from dataclasses import asdict, replace
from itertools import product
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests import support  # noqa: F401 - installs offline dependency stubs

from anki_generator.cards import english
from anki_generator.cards.english import resolve_canonical_content
from anki_generator.cards.renderers import build_canonical_en_word_card
from anki_generator.config import (
    EXAMPLE_AUDIO_ICON_FILENAME,
    EXAMPLE_AUDIO_ICON_PATH,
    LLM_SCHEMA_VERSION,
)
from anki_generator.dictionary.longman import parse_longman_provider_entries
from anki_generator.llm.cache import (
    CacheStore,
    build_alignment_cache_key,
    build_cache_key,
)
from anki_generator.media import audio, images
from anki_generator.models import (
    AudioAsset,
    CanonicalSense,
    CanonicalSenseRequest,
    DictionaryExample,
    InputItem,
    ProviderEntry,
    ProviderSense,
    ResolvedCanonicalContent,
    ResolvedContentField,
    WordPronunciation,
)
from anki_generator.senses import alignment
from anki_generator.senses.store import CanonicalStore
from anki_generator.utils import stable_guid


def provider_entry(
    source: str,
    native_id: str,
    definition: str,
    *,
    example: str = "",
    example_audio: str = "",
    image: str = "",
    ipa: str = "",
    word_audio: str = "",
    synonyms=None,
    thesaurus_terms=None,
    signpost: str = "",
) -> ProviderEntry:
    examples = (
        [DictionaryExample(text=example, audio_url=example_audio)]
        if example
        else []
    )
    return ProviderEntry(
        source=source,
        dataset="fixture",
        native_id=f"entry-{source}",
        word="trunk",
        pos="noun",
        pronunciation=WordPronunciation(
            source=source,
            pos="noun",
            ipa_uk=ipa,
            audio_uk_url=word_audio,
        ),
        senses=[
            ProviderSense(
                source=source,
                native_id=native_id,
                pos="noun",
                definition=definition,
                examples=examples,
                image_url=image,
                synonyms=list(synonyms or []),
                thesaurus_terms=list(thesaurus_terms or []),
                signpost=signpost,
            )
        ],
    )


def request(entries) -> CanonicalSenseRequest:
    return CanonicalSenseRequest(
        item=InputItem("en_word", "trunk noun 1"),
        word="trunk",
        pos="noun",
        canonical_sense=CanonicalSense(
            word="trunk",
            pos="noun",
            index=1,
            backbone_source="cambridge",
            backbone_sense_id="C1",
            cambridge_sense_id="C1",
            longman_sense_ids=["L1"],
            oxford_sense_ids=["O1"],
            canonical_key="canonical::trunk-1",
        ),
        provider_entries=entries,
    )


class SenseContentAndMediaTests(unittest.TestCase):
    def test_example_audio_icon_is_a_bundled_svg(self):
        self.assertEqual("audio_bre_initial.svg", EXAMPLE_AUDIO_ICON_FILENAME)
        self.assertTrue(EXAMPLE_AUDIO_ICON_PATH.is_file())

    def test_each_field_uses_its_own_source_and_records_provenance(self):
        longman_audio = (
            "https://www.ldoceonline.com/media/english/exaProns/example.mp3"
        )
        entries = [
            provider_entry(
                "cambridge",
                "C1",
                "Cambridge definition",
                example="Cambridge example.",
                image="https://dictionary.cambridge.org/images/full/trunk.jpg",
                ipa="cambridge-ipa",
                word_audio="https://dictionary.cambridge.org/cambridge.mp3",
            ),
            provider_entry(
                "longman",
                "L1",
                "Longman definition",
                example="Longman example.",
                example_audio=longman_audio,
                ipa="longman-ipa",
                word_audio="https://www.ldoceonline.com/longman.mp3",
            ),
            provider_entry(
                "oxford",
                "O1",
                "Oxford definition",
                example="Oxford example.",
                ipa="oxford-ipa",
                word_audio="https://www.oxfordlearnersdictionaries.com/oxford.mp3",
            ),
        ]
        content = resolve_canonical_content(request(entries))
        self.assertEqual(
            ("Longman definition", "longman", "L1"),
            (
                content.definition.value,
                content.definition.source,
                content.definition.sense_id,
            ),
        )
        self.assertEqual(
            ("Longman example.", "longman", "L1"),
            (content.example.value, content.example.source, content.example.sense_id),
        )
        self.assertEqual(
            (longman_audio, "longman", "L1"),
            (
                content.example_audio.value,
                content.example_audio.source,
                content.example_audio.sense_id,
            ),
        )
        self.assertEqual(("oxford-ipa", "oxford"), (content.ipa.value, content.ipa.source))
        self.assertEqual(
            ["oxford", "cambridge", "longman"],
            [field.source for field in content.word_audio_urls],
        )
        self.assertEqual(
            ["exact_pos", "exact_pos", "exact_pos"],
            [field.selection_scope for field in content.word_audio_urls],
        )
        self.assertEqual(
            ("cambridge", "C1"),
            (content.image.source, content.image.sense_id),
        )

    def test_non_longman_example_never_inherits_longman_audio(self):
        entries = [
            provider_entry("cambridge", "C1", "Cambridge", example="Cambridge example."),
            provider_entry(
                "longman",
                "L1",
                "Longman",
                example="",
                example_audio="https://www.ldoceonline.com/media/english/exaProns/unused.mp3",
            ),
            provider_entry("oxford", "O1", "Oxford", example="Oxford example."),
        ]
        content = resolve_canonical_content(request(entries))
        self.assertEqual("cambridge", content.example.source)
        self.assertEqual("", content.example_audio.value)

    def test_longman_definition_enrichment_is_rendered_without_changing_definition(self):
        entries = [
            provider_entry("cambridge", "C1", "Cambridge definition"),
            provider_entry(
                "longman",
                "L1",
                "base definition",
                synonyms=["give way", "surrender"],
                thesaurus_terms=["amount", "return"],
            ),
        ]
        content = resolve_canonical_content(request(entries))
        self.assertEqual("base definition", content.definition.value)
        self.assertEqual(["give way", "surrender"], content.definition_synonyms)
        self.assertEqual(["amount", "return"], content.definition_thesaurus_terms)

        card = build_canonical_en_word_card(content, [], None, None)
        self.assertIn(
            "<b>Definition (EN):</b> base definition "
            "SYN give way, surrender<br>",
            card.back,
        )
        self.assertTrue(card.back.endswith("<br><b>Thesaurus:</b> amount, return"))
        self.assertEqual(1, card.back.count("amount, return"))

    def test_renderer_metadata_and_media_combinations(self):
        for signpost, terms, with_audio, with_image in product(
            ("government/military", "", " \n "),
            ([" land ", "", "area", " \t "], [], [" \n "]),
            (False, True),
            (False, True),
        ):
            with self.subTest(
                signpost=signpost, terms=terms, audio=with_audio, image=with_image
            ):
                content = ResolvedCanonicalContent(
                    word="territory", pos="noun", index=3,
                    canonical_key="canonical::territory-fixture", status="active",
                    definition=ResolvedContentField(
                        "land under control", "longman", "L1"
                    ),
                    example=ResolvedContentField(
                        "The plane flew over it.", "longman", "L1"
                    ),
                    example_audio=ResolvedContentField(),
                    ipa=ResolvedContentField("ˈterətəri"),
                    definition_signpost=signpost,
                    definition_thesaurus_terms=terms,
                )
                example_audio = (
                    AudioAsset("example.mp3", Path("example.mp3"))
                    if with_audio else None
                )
                image = (
                    AudioAsset("image.jpg", Path("image.jpg"))
                    if with_image else None
                )
                word_audio = [AudioAsset("word.mp3", Path("word.mp3"))]
                card = build_canonical_en_word_card(
                    content, word_audio, example_audio, image
                )
                plain = build_canonical_en_word_card(
                    replace(
                        content, definition_signpost="", definition_thesaurus_terms=[]
                    ),
                    word_audio, example_audio, image,
                )
                self.assertEqual(plain.front, card.front)
                self.assertEqual(plain.guid_seed, card.guid_seed)
                self.assertEqual(
                    '<div style="text-align:center"><b>territory</b> /ˈterətəri/'
                    '<div class="sense-label">noun 3</div></div>',
                    card.front,
                )
                self.assertTrue(card.back.startswith("[sound:word.mp3]<br>"))
                without_metadata = card.back
                if signpost.strip():
                    opening = '<span class="sense-signpost"'
                    inner = card.back.split(opening, 1)[1].split('</span>', 1)[0]
                    tag = opening + inner + '</span> '
                    self.assertIn("background-color:#f18700", tag)
                    self.assertIn("color:#fff", tag)
                    self.assertIn("font-weight:bold", tag)
                    self.assertIn(
                        '</span> land under control<br>', card.back
                    )
                    self.assertIn('>government/military</span>', tag)
                    without_metadata = without_metadata.replace(tag, "", 1)
                else:
                    self.assertNotIn("sense-signpost", card.back)
                if any(term.strip() for term in terms):
                    row = "<br><b>Thesaurus:</b> land, area"
                    self.assertEqual(1, card.back.count(row))
                    anchor = '</audio>' if with_audio else 'The plane flew over it.'
                    self.assertIn(anchor + row, card.back)
                    if with_image:
                        self.assertIn(row + '<br><b>Image:</b>', card.back)
                    else:
                        self.assertTrue(card.back.endswith(row))
                    without_metadata = without_metadata.replace(row, "", 1)
                else:
                    self.assertNotIn("Thesaurus:", card.back)
                self.assertEqual(plain.back, without_metadata)
                self.assertNotIn("<br><br>", card.back)
                self.assertNotIn("<a ", card.back)
                self.assertNotIn("[sound:example.mp3]", card.back)

    def test_renderer_escapes_signpost_thesaurus_and_definition(self):
        dangerous = '<script>alert("x")</script> & land'
        content = ResolvedCanonicalContent(
            word="territory", pos="noun", index=1,
            canonical_key="canonical::escaping", status="active",
            definition=ResolvedContentField(dangerous),
            example=ResolvedContentField(dangerous),
            example_audio=ResolvedContentField(), ipa=ResolvedContentField(),
            definition_signpost=f"  {dangerous}  ",
            definition_thesaurus_terms=[" ", dangerous, " area & ground "],
            definition_synonyms=[dangerous],
        )
        card = build_canonical_en_word_card(content, [], None, None)
        escaped = '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; land'
        self.assertNotIn("<script", card.back)
        self.assertNotIn("<a ", card.back)
        self.assertIn(f'>{escaped}</span> {escaped} SYN {escaped}<br>', card.back)
        self.assertIn(f'<b>Example:</b><br>{escaped}', card.back)
        self.assertTrue(card.back.endswith(
            f'<br><b>Thesaurus:</b> {escaped}, area &amp; ground'
        ))

    def test_definition_metadata_uses_selected_longman_id_not_entry_order(self):
        for selected_signpost in ("tree", ""):
            with self.subTest(signpost=selected_signpost):
                entries = [
                    provider_entry(
                        "longman", "L2", "other definition",
                        signpost="other", thesaurus_terms=["other term"],
                        synonyms=["other synonym"],
                    ),
                    provider_entry(
                        "longman", "L1", "selected definition",
                        signpost=selected_signpost,
                        thesaurus_terms=["land"] if selected_signpost else [],
                        synonyms=["ground"] if selected_signpost else [],
                    ),
                    provider_entry(
                        "cambridge", "C1", "Cambridge definition",
                        example="Cambridge example.",
                    ),
                ]
                item = request(entries)
                item.canonical_sense.longman_sense_ids = ["missing", "L1", "L2"]
                content = resolve_canonical_content(item)
                self.assertEqual("L1", content.definition.sense_id)
                self.assertEqual("selected definition", content.definition.value)
                self.assertEqual(selected_signpost, content.definition_signpost)
                self.assertEqual(
                    ["land"] if selected_signpost else [],
                    content.definition_thesaurus_terms,
                )
                self.assertEqual(
                    ["ground"] if selected_signpost else [],
                    content.definition_synonyms,
                )
                self.assertEqual("cambridge", content.example.source)
                self.assertEqual("", content.example_audio.value)

    def test_fallback_definition_never_borrows_longman_metadata(self):
        for source in ("cambridge", "oxford", ""):
            with self.subTest(source=source):
                entries = [
                    provider_entry(
                        "longman", "L1", "", signpost="unusable",
                        thesaurus_terms=["unused"], synonyms=["unused"],
                        example="Longman example.",
                    ),
                    provider_entry(
                        "longman", "L2", "later Longman definition",
                        signpost="must not select", thesaurus_terms=["other"],
                    ),
                    provider_entry(
                        "cambridge", "C1", "Cambridge" if source == "cambridge" else "",
                        signpost="not Longman", thesaurus_terms=["not Longman"],
                    ),
                    provider_entry(
                        "oxford", "O1", "Oxford" if source == "oxford" else "",
                        signpost="not Longman", thesaurus_terms=["not Longman"],
                    ),
                ]
                item = request(entries)
                item.canonical_sense.longman_sense_ids = ["L1", "L2"]
                content = resolve_canonical_content(item)
                self.assertEqual(source, content.definition.source)
                self.assertEqual("", content.definition_signpost)
                self.assertEqual([], content.definition_thesaurus_terms)
                self.assertEqual([], content.definition_synonyms)
                self.assertEqual("longman", content.example.source)

    def test_old_caches_use_fresh_signpost_without_changing_identity(self):
        # Both cache payloads predate signpost metadata and stay unchanged.
        cambridge = provider_entry("cambridge", "C1", "a tree stem")

        def parsed_longman(signpost):
            return parse_longman_provider_entries(
                '<span class="ldoceEntry Entry">'
                '<span class="HWD">trunk</span><span class="POS">noun</span>'
                '<span class="Sense" id="L1">'
                f'{signpost}<span class="DEF">the main stem of a tree</span>'
                '</span></span>',
                "https://www.ldoceonline.com/dictionary/trunk",
                "trunk",
            )[0]

        old_longman = parsed_longman("")
        alignment_key = build_alignment_cache_key(
            word="trunk",
            pos="noun",
            model="fixture-model",
            schema_version=alignment.SENSE_ALIGNMENT_SCHEMA_VERSION,
            senses=[
                {"source": sense.source, "sense_id": sense.native_id,
                 "definition": sense.definition}
                for entry in (cambridge, old_longman)
                for sense in entry.senses
            ],
        )
        response = {
            "alignments": [{
                "cambridge_sense_id": "C1", "longman_sense_ids": ["L1"],
                "oxford_sense_ids": [], "relation": "equivalent", "confidence": 0.95,
            }],
            "unmatched": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_path = root / "cache.json"
            store_path = root / "canonical.json"
            cache = CacheStore(cache_path)
            cache.set(alignment_key, response)
            cache.save()
            identities = []
            manifests = []
            llm_key = None
            with (
                patch.object(alignment, "generate_json") as align_generate,
                patch.object(english, "call_openai_json") as llm_generate,
                patch.object(english, "ensure_english_audio") as word_audio,
                patch.object(english, "ensure_example_audio") as example_audio,
                patch.object(english, "ensure_noun_image") as noun_image,
            ):
                for expected in ("", "tree", "wood/tree"):
                    longman = parsed_longman(
                        f'<span class="SIGNPOST">{expected}</span>' if expected else ""
                    )
                    cache = CacheStore(cache_path)
                    store = CanonicalStore(store_path)
                    canonical = alignment.align_canonical_senses(
                        client=None, word="trunk", pos="noun",
                        cambridge_senses=cambridge.senses,
                        longman_senses=longman.senses, oxford_senses=[],
                        model="fixture-model", reasoning_effort="medium",
                        cache=cache, canonical_store=store,
                    )[0]
                    item = request([cambridge, longman])
                    item.canonical_sense = canonical
                    content = resolve_canonical_content(item)
                    current_key = build_cache_key(
                        mode="en_word", term=canonical.canonical_key,
                        hint=item.item.hint, model="fixture-model",
                        llm_schema=LLM_SCHEMA_VERSION,
                        dict_phonetic=content.ipa.value,
                        dict_phonetic_source=content.ipa.source,
                        parsed_word="trunk", requested_pos="noun",
                        dictionary_definition=content.definition.value,
                    )
                    if llm_key is None:
                        llm_key = current_key
                        cache.set(llm_key, {"example_simple_en": "A tree trunk."})
                        cache.save()
                        cache = CacheStore(cache_path)
                    self.assertEqual(llm_key, current_key)
                    card, assets, built_content = english.build_canonical_sense_card(
                        client=None, request=item, model="fixture-model",
                        tts_model="fixture-tts", audio_dir=root / "audio",
                        image_dir=root / "images", tts_voice_en="alloy",
                        cache=cache, reasoning_effort="medium",
                    )
                    self.assertEqual(expected, built_content.definition_signpost)
                    self.assertEqual("longman", built_content.definition.source)
                    self.assertEqual("llm", built_content.example.source)
                    self.assertEqual("A tree trunk.", built_content.example.value)
                    self.assertEqual([], assets)
                    self.assertEqual(
                        {alignment_key: response,
                         llm_key: {"example_simple_en": "A tree trunk."}},
                        cache.data,
                    )
                    identities.append((
                        asdict(canonical), card.front, card.guid_seed,
                        stable_guid("English::Test", card.guid_seed),
                    ))
                    store.save()
                    manifests.append(store_path.read_text(encoding="utf-8"))
                align_generate.assert_not_called()
                llm_generate.assert_not_called()
                word_audio.assert_not_called()
                example_audio.assert_not_called()
                noun_image.assert_not_called()
            self.assertEqual(identities[0], identities[1])
            self.assertEqual(identities[0], identities[2])
            self.assertEqual(manifests[0], manifests[1])
            self.assertEqual(manifests[0], manifests[2])

    def test_definition_and_example_fallbacks_are_independent(self):
        cases = (
            (
                [
                    provider_entry("cambridge", "C1", "Cambridge", example="Cambridge example."),
                    provider_entry("longman", "L1", "Longman"),
                    provider_entry("oxford", "O1", "Oxford", example="Oxford example."),
                ],
                ("longman", "cambridge"),
            ),
            (
                [
                    provider_entry("cambridge", "C1", ""),
                    provider_entry("longman", "L1", ""),
                    provider_entry("oxford", "O1", "Oxford", example="Oxford example."),
                ],
                ("oxford", "oxford"),
            ),
            (
                [
                    provider_entry("cambridge", "C1", ""),
                    provider_entry("longman", "L1", ""),
                    provider_entry("oxford", "O1", ""),
                ],
                ("", ""),
            ),
        )
        for entries, expected in cases:
            with self.subTest(expected=expected):
                content = resolve_canonical_content(request(entries))
                self.assertEqual(
                    expected,
                    (content.definition.source, content.example.source),
                )

    def test_example_audio_accepts_only_real_longman_example_urls(self):
        item = InputItem("en_word", "trunk noun 1")
        valid = "https://www.ldoceonline.com/media/english/exaProns/example.mp3"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(audio, "_try_download_english_mp3_from_urls", return_value=valid) as download:
                asset = audio.ensure_example_audio(root, item, valid)
            download.assert_called_once()
            self.assertEqual(("example_audio", "longman", valid), (
                asset.source_type, asset.source, asset.source_url
            ))

            with patch.object(audio, "_try_download_english_mp3_from_urls") as rejected:
                self.assertIsNone(audio.ensure_example_audio(
                    root,
                    item,
                    "https://dictionary.cambridge.org/media/example.mp3",
                ))
                self.assertIsNone(audio.ensure_example_audio(
                    root,
                    item,
                    "https://www.ldoceonline.com/media/english/word.mp3",
                ))
            rejected.assert_not_called()

    def test_canonical_image_never_uses_page_level_fallback(self):
        item = InputItem("en_word", "trunk noun 1")
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            images, "fetch_dictionary_image_url"
        ) as fallback:
            self.assertIsNone(
                images.ensure_noun_image(
                    Path(tmp),
                    item,
                    preferred_image_url="",
                    preferred_image_source="cambridge",
                    allow_fallback=False,
                )
            )
            self.assertIsNone(
                images.ensure_noun_image(
                    Path(tmp),
                    item,
                    preferred_image_url="https://example.test/not-dictionary.jpg",
                    preferred_image_source="cambridge",
                    allow_fallback=False,
                )
            )
        fallback.assert_not_called()

    def test_renderer_labels_sense_and_example_audio_is_click_only(self):
        content = ResolvedCanonicalContent(
            word="trunk & box",
            pos="noun",
            index=2,
            canonical_key="canonical::two",
            status="active",
            definition=ResolvedContentField("a box", "longman", "L2"),
            example=ResolvedContentField("Use <it>.", "longman", "L2"),
            example_audio=ResolvedContentField("url", "longman", "L2"),
            ipa=ResolvedContentField("trʌŋk", "oxford"),
        )
        word_audio = AudioAsset("word.mp3", Path("word.mp3"))
        example_audio = AudioAsset("example.mp3", Path("example.mp3"))
        image = AudioAsset("image.jpg", Path("image.jpg"))
        card = build_canonical_en_word_card(
            content, [word_audio], example_audio, image
        )
        self.assertIn("noun 2", card.front)
        self.assertNotIn("<b>Sense:</b>", card.back)
        self.assertTrue(card.back.startswith("[sound:word.mp3]<br><b>Definition (EN):</b>"))
        self.assertIn("[sound:word.mp3]", card.back)
        self.assertNotIn("[sound:example.mp3]", card.back)
        self.assertNotIn("▶ Play example", card.back)
        self.assertIn('src="audio_bre_initial.svg"', card.back)
        self.assertIn('aria-label="Play example audio"', card.back)
        self.assertIn('onclick="this.nextElementSibling.play()"', card.back)
        self.assertIn('preload="none" src="example.mp3"', card.back)
        self.assertIn("Use &lt;it&gt;.", card.back)
        self.assertEqual("en_word::canonical::two", card.guid_seed)

    def test_renderer_omits_example_audio_control_without_example_audio(self):
        content = ResolvedCanonicalContent(
            word="trunk",
            pos="noun",
            index=1,
            canonical_key="canonical::one",
            status="active",
            definition=ResolvedContentField("the main stem", "cambridge", "C1"),
            example=ResolvedContentField("A thick trunk.", "cambridge", "C1"),
            example_audio=ResolvedContentField(),
            ipa=ResolvedContentField("trʌŋk", "oxford"),
        )
        card = build_canonical_en_word_card(content, [], None, None)
        self.assertNotIn("audio_bre_initial.svg", card.back)
        self.assertNotIn("<audio", card.back)
        self.assertNotIn("<button", card.back)


if __name__ == "__main__":
    unittest.main()

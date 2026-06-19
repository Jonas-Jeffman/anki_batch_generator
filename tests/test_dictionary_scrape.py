from anki_batch_generator import (
    _is_candidate_dictionary_image_url,
    _normalize_url,
    is_cross_reference_definition,
    parse_english_term,
)


def test_parse_english_term_with_requested_pos():
    parsed = parse_english_term("pin noun")
    assert parsed.raw == "pin noun"
    assert parsed.word == "pin"
    assert parsed.requested_pos == "noun"

    parsed = parse_english_term("pin verb")
    assert parsed.raw == "pin verb"
    assert parsed.word == "pin"
    assert parsed.requested_pos == "verb"


def test_parse_english_term_without_requested_pos():
    parsed = parse_english_term("rose")
    assert parsed.raw == "rose"
    assert parsed.word == "rose"
    assert parsed.requested_pos == ""


def test_cambridge_media_url_normalization():
    assert _normalize_url("/media/english/uk_pron/u/ukr/ukroo/ukrooke025.mp3") == (
        "https://dictionary.cambridge.org/media/english/uk_pron/u/ukr/ukroo/ukrooke025.mp3"
    )


def test_dictionary_image_url_filtering():
    assert _is_candidate_dictionary_image_url(
        "https://dictionary.cambridge.org/images/full/rose_noun_002_32331.jpg"
    )
    assert not _is_candidate_dictionary_image_url(
        "https://dictionary.cambridge.org/external/images/og-image.png"
    )
    assert not _is_candidate_dictionary_image_url(
        "https://www.ldoceonline.com/external/images/logo.svg"
    )
    assert not _is_candidate_dictionary_image_url(
        "https://www.ldoceonline.com/media/english/illustration/banner_ad.jpg"
    )


def test_cross_reference_definition():
    assert is_cross_reference_definition("past simple of rise")

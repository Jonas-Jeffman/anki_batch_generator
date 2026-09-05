from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from models import BuiltCard
from utils import stable_anki_id, stable_guid

try:
    import genanki
except ImportError:
    genanki = None


def create_deck_apkg(
    deck_name: str,
    output_apkg: Path,
    cards: List[BuiltCard],
    media_files: Iterable[Path],
) -> None:
    if genanki is None:
        raise RuntimeError("genanki is not installed; install project requirements to generate .apkg files.")
    deck_id = stable_anki_id(f"deck::{deck_name}")
    model_id = stable_anki_id("model::anki_batch_generator::basic_v2")

    model = genanki.Model(
        model_id=model_id,
        name="BatchAIGeneratedBasicModelV2",
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
  line-height: 1.6;
}
ul {
  margin-top: 4px;
  margin-bottom: 8px;
}
""",
    )

    deck = genanki.Deck(deck_id, deck_name)
    for card in cards:
        note = genanki.Note(
            model=model,
            fields=[card.front, card.back],
            tags=card.tags,
            guid=stable_guid(deck_name, card.guid_seed),
        )
        deck.add_note(note)

    package = genanki.Package(deck)
    package.media_files = [str(p) for p in media_files]
    output_apkg.parent.mkdir(parents=True, exist_ok=True)
    package.write_to_file(str(output_apkg))


__all__ = ["create_deck_apkg"]

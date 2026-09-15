from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anki_generator.senses.store import (
    ACTIVE,
    INACTIVE,
    CanonicalStore,
    build_canonical_key,
    canonical_guid_seed,
    definition_fingerprint,
)
from anki_generator.models import CanonicalSense, ProviderSense
from anki_generator.utils import stable_guid


def provider_sense(source: str, native_id: str, definition: str) -> ProviderSense:
    return ProviderSense(
        source=source,
        native_id=native_id,
        pos="noun",
        definition=definition,
    )


def canonical(native_id: str, index: int = 1) -> CanonicalSense:
    return CanonicalSense(
        word="nail",
        pos="noun",
        index=index,
        backbone_source="cambridge",
        backbone_sense_id=native_id,
        cambridge_sense_id=native_id,
    )


class CanonicalStoreTests(unittest.TestCase):
    def test_provider_reorder_preserves_index_key_and_final_guid(self):
        senses = [
            provider_sense("cambridge", "C-metal", "a pointed metal fastener"),
            provider_sense("cambridge", "C-finger", "the hard end of a finger"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "canonical.json"
            store = CanonicalStore(path)
            first = store.reconcile(
                word="nail",
                pos="noun",
                canonical_senses=[canonical("C-metal"), canonical("C-finger", 2)],
                provider_senses=senses,
            )
            store.save()
            first_identity = {
                item.backbone_sense_id: (
                    item.index,
                    item.canonical_key,
                    stable_guid("English::Test", canonical_guid_seed(item.canonical_key)),
                )
                for item in first
            }

            reloaded = CanonicalStore(path)
            second = reloaded.reconcile(
                word="nail",
                pos="noun",
                canonical_senses=[canonical("C-finger"), canonical("C-metal", 2)],
                provider_senses=list(reversed(senses)),
            )
            second_identity = {
                item.backbone_sense_id: (
                    item.index,
                    item.canonical_key,
                    stable_guid("English::Test", canonical_guid_seed(item.canonical_key)),
                )
                for item in second
            }
            self.assertEqual(first_identity, second_identity)
            self.assertEqual([1, 2], [item.index for item in second])

    def test_native_id_change_matches_definition_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CanonicalStore(Path(tmp) / "canonical.json")
            first = store.reconcile(
                word="nail",
                pos="noun",
                canonical_senses=[canonical("old-id")],
                provider_senses=[provider_sense("cambridge", "old-id", "A pointed, metal fastener.")],
            )[0]
            changed = canonical("new-id")
            second = store.reconcile(
                word="nail",
                pos="noun",
                canonical_senses=[changed],
                provider_senses=[provider_sense("cambridge", "new-id", "a pointed metal fastener")],
            )[0]
            self.assertEqual((first.index, first.canonical_key), (second.index, second.canonical_key))
            self.assertEqual(["old-id", "new-id"], store.entries[0].native_ids["cambridge"])

    def test_added_and_disappeared_senses_never_reorder_or_reuse_indexes(self):
        definitions = {
            "C1": "a pointed metal fastener",
            "C2": "the hard end of a finger",
            "C3": "a small projecting piece",
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = CanonicalStore(Path(tmp) / "canonical.json")
            initial = store.reconcile(
                word="nail",
                pos="noun",
                canonical_senses=[canonical("C1"), canonical("C2", 2)],
                provider_senses=[provider_sense("cambridge", key, value) for key, value in definitions.items()],
            )
            old_c2_key = initial[1].canonical_key
            only_first = store.reconcile(
                word="nail",
                pos="noun",
                canonical_senses=[canonical("C1")],
                provider_senses=[provider_sense("cambridge", key, value) for key, value in definitions.items()],
            )
            self.assertEqual([1], [item.index for item in only_first])
            self.assertEqual(INACTIVE, next(item for item in store.entries if item.index == 2).status)

            with_new = store.reconcile(
                word="nail",
                pos="noun",
                canonical_senses=[canonical("C3"), canonical("C1", 2)],
                provider_senses=[provider_sense("cambridge", key, value) for key, value in definitions.items()],
            )
            self.assertEqual({"C1": 1, "C3": 3}, {item.backbone_sense_id: item.index for item in with_new})

            restored = store.reconcile(
                word="nail",
                pos="noun",
                canonical_senses=[canonical("C1"), canonical("C2", 2), canonical("C3", 3)],
                provider_senses=[provider_sense("cambridge", key, value) for key, value in definitions.items()],
            )
            restored_c2 = next(item for item in restored if item.backbone_sense_id == "C2")
            self.assertEqual((2, old_c2_key, ACTIVE), (restored_c2.index, restored_c2.canonical_key, restored_c2.status))

    def test_save_is_versioned_atomic_and_tolerates_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "canonical.json"
            path.write_text("not json", encoding="utf-8")
            store = CanonicalStore(path)
            self.assertEqual([], store.entries)
            store.reconcile(
                word="nail",
                pos="noun",
                canonical_senses=[canonical("C1")],
                provider_senses=[provider_sense("cambridge", "C1", "a pointed metal fastener")],
            )
            store.save()
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(1, raw["version"])
            self.assertFalse(path.with_name(f".{path.name}.tmp").exists())
            self.assertEqual(store.entries, CanonicalStore(path).entries)

    def test_fingerprint_key_and_guid_seed_goldens(self):
        fingerprint = definition_fingerprint("A pointed, metal fastener.")
        key = build_canonical_key("Nail", "noun", fingerprint, "cambridge:C-metal")
        self.assertEqual("d289c10a8e370c08ff2d891160b7b15e5046ab5a", fingerprint)
        self.assertEqual("canonical::dced90d78e57ef874d75", key)
        self.assertEqual("en_word::canonical::dced90d78e57ef874d75", canonical_guid_seed(key))
        self.assertEqual(
            "b7407cda592e18f21f73",
            stable_guid("English::Test", canonical_guid_seed(key)),
        )

    def test_duplicate_definitions_use_native_ids_but_ambiguous_id_changes_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CanonicalStore(Path(tmp) / "canonical.json")
            first = store.reconcile(
                word="nail",
                pos="noun",
                canonical_senses=[canonical("C1"), canonical("C2", 2)],
                provider_senses=[
                    provider_sense("cambridge", "C1", "same definition"),
                    provider_sense("cambridge", "C2", "same definition"),
                ],
            )
            self.assertEqual(2, len({item.canonical_key for item in first}))
            with self.assertRaisesRegex(ValueError, "ambiguous canonical fingerprint"):
                store.reconcile(
                    word="nail",
                    pos="noun",
                    canonical_senses=[canonical("new-id")],
                    provider_senses=[
                        provider_sense("cambridge", "new-id", "same definition"),
                    ],
                )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import unittest

from school_csm_control_center.storage.field_preset_store import (
    FieldPresetStore,
    FieldPresetStoreError,
    SUPPORTED_PRESET_FIELDS,
)


class FieldPresetStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary.name)
        self.store = FieldPresetStore(self.project_root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_supported_fields_cover_shared_and_mode_specific_inputs(self) -> None:
        self.assertEqual(
            SUPPORTED_PRESET_FIELDS,
            (
                "mode",
                "survey_date",
                "agency_visited",
                "service_availed",
                "client_type",
                "sex",
                "age",
                "region",
                "email",
            ),
        )

    def test_missing_file_loads_empty_and_save_round_trips_versioned_document(self) -> None:
        self.assertEqual(self.store.load(), {})
        saved = self.store.save(
            {
                "mode": {"value": "onsite", "prefill": True, "locked": False},
                "age": {"value": 31, "prefill": False, "locked": False},
                "email": {"value": None, "prefill": False, "locked": False},
            }
        )
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(
            self.store.path,
            self.project_root / "data" / "csm_survey" / "field_presets.mossjson",
        )
        document = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(document["file_type"], "CSM Survey Field Presets")
        self.assertEqual(document["schema_version"], "1.0")
        self.assertTrue(document["updated_at"].endswith("Z"))
        self.assertEqual(document["fields"], saved)

    def test_locked_always_implies_prefill(self) -> None:
        saved = self.store.save(
            {
                "service_availed": {
                    "value": "Enrollment",
                    "prefill": False,
                    "locked": True,
                }
            }
        )
        self.assertEqual(
            saved["service_availed"],
            {"value": "Enrollment", "prefill": True, "locked": True},
        )

    def test_service_list_preset_normalizes_deduplicates_and_round_trips(self) -> None:
        saved = self.store.save(
            {
                "service_availed": {
                    "value": [
                        "  Enrollment (Walk-in)  ",
                        "Records   Request",
                        "enrollment (walk-in)",
                        "",
                    ],
                    "prefill": True,
                    "locked": True,
                }
            }
        )
        expected = {
            "value": ["Enrollment (Walk-in)", "Records Request"],
            "prefill": True,
            "locked": True,
        }
        self.assertEqual(saved["service_availed"], expected)
        self.assertEqual(self.store.load()["service_availed"], expected)

    def test_service_list_preset_rejects_non_text_entries(self) -> None:
        for invalid_value in (
            ["Enrollment", 7],
            [None],
            [{"label": "Enrollment"}],
        ):
            with self.subTest(invalid_value=invalid_value), self.assertRaisesRegex(
                ValueError,
                "Every selected school transaction must be text",
            ):
                self.store.save(
                    {
                        "service_availed": {
                            "value": invalid_value,
                            "prefill": True,
                            "locked": False,
                        }
                    }
                )

    def test_save_rejects_unknown_fields_and_invalid_entries(self) -> None:
        with self.assertRaises(TypeError):
            self.store.save([])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.store.save(
                {"unknown": {"value": "x", "prefill": True, "locked": False}}
            )
        with self.assertRaises(TypeError):
            self.store.save({"mode": "onsite"})  # type: ignore[dict-item]
        with self.assertRaises(ValueError):
            self.store.save({"mode": {"prefill": True, "locked": False}})
        with self.assertRaises(ValueError):
            self.store.save(
                {"mode": {"value": ["onsite"], "prefill": True, "locked": False}}
            )
        with self.assertRaises(ValueError):
            self.store.save(
                {"age": {"value": math.nan, "prefill": True, "locked": False}}
            )
        with self.assertRaises(ValueError):
            self.store.save(
                {"mode": {"value": "onsite", "prefill": 1, "locked": False}}
            )

    def test_defensive_load_skips_invalid_and_unknown_entries(self) -> None:
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text(
            json.dumps(
                {
                    "file_type": "CSM Survey Field Presets",
                    "schema_version": "1.0",
                    "fields": {
                        "mode": {"value": "online", "prefill": True, "locked": False},
                        "age": {"value": [], "prefill": True, "locked": False},
                        "unknown": {"value": "ignored", "prefill": True, "locked": False},
                    },
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            self.store.load(),
            {"mode": {"value": "online", "prefill": True, "locked": False}},
        )
        self.assertIn("age", self.store.last_read_error or "")

    def test_malformed_file_is_preserved_and_save_fails_closed(self) -> None:
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.store.load(), {})
        self.assertIsNotNone(self.store.last_read_error)
        expected = {
            "region": {"value": "Region VIII", "prefill": True, "locked": False}
        }
        with self.assertRaises(FieldPresetStoreError):
            self.store.save(expected)
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), "{broken")
        self.assertIsNotNone(self.store.last_recovery_copy)
        self.assertTrue(self.store.last_recovery_copy.is_file())

    def test_atomic_save_leaves_no_temporary_files_and_returns_copies(self) -> None:
        fields = {
            "client_type": {"value": "Citizen", "prefill": True, "locked": False}
        }
        saved = self.store.save(fields)
        saved["client_type"]["value"] = "Changed"
        fields["client_type"]["value"] = "Also changed"
        self.assertEqual(self.store.load()["client_type"]["value"], "Citizen")
        self.assertFalse(list(self.store.path.parent.glob("*.tmp")))

    def test_custom_supported_fields_are_enforced(self) -> None:
        custom = FieldPresetStore(self.project_root, supported_fields=("mode",))
        self.assertEqual(
            custom.save(
                {"mode": {"value": "online", "prefill": True, "locked": True}}
            )["mode"],
            {"value": "online", "prefill": True, "locked": True},
        )
        with self.assertRaises(ValueError):
            custom.save(
                {"region": {"value": "Region VIII", "prefill": True, "locked": False}}
            )


if __name__ == "__main__":
    unittest.main()

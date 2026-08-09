from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from school_csm_control_center.services.narrative_report import (
    LocalNarrativeGenerator,
    SIGNATORY_ROLES,
    UnknownNumericalClaimError,
    build_narrative_source,
    validate_narrative_document,
    validity_statement,
)
from school_csm_control_center.services.openai_narrative import (
    OpenAINarrativeClient,
    OpenAINarrativeError,
)
from school_csm_control_center.storage.dashboard_snapshot_store import (
    DashboardSnapshotStore,
    SnapshotConflictError,
    SnapshotIntegrityError,
)
from school_csm_control_center.storage.narrative_report_store import (
    NarrativeReportStore,
    NarrativeReportStoreError,
)
from school_csm_control_center.storage.windows_credential_store import (
    OPENAI_API_KEY_CREDENTIAL_TARGET,
    OpenAIApiKeyCredentialStore,
)


PNG = b"\x89PNG\r\n\x1a\nimmutable-dashboard-image"


def analysis_fixture() -> dict:
    return {
        "overview": {
            "total_responses": 12,
            "onsite_responses": 7,
            "online_responses": 5,
            "valid_sqd_answers": 64,
            "positive_sqd_answers": 52,
            "positive_rate": 81.25,
            "rating": "Satisfactory",
            "period": {"from": "2026-07-01", "to": "2026-07-31"},
        },
        "response_mix": {"answer_coverage_rate": 96.88},
        "dimensions": [
            {
                "key": "sqd1",
                "code": "SQD1",
                "dimension": "Responsiveness",
                "positive_rate": 90.0,
            },
            {
                "key": "sqd2",
                "code": "SQD2",
                "dimension": "Reliability",
                "positive_rate": 70.0,
            },
        ],
        "trend": [
            {
                "period": "2026-07",
                "label": "July 2026",
                "positive_rate": 81.25,
            }
        ],
        "citizen_charter": {"awareness_rate": 75.0},
        "services": [
            {"service": "Enrollment", "positive_rate": 88.0},
            {"service": "Records Request", "positive_rate": 72.0},
        ],
        "demographics": {"sex": [{"label": "Female", "count": 7}]},
        "sqd0": {"positive_rate": 85.0},
        "feedback": [
            {"text": "Private respondent comment", "control_number": "RESP-999"}
        ],
        "recent": [{"respondent": "Private Person", "age": 43}],
    }


def save_snapshot(root: Path) -> tuple[DashboardSnapshotStore, dict, dict]:
    store = DashboardSnapshotStore(root)
    reference = store.save(
        "print-record-001",
        "CSMS-PRN-2026-07-0001",
        analysis=analysis_fixture(),
        filters={"mode": "all", "search": "private phrase"},
        school={
            "school_name": "Calapi Elementary School",
            "school_id": "123456",
            "contact_email": "private@example.invalid",
        },
        report={
            "scope": "All responses",
            "survey_count": 12,
            "page_count": 3,
            "orientation": "landscape",
            "component_bounds": {"overview": [0, 0, 800, 400]},
        },
        image_png=PNG,
        image_meta={
            "width": 1600,
            "height": 900,
            "render_scale": 2.0,
            "component_bounds": {"overview": [0, 0, 800, 400]},
        },
        asset_bytes={"trend_graph": b"graph-pixels"},
    )
    return store, reference, store.load(reference)


class DashboardSnapshotStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_snapshot_round_trip_is_portable_idempotent_and_complete(self) -> None:
        store, reference, manifest = save_snapshot(self.root)
        self.assertEqual(
            set(reference),
            {"type", "schema_version", "snapshot_id", "manifest_path", "digest_sha256"},
        )
        self.assertFalse(Path(reference["manifest_path"]).is_absolute())
        self.assertEqual(manifest["source"]["print_record_id"], "print-record-001")
        self.assertEqual(manifest["analysis"]["overview"]["total_responses"], 12)
        self.assertEqual(
            manifest["blobs"]["dashboard_image"]["metadata"]["component_bounds"]["overview"],
            [0, 0, 800, 400],
        )
        self.assertEqual(store.read_blob(reference), PNG)
        self.assertEqual(store.read_blob(reference, "trend_graph"), b"graph-pixels")
        self.assertTrue(store.verify(reference))

        repeated = store.save(
            "print-record-001",
            "CSMS-PRN-2026-07-0001",
            analysis=analysis_fixture(),
            filters={"mode": "all", "search": "private phrase"},
            school={
                "school_name": "Calapi Elementary School",
                "school_id": "123456",
                "contact_email": "private@example.invalid",
            },
            report={
                "scope": "All responses",
                "survey_count": 12,
                "page_count": 3,
                "orientation": "landscape",
                "component_bounds": {"overview": [0, 0, 800, 400]},
            },
            image_png=PNG,
            image_meta={
                "width": 1600,
                "height": 900,
                "render_scale": 2.0,
                "component_bounds": {"overview": [0, 0, 800, 400]},
            },
            asset_bytes={"trend_graph": b"graph-pixels"},
        )
        self.assertEqual(repeated, reference)

        changed = analysis_fixture()
        changed["overview"]["total_responses"] = 13
        with self.assertRaises(SnapshotConflictError):
            store.save(
                "print-record-001",
                "CSMS-PRN-2026-07-0001",
                analysis=changed,
                filters={"mode": "all", "search": "private phrase"},
                school={
                    "school_name": "Calapi Elementary School",
                    "school_id": "123456",
                    "contact_email": "private@example.invalid",
                },
                report={
                    "scope": "All responses",
                    "survey_count": 12,
                    "page_count": 3,
                    "orientation": "landscape",
                    "component_bounds": {"overview": [0, 0, 800, 400]},
                },
                image_png=PNG,
                image_meta={
                    "width": 1600,
                    "height": 900,
                    "render_scale": 2.0,
                    "component_bounds": {"overview": [0, 0, 800, 400]},
                },
                asset_bytes={"trend_graph": b"graph-pixels"},
            )

        with self.assertRaisesRegex(ValueError, "reserved control number"):
            store.save(
                "print-record-002",
                "CSMS-PRN-2026-07-0002",
                analysis=analysis_fixture(),
                filters={},
                school={},
                report={"control_number": "CSMS-PRN-2026-07-9999"},
                image_png=PNG,
                image_meta={},
            )
        with self.assertRaisesRegex(ValueError, "API credentials"):
            store.save(
                "print-record-003",
                "CSMS-PRN-2026-07-0003",
                analysis=analysis_fixture(),
                filters={"api_key": "must-never-be-snapshotted"},
                school={},
                report={},
                image_png=PNG,
                image_meta={},
            )

    def test_tamper_and_legacy_eligibility_are_detected(self) -> None:
        store, reference, _manifest = save_snapshot(self.root)
        dashboard_path = (
            self.root
            / "data"
            / "csm_survey"
            / "dashboard_snapshots"
            / reference["snapshot_id"]
            / "dashboard.png"
        )
        dashboard_path.write_bytes(PNG + b"tampered")
        with self.assertRaises(SnapshotIntegrityError):
            store.verify(reference)

        self.assertEqual(
            DashboardSnapshotStore.narrative_eligibility({"status": "confirmed"}),
            (False, "This older print record has no immutable Dashboard snapshot."),
        )
        eligible = {
            "status": "submitted",
            "dashboard_snapshot": reference,
        }
        self.assertTrue(
            DashboardSnapshotStore.is_print_record_narrative_eligible(eligible)
        )
        self.assertFalse(
            DashboardSnapshotStore.is_print_record_narrative_eligible(
                {"status": "failed", "dashboard_snapshot": reference}
            )
        )


class NarrativeGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.snapshots, self.reference, self.snapshot = save_snapshot(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_local_generation_is_deterministic_complete_and_source_bound(self) -> None:
        generator = LocalNarrativeGenerator()
        first = generator.generate(self.snapshot)
        second = generator.generate(self.snapshot)
        self.assertEqual(first, second)
        self.assertEqual(
            first["validity_statement"],
            validity_statement("CSMS-PRN-2026-07-0001"),
        )
        self.assertEqual(
            [entry["role"] for entry in first["signatories"]],
            list(SIGNATORY_ROLES),
        )
        self.assertEqual(first["source"]["snapshot_sha256"], self.reference["digest_sha256"])
        self.assertIn("81.25%", first["sections"][0]["paragraphs"][1])
        validate_narrative_document(first, self.snapshot)

        safe_source = build_narrative_source(self.snapshot)
        serialized = json.dumps(safe_source)
        self.assertNotIn("Private respondent comment", serialized)
        self.assertNotIn("Private Person", serialized)
        self.assertNotIn("private phrase", serialized)
        self.assertNotIn("private@example.invalid", serialized)

        nested = deepcopy(self.snapshot)
        nested["filters"] = {
            "demographics": {"search_text": "private nested phrase", "sex": "Female"}
        }
        nested_source = json.dumps(build_narrative_source(nested))
        self.assertNotIn("private nested phrase", nested_source)
        self.assertIn("Female", nested_source)

    def test_unknown_figure_is_rejected(self) -> None:
        document = LocalNarrativeGenerator().generate(self.snapshot)
        document["sections"][-1]["paragraphs"].append(
            "This unsupported result claims a 999.99% response rate."
        )
        with self.assertRaises(UnknownNumericalClaimError):
            validate_narrative_document(document, self.snapshot)
        hidden = LocalNarrativeGenerator().generate(self.snapshot)
        hidden["unsupported_notes"] = "Invented total: 777777."
        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate_narrative_document(hidden, self.snapshot)
        secret = LocalNarrativeGenerator().generate(self.snapshot)
        secret["sections"][-1]["paragraphs"].append(
            "Credential sk-proj-this-must-never-be-written"
        )
        with self.assertRaisesRegex(ValueError, "API credentials"):
            validate_narrative_document(secret, self.snapshot)

    def test_openai_adapter_uses_responses_schema_and_revalidates_output(self) -> None:
        sections = {
            "executive_summary": ["The preserved evidence provides the report baseline."],
            "scope_and_methodology": ["Only aggregate Dashboard evidence was used."],
            "key_findings": ["The result is supported by the preserved snapshot."],
            "strengths": ["Maintain practices associated with stronger results."],
            "areas_for_improvement": ["Review the lowest-performing service area."],
            "recommended_actions": ["Document assigned actions and monitor progress."],
            "conclusion": ["Use this narrative only with its referenced Dashboard."],
        }

        class Responses:
            def __init__(self, payload: dict) -> None:
                self.payload = payload
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return type(
                    "Response",
                    (),
                    {
                        "output_text": json.dumps(self.payload),
                        "id": "resp_safe_001",
                    },
                )()

        class FakeClient:
            def __init__(self, payload: dict) -> None:
                self.responses = Responses(payload)

        captured = {}
        fake = FakeClient(sections)

        def factory(key: str, timeout: float):
            captured["key"] = key
            captured["timeout"] = timeout
            return fake

        client = OpenAINarrativeClient(client_factory=factory)
        secret = "sk-proj-super-secret-value"
        document = client.generate(self.snapshot, api_key=secret)
        self.assertEqual(captured["key"], secret)
        self.assertFalse(fake.responses.kwargs["store"])
        self.assertEqual(
            fake.responses.kwargs["text"]["format"]["type"],
            "json_schema",
        )
        self.assertTrue(fake.responses.kwargs["text"]["format"]["strict"])
        self.assertNotIn(secret, json.dumps(document))
        self.assertNotIn(secret, json.dumps(client.last_response_metadata))

        unsafe = deepcopy(sections)
        unsafe["conclusion"] = ["An unsupported 999% result was invented."]
        unsafe_client = OpenAINarrativeClient(
            client_factory=lambda _key, _timeout: FakeClient(unsafe)
        )
        with self.assertRaises(OpenAINarrativeError) as raised:
            unsafe_client.generate(self.snapshot, api_key=secret)
        self.assertNotIn(secret, str(raised.exception))


class NarrativeReportStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.snapshots, self.reference, self.snapshot = save_snapshot(self.root)
        self.document = LocalNarrativeGenerator().generate(self.snapshot)
        self.store = NarrativeReportStore(
            self.root,
            snapshot_store=self.snapshots,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_create_approve_edit_and_print_audits_are_append_only(self) -> None:
        created = self.store.create(
            self.reference,
            self.document,
            operator="CSM Coordinator",
            created_at="2026-07-31T10:00:00+08:00",
        )
        self.assertEqual(created["control_number"], "CSMS-NAR-2026-07-0001")
        self.assertEqual(created["status"], "draft")
        self.assertEqual(len(created["revisions"]), 1)
        repeated = self.store.create(self.reference, self.document)
        self.assertEqual(repeated["id"], created["id"])

        approved = self.store.approve(created["id"], "School Head")
        self.assertEqual(approved["status"], "approved")
        first_print = self.store.record_print(
            created["id"],
            printer_name="Office Printer",
            page_count=4,
            copies=1,
            operator="CSM Coordinator",
        )
        self.assertEqual(first_print["print_audits"][0]["kind"], "original")
        reprinted = self.store.record_reprint(
            created["id"],
            printer_name="Office Printer",
            page_count=4,
            copies=2,
            operator="Records Officer",
        )
        self.assertEqual(
            [audit["kind"] for audit in reprinted["print_audits"]],
            ["original", "reprint"],
        )
        self.assertEqual(
            reprinted["print_audits"][1]["dashboard_control_number"],
            "CSMS-PRN-2026-07-0001",
        )

        edited_document = deepcopy(self.document)
        edited_document["sections"][-1]["paragraphs"].append(
            "The office will document follow-through on the approved actions."
        )
        edited = self.store.append_revision(
            created["id"],
            edited_document,
            editor="CSM Coordinator",
            reason="Clarified follow-through",
        )
        self.assertEqual(edited["status"], "draft")
        self.assertIsNone(edited["approval"])
        self.assertEqual(len(edited["approval_history"]), 1)
        self.assertEqual(len(edited["revisions"]), 2)
        self.assertEqual(len(edited["print_audits"]), 2)
        with self.assertRaisesRegex(NarrativeReportStoreError, "approved"):
            self.store.record_print(
                created["id"],
                printer_name="Office Printer",
                page_count=4,
            )

        reapproved = self.store.approve(created["id"], "School Head")
        final_print = self.store.record_reprint(
            created["id"],
            printer_name="Office Printer",
            page_count=4,
        )
        self.assertEqual(final_print["print_audits"][-1]["revision_number"], 2)
        self.assertEqual(
            final_print["print_audits"][-1]["approval_id"],
            reapproved["approval"]["approval_id"],
        )
        self.assertTrue(self.store.verify(created["id"]))

    def test_integrity_check_detects_revision_tampering(self) -> None:
        created = self.store.create(self.reference, self.document)
        document = json.loads(self.store.path.read_text(encoding="utf-8"))
        revision = document["records"][0]["revisions"][0]
        revision["document"]["sections"][-1]["paragraphs"][0] = "Tampered prose."
        self.store.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(NarrativeReportStoreError, "SHA-256"):
            self.store.verify(created["id"])

    def test_unknown_edits_secrets_and_corrupt_store_are_rejected(self) -> None:
        created = self.store.create(self.reference, self.document)
        unsafe = deepcopy(self.document)
        unsafe["sections"][-1]["paragraphs"].append("Invented total: 777777.")
        with self.assertRaises(UnknownNumericalClaimError):
            self.store.append_revision(created["id"], unsafe)

        other_root = self.root / "other"
        other_snapshots, other_reference, other_snapshot = save_snapshot(other_root)
        other_store = NarrativeReportStore(other_root, snapshot_store=other_snapshots)
        with self.assertRaisesRegex(ValueError, "credentials"):
            other_store.create(
                other_reference,
                LocalNarrativeGenerator().generate(other_snapshot),
                generation_method="openai",
                generation_metadata={"api_key": "must-never-be-written"},
            )
        with self.assertRaisesRegex(ValueError, "credentials"):
            other_store.create(
                other_reference,
                LocalNarrativeGenerator().generate(other_snapshot),
                generation_method="openai",
                generation_metadata={"model": "sk-proj-this-is-not-a-model"},
            )
        self.assertFalse(other_store.path.exists())

        before = self.store.path.read_bytes()
        self.store.path.write_text("{broken-json", encoding="utf-8")
        with self.assertRaisesRegex(NarrativeReportStoreError, "left unchanged"):
            self.store.create(self.reference, self.document)
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), "{broken-json")
        self.assertNotEqual(before, self.store.path.read_bytes())


class CredentialStoreTests(unittest.TestCase):
    def test_exact_target_and_explicit_key_lifecycle(self) -> None:
        class MemoryManager:
            def __init__(self) -> None:
                self.values = {}

            def write(self, target, secret):
                self.values[target] = secret

            def read(self, target):
                return self.values.get(target)

            def delete(self, target):
                return self.values.pop(target, None) is not None

        manager = MemoryManager()
        store = OpenAIApiKeyCredentialStore(manager)
        self.assertEqual(
            store.target,
            "MoSSLab.SchoolCSMControlCenter.OpenAIApiKey",
        )
        self.assertEqual(store.target, OPENAI_API_KEY_CREDENTIAL_TARGET)
        self.assertFalse(store.exists())
        store.save("sk-proj-operator-owned")
        self.assertEqual(store.load(), "sk-proj-operator-owned")
        self.assertTrue(store.exists())
        self.assertTrue(store.delete())
        self.assertFalse(store.exists())
        self.assertFalse(store.delete())


if __name__ == "__main__":
    unittest.main()

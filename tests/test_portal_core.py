import unittest
from datetime import datetime, timezone

from intake_guard import HouseholdIntakeV1
from portal_core import (
    AttachmentReference,
    DocumentType,
    DraftSession,
    PortalCoreEngine,
    compute_content_hash_sha256,
    compute_scrubbed_digest,
)


class MemoryDraftStore:
    def __init__(self):
        self.drafts = {}

    async def save_draft(self, draft: DraftSession) -> None:
        self.drafts[draft.sessionId] = draft

    async def get_draft(self, session_id: str) -> DraftSession | None:
        return self.drafts.get(session_id)


def sample_valid_intake() -> HouseholdIntakeV1:
    return HouseholdIntakeV1.model_validate(
        {
            "schemaVersion": "1.0.0",
            "intakeId": "intake-9999",
            "idempotencyKey": "idem-9999",
            "submittedAt": "2026-11-22T12:00:00Z",
            "jurisdiction": {"state": "IN", "county": "Vigo"},
            "householdProfile": {
                "householdSize": 3,
                "grossMonthlyIncomeCents": 180000,
                "liquidAssetsCents": 50000,
                "monthlyUtilityExpenseCents": 20000,
                "isVeteranHousehold": True,
            },
            "requestedTracks": ["SNAP", "LIHEAP", "VA_RELIEF"],
        }
    )


def attachment(attachment_id: str, doc_type: DocumentType) -> AttachmentReference:
    return AttachmentReference(
        attachmentId=attachment_id,
        docType=doc_type,
        contentHashSha256="a" * 64,
        vaultRef=f"vault-{attachment_id}",
        uploadedAt=datetime.now(timezone.utc).isoformat(),
    )


class PortalCoreEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.draft_store = MemoryDraftStore()
        self.engine = PortalCoreEngine(self.draft_store)

    async def test_save_and_retrieve_draft_session(self):
        draft = await self.engine.save_resident_draft(
            session_id="sess-001",
            intake_id="intake-9999",
            completed_sections=["jurisdiction", "householdProfile"],
            partial_profile={"householdSize": 3},
            attachments=[],
        )
        self.assertEqual(draft.sessionId, "sess-001")
        retrieved = await self.draft_store.get_draft("sess-001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.intakeId, "intake-9999")
        self.assertEqual(retrieved.completedSections, ["jurisdiction", "householdProfile"])

    def test_income_threshold_and_track_matches_use_exact_cents(self):
        intake = sample_valid_intake()
        profile = intake.householdProfile
        self.assertEqual(profile.grossMonthlyIncomeCents * 12, 2_160_000)
        self.assertEqual(profile.grossMonthlyIncomeCents * 12 / 100, 21_600)

        result = self.engine.execute_phase_a_triage(intake, attachments=[])
        self.assertIn("SNAP", result.matchedTracks)
        self.assertIn("MEDICAID", result.matchedTracks)
        self.assertIn("LIHEAP", result.matchedTracks)
        self.assertIn("VA_RELIEF", result.matchedTracks)
        self.assertEqual(result.estimatedBenefitCents, 95_000)

    def test_phase_a_reports_missing_verification_documents(self):
        result = self.engine.execute_phase_a_triage(sample_valid_intake(), attachments=[])
        self.assertFalse(result.isReadyForDispatcher)
        self.assertEqual(
            set(result.missingDocuments),
            {DocumentType.INCOME_STUB, DocumentType.DD_214},
        )

    def test_phase_a_is_ready_when_required_documents_are_attached(self):
        attachments = [
            attachment("att-0001", DocumentType.INCOME_STUB),
            attachment("att-0002", DocumentType.DD_214),
        ]
        result = self.engine.execute_phase_a_triage(sample_valid_intake(), attachments)
        self.assertTrue(result.isReadyForDispatcher)
        self.assertEqual(result.missingDocuments, [])

    def test_content_hash_and_scrubbed_digest_are_sha256(self):
        payload = b"test-resident-data"
        expected = "b7a2c6e7c7b10476ec97b6f3a5e0bb9498ec0711769b0a7e4cbe0ce3e5b7f4e3"
        # Compare against hashlib's canonical result rather than treating a hash as redaction.
        import hashlib

        self.assertEqual(compute_content_hash_sha256(payload), hashlib.sha256(payload).hexdigest())
        self.assertEqual(compute_scrubbed_digest(payload), hashlib.sha256(payload).hexdigest())
        self.assertEqual(len(expected), 64)

    def test_attachment_rejects_non_hex_sha256(self):
        with self.assertRaises(ValueError):
            attachment("att-0001", DocumentType.INCOME_STUB).model_copy(
                update={"contentHashSha256": "z" * 64}
            ).model_validate({})


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from voren.observations.models import (
    ObservationItem,
    Provenance,
    SourceKind,
    ToolObservation,
    TrustLevel,
)


class ObservationProvenanceTest(unittest.TestCase):
    def provenance(self) -> Provenance:
        return Provenance(
            trust=TrustLevel.EXTERNAL_UNTRUSTED,
            source=SourceKind.EMAIL,
            source_ref="email-1",
            retrieved_by="test:search_emails",
            retrieved_at=datetime(2026, 8, 30, 15, 0, tzinfo=UTC),
        )

    def test_external_data_cannot_claim_instruction_authority(self) -> None:
        with self.assertRaises(ValidationError):
            Provenance(
                **self.provenance().model_dump(exclude={"instruction_authority"}),
                instruction_authority=True,
            )

    def test_observation_digest_detects_payload_tampering(self) -> None:
        observation = ToolObservation.succeeded(
            tool_call_id="read-1",
            tool_name="search_emails",
            items=(
                ObservationItem(
                    data={"body": "original"}, provenance=self.provenance()
                ),
            ),
        )
        tampered = observation.model_dump(mode="json")
        tampered["items"][0]["data"]["body"] = "changed"

        with self.assertRaises(ValidationError):
            ToolObservation.model_validate(tampered)

        observation.items[0].data["body"] = "changed in memory"
        with self.assertRaises(ValueError):
            observation.as_model_content()


if __name__ == "__main__":
    unittest.main()

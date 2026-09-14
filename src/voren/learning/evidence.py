"""Durable evidence objects and the only routers into long-term learning."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Self

from pydantic import Field, model_validator

from voren.learning.models import EvidenceRef, EvidenceSource, FrozenModel
from voren.runs.models import RunEventType, RunStatus
from voren.runs.store import SQLiteRunStore


EVIDENCE_SCHEMA_VERSION = "voren-learning-evidence/v1"


class LearningEvidenceArtifact(FrozenModel):
    schema_version: str = EVIDENCE_SCHEMA_VERSION
    evidence_id: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$", max_length=200
    )
    source: EvidenceSource
    source_ref: str = Field(min_length=1, max_length=500)
    instruction_authority: bool
    evaluation_case_ids: tuple[str, ...] = ()
    payload_media_type: str = Field(min_length=1, max_length=200)
    payload: str = Field(min_length=1, max_length=1_000_000)
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(
        cls,
        *,
        evidence_id: str,
        source: EvidenceSource,
        source_ref: str,
        instruction_authority: bool,
        evaluation_case_ids: tuple[str, ...],
        payload_media_type: str,
        payload: str,
        created_at: datetime,
    ) -> Self:
        provisional = cls(
            evidence_id=evidence_id,
            source=source,
            source_ref=source_ref,
            instruction_authority=instruction_authority,
            evaluation_case_ids=evaluation_case_ids,
            payload_media_type=payload_media_type,
            payload=payload,
            payload_digest=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            created_at=created_at,
            artifact_digest="0" * 64,
        )
        return cls.model_validate(
            {
                **provisional.model_dump(mode="python"),
                "artifact_digest": provisional.calculated_digest(),
            }
        )

    @model_validator(mode="after")
    def source_and_content_are_consistent(self) -> Self:
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported learning evidence schema")
        if hashlib.sha256(self.payload.encode("utf-8")).hexdigest() != (
            self.payload_digest
        ):
            raise ValueError("learning evidence payload digest does not match")
        if self.source is EvidenceSource.OPERATOR_CORRECTION:
            if not self.instruction_authority:
                raise ValueError("operator correction must carry authority")
            if self.payload_media_type != "text/plain":
                raise ValueError("operator correction must contain plain text")
        elif self.source is EvidenceSource.VERIFIED_RUN:
            if self.instruction_authority:
                raise ValueError("verified run cannot carry instruction authority")
            if self.payload_media_type != "application/vnd.voren.run+json":
                raise ValueError("verified run must contain a run snapshot")
        else:
            raise ValueError(
                "model reflection and external observation are not durable "
                "authorizing evidence objects"
            )
        if len(self.evaluation_case_ids) != len(set(self.evaluation_case_ids)):
            raise ValueError("evidence evaluation case IDs must be unique")
        if any(not item.strip() for item in self.evaluation_case_ids):
            raise ValueError("evidence evaluation case IDs must be non-empty")
        return self

    def calculated_digest(self) -> str:
        unsigned = self.model_dump(mode="json", exclude={"artifact_digest"})
        canonical = json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def assert_integrity(self) -> None:
        if self.artifact_digest != self.calculated_digest():
            raise ValueError("learning evidence artifact digest does not match")

    def as_ref(self) -> EvidenceRef:
        self.assert_integrity()
        return EvidenceRef(
            evidence_id=self.evidence_id,
            source=self.source,
            digest=self.artifact_digest,
            instruction_authority=self.instruction_authority,
            evaluation_case_ids=self.evaluation_case_ids,
        )


class LearningEvidenceRoutingError(ValueError):
    pass


class DurableLearningRouter:
    def __init__(self, *, evidence_store, run_store: SQLiteRunStore | None = None):
        self._evidence_store = evidence_store
        self._run_store = run_store

    def record_operator_correction(
        self,
        *,
        evidence_id: str,
        correction: str,
        operator_ref: str,
        created_at: datetime | None = None,
    ) -> EvidenceRef:
        if not correction.strip():
            raise LearningEvidenceRoutingError(
                "operator correction must be non-empty"
            )
        artifact = LearningEvidenceArtifact.create(
            evidence_id=evidence_id,
            source=EvidenceSource.OPERATOR_CORRECTION,
            source_ref=operator_ref,
            instruction_authority=True,
            evaluation_case_ids=(),
            payload_media_type="text/plain",
            payload=correction,
            created_at=created_at or datetime.now(UTC),
        )
        return self._evidence_store.record_evidence(artifact).as_ref()

    def select_verified_run(
        self,
        *,
        evidence_id: str,
        run_id: str,
        evaluation_case_ids: tuple[str, ...] = (),
        created_at: datetime | None = None,
    ) -> EvidenceRef:
        if self._run_store is None:
            raise LearningEvidenceRoutingError(
                "verified-run routing requires a Run Store"
            )
        run = self._run_store.get_run(run_id)
        events = self._run_store.list_events(run_id)
        if run.status is not RunStatus.COMPLETED:
            raise LearningEvidenceRoutingError(
                "only a completed Run can become verified learning evidence"
            )
        if not events or events[-1].event_type is not RunEventType.RUN_COMPLETED:
            raise LearningEvidenceRoutingError(
                "completed Run is missing its terminal event"
            )
        observed_actions = any(
            event.event_type is RunEventType.ACTION_PROPOSED for event in events
        )
        receipt_events = tuple(
            event
            for event in events
            if event.event_type
            in {RunEventType.ACTION_RECEIPT, RunEventType.ACTION_RECONCILED}
        )
        if observed_actions and not receipt_events:
            raise LearningEvidenceRoutingError(
                "action-taking Run has no durable receipt evidence"
            )
        final_receipts = {
            str(event.payload.get("operation_id")): event
            for event in receipt_events
        }
        proposals = {
            str(event.payload.get("operation_id")): event.payload
            for event in events
            if event.event_type is RunEventType.ACTION_PROPOSED
        }
        approvals = {
            str(event.payload.get("operation_id")): event.payload
            for event in events
            if event.event_type is RunEventType.APPROVAL_ACCEPTED
            and event.payload.get("approved") is True
        }
        if set(proposals) != set(final_receipts) or set(proposals) != set(approvals):
            raise LearningEvidenceRoutingError(
                "Run proposal, approval, and receipt sets do not match"
            )
        for operation_id, receipt in final_receipts.items():
            payload = receipt.payload
            verification = payload.get("verification")
            proposal = proposals[operation_id]
            approval = approvals[operation_id]
            if (
                payload.get("status") != "verified"
                or payload.get("committed") is not True
                or not isinstance(verification, dict)
                or verification.get("passed") is not True
                or payload.get("proposal_digest")
                != proposal.get("proposal_digest")
                or approval.get("proposal_digest")
                != proposal.get("proposal_digest")
                or payload.get("approval_id") != approval.get("approval_id")
            ):
                raise LearningEvidenceRoutingError(
                    "Run contains an unverified or incorrectly bound action"
                )
        if observed_actions and run.last_receipt_status != "verified":
            raise LearningEvidenceRoutingError(
                "Run terminal state is not bound to a verified receipt"
            )
        for event in events:
            if event.event_type is not RunEventType.TOOL_OBSERVED:
                continue
            trust = event.payload.get("trust_levels", [])
            if (
                event.payload.get("instruction_authority") is True
                and "external_untrusted" in trust
            ):
                raise LearningEvidenceRoutingError(
                    "untrusted observation cannot authorize durable learning"
                )

        payload = json.dumps(
            {
                "run": run.model_dump(mode="json"),
                "events": [event.model_dump(mode="json") for event in events],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        artifact = LearningEvidenceArtifact.create(
            evidence_id=evidence_id,
            source=EvidenceSource.VERIFIED_RUN,
            source_ref=run_id,
            instruction_authority=False,
            evaluation_case_ids=evaluation_case_ids,
            payload_media_type="application/vnd.voren.run+json",
            payload=payload,
            created_at=created_at or datetime.now(UTC),
        )
        return self._evidence_store.record_evidence(artifact).as_ref()

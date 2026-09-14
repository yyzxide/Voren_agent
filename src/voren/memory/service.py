"""Evidence-aware classification into profile or episode memory."""

from __future__ import annotations

from datetime import UTC, datetime

from voren.learning.models import EvidenceSource
from voren.memory.models import MemoryEvidenceSource, MemoryKind, MemoryRecord
from voren.memory.store import MemoryStoreError, SQLiteMemoryStore


class MemoryService:
    def __init__(self, *, memories: SQLiteMemoryStore, evidence_store) -> None:
        self._memories = memories
        self._evidence_store = evidence_store

    def record_profile_preference(
        self,
        *,
        memory_id: str,
        evidence_id: str,
        reason: str,
        created_at: datetime | None = None,
    ) -> MemoryRecord:
        evidence = self._evidence_store.get_evidence(evidence_id)
        evidence.assert_integrity()
        if evidence.source is not EvidenceSource.OPERATOR_CORRECTION:
            raise MemoryStoreError(
                "only persisted operator correction evidence can update profile memory"
            )
        timestamp = created_at or datetime.now(UTC)
        record = MemoryRecord.create(
            kind=MemoryKind.PROFILE_PREFERENCE,
            memory_id=memory_id,
            content=evidence.payload,
            evidence_id=evidence.evidence_id,
            evidence_digest=evidence.artifact_digest,
            evidence_source=MemoryEvidenceSource.OPERATOR_CORRECTION,
            created_at=timestamp,
        )
        return self._memories.record_profile(
            record,
            reason=reason,
            activated_at=timestamp,
        )

    def record_episode_summary(
        self,
        *,
        memory_id: str,
        evidence_id: str,
        summary: str,
        created_at: datetime | None = None,
    ) -> MemoryRecord:
        evidence = self._evidence_store.get_evidence(evidence_id)
        evidence.assert_integrity()
        if evidence.source is not EvidenceSource.VERIFIED_RUN:
            raise MemoryStoreError(
                "only persisted verified-run evidence can create an episode summary"
            )
        record = MemoryRecord.create(
            kind=MemoryKind.EPISODE_SUMMARY,
            memory_id=memory_id,
            content=summary,
            evidence_id=evidence.evidence_id,
            evidence_digest=evidence.artifact_digest,
            evidence_source=MemoryEvidenceSource.VERIFIED_RUN,
            created_at=created_at or datetime.now(UTC),
        )
        return self._memories.record_episode(record)

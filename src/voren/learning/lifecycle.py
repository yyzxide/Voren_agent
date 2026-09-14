"""Integrity-chained audit events for the Skill candidate lifecycle."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from voren.learning.models import CandidateStatus, FrozenModel
from voren.skills.models import SkillVersionRef


class CandidateEventType(StrEnum):
    STAGED = "staged"
    DECIDED = "decided"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"


class CandidateLifecycleEvent(FrozenModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(gt=0)
    event_type: CandidateEventType
    from_status: CandidateStatus | None
    to_status: CandidateStatus
    reason: str = Field(min_length=1, max_length=2_000)
    occurred_at: datetime
    evaluation_artifact_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    active_from: SkillVersionRef | None = None
    active_to: SkillVersionRef | None = None
    previous_event_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    event_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        sequence: int,
        event_type: CandidateEventType,
        from_status: CandidateStatus | None,
        to_status: CandidateStatus,
        reason: str,
        occurred_at: datetime,
        evaluation_artifact_digest: str | None = None,
        active_from: SkillVersionRef | None = None,
        active_to: SkillVersionRef | None = None,
        previous_event_digest: str | None = None,
    ) -> Self:
        provisional = cls(
            candidate_id=candidate_id,
            sequence=sequence,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            occurred_at=occurred_at,
            evaluation_artifact_digest=evaluation_artifact_digest,
            active_from=active_from,
            active_to=active_to,
            previous_event_digest=previous_event_digest,
            event_digest="0" * 64,
        )
        return cls.model_validate(
            {
                **provisional.model_dump(mode="python"),
                "event_digest": provisional.calculated_digest(),
            }
        )

    @model_validator(mode="after")
    def transition_is_well_formed(self) -> Self:
        expected = {
            CandidateEventType.STAGED: (None, CandidateStatus.STAGED),
            CandidateEventType.PROMOTED: (
                CandidateStatus.ACCEPTED,
                CandidateStatus.PROMOTED,
            ),
            CandidateEventType.ROLLED_BACK: (
                CandidateStatus.PROMOTED,
                CandidateStatus.ROLLED_BACK,
            ),
        }
        if self.event_type is CandidateEventType.DECIDED:
            if self.from_status is not CandidateStatus.STAGED or self.to_status not in {
                CandidateStatus.ACCEPTED,
                CandidateStatus.REJECTED,
            }:
                raise ValueError("decision event has an invalid status transition")
        elif (self.from_status, self.to_status) != expected[self.event_type]:
            raise ValueError("candidate event has an invalid status transition")

        if self.event_type is CandidateEventType.STAGED:
            if self.sequence != 1 or self.previous_event_digest is not None:
                raise ValueError("staged event must start the event chain")
            if self.evaluation_artifact_digest is not None:
                raise ValueError("staged event cannot bind an evaluation")
        elif (
            self.sequence < 2
            or self.previous_event_digest is None
            or self.evaluation_artifact_digest is None
        ):
            raise ValueError(
                "post-staging event requires prior event and evaluation digests"
            )

        changes_active = self.event_type in {
            CandidateEventType.PROMOTED,
            CandidateEventType.ROLLED_BACK,
        }
        if changes_active != (
            self.active_from is not None and self.active_to is not None
        ):
            raise ValueError(
                "active Skill references are required only for pointer changes"
            )
        if changes_active and self.active_from == self.active_to:
            raise ValueError("active pointer event must change the Skill version")
        return self

    def calculated_digest(self) -> str:
        unsigned = self.model_dump(mode="json", exclude={"event_digest"})
        canonical = json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def assert_integrity(self) -> None:
        if self.event_digest != self.calculated_digest():
            raise ValueError("candidate lifecycle event digest does not match")

"""Deterministic email/calendar world used to prove action semantics."""

from __future__ import annotations

from typing import Literal

from voren.actions.errors import AmbiguousCommitError, KnownPreCommitFailure
from voren.actions.models import Effect, EffectKind, ObservedEffect, Sensitivity
from voren.adapters.workspace_contracts import (
    DEFAULT_ACCOUNT_EMAIL,
    CreateCalendarEventInput,
)


FailureMode = Literal[
    "none",
    "before_commit",
    "ambiguous_without_commit",
    "after_commit",
    "unexpected_effect",
]


class FakeWorkspaceAdapter:
    """In-memory state with operation markers and deterministic fault injection."""

    def __init__(
        self,
        *,
        account_email: str = DEFAULT_ACCOUNT_EMAIL,
        failure_mode: FailureMode = "none",
    ) -> None:
        self.account_email = account_email
        self.failure_mode = failure_mode
        self.events: dict[str, dict] = {}
        self.emails: dict[str, dict] = {}
        self.audit_entries: dict[str, dict] = {}
        self.operation_references: dict[str, dict[str, str]] = {}
        self.commit_attempts = 0

    def commit(self, proposal) -> None:
        self.commit_attempts += 1
        if proposal.operation_id in self.operation_references:
            return
        if self.failure_mode == "before_commit":
            raise KnownPreCommitFailure("simulated failure before external dispatch")
        if self.failure_mode == "ambiguous_without_commit":
            raise AmbiguousCommitError("simulated timeout with no observable commit")

        arguments = CreateCalendarEventInput.model_validate(proposal.arguments)
        event_id = str(len(self.events) + 1)
        email_id = str(len(self.emails) + 1)
        recipients = tuple(sorted(set((*arguments.participants, self.account_email))))
        self.events[event_id] = {
            "operation_id": proposal.operation_id,
            "title": arguments.title,
            "description": arguments.description,
            "start_time": arguments.start_time.isoformat(),
            "end_time": arguments.end_time.isoformat(),
            "location": arguments.location,
            "participants": list(recipients),
        }
        self.emails[email_id] = {
            "operation_id": proposal.operation_id,
            "subject": f"Invitation: {arguments.title}",
            "body": arguments.description,
            "recipients": list(recipients),
            "attachment_kind": "calendar_event",
        }
        references = {"event_id": event_id, "email_id": email_id}

        if self.failure_mode == "unexpected_effect":
            audit_id = str(len(self.audit_entries) + 1)
            self.audit_entries[audit_id] = {
                "operation_id": proposal.operation_id,
                "message": "unexpected audit write",
            }
            references["audit_id"] = audit_id

        self.operation_references[proposal.operation_id] = references
        if self.failure_mode == "after_commit":
            raise AmbiguousCommitError("simulated timeout after external commit")

    def observe(self, proposal) -> tuple[ObservedEffect, ...]:
        references = self.operation_references.get(proposal.operation_id)
        if references is None:
            return ()

        event_id = references["event_id"]
        email_id = references["email_id"]
        event = self.events[event_id]
        email = self.emails[email_id]
        observed: list[ObservedEffect] = [
            ObservedEffect(
                effect=Effect(
                    effect_id="calendar_event",
                    resource="calendar.events",
                    kind=EffectKind.CREATE,
                    target="new_event",
                    summary=f"Create calendar event: {event['title']}",
                    attributes={key: value for key, value in event.items() if key != "operation_id"},
                    reversible=True,
                    sensitivity=Sensitivity.INTERNAL,
                ),
                external_reference=event_id,
            ),
            ObservedEffect(
                effect=Effect(
                    effect_id="invitation_email",
                    resource="inbox.emails",
                    kind=EffectKind.SEND,
                    target="new_sent_email",
                    summary=f"Send invitation email: {event['title']}",
                    attributes={key: value for key, value in email.items() if key != "operation_id"},
                    reversible=False,
                    sensitivity=Sensitivity.INTERNAL,
                ),
                external_reference=email_id,
            ),
        ]
        audit_id = references.get("audit_id")
        if audit_id is not None:
            observed.append(
                ObservedEffect(
                    effect=Effect(
                        effect_id="unexpected_audit",
                        resource="workspace.audit",
                        kind=EffectKind.CREATE,
                        target="new_audit_entry",
                        summary="Create unexpected audit entry",
                        attributes={"message": self.audit_entries[audit_id]["message"]},
                        reversible=True,
                        sensitivity=Sensitivity.INTERNAL,
                    ),
                    external_reference=audit_id,
                )
            )
        return tuple(observed)

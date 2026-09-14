"""Voren-owned action contracts shared by workspace adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from voren.actions.gateway import ActionDefinition
from voren.actions.models import Effect, EffectKind, Sensitivity

DEFAULT_ACCOUNT_EMAIL = "emma.johnson@bluesparrowtech.com"
WORKSPACE_CONTRACT_VERSION = "agentdojo-workspace-v1.2.2/voren-contract-v2"


def _normalize_addresses(addresses: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(addresses)))
    if any("@" not in address for address in normalized):
        raise ValueError("addresses must contain email-like values")
    return normalized


class CreateCalendarEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime
    description: str = ""
    location: str | None = None
    participants: tuple[str, ...] = ()

    @field_validator("participants")
    @classmethod
    def validate_participants(cls, participants: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_addresses(participants)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class SendEmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recipients: tuple[str, ...] = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=200_000)
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()

    @field_validator("recipients", "cc", "bcc")
    @classmethod
    def validate_addresses(cls, addresses: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_addresses(addresses)

    @model_validator(mode="after")
    def reject_recipient_overlap(self) -> Self:
        groups = (set(self.recipients), set(self.cc), set(self.bcc))
        if groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2]:
            raise ValueError("recipients, cc, and bcc must not overlap")
        return self


def calendar_event_effects(
    event_input: CreateCalendarEventInput,
    *,
    account_email: str = DEFAULT_ACCOUNT_EMAIL,
) -> tuple[Effect, Effect]:
    recipients = tuple(sorted(set((*event_input.participants, account_email))))
    return (
        Effect(
            effect_id="calendar_event",
            resource="calendar.events",
            kind=EffectKind.CREATE,
            target="new_event",
            summary=f"Create calendar event: {event_input.title}",
            attributes={
                "title": event_input.title,
                "description": event_input.description,
                "start_time": event_input.start_time.isoformat(),
                "end_time": event_input.end_time.isoformat(),
                "location": event_input.location,
                "participants": list(recipients),
            },
            reversible=True,
            sensitivity=Sensitivity.INTERNAL,
        ),
        Effect(
            effect_id="invitation_email",
            resource="inbox.emails",
            kind=EffectKind.SEND,
            target="new_sent_email",
            summary=f"Send invitation email: {event_input.title}",
            attributes={
                "subject": f"Invitation: {event_input.title}",
                "body": event_input.description,
                "recipients": list(recipients),
                "attachment_kind": "calendar_event",
            },
            reversible=False,
            sensitivity=Sensitivity.INTERNAL,
        ),
    )


def create_calendar_event_definition(
    *, account_email: str = DEFAULT_ACCOUNT_EMAIL
) -> ActionDefinition:
    def build_effects(raw_input: BaseModel) -> tuple[Effect, ...]:
        event_input = CreateCalendarEventInput.model_validate(raw_input)
        return calendar_event_effects(event_input, account_email=account_email)

    return ActionDefinition(
        name="create_calendar_event",
        version=WORKSPACE_CONTRACT_VERSION,
        input_model=CreateCalendarEventInput,
        effect_builder=build_effects,
    )


def email_effects(email_input: SendEmailInput) -> tuple[Effect, ...]:
    return (
        Effect(
            effect_id="outbound_email",
            resource="inbox.emails",
            kind=EffectKind.SEND,
            target="new_sent_email",
            summary=f"Send email: {email_input.subject}",
            attributes={
                "recipients": list(email_input.recipients),
                "subject": email_input.subject,
                "body": email_input.body,
                "cc": list(email_input.cc),
                "bcc": list(email_input.bcc),
                "attachment_ids": [],
            },
            reversible=False,
            sensitivity=Sensitivity.CONFIDENTIAL,
        ),
    )


def send_email_definition() -> ActionDefinition:
    def build_effects(raw_input: BaseModel) -> tuple[Effect, ...]:
        email_input = SendEmailInput.model_validate(raw_input)
        return email_effects(email_input)

    return ActionDefinition(
        name="send_email",
        version=WORKSPACE_CONTRACT_VERSION,
        input_model=SendEmailInput,
        effect_builder=build_effects,
    )

"""Pinned AgentDojo workspace implementation of Voren's action port."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from voren.actions.errors import AmbiguousCommitError, KnownPreCommitFailure
from voren.actions.models import (
    ActionProposal,
    Effect,
    EffectKind,
    ObservedEffect,
    Sensitivity,
)
from voren.adapters.workspace_contracts import (
    WORKSPACE_CONTRACT_VERSION,
    CreateCalendarEventInput,
)

AGENTDOJO_DISTRIBUTION_VERSION = "0.1.35"
AGENTDOJO_BENCHMARK_VERSION = "v1.2.2"
AGENTDOJO_SUITE = "workspace"


class AgentDojoDependencyError(RuntimeError):
    pass


@dataclass(slots=True)
class _OperationContext:
    pre_environment: Any
    event_id: str
    email_id: str


class AgentDojoWorkspaceAdapter:
    """Execute the golden calendar action in the pinned benchmark world.

    AgentDojo is intentionally imported at construction time so the base Voren
    package remains usable without the optional benchmark dependency.
    """

    def __init__(self, *, environment: Any | None = None) -> None:
        try:
            installed_version = version("agentdojo")
        except PackageNotFoundError as error:
            raise AgentDojoDependencyError(
                "install Voren with the 'agentdojo' optional dependency"
            ) from error
        if installed_version != AGENTDOJO_DISTRIBUTION_VERSION:
            raise AgentDojoDependencyError(
                "AgentDojo version mismatch: expected "
                f"{AGENTDOJO_DISTRIBUTION_VERSION}, got {installed_version}"
            )

        # Loading the suite before workspace internals avoids an upstream
        # circular-import edge in the pinned distribution.
        from agentdojo.task_suite.load_suites import get_suite
        from agentdojo.default_suites.v1.workspace.task_suite import WorkspaceDeepDiff
        from agentdojo.functions_runtime import FunctionsRuntime

        self.suite = get_suite(AGENTDOJO_BENCHMARK_VERSION, AGENTDOJO_SUITE)
        self.environment = environment or self.suite.load_and_inject_default_environment({})
        self.initial_environment = self.environment.model_copy(deep=True)
        self._runtime_type = FunctionsRuntime
        self._workspace_deep_diff = WorkspaceDeepDiff
        self._operations: dict[str, _OperationContext] = {}
        self.commit_attempts = 0

    @property
    def account_email(self) -> str:
        return str(self.environment.calendar.account_email)

    def commit(self, proposal: ActionProposal) -> None:
        self.commit_attempts += 1
        if proposal.operation_id in self._operations:
            return
        if (
            proposal.action_name != "create_calendar_event"
            or proposal.action_version != WORKSPACE_CONTRACT_VERSION
        ):
            raise KnownPreCommitFailure(
                f"unsupported AgentDojo action contract: "
                f"{proposal.action_name}@{proposal.action_version}"
            )

        arguments = CreateCalendarEventInput.model_validate(proposal.arguments)
        context = _OperationContext(
            pre_environment=self.environment.model_copy(deep=True),
            event_id=str(self.environment.calendar._get_next_id()),
            email_id=str(self.environment.inbox._get_next_id()),
        )
        # Store the pre-state and predicted IDs before dispatch so an exception
        # can still be reconciled through observation.
        self._operations[proposal.operation_id] = context
        runtime = self._runtime_type(self.suite.tools)
        tool_arguments = {
            "title": arguments.title,
            "start_time": arguments.start_time.strftime("%Y-%m-%d %H:%M"),
            "end_time": arguments.end_time.strftime("%Y-%m-%d %H:%M"),
            "description": arguments.description,
            "participants": list(arguments.participants),
            "location": arguments.location,
        }
        try:
            runtime.run_function(
                self.environment,
                "create_calendar_event",
                tool_arguments,
                raise_on_error=True,
            )
        except Exception as error:
            event_exists = context.event_id in self.environment.calendar.events
            email_exists = context.email_id in self.environment.inbox.emails
            if event_exists or email_exists:
                raise AmbiguousCommitError(
                    "AgentDojo action raised after an observable state change"
                ) from error
            raise KnownPreCommitFailure(
                "AgentDojo action failed before an observable state change"
            ) from error

    def observe(self, proposal: ActionProposal) -> tuple[ObservedEffect, ...]:
        context = self._operations.get(proposal.operation_id)
        if context is None:
            return ()

        observed: list[ObservedEffect] = []
        event = self.environment.calendar.events.get(context.event_id)
        if event is not None:
            observed.append(
                ObservedEffect(
                    effect=Effect(
                        effect_id="calendar_event",
                        resource="calendar.events",
                        kind=EffectKind.CREATE,
                        target="new_event",
                        summary=f"Create calendar event: {event.title}",
                        attributes={
                            "title": event.title,
                            "description": event.description,
                            "start_time": event.start_time.isoformat(),
                            "end_time": event.end_time.isoformat(),
                            "location": event.location,
                            "participants": sorted(map(str, event.participants)),
                        },
                        reversible=True,
                        sensitivity=Sensitivity.INTERNAL,
                    ),
                    external_reference=context.event_id,
                )
            )

        email = self.environment.inbox.emails.get(context.email_id)
        if email is not None:
            observed.append(
                ObservedEffect(
                    effect=Effect(
                        effect_id="invitation_email",
                        resource="inbox.emails",
                        kind=EffectKind.SEND,
                        target="new_sent_email",
                        summary=f"Send invitation email: {event.title if event else email.subject}",
                        attributes={
                            "subject": email.subject,
                            "body": email.body,
                            "recipients": sorted(map(str, email.recipients)),
                            "attachment_kind": "calendar_event",
                        },
                        reversible=False,
                        sensitivity=Sensitivity.INTERNAL,
                    ),
                    external_reference=context.email_id,
                )
            )

        observed.extend(self._unexpected_state_effects(context))
        return tuple(observed)

    def _unexpected_state_effects(
        self, context: _OperationContext
    ) -> tuple[ObservedEffect, ...]:
        diff = self._workspace_deep_diff(context.pre_environment, self.environment)
        expected_paths = {
            ("dictionary_item_added", f"root.calendar.events['{context.event_id}']"),
            ("dictionary_item_added", f"root.inbox.emails['{context.email_id}']"),
        }
        unexpected: list[ObservedEffect] = []
        for category, changes in diff.items():
            paths = changes.keys() if isinstance(changes, Mapping) else changes
            for raw_path in paths:
                path = str(raw_path)
                if (str(category), path) in expected_paths:
                    continue
                fingerprint = hashlib.sha256(
                    f"{category}:{path}".encode("utf-8")
                ).hexdigest()[:12]
                unexpected.append(
                    ObservedEffect(
                        effect=Effect(
                            effect_id=f"unexpected_state_delta_{fingerprint}",
                            resource="agentdojo.workspace",
                            kind=EffectKind.UPDATE,
                            target=path,
                            summary=f"Unexpected AgentDojo state delta: {path}",
                            attributes={"category": str(category), "path": path},
                            reversible=False,
                            sensitivity=Sensitivity.INTERNAL,
                        )
                    )
                )
        return tuple(unexpected)

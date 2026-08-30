"""Runtime ports implemented by world-specific adapters."""

from __future__ import annotations

from typing import Protocol

from voren.actions.models import ActionProposal, ObservedEffect


class ActionAdapter(Protocol):
    def commit(self, proposal: ActionProposal) -> None:
        """Attempt the external mutation exactly once for ``operation_id``."""

    def observe(self, proposal: ActionProposal) -> tuple[ObservedEffect, ...]:
        """Read the world and return effects attributable to this operation."""

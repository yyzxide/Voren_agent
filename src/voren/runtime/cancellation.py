"""Thread-safe cooperative cancellation shared by runtime and providers."""

from __future__ import annotations

from threading import Event, Lock

from voren.runtime.models import CancellationReason, ModelUsage


class CancellationToken:
    """A small provider-neutral signal suitable for a future web run registry."""

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._reason: CancellationReason | None = None

    def cancel(
        self, reason: CancellationReason = CancellationReason.OPERATOR
    ) -> bool:
        """Request cancellation once and return whether this call won the race."""

        with self._lock:
            if self._event.is_set():
                return False
            self._reason = reason
            self._event.set()
            return True

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> CancellationReason | None:
        return self._reason

    def wait(self, timeout_seconds: float) -> bool:
        return self._event.wait(timeout_seconds)


class ModelRequestCancelled(RuntimeError):
    """A model request stopped locally, with optional provider confirmation."""

    def __init__(
        self,
        *,
        reason: CancellationReason,
        provider_confirmed: bool | None,
        detail_code: str | None = None,
        usage: ModelUsage | None = None,
    ) -> None:
        self.reason = reason
        self.provider_confirmed = provider_confirmed
        self.detail_code = detail_code
        self.usage = usage
        super().__init__(f"model request cancelled: {reason.value}")

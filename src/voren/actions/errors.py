"""Failures with distinct commit-safety semantics."""


class ActionGatewayError(RuntimeError):
    """Base class for deterministic action-protocol failures."""


class UnknownActionError(ActionGatewayError):
    pass


class InvalidProposalError(ActionGatewayError):
    pass


class ApprovalMismatchError(ActionGatewayError):
    pass


class ApprovalRejectedError(ActionGatewayError):
    pass


class StaleApprovalError(ActionGatewayError):
    pass


class InvalidOperationStateError(ActionGatewayError):
    pass


class KnownPreCommitFailure(ActionGatewayError):
    """The adapter confirms no external mutation was attempted."""


class AmbiguousCommitError(ActionGatewayError):
    """The adapter cannot tell whether the external mutation committed."""

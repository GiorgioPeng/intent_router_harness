class RouterError(RuntimeError):
    """Base router error."""


class SessionOwnershipError(RouterError):
    """Raised when one sessionId is reused by another customer."""


class PlannerRejectedError(RouterError):
    """Raised when planner output fails deterministic validation."""


class PlannerModelError(RouterError):
    """Raised when the planner model call fails."""

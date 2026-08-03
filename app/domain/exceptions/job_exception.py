from app.domain.exceptions.errors import ValidationError


class JobException(ValidationError):
    """Base exception for invalid job requests."""

class UnsupportedJobTypeError(JobException):
    """Raised when no application handler exists for a persisted job type."""

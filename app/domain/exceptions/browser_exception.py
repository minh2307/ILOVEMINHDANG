from app.domain.exceptions.errors import BrowserAutomationError, ProfileLockError


class BrowserException(BrowserAutomationError):
    """Base exception for browser operations."""

class BrowserConnectionError(BrowserException):
    """Raised when the shared browser endpoint cannot be reached."""

BrowserLockError = ProfileLockError

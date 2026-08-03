from app.domain.exceptions.errors import FacebookPublicationError


class FacebookException(FacebookPublicationError):
    """Base exception for Facebook operations."""

class FacebookLoginRequiredError(FacebookException):
    """Raised when an operator must complete Facebook authentication."""

class FacebookElementNotFoundError(FacebookException):
    """Raised when the verified Facebook UI state cannot be located."""

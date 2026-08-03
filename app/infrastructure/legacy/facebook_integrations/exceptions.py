"""Legacy error names for inactive click-only integrations."""


class FacebookIntegrationError(RuntimeError):
    pass


class FacebookLoginRequired(FacebookIntegrationError):
    pass


class FacebookElementNotFound(FacebookIntegrationError):
    pass

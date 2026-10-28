"""Exceptions about user accounts."""


class UserNotFoundError(Exception):
    """Raised when no active user has the given id."""

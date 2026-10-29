"""Database availability errors.

asyncpg's connect handshake raises the builtin ``TimeoutError``. AI deadlines
raise that same class. This type is the distinction: it is raised only at the
database connect boundary.
"""


class DatabaseUnavailableError(Exception):
    """The database could not open a connection. The caller may retry.

    The driver error is the ``__cause__``. This message stays fixed so a
    connection string or handshake detail cannot leak into client output.
    """

    def __init__(self) -> None:
        super().__init__("Database connection failed")

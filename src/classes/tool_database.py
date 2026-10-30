import mongoengine


def connect_database(settings) -> None:
    """
        The connection to MongoDB that every entity module registers when it is imported, always the same one.

        Every wait has an end. Without one, pymongo waits forever on a connection that died without closing: on
        midgard, a query to Atlas left the management API without an answer for minutes, its health check included,
        because the request that waited held the event loop. Now it fails after `db_timeout_ms`, and whoever asked
        tries again on a new connection. Connections that sit idle longer than `db_max_idle_ms` are closed before the
        network between midgard and Atlas drops them silently.
    """
    # mongoengine.connect is looked up when called, not imported: the tests swap it for an in-memory database.
    mongoengine.connect(
        db=settings.db_name,
        username=settings.db_username,
        password=settings.db_password,
        host=settings.db_host,
        socketTimeoutMS=settings.db_timeout_ms,
        connectTimeoutMS=settings.db_timeout_ms,
        serverSelectionTimeoutMS=settings.db_timeout_ms,
        maxIdleTimeMS=settings.db_max_idle_ms,
    )

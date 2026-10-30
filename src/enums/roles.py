from enum import Enum


class Role(Enum):
    CLIENT = "client"      # Owns chatbots and reaches only its own data
    OPERATOR = "operator"  # Runs the platform and registers clients
    SERVICE = "service"    # The response engine, for one client: reads what it needs to answer, writes nothing


class PersonRole(Enum):
    """
        What a person of a client may do there (ADR-0029, the role × action matrix): a fixed catalogue in the v1 (D2).
        A machine credential of a client may what ADMIN may (D3).
    """
    ADMIN = "cliente-admin"        # Everything of its client
    ANALYST = "cliente-analista"   # Reads, and sends approved templates: configures nothing


class PersonStatus(Enum):
    PENDING = "pending"    # Invited, and never in yet
    ACTIVE = "active"
    BLOCKED = "blocked"    # Gets no token, and may be let in again
    REMOVED = "removed"    # Gets no token, ever

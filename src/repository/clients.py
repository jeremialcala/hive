# -*- coding: utf-8 -*-
"""
    The registry of clients: who they are and which public keys they sign their tokens with. It is the operator's
    side of the data layer, and what authentication looks a signer up in.
"""
from uuid import UUID, uuid4

from mongoengine.errors import NotUniqueError

from classes import Client
from classes.tool_clock import utc_now
from enums import Role, Status, Worker

from . import data_keys, erasures
from .errors import Conflict, NotFound
from .tenant import TenantRepository, as_uuid
from .tokens import forget_tokens

OPERATOR_NAME = "operator"
# Two at most: the one in use and the one that replaces it, so a client rotates without a gap.
MAX_SIGNING_KEYS = 2


def operator_client() -> Client:
    """
        The operator's own client. It owns the chatbots that existed before clients did, and it is who registers
        the others. It is created on first use; the name is unique, so several workers starting at once end up
        with the same one.
    """
    client = Client.objects(name=OPERATOR_NAME).first()
    if client is None:
        now = utc_now()
        client = Client.objects(name=OPERATOR_NAME).upsert_one(
            set_on_insert__uuid=uuid4(),
            set_on_insert__role=Role.OPERATOR.value,
            set_on_insert__createdAt=now,
            set_on_insert__status=Status.ACT.value,
            set_on_insert__statusDate=now
        )
    return client


def register_client(name: str, signing_key: dict) -> Client:
    """`signing_key` must already be a validated public key: see auth.client_keys."""
    client = Client(name=name, role=Role.CLIENT.value, status=Status.ACT.value, signingKeys=[signing_key])
    try:
        client.save()
    except NotUniqueError:
        raise Conflict("name taken") from None
    return client


def get_client(client_id) -> Client:
    client = Client.objects(uuid=as_uuid(client_id)).first()
    if client is None:
        raise NotFound(client_id)
    return client


def list_clients() -> list:
    return list(Client.objects.order_by("createdAt"))


def _add_key(client_id, keys: str, key: dict, worker: Worker | None = None) -> Client:
    """
        Adds a public key to one of the two lists of a client. A key is in one list at most: which list it is in
        is what says whether whoever signs with it is the client or the response engine. A key of the engine
        also says which of its workers signs with it, and the limit is per worker.

        The checks go in the update itself, so that two changes at once cannot undo each other: saving the list
        as it was read would bring back a key that was revoked in between.
    """
    condition = {"signingKeys.kid": {"$ne": key["kid"]}, "serviceKeys.kid": {"$ne": key["kid"]}}
    if worker is None:
        condition[f"{keys}.{MAX_SIGNING_KEYS - 1}"] = {"$exists": False}
    else:
        key = {**key, "worker": worker.value}
        # A key from before keys had a worker has no such field: $ifNull turns it into a null that matches none.
        same_worker = {"$filter": {"input": {"$ifNull": [f"${keys}", []]},
                                   "cond": {"$eq": [{"$ifNull": ["$$this.worker", None]}, worker.value]}}}
        condition["$expr"] = {"$lt": [{"$size": same_worker}, MAX_SIGNING_KEYS]}
    added = Client.objects(uuid=as_uuid(client_id), __raw__=condition).update_one(__raw__={"$push": {keys: key}})
    client = get_client(client_id)
    if not added:
        if any(known.get("kid") == key["kid"] for known in [*client.signingKeys, *client.serviceKeys]):
            raise Conflict("key already registered")
        raise Conflict("too many keys")
    return client


def _remove_key(client_id, keys: str, kid: str, keep_one: bool) -> Client:
    condition = {f"{keys}.kid": kid}
    if keep_one:
        # Another key has to remain after this one goes.
        condition[f"{keys}.1"] = {"$exists": True}
    removed = Client.objects(uuid=as_uuid(client_id), __raw__=condition).update_one(
        __raw__={"$pull": {keys: {"kid": kid}}})
    client = get_client(client_id)
    if not removed:
        if not any(known.get("kid") == kid for known in getattr(client, keys)):
            raise NotFound(kid)
        raise Conflict("last key")
    return client


def add_signing_key(client_id, signing_key: dict) -> Client:
    return _add_key(client_id, "signingKeys", signing_key)


def remove_signing_key(client_id, kid: str, keep_one: bool = False) -> Client:
    """
        Revoking a key is deleting it. `keep_one` is for a client acting on itself: without it, it could leave
        itself with no way back in.
    """
    return _remove_key(client_id, "signingKeys", kid, keep_one)


def add_service_key(client_id, service_key: dict, worker: Worker) -> Client:
    """
        Lets a worker of the response engine act for a client: it registers the public key that worker signs
        with for it. Two at most per worker, as with the client's own, so that each worker rotates without a gap.
    """
    return _add_key(client_id, "serviceKeys", service_key, worker)


def remove_service_key(client_id, kid: str) -> Client:
    """Revoking the engine's key for a client is deleting it: it stops reaching that client's chatbots at once."""
    return _remove_key(client_id, "serviceKeys", kid, keep_one=False)


def delete_client(client_id) -> dict:
    """
        Deletes a client with everything stored for it, and says how many of each. The order is what makes it
        safe to interrupt and to call again: first nobody gets in as the client, then its data goes, and the
        client itself goes last, taking its wrapped data key with it.
    """
    client = get_client(client_id)
    if client.role == Role.OPERATOR.value:
        # Without the operator nobody registers a client or lets one back in.
        raise Conflict("operator")
    erasures.record(erasures.CLIENT, client.uuid)
    return erase_client(client.uuid)


def erase_client(client_id: UUID) -> dict:
    """
        Deletes a client without writing it down in the register of erasures: for delete_client, which writes
        it down first, and for making again the deletions the register holds.
    """
    Client.objects(uuid=client_id).update_one(set__signingKeys=[], set__serviceKeys=[],
                                              set__status=Status.DIS.value, set__statusDate=utc_now())
    deleted = TenantRepository(client_id).erase_all()
    forget_tokens(client_id)
    Client.objects(uuid=client_id).delete()
    # The open key of this process. Any other process drops its own within data_keys.OPEN_FOR seconds.
    data_keys.forget(client_id)
    return deleted


def find_signer(client_id: UUID, kid: str) -> tuple | None:
    """
        The active client, the public key registered for it under that kid, what whoever signs with it is (the
        client itself, with the role of its entry, or the response engine acting for it) and, for the engine,
        which of its workers. None if there is no such key.

        The worker is None for the keys of the engine registered before keys had one (ADR-0016).
    """
    client = Client.objects(uuid=client_id, status=Status.ACT.value).first()
    if client is None:
        return None
    for keys, role in ((client.signingKeys, Role(client.role)), (client.serviceKeys, Role.SERVICE)):
        for entry in keys:
            if entry.get("kid") == kid:
                key = dict(entry)
                worker = key.pop("worker", None)
                return client, key, role, Worker(worker) if worker else None
    return None

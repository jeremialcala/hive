# -*- coding: utf-8 -*-
"""
    The state of a chatbot, as Meta shows it (HIG-74, HIG-75; decisions of Jeremi of 2026-10-30):

        REG ──Meta verifies its webhook, or sends it a signed message──▶ ACT
        REG, ACT ──Meta turns its token or its permissions away──▶ ERR
        ERR ──new credentials, or Meta takes a send or a call on templates──▶ ACT

    Only what Meta says moves it: a bad signature anybody can send, and it would let a stranger put a chatbot in
    ERR. A message signed with the chatbot's App Secret activates it too: the chatbots that were live before this were
    verified long ago, and nobody wrote it down. Nothing here takes a chatbot out of LOK or DIS, and a new verification
    does not take one out of ERR: it proves the webhook, not the token.

    Each move is one conditional update (TenantRepository.move_bot) and a WARNING, which production keeps.
"""
import logging
from uuid import UUID

from classes import Settings
from enums import Status
from repository import TenantRepository

__all__ = ["TOKEN_OR_PERMISSION", "bot_verified", "bot_refused", "bot_accepted"]

settings = Settings()
log = logging.getLogger(settings.environment)

# Meta's codes for a token that is not good (0, 190) and for one without the permission (10, 131005)
# (developers.facebook.com, «Error codes», read on 2026-10-30).
TOKEN_OR_PERMISSION = frozenset({0, 190, 10, 131005})


def _move(repo: TenantRepository, bot_uuid: UUID, start: tuple[Status, ...], to: Status, why: str) -> bool:
    moved = repo.move_bot(bot_uuid, start, to)
    if moved:
        log.warning(f"Chatbot {bot_uuid} of client {repo.client_id} is now {to.name}: {why}")
    return moved


def bot_verified(repo: TenantRepository, bot, why: str) -> bool:
    """Meta reached the chatbot's webhook: a REG chatbot is live. Costs nothing for one that is not REG."""
    if bot.status != Status.REG.value:
        return False
    return _move(repo, bot.uuid, (Status.REG,), Status.ACT, why)


def bot_refused(repo: TenantRepository, bot_uuid: UUID, code: int | None) -> bool:
    """Meta turned the chatbot's token or its permissions away, with `code`: it is in ERR. Any other code, nothing."""
    if code not in TOKEN_OR_PERMISSION:
        return False
    return _move(repo, bot_uuid, (Status.REG, Status.ACT), Status.ERR, f"Meta turned its token away, {code}")


def bot_accepted(repo: TenantRepository, bot_uuid: UUID, why: str, status: int | None = None) -> bool:
    """
        Meta took what was sent with the chatbot's token, or the client loaded new credentials: one in ERR is live again.
        With the chatbot's `status` at hand, one that is not ERR costs nothing.
    """
    if status is not None and status != Status.ERR.value:
        return False
    return _move(repo, bot_uuid, (Status.ERR,), Status.ACT, why)

# -*- coding: utf-8 -*-
"""
    Where the API call of an intent may go (R6, RS16, T11). The worker of the engine that makes these calls does
    it with the network of the platform, so a destination a client writes must never be the platform itself: a
    confused deputy. The rule is checked when an intent is saved, and again before its actions are handed to the
    engine as tools; the worker has to check what the name resolves to when it calls (docs/03-implementation/
    destinos-de-los-intents.md).

    A destination is valid when:

    * the scheme is https;
    * it carries no user or password: the credentials of a call are its authRef;
    * the host is written out: no {placeholder} in the host or the port, so the model cannot choose where to call;
    * the host is a public address, or a name with at least two labels whose last one is not all digits (that is
      how 2130706433 or 0x7f.1 would reach 127.0.0.1) and that is not under a name kept for local networks;
    * the host is ASCII: an internationalised name goes in its xn-- form.

    The messages never repeat the destination: what a client sends is not written to an answer or a log.
"""
import ipaddress
import re
from urllib.parse import urlsplit

MAX_LENGTH = 2048
# Names that only mean something inside a network, never on the internet (RFC 6761, RFC 6762, RFC 8375, and the
# ones in common use). A single label, like the hosts of the platform's networks (andvari, hermod, bifrost), is
# already refused by needing two.
LOCAL_SUFFIXES = ("localhost", "local", "localdomain", "internal", "intranet", "lan", "home", "home.arpa", "corp",
                  "private")
LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def _check_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    if not address.is_global or address.is_multicast:
        raise ValueError("The destination must be a public address")


def _check_name(host: str) -> None:
    labels = host.split(".")
    if len(labels) < 2:
        raise ValueError("The destination must be a full domain name, not a host of a local network")
    if not all(LABEL.match(label) for label in labels):
        raise ValueError("The destination is not a valid domain name")
    if labels[-1].isdigit():
        raise ValueError("The destination must be a public address")
    if any(host == suffix or host.endswith("." + suffix) for suffix in LOCAL_SUFFIXES):
        raise ValueError("The destination is a name of a local network")


def check_destination(url: str) -> None:
    """Raises ValueError, with a message that does not repeat the url, if the url is not a valid destination."""
    if not isinstance(url, str) or not url or len(url) > MAX_LENGTH:
        raise ValueError(f"The destination must be a URL of {MAX_LENGTH} characters at most")
    if not url.isascii() or any(character.isspace() or ord(character) < 32 for character in url):
        raise ValueError("The destination must be ASCII without spaces: an international name goes as xn--")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise ValueError("The destination must use https")
    if "@" in parts.netloc:
        raise ValueError("The destination cannot carry a user or a password: use authRef")
    if "{" in parts.netloc or "}" in parts.netloc:
        raise ValueError("The host and port of the destination must be written out, not a placeholder")
    try:
        parts.port
    except ValueError:
        raise ValueError("The port of the destination is not valid") from None
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise ValueError("The destination has no host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        _check_name(host)
    else:
        _check_address(address)


def is_valid_destination(url: str) -> bool:
    try:
        check_destination(url)
    except ValueError:
        return False
    return True

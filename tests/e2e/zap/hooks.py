# -*- coding: utf-8 -*-
"""
    The hooks of the portal's scan (HIG-90): zap-full-scan.py loads them with --hook, inside ZAP's container.

    The PWA draws its pages with JavaScript, and ZAP's spider does not see the portal's own routes in it: the login
    and its callback, the session, the logout and the API that the PWA calls. They are visited once here, without a
    session, so that the spider and the active scan start from them too.
"""
import logging

CLIENT = "00000000-0000-4000-8000-000000000001"
SEEDS = ["/", "/index.html", f"/auth/login?client={CLIENT}&next=/", "/auth/callback?state=e2e&code=e2e",
         "/auth/session", "/api/bots", f"/api/chat/{CLIENT}/templates", "/health"]


def zap_started(zap, target):
    base = target.rstrip("/")
    for path in SEEDS:
        logging.info("Seed: %s", path)
        zap.urlopen(base + path)
    # The logout is a POST, with the PWA's own header: without it the portal answers 403 before anything else.
    host = base.split("//", 1)[1]
    for headers in ("", "X-Hive-Portal: 1\r\n"):
        zap.core.send_request(f"POST {base}/auth/logout HTTP/1.1\r\nHost: {host}\r\n{headers}Content-Length: 0\r\n\r\n")
        zap.core.send_request(f"POST {base}/api/bot HTTP/1.1\r\nHost: {host}\r\n{headers}"
                              f"Content-Type: application/json\r\nContent-Length: 2\r\n\r\n{{}}")
    return zap, target

"""The rules every request to Studio must pass (docs/adr/0004).

Studio listens on 127.0.0.1, but any website open in the same browser can still send requests to
it. So a request that changes something must prove it comes from a Studio page: it carries the
per-process token in a header, and its Origin and Sec-Fetch-Site headers, when present, point to
Studio itself. Every response gets headers that forbid inline scripts, framing and caching.
"""

import hmac
from collections.abc import Mapping

ALLOWED_HOSTS = ("127.0.0.1", "localhost")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
TOKEN_HEADER = "x-csrf-token"

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
    )
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def allowed_origins(port: int) -> frozenset[str]:
    return frozenset(f"http://{host}:{port}" for host in ALLOWED_HOSTS)


def request_problem(
    method: str, headers: Mapping[str, str], token: str, origins: frozenset[str]
) -> str | None:
    """Why a request must be refused, or None when it may go on.

    Reading pages is always allowed; anything else needs the token, and must not come from
    another site.
    """
    if method.upper() in SAFE_METHODS:
        return None
    sent = headers.get(TOKEN_HEADER, "")
    if not sent or not hmac.compare_digest(sent.encode(), token.encode()):
        return "the request token is missing or wrong"
    origin = headers.get("origin")
    if origin is not None and origin not in origins:
        return "the request comes from another site"
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in ("same-origin", "none"):
        return "the request comes from another site"
    return None

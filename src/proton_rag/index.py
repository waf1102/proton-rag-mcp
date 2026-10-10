"""Backend-neutral index identity, transport validation and safe errors."""

import re
from urllib.parse import urlsplit

KEY = re.compile(r"^proton-mail-[a-f0-9]{64}$")


def local_url(url):
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Index must use host loopback HTTP")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Invalid index URL")
    return url.rstrip("/")


class PendingIndexError(RuntimeError):
    pass


class IndexFailure(RuntimeError):
    def __init__(self, code, operation, http_status=None):
        self.diagnostic = {"code": code, "operation": operation}
        if http_status is not None:
            self.diagnostic["http_status"] = http_status
        super().__init__(f"Local index failure: {code} ({operation})")

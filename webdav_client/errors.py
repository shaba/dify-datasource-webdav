"""WebDAV client error type and credential-redaction helper.

``redact_credentials`` strips ``user:pass@`` userinfo from any URL embedded in
an error/diagnostic string before it can reach the LLM or end user. requests'
``RequestException`` text frequently includes the full request URL, which flows
into ``WebDAVError(f"... {exc}")`` and then into the provider validation
message; with a ``base_url`` that embeds credentials this would otherwise leak
the password.
"""

from __future__ import annotations

import re


class WebDAVError(Exception):
    """Raised on WebDAV transport or protocol errors."""


# Matches scheme://user:pass@host userinfo; strip it before any error string
# reaches the LLM/end-user, in case base_url embeds credentials.
_USERINFO_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)[^/@\s]*@")


def redact_credentials(text: object) -> str:
    """Strip user:pass@ userinfo from any URL embedded in a message."""
    return _USERINFO_RE.sub(r"\g<scheme>", str(text))

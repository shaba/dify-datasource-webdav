"""Thin, dependency-light WebDAV client (PROPFIND listing + GET download).

Independent of dify_plugin so it can be unit-tested in isolation. Uses only
``requests`` for HTTP and ``lxml`` for parsing PROPFIND multistatus XML.
"""

from .client import WebDAVClient
from .errors import WebDAVError, redact_credentials
from .parser import WebDAVResource, parse_propfind

__all__ = [
    "WebDAVClient",
    "WebDAVError",
    "WebDAVResource",
    "parse_propfind",
    "redact_credentials",
]

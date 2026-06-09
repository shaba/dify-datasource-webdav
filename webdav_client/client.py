"""Minimal WebDAV client: PROPFIND listing, GET download, base check.

Works against any RFC 4918 WebDAV server (Nextcloud, ownCloud, Apache mod_dav,
sabre/dav, ...). Uses HTTP Basic auth.
"""

from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit, urlunsplit

import requests

from .errors import WebDAVError, redact_credentials
from .parser import WebDAVResource, parse_propfind

PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:resourcetype/>"
    "<d:getcontentlength/>"
    "<d:getcontenttype/>"
    "</d:prop></d:propfind>"
)

__all__ = ["WebDAVClient", "WebDAVError", "redact_credentials"]


# Upper bound on a single download buffered into memory. The plugin runner's
# memory budget (manifest resource.memory) is 256 MiB; cap a single file well
# under that so one oversized share entry cannot OOM the runner.
DEFAULT_MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


class WebDAVClient:
    """A thin WebDAV client bound to a base URL and Basic-auth credentials.

    ``base_url`` is the WebDAV endpoint, e.g.
    ``https://example.com/remote.php/dav/files/alice/``.
    """

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        *,
        timeout: int = 30,
        session: requests.Session | None = None,
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    ) -> None:
        if not base_url:
            raise WebDAVError("base_url is required")
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.max_download_bytes = max_download_bytes
        self._session = session or requests.Session()
        if username or password:
            self._session.auth = (username, password)

        split = urlsplit(self.base_url)
        # Only HTTP(S) WebDAV endpoints are supported; reject other schemes
        # (file://, ftp://, ...) so a misconfigured base_url cannot redirect
        # requests away from the intended transport.
        if split.scheme not in ("http", "https"):
            raise WebDAVError("base_url must use http or https scheme")
        if not split.netloc:
            raise WebDAVError("base_url must include a host")
        self._scheme = split.scheme
        self._netloc = split.netloc
        # URL-decoded path of the base endpoint, used to resolve relative paths.
        self.base_path = split.path or "/"

    # -- URL helpers ---------------------------------------------------------

    def _url_for_path(self, path: str) -> str:
        """Build an absolute URL from a URL-decoded server path.

        Paths reaching this method are always URL-decoded (PROPFIND hrefs are
        decoded by the parser, and base/relative paths are plain strings), so we
        percent-encode unconditionally. Branching on ``"%" in path`` would mis-
        handle a filename that legitimately contains a literal percent sign
        (e.g. ``50%.txt``), leaving an invalid, unencoded ``%`` in the URL.
        """
        if not path:
            path = self.base_path
        path = quote(path, safe="/")
        return urlunsplit((self._scheme, self._netloc, path, "", ""))

    def _resolve(self, rel: str | None) -> str:
        """Resolve a folder path relative to the base endpoint.

        ``rel`` is a path under the base (e.g. ``"docs/"``) or an absolute
        server path, or empty/None for the base itself.
        """
        rel = (rel or "").strip()
        if not rel:
            return self.base_url
        if rel.startswith("/"):
            return self._url_for_path(rel)
        return self._url_for_path(self.base_path.rstrip("/") + "/" + rel)

    # -- Operations ----------------------------------------------------------

    def check(self) -> None:
        """Validate connectivity and credentials with a Depth: 0 PROPFIND."""
        try:
            resp = self._session.request(
                "PROPFIND",
                self.base_url,
                data=PROPFIND_BODY,
                headers={"Depth": "0", "Content-Type": "application/xml"},
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise WebDAVError(
                f"WebDAV connection failed: {redact_credentials(exc)}"
            ) from exc
        if resp.status_code in (401, 403):
            raise WebDAVError("WebDAV authentication failed (check username/password)")
        if resp.status_code == 404:
            raise WebDAVError("WebDAV base_url not found (404)")
        if resp.status_code not in (207, 200):
            raise WebDAVError(
                f"Unexpected WebDAV response {resp.status_code} for base_url"
            )

    def list_dir(self, path: str | None = None) -> list[WebDAVResource]:
        """List children of a collection (Depth: 1 PROPFIND)."""
        url = self._resolve(path)
        if not url.endswith("/"):
            url += "/"
        try:
            resp = self._session.request(
                "PROPFIND",
                url,
                data=PROPFIND_BODY,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise WebDAVError(
                f"WebDAV PROPFIND failed: {redact_credentials(exc)}"
            ) from exc
        if resp.status_code not in (207, 200):
            raise WebDAVError(
                f"WebDAV PROPFIND returned {resp.status_code} "
                f"for {redact_credentials(path or '/')}"
            )
        base_path = urlsplit(url).path
        return parse_propfind(resp.content, unquote(base_path))

    def download(self, path: str) -> tuple[bytes, str]:
        """GET a file by its server path; return ``(content, content_type)``.

        A trailing slash marks a collection (folder); GETting one yields an HTML
        index or a 405 rather than file bytes, so reject it up front with a clear
        error instead of ingesting bogus content.
        """
        if path.endswith("/"):
            raise WebDAVError(
                f"Cannot download a folder: {redact_credentials(path)}"
            )
        url = self._url_for_path(path) if path.startswith("/") else self._resolve(path)
        try:
            resp = self._session.get(
                url, timeout=self.timeout, allow_redirects=False
            )
        except requests.RequestException as exc:
            raise WebDAVError(
                f"WebDAV GET failed: {redact_credentials(exc)}"
            ) from exc
        if resp.status_code != 200:
            raise WebDAVError(
                f"WebDAV GET returned {resp.status_code} "
                f"for {redact_credentials(path)}"
            )
        # create_blob_message requires the full bytes (the SDK has no streaming
        # message API), so guard against buffering an oversized file into memory
        # and OOM-ing the plugin runner. The limit is the manifest memory budget.
        declared = resp.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > self.max_download_bytes:
                    raise WebDAVError(
                        f"File exceeds maximum download size "
                        f"({self.max_download_bytes} bytes): "
                        f"{redact_credentials(path)}"
                    )
            except ValueError:
                pass
        content = resp.content
        if len(content) > self.max_download_bytes:
            raise WebDAVError(
                f"File exceeds maximum download size "
                f"({self.max_download_bytes} bytes): "
                f"{redact_credentials(path)}"
            )
        content_type = resp.headers.get("Content-Type", "application/octet-stream")
        return content, content_type

import pytest
import requests

from webdav_client import WebDAVClient, WebDAVError, redact_credentials


class FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


class FakeSession:
    """Records the last request and returns canned responses by method."""

    def __init__(self, responses):
        self.responses = responses
        self.auth = None
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses[method]

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses["GET"]


BASE = "https://example.com/remote.php/dav/files/alice/"


def test_check_ok(propfind_listing):
    session = FakeSession({"PROPFIND": FakeResponse(207, propfind_listing)})
    client = WebDAVClient(BASE, "alice", "pw", session=session)
    client.check()
    method, url, kwargs = session.calls[0]
    assert method == "PROPFIND"
    assert kwargs["headers"]["Depth"] == "0"
    assert session.auth == ("alice", "pw")


@pytest.mark.parametrize("code", [401, 403])
def test_check_auth_failure(code):
    session = FakeSession({"PROPFIND": FakeResponse(code)})
    client = WebDAVClient(BASE, "alice", "bad", session=session)
    with pytest.raises(WebDAVError, match="authentication"):
        client.check()


def test_check_404():
    session = FakeSession({"PROPFIND": FakeResponse(404)})
    client = WebDAVClient(BASE, session=session)
    with pytest.raises(WebDAVError, match="not found"):
        client.check()


def test_list_dir_root(propfind_listing):
    session = FakeSession({"PROPFIND": FakeResponse(207, propfind_listing)})
    client = WebDAVClient(BASE, session=session)
    res = client.list_dir("")
    names = {r.name for r in res}
    assert names == {"Documents", "report final.pdf", "notes.txt", "50% done.txt"}
    method, url, kwargs = session.calls[0]
    assert kwargs["headers"]["Depth"] == "1"
    assert url == BASE


def test_list_dir_subfolder_encodes_path(propfind_listing):
    session = FakeSession({"PROPFIND": FakeResponse(207, propfind_listing)})
    client = WebDAVClient(BASE, session=session)
    client.list_dir("My Docs/")
    _, url, _ = session.calls[0]
    # Space must be percent-encoded and resolved under the base path.
    assert url == "https://example.com/remote.php/dav/files/alice/My%20Docs/"


def test_list_dir_absolute_path(propfind_listing):
    session = FakeSession({"PROPFIND": FakeResponse(207, propfind_listing)})
    client = WebDAVClient(BASE, session=session)
    client.list_dir("/remote.php/dav/files/alice/Documents/")
    _, url, _ = session.calls[0]
    assert url == "https://example.com/remote.php/dav/files/alice/Documents/"


def test_download_returns_bytes_and_type():
    session = FakeSession(
        {"GET": FakeResponse(200, b"hello", {"Content-Type": "text/plain"})}
    )
    client = WebDAVClient(BASE, session=session)
    content, ctype = client.download("/remote.php/dav/files/alice/notes.txt")
    assert content == b"hello"
    assert ctype == "text/plain"
    _, url, _ = session.calls[0]
    assert url == "https://example.com/remote.php/dav/files/alice/notes.txt"


def test_download_relative_path_resolved():
    session = FakeSession({"GET": FakeResponse(200, b"x", {})})
    client = WebDAVClient(BASE, session=session)
    content, ctype = client.download("report final.pdf")
    _, url, _ = session.calls[0]
    assert url == "https://example.com/remote.php/dav/files/alice/report%20final.pdf"
    assert ctype == "application/octet-stream"


def test_download_error():
    session = FakeSession({"GET": FakeResponse(404)})
    client = WebDAVClient(BASE, session=session)
    with pytest.raises(WebDAVError, match="404"):
        client.download("missing.txt")


def test_base_url_required():
    with pytest.raises(WebDAVError):
        WebDAVClient("")


@pytest.mark.parametrize("bad", ["file:///etc/passwd", "ftp://host/x", "/no/scheme"])
def test_base_url_rejects_non_http_scheme(bad):
    with pytest.raises(WebDAVError):
        WebDAVClient(bad)


def test_browse_download_round_trip(propfind_listing):
    """A file id produced by listing must download from the correct URL.

    This guards the regression where file ids were lstripped of their leading
    slash, causing download() to re-prepend the base path and 404. The id a
    browse emits is the resource href; feeding it straight back into download()
    must hit the original server path with no duplication.
    """
    session = FakeSession(
        {
            "PROPFIND": FakeResponse(207, propfind_listing),
            "GET": FakeResponse(200, b"data", {"Content-Type": "text/plain"}),
        }
    )
    client = WebDAVClient(BASE, session=session)
    resources = client.list_dir("")
    file_res = next(r for r in resources if not r.is_dir and r.name == "notes.txt")

    # The datasource uses res.href verbatim as the OnlineDriveFile id.
    file_id = file_res.href
    assert file_id == "/remote.php/dav/files/alice/notes.txt"

    client.download(file_id)
    _, url, _ = session.calls[-1]
    # No base-path duplication.
    assert url == "https://example.com/remote.php/dav/files/alice/notes.txt"


def test_browse_download_round_trip_encoded(propfind_listing):
    """A file whose name needs percent-encoding round-trips correctly."""
    session = FakeSession(
        {
            "PROPFIND": FakeResponse(207, propfind_listing),
            "GET": FakeResponse(200, b"data", {}),
        }
    )
    client = WebDAVClient(BASE, session=session)
    resources = client.list_dir("")
    file_res = next(r for r in resources if r.name == "report final.pdf")

    client.download(file_res.href)
    _, url, _ = session.calls[-1]
    assert url == "https://example.com/remote.php/dav/files/alice/report%20final.pdf"


def test_browse_download_round_trip_percent_in_name(propfind_listing):
    """A filename containing a literal '%' must encode and download correctly.

    Regression guard: the percent must be re-escaped to %25, never left raw
    (a raw '%.' / '%2' is an invalid percent-escape the server rejects).
    """
    session = FakeSession(
        {
            "PROPFIND": FakeResponse(207, propfind_listing),
            "GET": FakeResponse(200, b"data", {}),
        }
    )
    client = WebDAVClient(BASE, session=session)
    resources = client.list_dir("")
    file_res = next(r for r in resources if r.name == "50% done.txt")
    assert file_res.href == "/remote.php/dav/files/alice/50% done.txt"

    client.download(file_res.href)
    _, url, _ = session.calls[-1]
    assert url == "https://example.com/remote.php/dav/files/alice/50%25%20done.txt"


def test_requests_do_not_follow_redirects(propfind_listing):
    """PROPFIND and GET must disable redirect-following (SSRF hardening)."""
    session = FakeSession(
        {
            "PROPFIND": FakeResponse(207, propfind_listing),
            "GET": FakeResponse(200, b"x", {}),
        }
    )
    client = WebDAVClient(BASE, session=session)
    client.list_dir("")
    client.download("/remote.php/dav/files/alice/notes.txt")
    for _method, _url, kwargs in session.calls:
        assert kwargs.get("allow_redirects") is False


def test_download_rejects_folder_id():
    session = FakeSession({"GET": FakeResponse(200, b"x", {})})
    client = WebDAVClient(BASE, session=session)
    with pytest.raises(WebDAVError, match="folder"):
        client.download("/remote.php/dav/files/alice/Documents/")
    assert session.calls == []


def test_download_rejects_oversized_by_content_length():
    session = FakeSession(
        {"GET": FakeResponse(200, b"x", {"Content-Length": "999999999"})}
    )
    client = WebDAVClient(BASE, session=session, max_download_bytes=10)
    with pytest.raises(WebDAVError, match="maximum download size"):
        client.download("/remote.php/dav/files/alice/big.bin")


def test_download_rejects_oversized_body():
    session = FakeSession({"GET": FakeResponse(200, b"0123456789AB", {})})
    client = WebDAVClient(BASE, session=session, max_download_bytes=10)
    with pytest.raises(WebDAVError, match="maximum download size"):
        client.download("/remote.php/dav/files/alice/big.bin")


class RaisingSession(FakeSession):
    """A session whose request/get raise a RequestException leaking the URL."""

    def __init__(self):
        super().__init__({})

    def request(self, method, url, **kwargs):
        raise requests.ConnectionError(
            "HTTPSConnectionPool(host='secret.example.com'): failed for url: "
            "https://alice:s3cr3t@secret.example.com/dav/"
        )

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


def test_redact_credentials_strips_userinfo():
    msg = redact_credentials("connect to https://alice:s3cr3t@host/dav/ failed")
    assert "s3cr3t" not in msg
    assert "alice" not in msg
    assert "https://host/dav/" in msg


def test_redact_credentials_leaves_plain_url():
    assert redact_credentials("https://host/dav/") == "https://host/dav/"


def test_check_does_not_leak_password_on_connection_error():
    client = WebDAVClient(
        "https://alice:s3cr3t@secret.example.com/dav/",
        session=RaisingSession(),
    )
    with pytest.raises(WebDAVError) as excinfo:
        client.check()
    assert "s3cr3t" not in str(excinfo.value)


def test_propfind_does_not_leak_password_on_connection_error():
    client = WebDAVClient(
        "https://alice:s3cr3t@secret.example.com/dav/",
        session=RaisingSession(),
    )
    with pytest.raises(WebDAVError) as excinfo:
        client.list_dir("")
    assert "s3cr3t" not in str(excinfo.value)


def test_download_does_not_leak_password_on_connection_error():
    client = WebDAVClient(
        "https://alice:s3cr3t@secret.example.com/dav/",
        session=RaisingSession(),
    )
    with pytest.raises(WebDAVError) as excinfo:
        client.download("/dav/notes.txt")
    assert "s3cr3t" not in str(excinfo.value)

import pytest

from webdav_client import WebDAVError, parse_propfind


def test_parse_nextcloud_listing(propfind_listing):
    res = parse_propfind(propfind_listing, "/remote.php/dav/files/alice/")
    by_name = {r.name: r for r in res}

    # The collection itself is excluded; only its children remain.
    assert set(by_name) == {
        "Documents",
        "report final.pdf",
        "notes.txt",
        "50% done.txt",
    }

    # A literal percent in the name must survive the encode/decode round-trip.
    pct = by_name["50% done.txt"]
    assert pct.is_dir is False
    assert pct.href == "/remote.php/dav/files/alice/50% done.txt"

    assert by_name["Documents"].is_dir is True
    assert by_name["Documents"].size == 0

    pdf = by_name["report final.pdf"]
    assert pdf.is_dir is False
    assert pdf.size == 102400
    assert pdf.content_type == "application/pdf"
    # href is URL-decoded (percent-encoded space resolved).
    assert pdf.href == "/remote.php/dav/files/alice/report final.pdf"

    assert by_name["notes.txt"].size == 42


def test_parser_ignores_404_propstat(propfind_listing):
    res = parse_propfind(propfind_listing, "/remote.php/dav/files/alice/")
    docs = next(r for r in res if r.name == "Documents")
    # getcontenttype came back as 404 -> must not be picked up.
    assert docs.content_type == ""


def test_parse_apache_absolute_hrefs(propfind_apache):
    res = parse_propfind(propfind_apache, "/dav/")
    by_name = {r.name: r for r in res}
    assert set(by_name) == {"sub", "readme.md"}
    assert by_name["sub"].is_dir is True
    assert by_name["readme.md"].is_dir is False
    assert by_name["readme.md"].size == 17
    assert by_name["readme.md"].href == "/dav/readme.md"


def test_parse_empty_returns_empty():
    # An empty body (and a well-formed non-DAV document) is an empty folder.
    assert parse_propfind(b"", "/dav/") == []
    assert parse_propfind(b"   ", "/dav/") == []
    assert parse_propfind(b"<not-dav/>", "/dav/") == []


def test_parse_broken_xml_raises():
    """A syntactically broken response must raise, not degrade to []."""
    with pytest.raises(WebDAVError, match="Malformed"):
        parse_propfind(b"<d:multistatus><d:response>", "/dav/")


def test_parse_rejects_doctype():
    """A DOCTYPE (XXE / billion-laughs vector) must be refused outright."""
    xxe = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE multistatus [<!ENTITY x "boom">]>'
        b'<d:multistatus xmlns:d="DAV:"></d:multistatus>'
    )
    with pytest.raises(WebDAVError, match="DOCTYPE"):
        parse_propfind(xxe, "/dav/")


def test_status_without_reason_phrase_is_honoured():
    """A 200 status line with no reason phrase must still yield props.

    RFC 7230 allows an empty reason phrase ("HTTP/1.1 200"), so props must be
    matched on the numeric code, not a " 200 " substring.
    """
    xml = (
        '<d:multistatus xmlns:d="DAV:">'
        "<d:response>"
        "<d:href>/dav/file.bin</d:href>"
        "<d:propstat>"
        "<d:prop>"
        "<d:resourcetype/>"
        "<d:getcontentlength>9</d:getcontentlength>"
        "<d:getcontenttype>application/octet-stream</d:getcontenttype>"
        "</d:prop>"
        "<d:status>HTTP/1.1 200</d:status>"
        "</d:propstat>"
        "</d:response>"
        "</d:multistatus>"
    ).encode("utf-8")
    res = parse_propfind(xml, "/dav/")
    assert len(res) == 1
    assert res[0].name == "file.bin"
    assert res[0].size == 9
    assert res[0].content_type == "application/octet-stream"

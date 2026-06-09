"""Parse a WebDAV PROPFIND multistatus (RFC 4918) response into resources."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from lxml import etree

from .errors import WebDAVError

DAV_NS = "DAV:"

# Hardened parser: the multistatus body comes from a remote (potentially hostile
# or MITM'd) WebDAV server. Disable entity resolution, DTD loading and network
# access to neutralise internal-entity expansion ("billion laughs") and XXE,
# and cap tree size (no huge_tree). recover is left at its default (False) so a
# syntactically broken response raises instead of degrading to a partial list.
_XML_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
)


@dataclass(frozen=True)
class WebDAVResource:
    """A single entry from a PROPFIND listing.

    ``href`` is the server path of the resource (URL-decoded). ``name`` is the
    last path segment. ``is_dir`` is True for collections (folders).
    """

    href: str
    name: str
    is_dir: bool
    size: int
    content_type: str


def _local(tag: str | None) -> str:
    """Return the local part of a possibly namespaced lxml tag."""
    if not tag:
        return ""
    return tag.rsplit("}", 1)[-1]


def _path_of(href: str) -> str:
    """Extract the URL-decoded path component from an href (absolute or not)."""
    parts = urlsplit(href)
    return unquote(parts.path or href)


def parse_propfind(xml: bytes | str, base_path: str) -> list[WebDAVResource]:
    """Parse PROPFIND XML into resources, excluding the collection itself.

    ``base_path`` is the URL-decoded path of the queried collection; the entry
    whose href equals it (the collection itself, returned at Depth: 1) is
    skipped so only children remain.
    """
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    if not xml.strip():
        return []
    try:
        root = etree.fromstring(xml, parser=_XML_PARSER)
    except etree.XMLSyntaxError as exc:
        # A broken/truncated multistatus must surface as an error rather than
        # silently degrade to a partial/empty listing, so a malfunctioning
        # server is distinguishable from a genuinely empty folder.
        raise WebDAVError(f"Malformed WebDAV PROPFIND response: {exc}") from exc
    if root is None:
        return []
    if root.getroottree().docinfo.doctype:
        # Reject any DOCTYPE outright (XXE / billion-laughs defence); a
        # legitimate WebDAV multistatus never carries one.
        raise WebDAVError(
            "WebDAV PROPFIND response contains a DOCTYPE; refusing to parse"
        )

    base_norm = base_path.rstrip("/")
    resources: list[WebDAVResource] = []

    for resp in root.iter("{%s}response" % DAV_NS):
        href_el = resp.find("{%s}href" % DAV_NS)
        if href_el is None or not href_el.text:
            continue
        path = _path_of(href_el.text)

        # Only consider 200 OK propstats.
        is_dir = False
        size = 0
        content_type = ""
        for propstat in resp.iter("{%s}propstat" % DAV_NS):
            status_el = propstat.find("{%s}status" % DAV_NS)
            if status_el is not None and status_el.text:
                # Status line is "HTTP/<ver> <code> [reason]"; the reason phrase
                # is optional (RFC 7230), so parse the numeric code rather than
                # substring-matching " 200 ". Only 2xx propstats carry valid props.
                parts = status_el.text.split()
                if len(parts) < 2 or not parts[1].startswith("2"):
                    continue
            prop = propstat.find("{%s}prop" % DAV_NS)
            if prop is None:
                continue
            for child in prop:
                name = _local(child.tag)
                if name == "resourcetype":
                    if child.find("{%s}collection" % DAV_NS) is not None:
                        is_dir = True
                elif name == "getcontentlength" and child.text:
                    try:
                        size = int(child.text.strip())
                    except ValueError:
                        size = 0
                elif name == "getcontenttype" and child.text:
                    content_type = child.text.strip()

        path_norm = path.rstrip("/")
        if path_norm == base_norm or path_norm == "":
            # The collection itself; skip.
            continue

        name = unquote(path_norm.rsplit("/", 1)[-1])
        resources.append(
            WebDAVResource(
                href=path,
                name=name,
                is_dir=is_dir,
                size=0 if is_dir else size,
                content_type=content_type,
            )
        )

    return resources

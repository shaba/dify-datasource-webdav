# WebDAV Datasource for Dify

Browse and ingest files from any WebDAV server — Nextcloud, ownCloud, Apache
`mod_dav`, sabre/dav, and other RFC 4918 compliant servers — into Dify as an
**online drive** datasource. Navigate folders, pick files, and Dify downloads
and ingests them.

## Configuration

Configure the datasource in Dify with three credentials:

| Field      | Required | Description |
|------------|----------|-------------|
| `base_url` | yes      | The WebDAV endpoint URL (a collection/folder). |
| `username` | no       | WebDAV account username (omit for anonymous servers). |
| `password` | no       | WebDAV account password or app token (stored as a secret). |

### Nextcloud example

Nextcloud and ownCloud expose per-user WebDAV under `/remote.php/dav/files/`:

```
https://example.com/remote.php/dav/files/<user>/
```

For a generic Apache `mod_dav` share the `base_url` is simply the share URL,
for example `https://example.com/dav/`.

Credentials are validated on save with a `PROPFIND` request against `base_url`.

## How it works

- **Browse** issues a `Depth: 1` `PROPFIND` against the selected folder and
  parses the multistatus XML into folders and files (name, size, type).
- **Download** issues a plain `GET` for the chosen file and streams its bytes
  (with the server's `Content-Type`) back to Dify for ingestion.
- Folder navigation uses the resource path as the id; files use their full
  server path.

The WebDAV protocol logic lives in the `webdav_client/` package (a thin
`PROPFIND` parser and HTTP client built on `requests` + `lxml`), independent of
the Dify SDK and unit-tested with mocked HTTP. The datasource class is a thin
adapter over the SDK's `OnlineDriveDatasource` interface.

## Behaviour and caveats

- **A broken or partial XML response raises rather than returning an empty
  listing.** The `PROPFIND` parser uses a hardened lxml configuration (no DTD,
  no entity resolution, no network, no huge trees) and rejects any document
  carrying a `DOCTYPE` (XXE / "billion laughs" defence). A syntactically broken
  multistatus body now raises an error instead of silently degrading to a
  partial or empty list — so a malfunctioning server is distinguishable from a
  genuinely empty folder. **An empty folder** is a well-formed multistatus that
  lists only the collection itself (which is excluded), yielding `[]`.
- **Redirects are intentionally not followed** (`allow_redirects=False`) as an
  SSRF guard, so a legitimate `301`/`302` from the server surfaces as an
  "unexpected response" error rather than being chased. Point `base_url` at the
  final, canonical collection URL.
- **The pagination cursor re-reads the directory between pages.** WebDAV
  `PROPFIND` has no server-side paging, so each page re-lists the folder and
  slices it client-side. If the folder changes mid-crawl you can get duplicate
  or skipped entries between pages — acceptable for ingest, but not a
  point-in-time snapshot.
- **Credentials are never echoed in error text.** If `base_url` embeds
  `user:pass@`, any `user:pass@` userinfo is stripped from error messages
  before they reach Dify / the LLM.

## Development

This plugin uses [uv](https://docs.astral.sh/uv/) with `pyproject.toml` +
`uv.lock` (there is no `requirements.txt`).

```sh
uv sync
uv run ruff check .
uv run pytest -q
```

Without uv, create a virtualenv and install the runtime + dev tools manually:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e . ruff pytest
ruff check .
python -m pytest -q
```

## License

Apache-2.0 © 2026 Alexey Shabalin

## Repository

https://github.com/shaba/dify-datasource-webdav

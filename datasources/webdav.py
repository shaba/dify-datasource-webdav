from collections.abc import Generator

from dify_plugin.entities.datasource import (
    DatasourceMessage,
    OnlineDriveBrowseFilesRequest,
    OnlineDriveBrowseFilesResponse,
    OnlineDriveDownloadFileRequest,
    OnlineDriveFile,
    OnlineDriveFileBucket,
)
from dify_plugin.interfaces.datasource.online_drive import OnlineDriveDatasource

from webdav_client import WebDAVClient

# WebDAV has no concept of buckets; we expose one logical store at the base URL.
BUCKET = "webdav"


class WebDAVDataSource(OnlineDriveDatasource):
    def _client(self) -> WebDAVClient:
        credentials = self.runtime.credentials
        if not credentials:
            raise ValueError("Credentials not found")
        base_url = str(credentials.get("base_url") or "").strip()
        if not base_url:
            raise ValueError("WebDAV URL not configured")
        return WebDAVClient(
            base_url=base_url,
            username=str(credentials.get("username") or ""),
            password=str(credentials.get("password") or ""),
        )

    def _browse_files(
        self, request: OnlineDriveBrowseFilesRequest
    ) -> OnlineDriveBrowseFilesResponse:
        client = self._client()

        # No bucket selected yet -> advertise the single WebDAV store.
        if not request.bucket:
            return OnlineDriveBrowseFilesResponse(
                result=[
                    OnlineDriveFileBucket(
                        bucket=BUCKET,
                        files=[],
                        is_truncated=False,
                        next_page_parameters={},
                    )
                ]
            )

        next_page = request.next_page_parameters or {}
        # Prefer the directory echoed into the cursor (a paged follow-up call) so
        # listing stays anchored even if the SDK drops request.prefix on page 2+.
        prefix = request.prefix or next_page.get("prefix") or ""
        resources = client.list_dir(prefix)

        files = [
            OnlineDriveFile(
                # Ids are absolute server paths (folders keep a trailing slash so
                # we can navigate into them). Keeping the leading slash for files
                # too lets download() route them through its absolute-path branch
                # instead of re-prepending the base path.
                id=res.href,
                name=res.name,
                size=res.size,
                type="folder" if res.is_dir else "file",
            )
            for res in resources
        ]
        files.sort(key=lambda f: (f.type != "folder", f.name.lower()))

        # WebDAV (PROPFIND) has no server-side paging, so we slice the full
        # listing client-side into max_keys-sized pages and emit an offset
        # cursor, honouring the OnlineDrive paging contract.
        page_size = request.max_keys or len(files)
        start = 0
        if next_page:
            try:
                start = int(next_page.get("offset", 0))
            except (TypeError, ValueError):
                start = 0
        end = start + page_size if page_size else len(files)
        page = files[start:end]
        is_truncated = end < len(files)

        # Echo bucket/prefix back in the cursor so paged follow-up calls re-list
        # the same directory even if the SDK does not preserve request.prefix.
        next_params = (
            {"offset": end, "bucket": request.bucket, "prefix": prefix}
            if is_truncated
            else {}
        )
        bucket = OnlineDriveFileBucket(
            bucket=request.bucket,
            files=page,
            is_truncated=is_truncated,
            next_page_parameters=next_params,
        )
        return OnlineDriveBrowseFilesResponse(result=[bucket])

    def _download_file(
        self, request: OnlineDriveDownloadFileRequest
    ) -> Generator[DatasourceMessage, None, None]:
        client = self._client()
        file_id = request.id
        if not file_id:
            raise ValueError("File id not found")
        # Folder ids carry a trailing slash; they are not downloadable.
        if file_id.endswith("/"):
            raise ValueError("Cannot download a folder")

        content, content_type = client.download(file_id)
        file_name = file_id.rstrip("/").rsplit("/", 1)[-1]

        yield self.create_blob_message(
            content,
            meta={"file_name": file_name, "mime_type": content_type},
        )

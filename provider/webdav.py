from collections.abc import Mapping
from typing import Any

from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from dify_plugin.interfaces.datasource import DatasourceProvider

from webdav_client import WebDAVClient, WebDAVError, redact_credentials


class WebDAVDatasourceProvider(DatasourceProvider):
    def _validate_credentials(self, credentials: Mapping[str, Any]) -> None:
        base_url = str(credentials.get("base_url") or "").strip()
        if not base_url:
            raise ToolProviderCredentialValidationError("WebDAV URL is required.")

        client = WebDAVClient(
            base_url=base_url,
            username=str(credentials.get("username") or ""),
            password=str(credentials.get("password") or ""),
        )
        try:
            client.check()
        except WebDAVError as exc:
            raise ToolProviderCredentialValidationError(
                redact_credentials(exc)
            ) from exc

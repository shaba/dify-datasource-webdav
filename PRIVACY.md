# Privacy

This plugin connects to the WebDAV server you configure to browse folders and
download the files you select for ingestion. Requests go only to that server,
using the credentials you provide over HTTPS (when the endpoint uses HTTPS).

The plugin sends no data to third parties. Your WebDAV URL, username and
password are stored by your Dify instance as a workspace credential and are
not persisted anywhere else. The plugin itself keeps nothing between calls.
File content is passed to Dify solely for the ingestion you initiate.
